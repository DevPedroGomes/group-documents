"""
Hybrid search combining semantic (pgvector HNSW) + keyword (tsvector GIN)
with Reciprocal Rank Fusion (RRF).
"""

import logging
import uuid as uuid_mod
from typing import Optional

from sqlalchemy import insert, text as sqltext

from app.config.settings import get_settings
from app.db.engine import engine
from app.services.embedding import embed_documents

logger = logging.getLogger(__name__)


def add_chunks(
    texts: list[str],
    enriched_texts: list[str],
    embeddings: list[list[float]],
    user_id: str,
    document_id: str,
    pages: list[int],
    chunk_indices: list[int],
) -> int:
    """Insert chunks with embeddings into pgvector. Returns count inserted.

    `texts` are the raw chunk texts, `enriched_texts` are the contextually-enriched
    versions used for embedding (may equal texts for non-PDF flows).
    """
    from app.db.models import chunks

    recs = []
    for i, (txt, etxt, emb) in enumerate(zip(texts, enriched_texts, embeddings)):
        recs.append({
            "user_id": user_id,
            "document_id": document_id,
            "page": pages[i],
            "chunk_index": chunk_indices[i],
            "content": txt,
            "enriched_content": etxt if etxt and etxt != txt else None,
            "embedding": emb,
        })

    with engine.begin() as conn:
        conn.execute(insert(chunks), recs)

    return len(recs)


def hybrid_search(
    query_embedding: list[float],
    query_text: str,
    user_id: str,
    top_k: int = 5,
    document_ids: Optional[list[str]] = None,
    as_of: Optional[str] = None,
) -> list[dict]:
    """
    Hybrid search: semantic (pgvector) + keyword (tsvector) fused with RRF.
    Returns top_k results sorted by combined RRF score.

    `user_id` is REQUIRED — every chunk read is filtered by ownership.

    `as_of` responde com o acervo COMO ELE ESTAVA numa data: so entram
    documentos ingeridos ate ali. E o que separa consulta de auditoria, e sai
    barato porque a ingestao ja registra `created_at` em `documents`. Filtra
    pelo documento, nao pelo chunk: reprocessar um documento nao deve fazer ele
    aparecer num recorte anterior a existencia dele.

    Sobre o campo `snippet`, que e o texto que chega ao gerador:

    1. Vem de `content` (o chunk cru), nao de `enriched_content`. O contexto
       escrito pela IA no enriquecimento existe para melhorar o EMBEDDING, que
       e onde ele entra. Mandar esse resumo para o gerador faz o modelo ler o
       resumo que outro modelo escreveu, em vez do documento — ruido e risco de
       alucinacao, sem ganho.
    2. O corte era em 500 caracteres. Chunks tem 500 TOKENS (~2000 caracteres),
       entao o gerador recebia menos de um terco do chunk, cortado no meio de
       uma frase. Com enriquecimento ficava pior: o prefixo de contexto ocupava
       ~300 desses 500 e sobravam ~200 de documento de verdade. Media as duas
       coisas e a feature de qualidade estava degradando a resposta.
       O teto de 4000 aqui e so um limite de sanidade, acima do tamanho de um
       chunk; nao corta conteudo em uso normal.

    O preview curto das citacoes na UI continua sendo cortado por quem exibe.
    """
    settings = get_settings()
    prefetch = top_k * settings.search_candidates_multiplier

    qvec_str = "[" + ",".join(map(str, query_embedding)) + "]"

    # Build WHERE clause for optional document filtering
    doc_filter = ""
    params = {
        "qvec": qvec_str,
        "query_text": query_text,
        "user_id": user_id,
        "limit": prefetch,
    }

    data_filter = ""
    if as_of:
        params["as_of"] = as_of
        data_filter = "AND d.created_at <= CAST(:as_of AS timestamptz)"

    if document_ids:
        valid_ids = []
        for did in document_ids:
            try:
                uuid_mod.UUID(did)
                valid_ids.append(did)
            except (ValueError, AttributeError):
                continue
        if valid_ids:
            params["document_ids"] = "{" + ",".join(valid_ids) + "}"
            doc_filter = "AND c.document_id = ANY(CAST(:document_ids AS uuid[]))"

    # 1. Semantic search (pgvector HNSW)
    semantic_sql = sqltext(f"""
        SELECT c.id, c.document_id, d.title as document_title, c.page,
               left(c.content, 4000) as snippet,
               1 - (c.embedding <=> CAST(:qvec AS vector)) as score
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE 1 - (c.embedding <=> CAST(:qvec AS vector)) >= 0.1
        AND c.user_id = CAST(:user_id AS uuid)
        {doc_filter}
        {data_filter}
        ORDER BY c.embedding <=> CAST(:qvec AS vector)
        LIMIT :limit
    """)

    # 2. Keyword search (tsvector GIN)
    keyword_sql = sqltext(f"""
        SELECT c.id, c.document_id, d.title as document_title, c.page,
               left(c.content, 4000) as snippet,
               ts_rank(c.search_vector, plainto_tsquery('english', :query_text)) as score
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE c.search_vector @@ plainto_tsquery('english', :query_text)
        AND c.user_id = CAST(:user_id AS uuid)
        {doc_filter}
        {data_filter}
        ORDER BY score DESC
        LIMIT :limit
    """)

    with engine.begin() as conn:
        semantic_rows = conn.execute(semantic_sql, params).mappings().all()
        keyword_rows = conn.execute(keyword_sql, params).mappings().all()

    # 3. Reciprocal Rank Fusion (RRF)
    k = settings.rrf_k
    scores: dict[str, dict] = {}

    for rank, row in enumerate(semantic_rows):
        rid = str(row["id"])
        rrf = 1.0 / (k + rank + 1)
        if rid not in scores:
            scores[rid] = {"score": 0.0, "data": dict(row)}
        scores[rid]["score"] += rrf

    for rank, row in enumerate(keyword_rows):
        rid = str(row["id"])
        rrf = 1.0 / (k + rank + 1)
        if rid not in scores:
            scores[rid] = {"score": 0.0, "data": dict(row)}
        scores[rid]["score"] += rrf

    # Sort by combined RRF score
    sorted_results = sorted(scores.values(), key=lambda x: x["score"], reverse=True)

    results = []
    for item in sorted_results[:top_k]:
        data = item["data"]
        results.append({
            "id": str(data["id"]),
            "document_id": str(data["document_id"]),
            "document_title": data["document_title"],
            "page": data["page"],
            "snippet": data["snippet"],
            "relevance_score": item["score"],
            # A escala viaja junto com o numero. Sem isto o grader compara um
            # score de RRF (maximo 2/(k+1), ~0,033 com k=60) contra um limiar
            # pensado para a relevancia 0..1 da Cohere, e descarta tudo.
            "score_scale": "rrf",
        })

    return results

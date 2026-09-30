"""
Two-stage retriever: Multi-query → Hybrid Search → Rerank.
Entry point for all document retrieval.
"""

import logging
import re
from typing import NamedTuple, Optional

from app.config.settings import get_settings
from app.core.llm_client import chat_complete
from app.services.embedding_cache import get_query_embeddings
from app.services.vector_store import hybrid_search
from app.core.rag.reranker import rerank_documents, reranker_ativo

logger = logging.getLogger(__name__)

# A condensacao so precisa do fio recente da conversa. Mais que isso encarece
# a chamada e puxa a pergunta para assuntos que a pessoa ja deixou para tras.
_MENSAGENS_PARA_CONDENSAR = 6
_MAX_CHARS_POR_MENSAGEM = 600

# Numeracao, marcador de lista e rotulo que o modelo as vezes poe na linha.
_PREFIXO = re.compile(
    r"^\s*(?:[-*\u2022]+\s*|\d{1,2}[.)]\s+)?(?:(?:standalone\s+)?(?:question|query)\s*:\s*)?",
    re.IGNORECASE,
)


class Recuperacao(NamedTuple):
    """O que a busca devolveu e com quais consultas.

    `queries` vai para a trilha (`decisions.queries`): sem ela, "por que ele
    achou isso?" nao tem resposta quando a pergunta foi condensada.
    """

    documents: list[dict]
    queries: list[str]


def _limpar_linha(linha: str) -> str:
    return _PREFIXO.sub("", linha).strip().strip("\"'").strip()


def _sem_repeticao(consultas: list[str]) -> list[str]:
    vistas: set[str] = set()
    saida = []
    for c in consultas:
        chave = c.casefold()
        if c and chave not in vistas:
            vistas.add(chave)
            saida.append(c)
    return saida


def _conversa(historico: list[dict]) -> str:
    linhas = []
    for msg in historico:
        papel = "User" if msg.get("role") == "user" else "Assistant"
        texto = " ".join(str(msg.get("content") or "").split())[:_MAX_CHARS_POR_MENSAGEM]
        linhas.append(f"{papel}: {texto}")
    return "\n".join(linhas)


def generate_multi_queries(question: str, history: Optional[list[dict]] = None) -> list[str]:
    """Devolve as consultas a buscar: a principal primeiro, depois as variantes.

    Sem historico, a principal e a propria pergunta. Com historico, a PRIMEIRA
    linha da resposta do modelo e a pergunta reescrita para se sustentar sozinha
    ("e em marco de 2025?" vira "qual o prazo de entrega em marco de 2025?") e
    substitui a original na busca; as demais sao variantes. Falha na chamada ou
    resposta vazia: busca com a pergunta original.
    """
    settings = get_settings()
    recentes = list(history or [])[-_MENSAGENS_PARA_CONDENSAR:]
    n = settings.multi_query_count

    if recentes:
        conteudo = (
            "Below is a conversation between a person and an assistant that answers "
            "from the person's documents, followed by the person's latest message.\n\n"
            f"<conversation>\n{_conversa(recentes)}\n</conversation>\n\n"
            f'Latest message: "{question}"\n\n'
            "First, rewrite the latest message as a standalone search question that can "
            "be understood without the conversation: resolve references such as \"and in "
            "March 2025?\" or \"what about the other contract?\" using the conversation, "
            "keep the language of the latest message, and do not answer it. If it is "
            "already standalone, repeat it unchanged.\n"
            f"Then write {n} different search queries for that standalone question, each "
            "approaching it from a different angle (synonyms, related concepts, specific "
            "aspects).\n\n"
            "Return ONLY plain lines: the standalone question on the first line, then one "
            "query per line. No numbering, no bullets, no labels."
        )
    else:
        conteudo = (
            f"Generate {n} different search queries "
            f"that would help find information to answer this question:\n\n"
            f'"{question}"\n\n'
            "Each query should approach the topic from a different angle "
            "(synonyms, related concepts, specific aspects).\n"
            "Return ONLY the queries, one per line, no numbering or bullets."
        )

    try:
        text = chat_complete(
            model=settings.fast_model,
            max_tokens=300,
            messages=[{"role": "user", "content": conteudo}],
        )
    except Exception as e:
        logger.warning(f"Multi-query generation failed: {e}")
        return [question]

    linhas = [l for l in (_limpar_linha(x) for x in (text or "").splitlines()) if l]
    if recentes:
        if not linhas:
            return [question]
        principal, variantes = linhas[0], linhas[1:]
    else:
        principal, variantes = question, linhas
    return _sem_repeticao([principal] + variantes)[: n + 1]


def _candidatos(
    consultas: list[str],
    user_id: str,
    top_k: int,
    document_ids: Optional[list[str]],
    as_of: Optional[str],
) -> list[dict]:
    """Busca hibrida por consulta, fundida por chunk (maior score RRF fica)."""
    settings = get_settings()
    search_top_k = top_k * settings.search_candidates_multiplier
    all_results: dict[str, dict] = {}  # keyed by chunk id to deduplicate

    # Todos os embeddings numa chamada so. Um por vez eram 4 requisicoes por
    # pergunta contra o limite de 3/min do plano Voyage sem cartao: toda
    # pergunta inedita estourava.
    embeddings = get_query_embeddings(consultas)

    for q, query_embedding in zip(consultas, embeddings):
        results = hybrid_search(
            query_embedding=query_embedding,
            query_text=q,
            user_id=user_id,
            top_k=search_top_k,
            document_ids=document_ids,
            as_of=as_of,
        )
        for r in results:
            rid = r["id"]
            if rid not in all_results or r["relevance_score"] > all_results[rid]["relevance_score"]:
                all_results[rid] = r

    return sorted(
        all_results.values(),
        key=lambda x: x["relevance_score"],
        reverse=True,
    )[:search_top_k]


def retrieve_documents(
    question: str,
    user_id: str,
    document_ids: Optional[list[str]] = None,
    as_of: Optional[str] = None,
    top_k: int = 5,
    history: Optional[list[dict]] = None,
) -> Recuperacao:
    """
    Full retrieval pipeline:
    1. Multi-query (condensing a follow-up question when there is history)
    2. For each query: embed → hybrid search
    3. Merge & deduplicate results
    4. Rerank with Cohere cross-encoder, against the main query
    5. Return top_k, plus the queries actually searched

    `user_id` is REQUIRED for tenant isolation.
    """
    consultas = generate_multi_queries(question, history)
    candidates = _candidatos(consultas, user_id, top_k, document_ids, as_of)

    if not candidates:
        return Recuperacao([], consultas)

    # A consulta principal (condensada, quando havia historico) e a que o
    # reranker julga: o fragmento "e em marco?" sozinho nao diz o que procurar.
    reranked = rerank_documents(
        query=consultas[0],
        documents=candidates,
        top_n=top_k,
    )

    return Recuperacao(reranked, consultas)


def _fundir_por_id(*lotes: list[dict]) -> list[dict]:
    """Uniao dos lotes por chunk, com o MAIOR score, do melhor para o pior.

    So faz sentido com os lotes na mesma escala; quem chama garante isso.
    """
    por_id: dict[str, dict] = {}
    for lote in lotes:
        for d in lote:
            atual = por_id.get(d["id"])
            if atual is None or d["relevance_score"] > atual["relevance_score"]:
                por_id[d["id"]] = d
    return sorted(por_id.values(), key=lambda d: d["relevance_score"], reverse=True)


def reconsultar(
    consulta: str,
    pergunta: str,
    anteriores: list[dict],
    user_id: str,
    document_ids: Optional[list[str]] = None,
    as_of: Optional[str] = None,
    top_k: int = 5,
) -> list[dict]:
    """Passo corretivo: UMA busca com a consulta reescrita, fundida ao lote anterior.

    Antes a reescrita so alimentava a busca web e o acervo nunca era
    reconsultado. Sem multi-query aqui: o rewrite ja e a variante.

    Escala: o lote anterior pode vir em "cohere" e a busca nova sempre vem em
    "rrf". Com o reranker ligado, a uniao inteira e reordenada contra
    `pergunta` (a mesma consulta que o grader julga, e nao o rewrite, para o
    score continuar medindo se o trecho responde a pessoa). Se esse rerank
    falhar, o resultado misturaria escalas, entao fica o lote anterior. Sem
    reranker os dois lotes estao em RRF e a fusao por maior score e honesta.
    """
    novos = _candidatos([consulta], user_id, top_k, document_ids, as_of)
    if not novos:
        return list(anteriores)

    if reranker_ativo():
        uniao = list({d["id"]: d for d in [*novos, *anteriores]}.values())
        reordenados = rerank_documents(query=pergunta, documents=uniao, top_n=top_k)
        if all(d.get("score_scale") == "cohere" for d in reordenados):
            return reordenados
        if any(d.get("score_scale") == "cohere" for d in anteriores):
            logger.warning("rerank da reconsulta falhou; mantendo o lote anterior")
            return list(anteriores)

    return _fundir_por_id(anteriores, novos)[:top_k]

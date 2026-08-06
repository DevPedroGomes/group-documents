"""
Semantic chunking with contextual enrichment.

Chunking: tiktoken-based recursive splitting with overlap.
Enrichment: Claude Haiku generates context per chunk (Anthropic's contextual retrieval technique).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import tiktoken

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_encoder = tiktoken.encoding_for_model("gpt-4o")


def _token_count(text: str) -> int:
    return len(_encoder.encode(text))


def chunk_text(text: str, max_tokens: int = 500, overlap: int = 100) -> list[str]:
    """
    Split text into chunks of max_tokens with overlap.
    Uses sentence boundaries for semantic coherence.
    """
    import re

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current_chunk: list[str] = []
    current_tokens = 0

    for sent in sentences:
        sent_tokens = _token_count(sent)

        if current_tokens + sent_tokens > max_tokens and current_chunk:
            chunks.append(" ".join(current_chunk))

            # Keep overlap: walk backwards to find sentences that fit in overlap
            overlap_chunk: list[str] = []
            overlap_tokens = 0
            for s in reversed(current_chunk):
                s_tokens = _token_count(s)
                if overlap_tokens + s_tokens > overlap:
                    break
                overlap_chunk.insert(0, s)
                overlap_tokens += s_tokens

            current_chunk = overlap_chunk + [sent]
            current_tokens = overlap_tokens + sent_tokens
        else:
            current_chunk.append(sent)
            current_tokens += sent_tokens

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


def chunk_document_pages(
    pages: list[str],
) -> list[tuple[str, dict]]:
    """
    Chunk a document's pages into (text, metadata) tuples.
    Metadata includes page number and chunk index.
    """
    settings = get_settings()
    all_chunks = []
    global_idx = 0

    for page_num, page_text in enumerate(pages, start=1):
        if not page_text.strip():
            continue

        page_chunks = chunk_text(
            page_text,
            max_tokens=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )

        for chunk in page_chunks:
            metadata = {
                "page": page_num,
                "chunk_index": global_idx,
            }
            all_chunks.append((chunk, metadata))
            global_idx += 1

    return all_chunks


_INSTRUCAO_CONTEXTO = (
    "Give a short succinct context (2-3 sentences) to situate this chunk "
    "within the overall document. Answer only with the context, no preamble."
)

# Quantos chunks enriquecer em paralelo DEPOIS que o cache esta quente. Modesto
# de proposito: a VPS tem 2 vCPU e estas chamadas ja sao I/O, nao CPU.
_PARALELISMO = 4


def _contexto_de_um_chunk(doc_block: dict, chunk_text_str: str, meta: dict) -> str:
    """Gera o contexto de um chunk. Devolve o chunk cru se a chamada falhar."""
    from app.core.llm_client import chat_complete

    settings = get_settings()
    try:
        contexto = chat_complete(
            model=settings.fast_model,
            max_tokens=150,
            messages=[
                {
                    "role": "user",
                    # Dois blocos, nao uma string: o primeiro carrega o
                    # documento e o marcador de cache, o segundo carrega a parte
                    # que muda a cada chunk. So o que vem DEPOIS do marcador e
                    # cobrado como input novo.
                    "content": [
                        doc_block,
                        {
                            "type": "text",
                            "text": (
                                f"Here is a chunk from this document:\n"
                                f"<chunk>\n{chunk_text_str}\n</chunk>\n\n"
                                f"{_INSTRUCAO_CONTEXTO}"
                            ),
                        },
                    ],
                }
            ],
        ).strip()
        return f"{contexto}\n\n{chunk_text_str}"
    except Exception as e:
        logger.warning(f"Contextual enrichment failed for chunk {meta['chunk_index']}: {e}")
        return chunk_text_str


def enrich_chunks_with_context(
    chunks: list[tuple[str, dict]],
    full_document_text: str,
    document_title: str,
) -> list[tuple[str, dict]]:
    """
    Contextual Retrieval da Anthropic: um modelo rapido (classe Haiku) escreve
    50-100 tokens situando cada chunk no documento, prefixados antes do embedding.
    Reduz falha de recuperacao em ~49%, e ~67% junto com reranking.

    O detalhe que decide o custo e o prompt caching. O documento inteiro (ate
    50k caracteres, ~12,5k tokens) vai como input de TODA chamada — uma por
    chunk. Sem cache, um PDF de 30 paginas (~50 chunks) manda ~650k tokens de
    input e custa por volta de US$ 0,69 com Haiku 4.5. Marcando o bloco do
    documento como cacheavel, a primeira chamada grava o cache e as seguintes
    leem a 10% do preco: ~US$ 0,10 pelo mesmo documento, ~85% mais barato. E a
    mesma ordem de grandeza que a propria Anthropic publica para esta tecnica
    (US$ 94 -> US$ 12 em 1000 documentos).

    Por isso o primeiro chunk roda SOZINHO: ele e quem grava o cache. Disparar
    todos de uma vez faria todos errarem o cache ao mesmo tempo e cada um
    pagaria a gravacao — o contrario do que se quer. Com o cache quente, o resto
    vai em paralelo, o que tambem tira a ingestao de ~2 minutos sequenciais.

    O cache efemero da Anthropic dura ~5 minutos e cada chamada renova a
    janela, entao um documento longo se mantem quente do inicio ao fim.
    """
    if not chunks:
        return []

    doc_context = full_document_text[:50000]
    doc_block = {
        "type": "text",
        "text": f'<document title="{document_title}">\n{doc_context}\n</document>',
        # Funciona nos dois caminhos do llm_client: nativo Anthropic e
        # OpenRouter (que repassa cache_control por bloco de conteudo).
        # Prompts curtos ficam abaixo do minimo cacheavel e simplesmente nao
        # sao cacheados — sem erro, e sem custo relevante, porque documento
        # curto tem poucos chunks.
        "cache_control": {"type": "ephemeral"},
    }

    resultados: list[Optional[str]] = [None] * len(chunks)

    # 1) Primeiro chunk sozinho: grava o cache.
    primeiro_texto, primeiro_meta = chunks[0]
    resultados[0] = _contexto_de_um_chunk(doc_block, primeiro_texto, primeiro_meta)

    # 2) O resto em paralelo, ja lendo do cache.
    if len(chunks) > 1:
        with ThreadPoolExecutor(max_workers=_PARALELISMO) as pool:
            futuros = {
                pool.submit(_contexto_de_um_chunk, doc_block, texto, meta): i
                for i, (texto, meta) in enumerate(chunks[1:], start=1)
            }
            for futuro in as_completed(futuros):
                i = futuros[futuro]
                try:
                    resultados[i] = futuro.result()
                except Exception as e:
                    # `_contexto_de_um_chunk` ja trata as suas falhas; isto aqui
                    # e a rede de baixo, para nao perder o chunk se o pool
                    # quebrar por outro motivo.
                    logger.warning(f"Enrichment worker failed for chunk {i}: {e}")
                    resultados[i] = chunks[i][0]

    # A ordem importa: `add_chunks` casa texts[i] com enriched_texts[i].
    return [(resultados[i] or chunks[i][0], chunks[i][1]) for i in range(len(chunks))]

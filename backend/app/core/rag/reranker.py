"""Cohere cross-encoder reranking for precision improvement."""

import logging
from typing import TYPE_CHECKING, Optional

from app.config.settings import get_settings
from app.core import chamadas_pagas

if TYPE_CHECKING:
    import cohere

logger = logging.getLogger(__name__)

_client: Optional["cohere.ClientV2"] = None


def _get_client() -> "cohere.ClientV2":
    # ClientV2 e o cliente da API v2; o `cohere.Client` v1 e o legado. A
    # assinatura de `rerank` foi conferida contra o SDK instalado (7.0.9).
    import cohere as _cohere

    global _client
    if _client is None:
        settings = get_settings()
        _client = _cohere.ClientV2(api_key=settings.cohere_api_key)
    return _client


def reranker_ativo() -> bool:
    settings = get_settings()
    return bool(settings.enable_reranking and settings.cohere_api_key)


def rerank_documents(
    query: str,
    documents: list[dict],
    top_n: int = 5,
) -> list[dict]:
    """
    Rerank documents using Cohere cross-encoder.
    Falls back to original order if Cohere is unavailable.

    Cada documento volta como veio (copia do dict, com `document_date` e o
    resto), so com `relevance_score` e `score_scale` trocados.
    """
    settings = get_settings()

    if not reranker_ativo():
        return documents[:top_n]

    if len(documents) <= 1:
        return documents

    try:
        client = _get_client()
        texts = [doc["snippet"] for doc in documents]

        response = client.rerank(
            model=settings.cohere_rerank_model,
            query=query,
            documents=texts,
            top_n=min(top_n, len(documents)),
        )
        chamadas_pagas.registrar("cohere")

        reranked = []
        for result in response.results:
            doc = documents[result.index].copy()
            doc["relevance_score"] = result.relevance_score
            # So aqui o score passa a ser relevancia calibrada 0..1. E a unica
            # situacao em que o limiar absoluto do grader faz sentido.
            doc["score_scale"] = "cohere"
            reranked.append(doc)

        return reranked

    except Exception as e:
        logger.error(f"Cohere rerank failed, using original order: {e}")
        return documents[:top_n]

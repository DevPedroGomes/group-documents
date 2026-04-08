"""Cohere cross-encoder reranking for precision improvement."""

import logging
from typing import TYPE_CHECKING, Optional

from app.config.settings import get_settings

if TYPE_CHECKING:
    import cohere

logger = logging.getLogger(__name__)

_client: Optional["cohere.Client"] = None


def _get_client() -> "cohere.Client":
    import cohere as _cohere

    global _client
    if _client is None:
        settings = get_settings()
        _client = _cohere.Client(api_key=settings.cohere_api_key)
    return _client


def rerank_documents(
    query: str,
    documents: list[dict],
    top_n: int = 5,
) -> list[dict]:
    """
    Rerank documents using Cohere cross-encoder.
    Falls back to original order if Cohere is unavailable.
    """
    settings = get_settings()

    if not settings.enable_reranking or not settings.cohere_api_key:
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
            return_documents=False,
        )

        reranked = []
        for result in response.results:
            doc = documents[result.index].copy()
            doc["relevance_score"] = result.relevance_score
            reranked.append(doc)

        return reranked

    except Exception as e:
        logger.error(f"Cohere rerank failed, using original order: {e}")
        return documents[:top_n]

import voyageai

from app.config.settings import get_settings

_client = None


def _get_client() -> voyageai.Client:
    global _client
    if _client is None:
        settings = get_settings()
        _client = voyageai.Client(api_key=settings.voyage_api_key)
    return _client


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document chunks using Voyage 4 Large (optimized for documents)."""
    if not texts:
        return []
    settings = get_settings()
    client = _get_client()
    result = client.embed(
        texts,
        model=settings.voyage_doc_model,
        input_type="document",
    )
    vectors = result.embeddings
    if len(vectors) != len(texts):
        raise RuntimeError(f"Embedding count mismatch: expected {len(texts)}, got {len(vectors)}")
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a search query using Voyage 4 Lite (optimized for queries)."""
    settings = get_settings()
    client = _get_client()
    result = client.embed(
        [text],
        model=settings.voyage_query_model,
        input_type="query",
    )
    return result.embeddings[0]

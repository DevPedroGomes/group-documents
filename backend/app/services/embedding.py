import voyageai

from app.config.settings import get_settings

_client = None


def _get_client() -> voyageai.Client:
    global _client
    if _client is None:
        settings = get_settings()
        _client = voyageai.Client(api_key=settings.voyage_api_key)
    return _client


def _assert_dim(vector: list[float], model: str) -> None:
    """Falha cedo e com mensagem clara se o modelo mudar de dimensao.

    Sem isto o erro so aparece no INSERT, como
    `expected 1536 dimensions, not 1024` — que aponta pro banco e nao pro
    modelo, que e a causa real. Foi assim que a ingestao ficou 100% quebrada
    por meses sem ninguem notar.
    """
    expected = get_settings().embedding_dimensions
    if len(vector) != expected:
        raise RuntimeError(
            f"Embedding dim mismatch: modelo '{model}' devolveu {len(vector)} dims, "
            f"mas o schema espera {expected}. Alinhe EMBEDDING_DIMENSIONS, o "
            f"vector(N) das migrations e o modelo — os tres tem que bater."
        )


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
    _assert_dim(vectors[0], settings.voyage_doc_model)
    return vectors


def embed_queries(texts: list[str]) -> list[list[float]]:
    """Embed varias queries numa UNICA chamada ao provider.

    A API aceita lista. O custo em tokens e o mesmo de chamar uma a uma, mas
    conta como 1 requisicao em vez de N — e o limite do plano Voyage sem meio
    de pagamento e por REQUISICAO (3 por minuto), nao por token.
    """
    if not texts:
        return []
    settings = get_settings()
    client = _get_client()
    result = client.embed(
        texts,
        model=settings.voyage_query_model,
        input_type="query",
    )
    vectors = result.embeddings
    if len(vectors) != len(texts):
        raise RuntimeError(
            f"Embedding count mismatch: expected {len(texts)}, got {len(vectors)}"
        )
    _assert_dim(vectors[0], settings.voyage_query_model)
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a search query. Atalho de uma query so sobre `embed_queries`."""
    return embed_queries([text])[0]

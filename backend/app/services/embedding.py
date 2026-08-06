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


def embed_sequences(inputs: list[list], input_type: str = "document") -> list[list[float]]:
    """Chamada crua ao endpoint multimodal.

    `inputs` e uma lista de SEQUENCIAS. Cada sequencia e o conteudo de um item
    e pode misturar str, PIL.Image e Video na ordem que fizer sentido — e o que
    permite embedar "esta figura, legendada assim" como uma coisa so.
    """
    if not inputs:
        return []
    settings = get_settings()
    client = _get_client()
    result = client.multimodal_embed(
        inputs,
        model=settings.voyage_doc_model,
        input_type=input_type,
    )
    vectors = result.embeddings
    if len(vectors) != len(inputs):
        raise RuntimeError(
            f"Embedding count mismatch: expected {len(inputs)}, got {len(vectors)}"
        )
    _assert_dim(vectors[0], settings.voyage_doc_model)
    return vectors


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document chunks. Texto entra pelo modelo multimodal de proposito.

    Usar um modelo so-de-texto aqui e o multimodal nas imagens colocaria os
    dois em espacos vetoriais incomparaveis, e a busca por texto nunca
    encontraria uma imagem.
    """
    if not texts:
        return []
    return embed_sequences([[t] for t in texts], input_type="document")


def embed_images(images: list, legendas: list[str] | None = None) -> list[list[float]]:
    """Embed imagens (PIL.Image) direto, sem passar por legenda.

    Quando ha legenda, ela entra JUNTO no mesmo item — o vetor passa a
    representar imagem e texto ao mesmo tempo, em vez de um ou outro.
    """
    if not images:
        return []
    if legendas and len(legendas) != len(images):
        raise ValueError("legendas e images precisam ter o mesmo tamanho")
    inputs = [
        ([legendas[i], img] if legendas and legendas[i] else [img])
        for i, img in enumerate(images)
    ]
    return embed_sequences(inputs, input_type="document")


def embed_queries(texts: list[str]) -> list[list[float]]:
    """Embed varias queries numa UNICA chamada ao provider.

    A API aceita lista. O custo em tokens e o mesmo de chamar uma a uma, mas
    conta como 1 requisicao em vez de N — e o limite do plano Voyage sem meio
    de pagamento e por REQUISICAO (3 por minuto), nao por token.
    """
    if not texts:
        return []
    return embed_sequences([[t] for t in texts], input_type="query")


def embed_query(text: str) -> list[float]:
    """Embed a search query. Atalho de uma query so sobre `embed_queries`."""
    return embed_queries([text])[0]

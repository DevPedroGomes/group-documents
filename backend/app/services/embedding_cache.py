import hashlib
import json
import logging
from typing import Optional

import redis

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_redis_client = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _cache_key(query: str) -> str:
    query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()
    return f"emb:{query_hash}"


def get_cached_embedding(query: str) -> Optional[list[float]]:
    """Return cached embedding or None."""
    try:
        r = _get_redis()
        data = r.get(_cache_key(query))
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        logger.warning(f"Redis get error: {e}")
        return None


def cache_embedding(query: str, embedding: list[float]) -> None:
    """Cache an embedding with TTL."""
    try:
        settings = get_settings()
        r = _get_redis()
        r.setex(
            _cache_key(query),
            settings.embedding_cache_ttl,
            json.dumps(embedding),
        )
    except Exception as e:
        logger.warning(f"Redis set error: {e}")


def get_query_embedding(query: str) -> list[float]:
    """Get query embedding with cache-through pattern."""
    return get_query_embeddings([query])[0]


def get_query_embeddings(queries: list[str]) -> list[list[float]]:
    """Embeddings de VARIAS queries, com no maximo UMA chamada ao provider.

    O retriever gera variantes da pergunta (multi-query) e precisa do embedding
    de cada uma. Pedir uma por vez custava N requisicoes por pergunta — com o
    padrao `multi_query_count=3`, quatro. A conta Voyage sem meio de pagamento
    e limitada a 3 requisicoes por MINUTO, entao toda pergunta INEDITA estourava
    o limite e o chat morria mostrando o erro cru do provider ao visitante.
    Pergunta repetida funcionava, porque as variantes vinham do cache — o que
    fazia a falha parecer intermitente em vez de estrutural.

    Uma requisicao resolve todas as variantes que faltam, independente de
    quantas sejam.
    """
    if not queries:
        return []

    resultados: list[Optional[list[float]]] = [get_cached_embedding(q) for q in queries]
    faltando = [i for i, v in enumerate(resultados) if v is None]

    if faltando:
        from app.services.embedding import embed_queries

        novos = embed_queries([queries[i] for i in faltando])
        for i, vetor in zip(faltando, novos):
            resultados[i] = vetor
            cache_embedding(queries[i], vetor)

    # `or []` so satisfaz o tipo: todo indice foi preenchido acima.
    return [v or [] for v in resultados]

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
    cached = get_cached_embedding(query)
    if cached is not None:
        return cached

    from app.services.embedding import embed_query
    vector = embed_query(query)
    cache_embedding(query, vector)
    return vector

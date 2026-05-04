"""Shared slowapi limiter — defined here to avoid circular imports.

`app.main` imports this and registers it on `app.state.limiter`. Route modules
import `limiter` from here to decorate their endpoints.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config.settings import get_settings


def _build_limiter() -> Limiter:
    settings = get_settings()
    # `headers_enabled=False` because slowapi requires endpoints to inject the
    # `response: Response` parameter when headers are on, and our endpoints
    # (especially the SSE /chat) already return their own response objects.
    return Limiter(
        key_func=get_remote_address,
        storage_uri=settings.redis_url,
        default_limits=[],
        headers_enabled=False,
    )


limiter = _build_limiter()

import httpx
from supabase import create_client, Client

from app.config.settings import get_settings

_svc: Client | None = None
_anon: Client | None = None


def svc_client() -> Client:
    global _svc
    if _svc is None:
        settings = get_settings()
        _svc = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _svc


def anon_client() -> Client:
    global _anon
    if _anon is None:
        settings = get_settings()
        _anon = create_client(settings.supabase_url, settings.supabase_anon_key)
    return _anon


def create_signed_url(path: str, expires: int = 600) -> str:
    settings = get_settings()
    sb = svc_client()
    res = sb.storage.from_(settings.storage_bucket).create_signed_url(path, expires)
    return res.get("signedUrl") or res.get("signedURL")

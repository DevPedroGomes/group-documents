import os
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
STORAGE_BUCKET = os.getenv('STORAGE_BUCKET', 'docs')

# Singleton clients — created once at module load
_svc: Client | None = None
_anon: Client | None = None


def svc_client() -> Client:
    global _svc
    if _svc is None:
        _svc = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _svc


def anon_client() -> Client:
    global _anon
    if _anon is None:
        _anon = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _anon


def create_signed_url(path: str, expires: int = 600) -> str:
    """Create a signed URL for a storage object. Synchronous (Supabase Python SDK is sync)."""
    sb = svc_client()
    res = sb.storage.from_(STORAGE_BUCKET).create_signed_url(path, expires)
    return res.get('signedUrl') or res.get('signedURL')


async def rest_select_with_token(token: str, table: str, params: dict):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {'Authorization': f'Bearer {token}', 'apikey': SUPABASE_ANON_KEY}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers, params=params)
        r.raise_for_status()
        return r.json()


async def get_doc_owned(token: str, document_id: str):
    rows = await rest_select_with_token(token, 'documents', {
        'id': 'eq.' + document_id,
        'select': 'id,user_id,title,mime,storage_path'
    })
    return rows[0] if rows else None

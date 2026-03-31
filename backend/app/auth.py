import os
import jwt
from fastapi import Request, HTTPException

SUPABASE_JWT_SECRET = os.getenv('SUPABASE_JWT_SECRET', '')

async def require_user(request: Request):
    """Validate JWT token and extract user_id from request."""
    auth = request.headers.get('authorization') or request.headers.get('Authorization')
    if not auth or not auth.lower().startswith('bearer '):
        raise HTTPException(401, 'Missing bearer token')

    token = auth.split(' ', 1)[1].strip()

    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=['HS256'],
            audience='authenticated'
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, 'Token expired')
    except jwt.InvalidTokenError:
        raise HTTPException(401, 'Invalid token')

    user_id = payload.get('sub') or payload.get('user_id') or payload.get('uid')
    if not user_id:
        raise HTTPException(401, 'No user in token')

    request.state.user_id = user_id
    request.state.user_token = token
    return user_id

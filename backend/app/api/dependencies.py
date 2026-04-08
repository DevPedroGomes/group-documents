"""FastAPI dependencies: authentication, database access."""

import jwt
from fastapi import Request, HTTPException

from app.config.settings import get_settings


async def require_user(request: Request) -> str:
    """Validate JWT token and extract user_id."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")

    token = auth.split(" ", 1)[1].strip()
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

    user_id = payload.get("sub") or payload.get("user_id") or payload.get("uid")
    if not user_id:
        raise HTTPException(401, "No user in token")

    request.state.user_id = user_id
    request.state.user_token = token
    return user_id

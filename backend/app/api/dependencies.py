"""FastAPI dependencies: authentication, database access."""

from jose import jwt, JWTError
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
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "No user in token")

    request.state.user_id = user_id
    request.state.user_token = token
    return user_id

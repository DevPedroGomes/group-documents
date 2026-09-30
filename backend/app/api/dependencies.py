"""FastAPI dependencies: authentication, database access, daily quota."""

import uuid

import jwt
from agent_ops import metering
from fastapi import Request, HTTPException
from sqlalchemy import text as sqltext
from starlette.concurrency import run_in_threadpool

from app.config.settings import get_settings
from app.db.engine import engine


def _usuario_ativo(user_id: str) -> bool:
    """True so se o usuario existe e esta ativo. Uma consulta pequena por request."""
    with engine.connect() as conn:
        row = conn.execute(
            sqltext("SELECT is_active FROM users WHERE id = :id"),
            {"id": user_id},
        ).first()
    return bool(row and row[0])


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
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "No user in token")

    # O JWT sozinho vale ate expirar: sem esta consulta, conta desativada ou
    # apagada continuaria acessando tudo com um token ainda valido.
    try:
        uuid.UUID(str(user_id))
    except ValueError:
        raise HTTPException(401, "Invalid or expired token")
    if not await run_in_threadpool(_usuario_ativo, str(user_id)):
        raise HTTPException(401, "User not found or inactive")

    request.state.user_id = user_id
    request.state.user_token = token
    return user_id


async def consumir_cota(tipo: str, limite: int) -> None:
    """Consome uma unidade do teto diario `tipo`, ou responde o motivo da recusa.

    `TetoIndisponivel` (Redis ilegivel) vira 503 sem `Retry-After`, porque
    ninguem sabe quando volta; `TetoAtingido` vira 429 com o tempo ate a virada
    do dia em UTC.
    """
    try:
        await metering.consumir(tipo, limite)
    except metering.TetoIndisponivel as exc:
        raise HTTPException(status_code=503, detail=exc.mensagem) from exc
    except metering.TetoAtingido as exc:
        raise HTTPException(
            status_code=429,
            detail=exc.mensagem,
            headers={"Retry-After": str(metering.segundos_ate_meia_noite_utc())},
        ) from exc

"""Auth routes: register, login, guest, me.

Bcrypt e SQL sao sincronos e o uvicorn roda um worker so: dentro de um
`async def` eles seguram o event loop e congelam todo o app enquanto rodam.
Rota que nao precisa esperar nada (register, login) e `def`, e o FastAPI a roda
no threadpool; rota que espera `require_user` passa o trabalho sincrono por
`run_in_threadpool`.
"""

import uuid
import logging
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from agent_ops import metering
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr
import bcrypt as _bcrypt
from slowapi.util import get_remote_address
from sqlalchemy import insert, text as sqltext
from starlette.concurrency import run_in_threadpool

from app.config.settings import get_settings
from app.db.engine import engine
from app.db.models import users
from app.api.dependencies import CONVIDADO_DESLIGADO, consumir_cota, id_do_acervo_demo, require_user
from app.api.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode("utf-8")[:72], _bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return _bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))


class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class LoginBody(BaseModel):
    email: EmailStr
    password: str

    model_config = ConfigDict(str_strip_whitespace=True)


def _create_token(user_id: str, email: str, expira_em: datetime | None = None) -> str:
    """JWT da sessao. O do convidado vence junto com a linha (`expires_at`)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": expira_em or now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@router.post("/register")
@limiter.limit(get_settings().auth_rate_limit)
def register(request: Request, body: RegisterBody):
    """Create a new user account and return a JWT token."""
    if len(body.password) < 12:
        raise HTTPException(400, "Password must be at least 12 characters")

    password_hash = _hash_password(body.password)
    user_id = str(uuid.uuid4())

    try:
        with engine.begin() as conn:
            conn.execute(
                insert(users).values(
                    id=user_id,
                    email=body.email,
                    password_hash=password_hash,
                    full_name=body.full_name,
                )
            )
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(409, "Email already registered")
        logger.error(f"Registration error: {e}")
        raise HTTPException(500, "Error creating account")

    token = _create_token(user_id, body.email)

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "email": body.email,
            "full_name": body.full_name,
        },
    }


@router.post("/login")
@limiter.limit(get_settings().auth_rate_limit)
def login(request: Request, body: LoginBody):
    """Authenticate user and return a JWT token."""
    with engine.begin() as conn:
        row = conn.execute(
            sqltext("SELECT id, email, password_hash, full_name, is_active FROM users "
                    "WHERE email = :email AND NOT is_guest"),
            {"email": body.email},
        ).first()

    if not row:
        raise HTTPException(401, "Invalid email or password")

    user_id, email, password_hash, full_name, is_active = row

    if not is_active:
        raise HTTPException(403, "Account is disabled")

    if not _verify_password(body.password, password_hash):
        raise HTTPException(401, "Invalid email or password")

    token = _create_token(str(user_id), email)

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(user_id),
            "email": email,
            "full_name": full_name,
        },
    }


# Sessao de convidado: tres por hora por IP aqui, e o teto diario por IP
# (`guest_sessions_per_ip_daily`) na rota. Cada sessao e uma linha nova em
# `users`, entao o rate limit e o que separa um script de uma tabela inchada.
GUEST_RATE_LIMIT = "3/hour"


def _criar_convidado(expira_em: datetime) -> tuple[str, str]:
    """Grava o usuario temporario e devolve `(id, email)`.

    O email usa o TLD reservado `.invalid`, que o `EmailStr` do cadastro e do
    login recusa: ninguem registra nem entra com ele. A senha guardada nao e um
    hash bcrypt, entao nenhuma senha confere, e o login ainda filtra
    `NOT is_guest`.
    """
    user_id = str(uuid.uuid4())
    email = f"convidado-{user_id}@convidado.invalid"
    with engine.begin() as conn:
        conn.execute(
            insert(users).values(
                id=user_id,
                email=email,
                password_hash=f"!convidado:{secrets.token_hex(16)}",
                full_name="Guest",
                is_guest=True,
                expires_at=expira_em,
            )
        )
    return user_id, email


@router.post("/guest")
@limiter.limit(GUEST_RATE_LIMIT)
async def guest(request: Request):
    """Abre uma sessao de convidado: le o acervo de demonstracao, so leitura.

    503 `guest_disabled` com o modo desligado ou sem a conta do acervo, antes de
    gastar a cota do IP. A sessao (e o token) vale `guest_ttl_minutes`.
    """
    settings = get_settings()
    if not settings.guest_enabled:
        raise HTTPException(503, CONVIDADO_DESLIGADO)
    if not await run_in_threadpool(id_do_acervo_demo):
        raise HTTPException(503, CONVIDADO_DESLIGADO)

    ip = get_remote_address(request)
    await consumir_cota("convidado_ip", settings.guest_sessions_per_ip_daily, escopo=ip)

    expira_em = datetime.now(timezone.utc) + timedelta(minutes=settings.guest_ttl_minutes)
    try:
        user_id, email = await run_in_threadpool(_criar_convidado, expira_em)
    except Exception:
        logger.exception("convidado.criacao_falhou")
        await metering.devolver("convidado_ip", escopo=ip)
        raise HTTPException(500, "Error creating guest session")

    expira_iso = expira_em.isoformat()
    return {
        "access_token": _create_token(user_id, email, expira_em),
        "token_type": "bearer",
        "is_guest": True,
        "expires_at": expira_iso,
        "user": {
            "id": user_id,
            "email": email,
            "full_name": "Guest",
            "is_guest": True,
            "expires_at": expira_iso,
        },
    }


def _ler_usuario(user_id: str):
    with engine.begin() as conn:
        return conn.execute(
            sqltext("SELECT id, email, full_name, is_active, created_at, is_guest, expires_at "
                    "FROM users WHERE id = :id"),
            {"id": user_id},
        ).first()


@router.get("/me")
async def me(request: Request):
    """Return the current authenticated user."""
    user_id = await require_user(request)
    row = await run_in_threadpool(_ler_usuario, user_id)

    if not row:
        raise HTTPException(404, "User not found")

    return {
        "id": str(row[0]),
        "email": row[1],
        "full_name": row[2],
        "is_active": row[3],
        "created_at": str(row[4]),
        "is_guest": bool(row[5]),
        "expires_at": row[6].isoformat() if row[6] else None,
    }

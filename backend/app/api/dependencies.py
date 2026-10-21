"""FastAPI dependencies: authentication, database access, daily quota."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import jwt
from agent_ops import metering
from fastapi import Request, HTTPException
from sqlalchemy import text as sqltext
from starlette.concurrency import run_in_threadpool

from app.config.settings import get_settings
from app.db.engine import engine

CONVIDADO_SO_LEITURA = "guest_read_only"
CONVIDADO_DESLIGADO = "guest_disabled"


@dataclass(frozen=True)
class Conta:
    convidado: bool


def _linha_da_conta(user_id: str):
    """`(is_active, is_guest, expires_at)` da conta, ou None. Uma consulta pequena por request."""
    with engine.connect() as conn:
        return conn.execute(
            sqltext("SELECT is_active, is_guest, expires_at FROM users WHERE id = :id"),
            {"id": user_id},
        ).first()


def _conta_ativa(user_id: str) -> Conta | None:
    """A conta, se existe e pode usar o app agora; None se nao.

    Convidado so vale com o modo ligado e antes de `expires_at`. O JWT dele ja
    vence junto, mas e esta consulta que manda: desligar `guest_enabled` ou
    vencer a linha no banco derruba a sessao na proxima requisicao.
    """
    linha = _linha_da_conta(user_id)
    if not linha or not linha[0]:
        return None
    _, convidado, expira_em = linha
    if convidado:
        if not get_settings().guest_enabled:
            return None
        if expira_em is None or expira_em <= datetime.now(timezone.utc):
            return None
    return Conta(convidado=bool(convidado))


async def require_user(request: Request) -> str:
    """Validate JWT token and extract user_id.

    Grava em `request.state.is_guest` se a conta e de convidado; as rotas leem
    com `e_convidado`.
    """
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
    conta = await run_in_threadpool(_conta_ativa, str(user_id))
    if conta is None:
        raise HTTPException(401, "User not found or inactive")

    request.state.user_id = user_id
    request.state.user_token = token
    request.state.is_guest = conta.convidado
    return user_id


def e_convidado(request: Request) -> bool:
    """True so quando `require_user` autenticou uma sessao de convidado."""
    return getattr(request.state, "is_guest", False) is True


async def require_writer(request: Request) -> str:
    """Como `require_user`, mas recusa convidado com 403 `guest_read_only`.

    Vale para toda rota que grava ou que gasta fora do chat: upload, crawl,
    ingest, apagar documento ou conversa, e as duas rotas de voz. A recusa vem
    antes de ler o corpo, consumir cota ou tocar no banco.
    """
    user_id = await require_user(request)
    if e_convidado(request):
        raise HTTPException(403, CONVIDADO_SO_LEITURA)
    return user_id


def id_do_acervo_demo() -> str | None:
    """Id da conta `demo_corpus_email`, dona do acervo que o convidado le."""
    with engine.connect() as conn:
        valor = conn.execute(
            sqltext("SELECT id FROM users WHERE email = :email AND NOT is_guest"),
            {"email": get_settings().demo_corpus_email},
        ).scalar()
    return str(valor) if valor else None


def corpus_owner(user_id: str, *, convidado: bool) -> str:
    """De quem e o acervo que esta pessoa le.

    Convidado le o acervo da conta de demonstracao; os demais, o proprio. Vale
    SO para a leitura de documentos e trechos: threads, mensagens e trilhas
    continuam no `user_id` de quem pergunta, e e isso que isola um convidado do
    outro. Sem a conta do acervo, o convidado recebe 503 em vez de um acervo
    vazio que parece um app quebrado.
    """
    if not convidado:
        return user_id
    acervo = id_do_acervo_demo()
    if not acervo:
        raise HTTPException(503, CONVIDADO_DESLIGADO)
    return acervo


async def acervo_de(request: Request, user_id: str) -> str:
    """`corpus_owner` para a requisicao autenticada; so convidado consulta o banco."""
    if not e_convidado(request):
        return user_id
    return await run_in_threadpool(corpus_owner, user_id, convidado=True)


async def consumir_cota(tipo: str, limite: int, escopo: str | None = None) -> int | None:
    """Consome uma unidade do teto diario `tipo` e devolve quanto sobrou, ou
    responde o motivo da recusa.

    `escopo` separa contadores do mesmo tipo (por IP, por sessao). O metering
    recusa `:` dentro de `tipo`, entao o recorte vai sempre por aqui.

    `TetoIndisponivel` (Redis ilegivel) vira 503 sem `Retry-After`, porque
    ninguem sabe quando volta; `TetoAtingido` vira 429 com o tempo ate a virada
    do dia em UTC.
    """
    recorte = {} if escopo is None else {"escopo": escopo}
    try:
        return await metering.consumir(tipo, limite, **recorte)
    except metering.TetoIndisponivel as exc:
        raise HTTPException(status_code=503, detail=exc.mensagem) from exc
    except metering.TetoAtingido as exc:
        raise HTTPException(
            status_code=429,
            detail=exc.mensagem,
            headers={"Retry-After": str(metering.segundos_ate_meia_noite_utc())},
        ) from exc


async def consumir_cota_de_chat(user_id: str, convidado: bool) -> int | None:
    """Uma pergunta: o teto `chat` para conta normal, os de convidado para convidado.

    Convidado nunca toca o `chat`: um visitante sem cadastro esgotaria o dia de
    quem tem conta. Ele consome a cota da propria sessao (`chat_convidado` com
    escopo no id) e a de todos os convidados no dia (`chat_convidado`); se a
    segunda recusar, a primeira volta. Para convidado devolve quantas perguntas
    restam na sessao; para conta normal, None.
    """
    s = get_settings()
    if not convidado:
        await consumir_cota("chat", s.daily_chat_limit)
        return None
    restantes = await consumir_cota("chat_convidado", s.guest_chat_limit, escopo=user_id)
    try:
        await consumir_cota("chat_convidado", s.daily_guest_chat_limit)
    except HTTPException:
        await metering.devolver("chat_convidado", escopo=user_id)
        raise
    return restantes


async def devolver_cota_de_chat(user_id: str, convidado: bool) -> None:
    """Desfaz `consumir_cota_de_chat` quando nenhuma chamada paga aconteceu."""
    if not convidado:
        await metering.devolver("chat")
        return
    await metering.devolver("chat_convidado", escopo=user_id)
    await metering.devolver("chat_convidado")

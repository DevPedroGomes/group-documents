"""Historico, trilha de decisao e grafo rodando SQL de verdade.

Banco criado so pelas migrations (fixture `banco_limpo`). Sem `TEST_REDIS_URL`
o limiter e desligado; nenhuma rota exercitada aqui consome cota.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sqltext

pytestmark = pytest.mark.integration

SENHA = "uma-senha-longa-123"
INICIO = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def cliente(banco_limpo):
    from app.api.rate_limit import limiter
    from app.main import create_app

    if not os.environ.get("TEST_REDIS_URL"):
        limiter.enabled = False
    else:
        limiter.reset()
    try:
        yield TestClient(create_app())
    finally:
        limiter.enabled = True


def _registrar(cliente, email: str) -> tuple[str, dict]:
    r = cliente.post("/auth/register", json={"email": email, "password": SENHA})
    assert r.status_code == 200, r.text
    corpo = r.json()
    return corpo["user"]["id"], {"Authorization": f"Bearer {corpo['access_token']}"}


def _usuario(email: str = "u@exemplo.com") -> str:
    from app.db.engine import engine

    with engine.begin() as conn:
        return str(conn.execute(
            sqltext("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"),
            {"e": email},
        ).scalar_one())


def _thread_com_mensagens(user_id: str, n: int) -> str:
    """Thread com `n` mensagens alternando user/assistant, um minuto entre cada."""
    from app.db.engine import engine

    with engine.begin() as conn:
        thread_id = str(conn.execute(
            sqltext("INSERT INTO threads (user_id) VALUES (:u) RETURNING id"), {"u": user_id}
        ).scalar_one())
        for i in range(n):
            conn.execute(
                sqltext("INSERT INTO messages (thread_id, role, content, created_at) "
                        "VALUES (:t, :r, :c, :em)"),
                {"t": thread_id, "r": "user" if i % 2 == 0 else "assistant",
                 "c": f"m{i}", "em": INICIO + timedelta(minutes=i)},
            )
    return thread_id


# ---------------------------------------------------------------------------
# Historico: as mais recentes, em ordem cronologica
# ---------------------------------------------------------------------------

def test_historico_devolve_as_mais_recentes_em_ordem_cronologica(banco_limpo):
    """Com `ASC LIMIT` a conversa de 25 mensagens entregava m0..m19 e o modelo
    nunca via os ultimos turnos."""
    from app.api.routes.chat import get_thread_history

    u = _usuario()
    thread_id = _thread_com_mensagens(u, 25)

    historico = get_thread_history(thread_id, u, limit=20)

    assert [m["content"] for m in historico] == [f"m{i}" for i in range(5, 25)]
    assert historico[-1]["role"] == "user"  # m24: indice par


def test_historico_de_thread_alheia_vem_vazio(banco_limpo):
    from app.api.routes.chat import get_thread_history

    dono = _usuario("dono@exemplo.com")
    outro = _usuario("outro@exemplo.com")
    thread_id = _thread_com_mensagens(dono, 3)

    assert get_thread_history(thread_id, outro, limit=20) == []


def test_rota_de_mensagens_devolve_as_100_mais_recentes(cliente):
    user_id, cab = _registrar(cliente, "a@exemplo.com")
    thread_id = _thread_com_mensagens(user_id, 105)

    r = cliente.get(f"/threads/{thread_id}/messages", headers=cab)

    assert r.status_code == 200, r.text
    assert [m["content"] for m in r.json()["messages"]] == [f"m{i}" for i in range(5, 105)]

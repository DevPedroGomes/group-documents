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


# ---------------------------------------------------------------------------
# Trilha de decisao: so o dono le, e as consultas usadas voltam
# ---------------------------------------------------------------------------

def _mensagem(thread_id: str) -> str:
    from app.api.routes.chat import save_message

    return save_message(thread_id, "assistant", "resposta")


def test_trilha_so_e_lida_pelo_dono_e_traz_as_consultas(cliente):
    from app.api.routes.chat import save_decision

    dono, cab_dono = _registrar(cliente, "dono@exemplo.com")
    _, cab_outro = _registrar(cliente, "outro@exemplo.com")
    thread_id = _thread_com_mensagens(dono, 0)
    message_id = _mensagem(thread_id)
    save_decision(
        user_id=dono, thread_id=thread_id, message_id=message_id,
        question="e em marco de 2025?",
        retrieved=[{"document_id": "00000000-0000-0000-0000-000000000001", "document_title": "Politica",
                    "page": 1, "relevance_score": 0.03, "score_scale": "rrf", "document_date": "2025-03-01"}],
        graded=[], web_used=False, low_confidence=True, answered=True, latency_ms=10,
        queries=["qual o prazo em marco de 2025?", "prazo 2025"],
    )

    r = cliente.get(f"/decisions/{message_id}", headers=cab_dono)
    assert r.status_code == 200, r.text
    assert r.json()["queries"] == ["qual o prazo em marco de 2025?", "prazo 2025"]
    assert r.json()["retrieved"][0]["document_date"] == "2025-03-01"
    assert r.json()["score_scale_hint"].startswith("Rank fusion score (RRF)")
    assert [d["message_id"] for d in cliente.get("/decisions", headers=cab_dono).json()["decisions"]] == [message_id]

    # Filtrar so por message_id deixaria qualquer um ler a trilha de outro.
    assert cliente.get(f"/decisions/{message_id}", headers=cab_outro).status_code == 404
    assert cliente.get("/decisions", headers=cab_outro).json() == {"decisions": []}


# ---------------------------------------------------------------------------
# Grafo: sai da trilha, so do dono
# ---------------------------------------------------------------------------

def test_grafo_so_mostra_as_decisoes_do_dono(cliente):
    from app.api.routes.chat import save_decision

    dono, cab_dono = _registrar(cliente, "dono@exemplo.com")
    _, cab_outro = _registrar(cliente, "outro@exemplo.com")
    trechos = [
        {"document_id": "00000000-0000-0000-0000-00000000000d", "document_title": "Contrato",
         "page": 1, "relevance_score": 0.9, "score_scale": "cohere"},
        {"document_id": "00000000-0000-0000-0000-00000000000e", "document_title": "Aditivo",
         "page": 1, "relevance_score": 0.8, "score_scale": "cohere"},
    ]
    save_decision(
        user_id=dono, thread_id=_thread_com_mensagens(dono, 0), message_id=None,
        question="qual o prazo?", retrieved=trechos, graded=trechos, web_used=False,
        low_confidence=False, answered=True, latency_ms=10,
        conflict={"summary": "O prazo difere.", "sources": ["Contrato", "Aditivo"], "vigente": "Aditivo"},
    )

    grafo = cliente.get("/graph", headers=cab_dono).json()
    assert sorted(n["id"] for n in grafo["nodes"] if n["type"] == "document") == [
        "d:00000000-0000-0000-0000-00000000000d", "d:00000000-0000-0000-0000-00000000000e",
    ]
    assert [e["type"] for e in grafo["edges"]].count("DIVERGE") == 1

    vazio = cliente.get("/graph", headers=cab_outro).json()
    assert vazio["nodes"] == [] and vazio["edges"] == []


def test_save_message_devolve_o_id_da_linha_gravada(banco_limpo):
    """O `done` leva este id, e a trilha aponta para ele: sem ele a decisao
    ficaria orfa e o "por que ele respondeu isso?" nao teria ancora."""
    from app.api.routes.chat import save_message
    from app.db.engine import engine

    u = _usuario()
    thread_id = _thread_com_mensagens(u, 0)

    message_id = save_message(thread_id, "assistant", "resposta", [{"kind": "document"}])

    with engine.connect() as conn:
        linha = conn.execute(
            sqltext("SELECT content, citations FROM messages WHERE id = CAST(:id AS uuid)"),
            {"id": message_id},
        ).one()
    assert linha.content == "resposta"
    assert linha.citations == [{"kind": "document"}]

"""Instalacao limpa: um banco criado SO pelas migrations tem que servir a app.

Producao nasceu de um `init.sql` que nao existe mais; se as migrations nao
bastarem, register/login/me e a lista de threads quebram com UndefinedColumn.

Redis: com `TEST_REDIS_URL` o limiter usa Redis real (o conftest exporta
`REDIS_URL` antes do import). Sem ela, o limiter e desligado no teste — nenhuma
das rotas exercitadas aqui usa o metering, entao nao ha double dele.
"""
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sqltext

pytestmark = pytest.mark.integration

SENHA = "uma-senha-longa-123"


@pytest.fixture
def cliente(banco_limpo):
    from app.api.rate_limit import limiter
    from app.main import create_app

    if not __import__("os").environ.get("TEST_REDIS_URL"):
        limiter.enabled = False
    else:
        limiter.reset()
    try:
        # Sem `with`: nao dispara o lifespan (migrations ja rodaram, e o pool
        # do arq exigiria Redis).
        yield TestClient(create_app())
    finally:
        limiter.enabled = True


def _registrar(cliente, email="a@exemplo.com"):
    r = cliente.post("/auth/register", json={"email": email, "password": SENHA})
    assert r.status_code == 200, r.text
    return r.json()


def test_todas_as_migrations_aplicam_em_banco_limpo(banco_limpo):
    from app.db.engine import engine
    from app.db.migrate import _discover, run_migrations

    with engine.connect() as conn:
        aplicadas = {r[0] for r in conn.execute(sqltext("SELECT version FROM schema_migrations"))}
        colunas = {
            (r[0], r[1])
            for r in conn.execute(sqltext(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'"
            ))
        }
        cache = conn.execute(sqltext("SELECT to_regclass('public.semantic_cache')")).scalar()

    assert aplicadas == {nome for nome, _ in _discover()}
    assert {("users", "is_active"), ("threads", "updated_at"),
            ("documents", "effective_date"), ("decisions", "queries")} <= colunas
    assert cache is None
    run_migrations()  # segunda rodada e no-op


def test_backfill_da_data_efetiva_ignora_lixo(banco_limpo):
    """A 007 roda sobre dados legados: data valida converte, lixo vira NULL."""
    from app.db.engine import engine
    from app.db.migrate import MIGRATIONS_DIR

    with engine.begin() as conn:
        conn.execute(sqltext("ALTER TABLE documents DROP COLUMN effective_date"))
        conn.execute(sqltext("INSERT INTO users (id, email, password_hash) VALUES (gen_random_uuid(), 'u@x.com', 'h')"))
        uid = conn.execute(sqltext("SELECT id FROM users")).scalar()
        for i, emitido in enumerate(["2024-03-15", "2024-02-31", "ontem", "15/03/2024"]):
            conn.execute(
                sqltext("INSERT INTO documents (user_id, title, storage_path, meta) "
                        "VALUES (:u, :t, 'p', CAST(:m AS jsonb))"),
                {"u": uid, "t": f"d{i}", "m": f'{{"emitido_em": "{emitido}"}}'},
            )
        conn.execute(sqltext("INSERT INTO documents (user_id, title, storage_path) VALUES (:u, 'sem', 'p')"), {"u": uid})
        conn.execute(sqltext((MIGRATIONS_DIR / "007_instalacao_limpa.sql").read_text("utf-8")))
        datas = dict(conn.execute(sqltext("SELECT title, effective_date::text FROM documents")).all())

    assert datas == {"d0": "2024-03-15", "d1": None, "d2": None, "d3": None, "sem": None}


def test_register_login_me_e_threads_em_banco_limpo(cliente):
    reg = _registrar(cliente)
    login = cliente.post("/auth/login", json={"email": "a@exemplo.com", "password": SENHA})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    cab = {"Authorization": f"Bearer {token}"}

    me = cliente.get("/auth/me", headers=cab)
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "a@exemplo.com" and me.json()["is_active"] is True
    assert me.json()["id"] == reg["user"]["id"]

    threads = cliente.get("/threads", headers=cab)
    assert threads.status_code == 200, threads.text
    assert threads.json() == {"threads": []}


def test_usuario_inativo_ou_apagado_recebe_401_com_token_valido(cliente):
    from app.db.engine import engine

    token = _registrar(cliente)["access_token"]
    cab = {"Authorization": f"Bearer {token}"}
    assert cliente.get("/auth/me", headers=cab).status_code == 200

    with engine.begin() as conn:
        conn.execute(sqltext("UPDATE users SET is_active = false"))
    assert cliente.get("/auth/me", headers=cab).status_code == 401
    assert cliente.get("/threads", headers=cab).status_code == 401

    with engine.begin() as conn:
        conn.execute(sqltext("DELETE FROM users"))
    assert cliente.get("/auth/me", headers=cab).status_code == 401


def test_token_invalido_ou_com_sub_nao_uuid_recebe_401(cliente):
    from app.config.settings import get_settings

    s = get_settings()
    lixo = jwt.encode({"sub": "nao-e-uuid"}, s.jwt_secret, algorithm=s.jwt_algorithm)
    for tk in (lixo, "abc.def.ghi"):
        assert cliente.get("/auth/me", headers={"Authorization": f"Bearer {tk}"}).status_code == 401


def test_save_message_sobe_a_thread_na_ordenacao(cliente):
    from app.api.routes.chat import save_message
    from app.db.engine import engine

    token = _registrar(cliente)["access_token"]
    cab = {"Authorization": f"Bearer {token}"}
    with engine.begin() as conn:
        uid = conn.execute(sqltext("SELECT id FROM users")).scalar()
        ids = [
            conn.execute(
                sqltext("INSERT INTO threads (user_id, title, updated_at) VALUES (:u, :t, now() - interval '1 day' * :d) RETURNING id"),
                {"u": uid, "t": t, "d": d},
            ).scalar()
            for t, d in (("nova", 1), ("velha", 2))
        ]
    assert [t["title"] for t in cliente.get("/threads", headers=cab).json()["threads"]] == ["nova", "velha"]

    save_message(str(ids[1]), "user", "oi")
    assert [t["title"] for t in cliente.get("/threads", headers=cab).json()["threads"]] == ["velha", "nova"]

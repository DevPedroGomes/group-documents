"""Configuracao comum da suite.

Os defaults abaixo vem ANTES de qualquer import do app: `Settings` exige
`DATABASE_URL`, `JWT_SECRET` e `VOYAGE_API_KEY` e `app.db.engine` a instancia no
import. `setdefault` deixa uma env real (CI, dev) prevalecer. A suite unitaria
segue sem rede e sem chave.
"""
import os
import sys
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost:1/x")
os.environ.setdefault("JWT_SECRET", "segredo-de-teste-com-32-bytes-ou-mais")
os.environ.setdefault("VOYAGE_API_KEY", "chave-falsa")
# Com Redis real disponivel o limiter da app o usa; o `REDIS_URL` precisa estar
# definido antes do import de `app.api.rate_limit`, que le a URL uma vez.
if os.environ.get("TEST_REDIS_URL"):
    os.environ["REDIS_URL"] = os.environ["TEST_REDIS_URL"]

import pytest  # noqa: E402
from sqlalchemy import create_engine, text as sqltext  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: usa Postgres real (pgvector); exige TEST_DATABASE_URL, senao e pulado",
    )


def _trocar_engine(antigo, novo) -> None:
    """Aponta para `novo` toda referencia a `antigo` nos modulos ja importados."""
    for nome, modulo in list(sys.modules.items()):
        if not nome.startswith("app") or modulo is None:
            continue
        for attr, valor in list(vars(modulo).items()):
            if valor is antigo:
                setattr(modulo, attr, novo)


@pytest.fixture
def banco_limpo(monkeypatch):
    """Banco Postgres temporario, so com as migrations aplicadas; devolve a URL.

    Exige `TEST_DATABASE_URL` (um banco de onde se possa dar CREATE DATABASE, ex.:
    `postgresql://localhost/postgres`); sem ela o teste e pulado. Cria
    `gd_test_<uuid>`, roda `run_migrations()` e dropa o banco no fim.

    Mecanismo do engine: `engine` e um global de `app.db.engine` importado POR
    NOME em varios modulos (`from app.db.engine import engine`), entao trocar so
    o atributo do modulo de origem nao alcanca quem ja importou. A fixture varre
    `sys.modules` e substitui, em todo modulo `app.*`, cada nome que aponta para
    o engine original pelo engine do banco temporario; no teardown faz o inverso.
    Modulos importados DURANTE o teste pegam o engine novo pelo atributo de
    `app.db.engine` e tambem sao revertidos na varredura final.
    """
    admin_url = os.environ.get("TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("TEST_DATABASE_URL nao definida: teste de integracao pulado")

    import app.db.engine as engine_mod

    nome = f"gd_test_{uuid.uuid4().hex}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sqltext(f'CREATE DATABASE "{nome}"'))

    url = make_url(admin_url).set(database=nome)
    original = engine_mod.engine
    temporario = create_engine(url, pool_pre_ping=True)
    _trocar_engine(original, temporario)
    try:
        from app.db.migrate import run_migrations

        run_migrations()
        yield url.render_as_string(hide_password=False)
    finally:
        _trocar_engine(temporario, original)
        temporario.dispose()
        with admin.connect() as conn:
            conn.execute(
                sqltext("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d"),
                {"d": nome},
            )
            conn.execute(sqltext(f'DROP DATABASE IF EXISTS "{nome}"'))
        admin.dispose()


@pytest.fixture
def limiter_em_memoria(monkeypatch):
    """O limiter da app de verdade, contando em memoria em vez de no Redis.

    Serve para prender um rate limit pela rota sem Redis de pe: o decorador, a
    chave por IP e o 429 sao os reais; so o armazenamento dos contadores muda.
    """
    from limits.storage import MemoryStorage
    from limits.strategies import FixedWindowRateLimiter

    from app.api.rate_limit import limiter

    armazenamento = MemoryStorage()
    monkeypatch.setattr(limiter, "_storage", armazenamento)
    monkeypatch.setattr(limiter, "_limiter", FixedWindowRateLimiter(armazenamento))
    monkeypatch.setattr(limiter, "enabled", True)
    return limiter

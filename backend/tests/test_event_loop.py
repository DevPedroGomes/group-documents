"""Trabalho sincrono das rotas nao segura o event loop.

O uvicorn roda um worker so. SQL, bcrypt ou disco rodando direto dentro de um
`async def` congelam todo request concorrente, ate o /healthz. Aqui o banco de
uma rota e trocado por um que demora, e um /healthz disparado DEPOIS dela tem de
terminar ANTES. Com o SQL no event loop, o /healthz so rodaria quando o SQL
lento acabasse.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from contextlib import contextmanager

import httpx
import pytest

USUARIO = "00000000-0000-0000-0000-00000000000a"
DEMORA = 0.5


class _Resultado:
    rowcount = 0

    def mappings(self):
        return self

    def all(self):
        return []

    def fetchall(self):
        return []

    def first(self):
        return None

    def scalar(self):
        return None


class MotorLento:
    """Engine que demora `DEMORA` segundos em cada SQL e nao devolve nada."""

    @contextmanager
    def begin(self):
        yield self

    def execute(self, *_a, **_k):
        time.sleep(DEMORA)
        return _Resultado()


@pytest.mark.parametrize("modulo,metodo,caminho,corpo", [
    ("app.api.routes.documents", "GET", "/documents", None),
    ("app.api.routes.chat", "GET", "/threads", None),
    ("app.api.routes.chat", "GET", "/decisions", None),
    ("app.api.routes.auth", "GET", "/auth/me", None),
    ("app.api.routes.auth", "POST", "/auth/login",
     {"email": "a@exemplo.com.br", "password": "uma-senha-longa-123"}),
])
def test_sql_lento_numa_rota_nao_segura_um_request_concorrente(monkeypatch, modulo, metodo, caminho, corpo):
    from app.api.rate_limit import limiter
    from app.main import create_app

    rota = importlib.import_module(modulo)
    monkeypatch.setattr(rota, "engine", MotorLento())

    async def usuario(_request):
        return USUARIO

    monkeypatch.setattr(rota, "require_user", usuario)
    monkeypatch.setattr(limiter, "enabled", False)
    app = create_app()
    terminou: dict[str, float] = {}

    async def cenario():
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://teste") as cliente:

            async def pedir(nome: str, *args, **kwargs) -> httpx.Response:
                resposta = await cliente.request(*args, **kwargs)
                terminou[nome] = time.monotonic()
                return resposta

            lenta = asyncio.create_task(
                pedir("lenta", metodo, caminho, json=corpo, headers={"Authorization": "Bearer x"})
            )
            await asyncio.sleep(0.1)  # a rota lenta ja esta no SQL
            saude = await pedir("saude", "GET", "/healthz")
            return await lenta, saude

    lenta, saude = asyncio.run(cenario())

    assert saude.status_code == 200
    assert lenta.status_code < 500, lenta.text
    assert terminou["saude"] < terminou["lenta"], (
        f"{caminho} segurou o event loop: o /healthz esperou o SQL lento acabar"
    )

"""Uma recusa da fila DESFAZ o que a rota ja tinha feito.

Sem isso, um 429 ainda cobrava a cota do dia, deixava o arquivo orfao no volume
e a linha `pending` para sempre — com o navegador daquele usuario consultando
ela a cada 3s. Aqui `_recusar_e_desfazer` roda sozinho, com o SQL de verdade
anotado; pelas tres rotas (enfileiramento com tenant, `job_id` na resposta, a
ingestao fora do processo web e a recusa desfeita) em tests/test_rotas_documentos.py.
"""

import asyncio

import pytest
from agent_ops.queue import FilaCheia, FilaIndisponivel
from fastapi import HTTPException

from tests.motor_falso import MotorFalso


def _rotas_instrumentadas(monkeypatch):
    """Rotas com banco, cota e disco trocados por dubles. Nada sai do processo."""
    from app.api.routes import documents as rotas

    devolvido: list[str] = []
    apagados: list[str] = []
    motor = MotorFalso()

    async def devolver(tipo, *_a, **_k):
        devolvido.append(tipo)

    monkeypatch.setattr(rotas.metering, "devolver", devolver)
    monkeypatch.setattr(rotas, "engine", motor)
    monkeypatch.setattr(rotas, "delete_file", lambda caminho: apagados.append(caminho))
    return rotas, devolvido, apagados, motor


def test_recusa_da_fila_devolve_cota_apaga_documento_e_arquivo(monkeypatch):
    rotas, devolvido, apagados, motor = _rotas_instrumentadas(monkeypatch)

    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            rotas._recusar_e_desfazer(
                "doc-1", "u/docs/a.pdf", FilaCheia("cheia", retry_after=30)
            )
        )

    assert erro.value.status_code == 429
    assert erro.value.headers["Retry-After"] == "30"
    assert devolvido == ["ingest"], "cota gasta por um trabalho que nunca rodou"
    assert motor.gravou("DELETE FROM documents WHERE id"), (
        "documento fantasma: linha `pending` para sempre, consultada a cada 3s"
    )
    assert apagados == ["u/docs/a.pdf"], "arquivo orfao no volume"


def test_fila_indisponivel_desfaz_igual_mas_responde_503(monkeypatch):
    # Fila cheia tem prazo para voltar (429 + Retry-After); Redis ilegivel nao
    # tem (503). O desfazimento e o mesmo nos dois casos.
    rotas, devolvido, apagados, motor = _rotas_instrumentadas(monkeypatch)

    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            rotas._recusar_e_desfazer(
                "doc-1", "u/docs/a.pdf", FilaIndisponivel("fora do ar")
            )
        )

    assert erro.value.status_code == 503
    assert devolvido == ["ingest"]
    assert motor.gravou("DELETE FROM documents WHERE id")
    assert apagados == ["u/docs/a.pdf"]

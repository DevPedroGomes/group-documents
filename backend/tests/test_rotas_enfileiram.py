"""Prende que a ingestao nao roda mais no processo web.

O que se prende aqui:
- nenhuma rota usa `BackgroundTasks`. Era isso que fazia um redeploy no meio de
  uma ingestao perder o job em silencio, e uma operacao bloqueante travar ate o
  `/healthz`;
- o job id volta na resposta. Sem ele o cliente nao consegue acompanhar o
  progresso — e no caminho de deduplicacao `enfileirar` devolve None, entao a
  resposta precisa do `job_id_de`, nao do retorno do enfileiramento;
- a deduplicacao leva o tenant. So o digest faria dois usuarios que subiram o
  mesmo arquivo compartilharem job, e o segundo receberia o progresso do
  primeiro;
- uma recusa da fila DESFAZ o que a rota ja tinha feito. Sem isso, um 429 ainda
  cobrava a cota do dia, deixava o arquivo orfao no volume e a linha `pending`
  para sempre — com o navegador daquele usuario consultando ela a cada 3s.
"""

import asyncio
from pathlib import Path

import pytest
from agent_ops.queue import FilaCheia, FilaIndisponivel
from fastapi import HTTPException

from tests.motor_falso import MotorFalso

BACKEND = Path(__file__).resolve().parents[1]
ROTAS = BACKEND / "app" / "api" / "routes" / "documents.py"


def test_nenhuma_rota_usa_background_tasks():
    fonte = ROTAS.read_text("utf-8")
    assert "BackgroundTasks" not in fonte
    assert "background_tasks.add_task" not in fonte


def test_o_enfileiramento_leva_tenant():
    fonte = ROTAS.read_text("utf-8")
    assert fonte.count("tenant=user_id") >= 3, (
        "alguma rota enfileira sem tenant; dois usuarios com o mesmo arquivo "
        "dividiriam job e progresso"
    )


def test_a_resposta_devolve_o_job_id():
    fonte = ROTAS.read_text("utf-8")
    assert fonte.count('"job_id": job_id') >= 3


def test_as_rotas_separam_fila_indisponivel_de_fila_cheia():
    fonte = ROTAS.read_text("utf-8")
    assert "FilaIndisponivel" in fonte
    assert "FilaCheia" in fonte
    assert fonte.index("FilaIndisponivel as exc") < fonte.index("FilaCheia as exc"), (
        "FilaCheia vem antes e engole o subtipo FilaIndisponivel"
    )


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


def test_as_tres_rotas_desfazem_a_recusa():
    fonte = ROTAS.read_text("utf-8")
    assert fonte.count("await _recusar_e_desfazer(") == 6, (
        "alguma rota trata a recusa da fila sem desfazer cota, linha e arquivo"
    )


def test_as_rotas_nao_importam_a_tabela_de_chunks():
    # `chunks` era importado e nunca usado: a unica leitura de chunks aqui e SQL
    # cru dentro de `list_documents`.
    fonte = ROTAS.read_text("utf-8")
    assert "from app.db.models import documents\n" in fonte

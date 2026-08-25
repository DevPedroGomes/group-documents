"""Prende o COMPORTAMENTO do envelope do job, nao o texto dele.

Os testes que ja existiam liam `inspect.getsource(ingerir)` e conferiam que as
palavras `descartar` e `tentar_de_novo` apareciam ali. As duas apareciam — e as
duas eram inalcancaveis, porque `process_ingestion` engolia a propria excecao e
o envelope nunca via falha nenhuma. Um teste que le fonte nao consegue notar
isso; por isso os daqui EXECUTAM o envelope.

Sem rede, sem Redis e sem Postgres: o engine e um duble que anota o SQL
(`tests/motor_falso.py`) e a fila e monkeypatchada. Sem `pytest-asyncio` —
`asyncio.run` basta para chamar uma corrotina de dentro de um teste sincrono.

O que cada teste prende:
- a falha do dominio CHEGA ao envelope (C1). Enquanto nao chegava, o job
  terminava gravando `concluido` em `job_progress` para um documento que a
  propria ingestao acabara de marcar `failed`;
- o dead-letter e o retry mexem TAMBEM na linha do documento. So `job_progress`
  deixaria o documento em `processing` para sempre, e o frontend consulta esse
  documento a cada 3s enquanto ele estiver assim;
- `CancelledError` (timeout do job, SIGTERM do worker) e tratada e repropagada
  (C2). Ela deriva de BaseException: nenhum `except Exception` a pega;
- uma reexecucao apaga os chunks anteriores antes de reprocessar (I1).
"""

import asyncio

import pytest
from arq.worker import Retry

from app.jobs import ingestao, worker
from tests.motor_falso import MotorFalso, status_do_documento


def _instrumentar(monkeypatch) -> tuple[list[dict], MotorFalso]:
    """Troca banco e fila por dubles. Devolve (chamadas da fila, engine falso).

    `marcar` e `descartar` viram registro; `esgotou` e `tentar_de_novo` seguem
    reais, porque a decisao entre retentar e descartar e justamente o que estes
    testes precisam observar.
    """
    chamadas: list[dict] = []
    motor = MotorFalso()

    monkeypatch.setattr(worker, "engine", motor)
    monkeypatch.setattr(ingestao, "engine", motor)
    monkeypatch.setattr(
        worker.queue, "marcar", lambda *a, **k: chamadas.append(k)
    )
    monkeypatch.setattr(
        worker.queue, "descartar", lambda *a, **k: chamadas.append(k)
    )
    return chamadas, motor


def _ingestao_que_falha(monkeypatch) -> None:
    """Faz a ingestao REAL falhar, no primeiro passo que toca o mundo externo.

    Falhar aqui (e nao com um duble de `process_ingestion`) e o que faz estes
    testes atravessarem os dois lados do contrato: o `except` da ingestao grava
    `failed` e re-lanca, e so entao o envelope decide o estado terminal.
    """

    def indisponivel(*_a, **_k):
        raise RuntimeError("provider fora do ar")

    monkeypatch.setattr(ingestao, "get_file", indisponivel)


def test_falha_do_job_vai_para_dead_letter(monkeypatch):
    chamadas, motor = _instrumentar(monkeypatch)

    async def explode(*_a):
        raise RuntimeError("provider fora do ar")

    monkeypatch.setattr(worker, "process_ingestion", explode)

    asyncio.run(worker.ingerir({"job_id": "j", "job_try": 5}, "d", "u", "p"))

    assert any(c.get("motivo") for c in chamadas), "dead-letter nunca rodou"
    assert not any(c.get("estado") == "concluido" for c in chamadas)
    assert status_do_documento(motor) == ["failed"], (
        "o descarte so mexeu em job_progress: o documento fica em processing "
        "para sempre e o frontend consulta ele a cada 3s indefinidamente"
    )


def test_falha_real_da_ingestao_nao_e_gravada_como_concluido(monkeypatch):
    # O mesmo caminho do teste acima, mas com a ingestao de verdade no meio: e
    # este que pega o C1, porque o duble de `process_ingestion` levanta por
    # construcao e a funcao real (antes do fix) nao levantava nunca.
    chamadas, motor = _instrumentar(monkeypatch)
    _ingestao_que_falha(monkeypatch)

    asyncio.run(worker.ingerir({"job_id": "j", "job_try": 5}, "doc-1", "u", "p"))

    assert not any(c.get("estado") == "concluido" for c in chamadas), (
        "job_progress diz `concluido` para um documento que a ingestao marcou "
        "`failed`: duas fontes duraveis discordando, e a errada e a nova"
    )
    assert any(c.get("motivo") for c in chamadas), "dead-letter nunca rodou"
    assert status_do_documento(motor)[-1] == "failed"


def test_falha_antes_do_teto_pede_retentativa(monkeypatch):
    chamadas, motor = _instrumentar(monkeypatch)
    _ingestao_que_falha(monkeypatch)

    with pytest.raises(Retry):
        asyncio.run(worker.ingerir({"job_id": "j", "job_try": 1}, "doc-1", "u", "p"))

    assert not any(c.get("estado") == "concluido" for c in chamadas)
    assert not any(c.get("motivo") for c in chamadas), (
        "descartou na primeira tentativa; ainda havia retentativa sobrando"
    )
    assert status_do_documento(motor)[-1] == "processing", (
        "o documento ficou `failed` com uma retentativa agendada: a UI mostra "
        "falha definitiva para um trabalho que ainda vai acontecer"
    )


def test_cancelamento_marca_falhou_e_repropaga(monkeypatch):
    chamadas, motor = _instrumentar(monkeypatch)

    async def cancelado(*_a):
        # E o que o arq provoca quando o `job_timeout` estoura (o `wait_for`
        # cancela a task) e no shutdown do worker.
        raise asyncio.CancelledError()

    monkeypatch.setattr(worker, "process_ingestion", cancelado)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.ingerir({"job_id": "j", "job_try": 1}, "doc-1", "u", "p"))

    assert any(c.get("estado") == "falhou" for c in chamadas), (
        "CancelledError deriva de BaseException e escapou de todo except: a "
        "linha fica em `rodando` para sempre"
    )
    assert status_do_documento(motor) == ["failed"]


def test_a_ingestao_relanca_a_falha(monkeypatch):
    # O outro lado do C1, direto: quem decide entre retentativa e fim da linha e
    # o envelope, entao a ingestao registra `failed` e devolve a excecao.
    motor = MotorFalso()
    monkeypatch.setattr(ingestao, "engine", motor)
    _ingestao_que_falha(monkeypatch)

    with pytest.raises(RuntimeError):
        asyncio.run(ingestao.process_ingestion("doc-1", "u", "p"))

    assert status_do_documento(motor) == ["processing", "failed"]


def test_reexecucao_limpa_os_chunks_na_transacao_que_marca_processing(monkeypatch):
    # A fila e at-least-once e `add_chunks` e INSERT puro, sem unique em
    # (document_id, chunk_index): sem esta limpeza a retentativa ANEXA um
    # segundo conjunto completo de chunks.
    motor = MotorFalso()
    monkeypatch.setattr(ingestao, "engine", motor)
    _ingestao_que_falha(monkeypatch)

    with pytest.raises(RuntimeError):
        asyncio.run(ingestao.process_ingestion("doc-1", "u", "p"))

    limpezas = [
        (tx, params)
        for tx, sql, params in motor.executados
        if "DELETE FROM chunks WHERE document_id" in sql
    ]
    assert limpezas, "reexecucao duplicaria todos os chunks do documento"
    assert limpezas[0][1] == {"id": "doc-1"}

    marcacoes = [
        tx for tx, sql, _ in motor.executados if "status = 'processing'" in sql
    ]
    assert limpezas[0][0] == marcacoes[0], (
        "limpeza fora da transacao que marca `processing`: da para commitar a "
        "remocao dos chunks e nao entrar em reprocessamento"
    )

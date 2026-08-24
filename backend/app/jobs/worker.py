"""Processo que consome a fila. Roda em container proprio, nao no web.

O envelope de cada job vive aqui, e o trabalho em si em `ingestao.py`: assim o
que muda por causa da fila (progresso, retentativa, dead-letter) nao se mistura
com o que muda por causa do dominio (como se lê um PDF).

Contrato de retentativa: `esgotou` usa `MAX_TENTATIVAS` do nucleo, e
`WorkerSettings.max_tries` usa a MESMA constante. Se os dois divergirem, o arq
encerra o job sem chamar a funcao na ultima tentativa, `descartar` nunca roda, e
a linha fica em `rodando` para sempre — o job some da tela sem erro nenhum.
"""

from __future__ import annotations

import logging
import os

from agent_ops import queue
from arq.connections import RedisSettings

from app.db.engine import engine
from app.jobs.ingestao import process_ingestion

logger = logging.getLogger(__name__)


async def ingerir(ctx, doc_id: str, user_id: str, storage_path: str) -> None:
    """Envelope da ingestao: progresso, retentativa e dead-letter."""
    job_id = ctx["job_id"]
    queue.marcar(
        engine, job_id, estado="rodando", percentual=0, tentativas=ctx["job_try"]
    )
    try:
        await process_ingestion(doc_id, user_id, storage_path)
    except Exception as exc:
        if queue.esgotou(ctx):
            queue.descartar(engine, job_id, motivo=f"{type(exc).__name__}: {exc}")
            return
        logger.warning(
            "ingestao.retentativa doc_id=%s tentativa=%d erro=%s",
            doc_id, ctx["job_try"], exc,
        )
        queue.tentar_de_novo(ctx)
    else:
        queue.marcar(engine, job_id, estado="concluido", percentual=100)


async def _ao_subir(ctx) -> None:
    """Garante a tabela de progresso antes de aceitar o primeiro job.

    Esquecer isso e silencioso: `marcar` engole "no such table" e `ler` devolve
    None, indistinguivel de "o job nunca comecou".

    O web (`app/db/migrate.py`) chama o MESMO `aplicar_schema` no proprio boot,
    e no deploy os dois processos sobem ao mesmo tempo. `CREATE TABLE IF NOT
    EXISTS` nao e atomico entre duas sessoes Postgres concorrentes: pode
    levantar erro de chave duplicada em `pg_type`/`pg_class` em vez de uma das
    duas simplesmente vencer em silencio. Essa e uma corrida ESPERADA (nao uma
    falha de schema), entao aqui — do lado do worker — ela e tolerada: loga e
    segue. O worker PRECISA subir mesmo sozinho (sem o web por perto), entao
    nunca faz sentido derrubar o boot por causa dela. O `except` e amplo de
    proposito (nao so a excecao da corrida), mas o log preserva o traceback e o
    tipo, entao um problema de schema genuino continua visivel — so nao
    encerra o worker.
    """
    try:
        queue.aplicar_schema(engine)
    except Exception as exc:
        logger.exception(
            "worker.aplicar_schema_tolerado erro=%s: %s — seguindo o boot "
            "(a tabela existe de um jeito ou de outro; se nao for a corrida "
            "com o web, o proximo `marcar`/`ler` vai logar de novo)",
            type(exc).__name__,
            exc,
        )


class WorkerSettings:
    functions = [ingerir]
    on_startup = _ao_subir
    redis_settings = RedisSettings.from_dsn(
        os.environ.get("AGENT_OPS_REDIS_URL", "redis://redis:6379")
    )
    # Dimensionado para a VPS de 2 nucleos. A ingestao e I/O-bound (espera de
    # provider), entao mais de 4 em paralelo compete por CPU sem ganhar vazao.
    max_jobs = 4
    max_tries = queue.MAX_TENTATIVAS
    # A ingestao de um PDF grande com enriquecimento por chunk passa dos 5min
    # padrao do arq com folga.
    job_timeout = 1_800

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

import asyncio
import logging

from agent_ops import queue
from agent_ops.config import get_config
from arq.connections import RedisSettings
from sqlalchemy import text as sqltext

from app.db.engine import engine
from app.db.migrate import _LOCK_KEY
from app.jobs.ingestao import process_ingestion

logger = logging.getLogger(__name__)


def _marcar_documento(doc_id: str, status: str) -> None:
    """Escreve o estado terminal na LINHA DO DOCUMENTO, nao so em `job_progress`.

    Sao duas fontes duraveis e o frontend le a do documento: ele consulta
    `/documents` a cada 3s enquanto o status for `processing`. Um job morto que
    so mexesse em `job_progress` deixaria a linha em `processing` para sempre —
    o navegador daquele usuario consultando a mesma coisa indefinidamente.

    Melhor esforco, mesmo contrato do `queue.marcar`: perder o estado da tela e
    ruim, derrubar o envelope (e com ele a dead-letter) por causa de um UPDATE
    e pior.
    """
    try:
        with engine.begin() as conn:
            conn.execute(
                sqltext("UPDATE documents SET status = :status WHERE id = :id"),
                {"status": status, "id": doc_id},
            )
    except Exception as exc:
        logger.exception(
            "worker.status_documento_falhou doc_id=%s status=%s erro=%s: %s",
            doc_id, status, type(exc).__name__, exc,
        )


async def ingerir(ctx, doc_id: str, user_id: str, storage_path: str) -> None:
    """Envelope da ingestao: progresso, retentativa e dead-letter.

    Todo caminho de saida daqui deixa `job_progress` e `documents` CONTANDO A
    MESMA HISTORIA. Enquanto `process_ingestion` engolia a propria excecao, este
    envelope caia no `else` e gravava `concluido` para um documento `failed`.
    """
    job_id = ctx["job_id"]
    queue.marcar(
        engine, job_id, estado="rodando", percentual=0, tentativas=ctx["job_try"]
    )
    try:
        await process_ingestion(doc_id, user_id, storage_path)
    except asyncio.CancelledError:
        # `CancelledError` deriva de BaseException, entao o `except Exception`
        # abaixo NAO a pega. Sem esta clausula, um job que estoura o
        # `job_timeout` — ou que e cancelado no shutdown do worker — deixa a
        # linha em `rodando` para sempre e o documento em `processing`, e o
        # frontend consulta aquele documento a cada 3s indefinidamente. Nao e
        # caso exotico: o enriquecimento chama o LLM uma vez por chunk, entao um
        # PDF grande contra um provider lento passa dos 30 min do `job_timeout`.
        queue.marcar(engine, job_id, estado="falhou",
                     detalhe="cancelado (timeout ou shutdown do worker)")
        _marcar_documento(doc_id, "failed")
        # Re-lanca sempre: cancelamento nao se engole. No SIGTERM do redeploy e
        # o arq quem reenfileira o job, e engolir aqui o daria por terminado.
        raise
    except Exception as exc:
        if queue.esgotou(ctx):
            queue.descartar(engine, job_id, motivo=f"{type(exc).__name__}: {exc}")
            # `descartar` so mexe em `job_progress`. Sem esta linha o documento
            # fica em `processing` para sempre depois da ultima tentativa.
            _marcar_documento(doc_id, "failed")
            return
        logger.warning(
            "ingestao.retentativa doc_id=%s tentativa=%d erro=%s",
            doc_id, ctx["job_try"], exc,
        )
        # `process_ingestion` ja gravou `failed` ao sair. Ainda ha tentativa
        # sobrando, entao o documento volta para `processing`: senao a UI mostra
        # falha definitiva enquanto uma retentativa esta agendada.
        _marcar_documento(doc_id, "processing")
        queue.tentar_de_novo(ctx)
    else:
        queue.marcar(engine, job_id, estado="concluido", percentual=100)


async def _ao_subir(ctx) -> None:
    """Garante a tabela de progresso antes de aceitar o primeiro job.

    Esquecer isso e silencioso: `marcar` engole "no such table" e `ler` devolve
    None, indistinguivel de "o job nunca comecou".

    O web (`app/db/migrate.py`) chama o MESMO `aplicar_schema` no proprio boot,
    e no deploy os dois processos sobem ao mesmo tempo. `CREATE TABLE IF NOT
    EXISTS` nao e atomico entre duas sessoes Postgres concorrentes: sem
    coordenacao, pode levantar erro de chave duplicada em `pg_type`/`pg_class`
    em vez de uma das duas simplesmente vencer em silencio.

    A corrida e fechada aqui tomando o MESMO lock advisory que `migrate.py` usa
    para serializar replicas do web entre si (`_LOCK_KEY`, importado — nao
    duplicado — de la: duas copias da mesma constante que um dia divergem sao
    piores que uma corrida ocasional). Com o lock, web e worker esperam a vez
    um do outro antes de rodar `aplicar_schema`, entao a corrida de
    `pg_type`/`pg_class` deixa de ser alcancavel.

    O `try/except` continua aqui, agora como cinto-e-suspensorio: o worker
    PRECISA subir mesmo sozinho (sem o web por perto, ou se o lock falhar por
    algum motivo de infra), entao nunca faz sentido derrubar o boot por causa
    disto. O `except` e amplo de proposito (nao so a excecao da corrida), mas o
    log preserva o traceback e o tipo, entao um problema de schema genuino
    continua visivel — so nao encerra o worker.
    """
    with engine.connect() as conn:
        try:
            # `pg_advisory_lock` espera para SEMPRE por default, e este e o
            # MESMO lock que `run_migrations` segura durante o laco inteiro de
            # migrations. Sem timeout, um boot do web com migration demorada (ou
            # um lock orfao de uma sessao pendurada) prende o worker no
            # `on_startup`: o container fica de pe sem consumir job nenhum, que e
            # a falha mais silenciosa possivel numa fila. Com o timeout, a espera
            # vira excecao e cai no `except` tolerante abaixo — que e o que este
            # docstring ja prometia que acontecia.
            conn.execute(sqltext("SET lock_timeout = '30s'"))
            conn.execute(sqltext("SELECT pg_advisory_lock(:k)"), {"k": _LOCK_KEY})
            queue.aplicar_schema(engine)
        except Exception as exc:
            logger.exception(
                "worker.aplicar_schema_tolerado erro=%s: %s — seguindo o boot "
                "(a tabela existe de um jeito ou de outro; o log preserva o "
                "tipo caso nao seja apenas uma corrida)",
                type(exc).__name__,
                exc,
            )
        finally:
            # Fecha qualquer transacao pendente antes do unlock, senao o
            # proprio unlock reabriria uma e a conexao morreria com tx aberta
            # (mesmo motivo do finally identico em migrate.py). Destravar um
            # lock que nao chegou a ser tomado (timeout acima) e no-op: o
            # Postgres devolve `false` e avisa, nao levanta.
            conn.rollback()
            conn.execute(sqltext("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
            conn.commit()


class WorkerSettings:
    functions = [ingerir]
    on_startup = _ao_subir
    # A MESMA resolucao que o lado que enfileira usa (`queue.criar_pool` tambem
    # le `get_config().redis_url`). Ler a env na mao aqui criava defaults
    # divergentes nas duas pontas de uma fila so: faltando a env no container, o
    # web falhava alto (`localhost` sem Redis) e o worker se conectava calado a
    # OUTRO Redis, ficando ocioso numa fila que nunca recebe nada.
    redis_settings = RedisSettings.from_dsn(get_config().redis_url)
    # Dimensionado para a VPS de 2 nucleos. A ingestao e I/O-bound (espera de
    # provider), entao mais de 4 em paralelo compete por CPU sem ganhar vazao.
    max_jobs = 4
    max_tries = queue.MAX_TENTATIVAS
    # A ingestao de um PDF grande com enriquecimento por chunk passa dos 5min
    # padrao do arq com folga.
    job_timeout = 1_800
    # De quanto em quanto tempo o worker renova a chave de saude no Redis — a
    # mesma que `arq ... --check` (o healthcheck do compose) le. O default do
    # arq e 3600s: com ele, um worker morto continuaria "saudavel" por ate uma
    # hora, e o `restart` do Docker so agiria depois disso.
    health_check_interval = 30

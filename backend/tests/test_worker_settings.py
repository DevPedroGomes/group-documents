"""Prende o contrato do worker.

O que se prende aqui:
- `max_tries` e o `esgotou` do envelope usam a MESMA constante. Divergir faz o
  arq encerrar o job sem chamar a funcao na ultima tentativa: `descartar` nunca
  roda e a linha fica em `rodando` para sempre, sumindo da tela sem erro;
- o job de ingestao esta registrado, senao a fila aceita trabalho que nenhum
  worker sabe executar;
- o schema de progresso e aplicado no startup, senao `marcar` engole "no such
  table" e todo progresso vira silencio;
- o boot toma o MESMO lock advisory que `migrate.py` usa para serializar
  replicas do web entre si. Sem isto, web e worker sobem juntos no deploy e
  podem rodar `CREATE TABLE IF NOT EXISTS job_progress` ao mesmo tempo — o
  Postgres pode responder com erro de chave duplicada em `pg_type` em vez de
  um dos dois simplesmente vencer em silencio.

Sem rede e sem Redis: le a classe, nao sobe worker.
"""

import inspect

from agent_ops import queue
from agent_ops.config import get_config
from arq.connections import RedisSettings

from app.db import migrate
from app.jobs import worker


def test_max_tries_amarrado_a_constante_do_nucleo():
    assert worker.WorkerSettings.max_tries == queue.MAX_TENTATIVAS


def test_o_job_de_ingestao_esta_registrado():
    nomes = {f.__name__ for f in worker.WorkerSettings.functions}
    assert "ingerir" in nomes


def test_o_schema_de_progresso_e_aplicado_no_startup():
    fonte = inspect.getsource(worker._ao_subir)
    assert "aplicar_schema" in fonte


def test_o_lock_do_boot_e_o_mesmo_objeto_do_migrate():
    # Importado de `migrate.py`, nao duplicado aqui: duas copias da mesma
    # constante que um dia divergem sao piores que a corrida que o lock fecha.
    assert worker._LOCK_KEY is migrate._LOCK_KEY


def test_o_boot_serializa_o_schema_com_o_lock_advisory_do_migrate():
    fonte = inspect.getsource(worker._ao_subir)
    assert "pg_advisory_lock" in fonte
    assert "pg_advisory_unlock" in fonte


def test_o_envelope_marca_concluido_e_descarta():
    fonte = inspect.getsource(worker.ingerir)
    assert 'estado="rodando"' in fonte
    assert 'estado="concluido"' in fonte
    assert "descartar" in fonte
    assert "tentar_de_novo" in fonte


def test_o_timeout_cabe_uma_ingestao_longa():
    # O padrao do arq e 300s. Um PDF grande com enriquecimento por chunk passa
    # disso com folga, e o job seria morto no meio.
    assert worker.WorkerSettings.job_timeout >= 1_800


def test_o_worker_resolve_o_redis_pela_config_compartilhada():
    # As duas pontas da MESMA fila. Lendo a env na mao, o worker tinha default
    # `redis://redis:6379` e o web (via `get_config`) `redis://localhost:6379`:
    # faltando a env no container, o web falha alto e o worker atende calado uma
    # fila que nunca recebe nada.
    esperado = RedisSettings.from_dsn(get_config().redis_url)
    assert worker.WorkerSettings.redis_settings.host == esperado.host
    assert worker.WorkerSettings.redis_settings.port == esperado.port


def test_o_health_check_e_renovado_com_frequencia_util():
    # O healthcheck do compose (`arq ... --check`) le a chave que o worker
    # renova nesse intervalo. O default do arq e 3600s: um worker morto passaria
    # ate uma hora "saudavel", sem restart.
    assert worker.WorkerSettings.health_check_interval <= 60


def test_o_lock_do_boot_tem_timeout():
    import inspect

    fonte = inspect.getsource(worker._ao_subir)
    assert "lock_timeout" in fonte, (
        "`pg_advisory_lock` sem timeout espera para sempre pelo lock que "
        "`run_migrations` segura: o worker sobe e nunca consome job nenhum"
    )
    assert fonte.index("lock_timeout") < fonte.index("pg_advisory_lock(")
    # Dentro do `try` tolerante, senao o timeout derruba o boot em vez de virar
    # o log que o docstring promete.
    assert fonte.index("try:") < fonte.index("pg_advisory_lock(")

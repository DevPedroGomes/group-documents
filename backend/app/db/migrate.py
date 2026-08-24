"""Runner de migrations versionadas, idempotente, executado no boot da app.

Motivacao: o schema deste projeto divergiu do codigo em silencio — a coluna
`chunks.embedding` continuou `vector(1536)` (era OpenAI) depois da migracao pro
Voyage (1024), e TODA ingestao passou a falhar no INSERT. Ninguem percebeu
porque nada comparava schema esperado x schema real.

Contrato:
- Arquivos ficam em `sql/migrations/NNN_nome.sql`, aplicados em ordem numerica.
- Cada arquivo roda UMA vez, dentro de UMA transacao, e e registrado em
  `schema_migrations`. Falha => rollback daquele arquivo e a app NAO sobe.
- Migration ja registrada e pulada. Rodar duas vezes e no-op.
- Um lock advisory serializa instancias concorrentes (varias replicas subindo
  juntas nao aplicam a mesma migration duas vezes).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import text as sqltext

from app.db.engine import engine

logger = logging.getLogger(__name__)

# app/db/migrate.py -> app/ -> backend/ -> backend/migrations
# Fica DENTRO de backend/ de proposito: o build context da imagem e `./backend`,
# entao `sql/` na raiz do repo nao existe no container.
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

# Numero arbitrario mas fixo: identifica ESTE runner no pg_advisory_lock.
_LOCK_KEY = 8_270_114_512_003


def _discover() -> list[tuple[str, Path]]:
    if not MIGRATIONS_DIR.is_dir():
        logger.warning("migrate: diretorio nao encontrado: %s", MIGRATIONS_DIR)
        return []
    found: list[tuple[str, Path]] = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        m = re.match(r"^(\d+)_", path.name)
        if not m:
            logger.warning("migrate: ignorando '%s' (sem prefixo numerico)", path.name)
            continue
        found.append((path.name, path))
    return sorted(found, key=lambda t: (int(re.match(r"^(\d+)_", t[0]).group(1)), t[0]))


def run_migrations() -> None:
    """Aplica as migrations pendentes. Levanta excecao se alguma falhar."""
    migrations = _discover()
    if not migrations:
        logger.info("migrate: nenhuma migration encontrada")
        return

    with engine.begin() as conn:
        conn.execute(sqltext("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))

    with engine.connect() as conn:
        # Serializa entre instancias. O lock e de SESSAO, nao de transacao:
        # sobrevive aos commits abaixo e so cai no unlock/close.
        conn.execute(sqltext("SELECT pg_advisory_lock(:k)"), {"k": _LOCK_KEY})
        try:
            applied = {
                r[0] for r in conn.execute(sqltext("SELECT version FROM schema_migrations"))
            }
            # SQLAlchemy 2.0 abre transacao implicita no primeiro execute(); fechar
            # aqui e o que permite abrir uma transacao explicita por migration.
            conn.commit()

            # Schema de progresso da fila (`job_progress`). Dono e o pacote
            # `agent_ops`, nao uma migration numerada aqui — duplicar o DDL
            # criaria uma segunda fonte de verdade que diverge da primeira.
            # Fica DENTRO do lock advisory (antes do `finally` que libera),
            # nao depois: e o que serializa esta chamada contra outra REPLICA
            # DO WEB subindo ao mesmo tempo — a mesma razao do lock existir
            # para as migrations numeradas acima. `aplicar_schema` roda em
            # `CREATE TABLE IF NOT EXISTS`, que nao e atomico entre duas
            # sessoes Postgres concorrentes sem coordenacao; sem o lock, duas
            # replicas do web chegando juntas aqui arriscam um erro de chave
            # duplicada em `pg_type` em vez de uma delas so encontrar a tabela
            # pronta.
            #
            # O que este lock NAO cobre e o worker: `app/jobs/worker.py`
            # chama o MESMO `aplicar_schema` na propria conexao, sem tomar
            # este lock (precisa subir mesmo sem o web por perto). No deploy,
            # web e worker sobem juntos, entao a mesma corrida pode acontecer
            # entre os dois. Essa metade e tolerada do lado do worker
            # (`_ao_subir`, que loga e segue) porque so ele pode dar-se ao
            # luxo de nao ser fatal aqui; este `run_migrations()` continua
            # deliberadamente fatal para schema realmente quebrado.
            from agent_ops import queue

            queue.aplicar_schema(engine)

            pending = [(v, p) for v, p in migrations if v not in applied]
            if not pending:
                logger.info("migrate: schema em dia (%d aplicadas)", len(applied))
                return

            logger.info("migrate: %d pendente(s): %s", len(pending), ", ".join(v for v, _ in pending))
            for version, path in pending:
                sql = path.read_text(encoding="utf-8")
                # Transacao por migration: uma falha nao deixa schema meio-aplicado.
                try:
                    with conn.begin():
                        conn.execute(sqltext(sql))
                        conn.execute(
                            sqltext("INSERT INTO schema_migrations (version) VALUES (:v)"),
                            {"v": version},
                        )
                    logger.info("migrate: aplicada %s", version)
                except Exception:
                    logger.exception("migrate: FALHOU em %s — schema inalterado", version)
                    raise
        finally:
            # Fecha qualquer transacao pendente antes do unlock, senao o proprio
            # unlock reabriria uma e a conexao morreria com tx aberta.
            conn.rollback()
            conn.execute(sqltext("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
            conn.commit()

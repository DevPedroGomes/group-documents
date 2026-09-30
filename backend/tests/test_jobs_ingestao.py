"""Prende a ingestao fora do modulo de rotas.

Por que: enquanto `process_ingestion` morava em `api/routes/documents.py`, era
natural chama-la direto do handler. Com ela em `app/jobs/`, a chamada de dentro
do processo web fica visivelmente errada — que e o ponto, porque a proxima task
troca essa chamada por enfileiramento.
"""

import inspect
from pathlib import Path

from app.jobs import ingestao

BACKEND = Path(__file__).resolve().parents[1]


def test_a_ingestao_mora_em_jobs():
    assert (BACKEND / "app" / "jobs" / "ingestao.py").exists()
    assert inspect.iscoroutinefunction(ingestao.process_ingestion)


def test_a_assinatura_nao_mudou():
    parametros = list(inspect.signature(ingestao.process_ingestion).parameters)
    assert parametros == ["doc_id", "user_id", "storage_path"]


# A rota enfileira em vez de rodar a ingestao no processo web: pela rota, em
# tests/test_rotas_documentos.py.


# ---------------------------------------------------------------------------
# A causa da falha precisa chegar a quem subiu o arquivo
# ---------------------------------------------------------------------------

# `erro`, `preso` e `effective_date` na resposta de GET /documents: pela rota,
# com SQL de verdade, em tests/test_integracao_busca.py.
#
# O sucesso apaga so o erro da tentativa anterior, e a falha preserva o resto de
# `meta` (a origem de um documento vindo de URL): com SQL de verdade, em
# tests/test_integracao_ingestao.py.

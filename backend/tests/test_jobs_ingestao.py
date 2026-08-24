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


def test_a_rota_nao_define_mais_a_ingestao():
    fonte = (BACKEND / "app" / "api" / "routes" / "documents.py").read_text("utf-8")
    assert "async def process_ingestion" not in fonte, (
        "a ingestao voltou para o modulo de rotas"
    )

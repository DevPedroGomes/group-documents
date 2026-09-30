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


def test_o_sucesso_apaga_o_erro_da_tentativa_anterior():
    """Sem isto o erro fica grudado num documento que depois deu certo.

    Medido em producao: 6 documentos `completed` carregando um "You have not
    yet added your payment method" de uma recusa antiga da Voyage. Enquanto a
    causa nunca era projetada pela API isso era invisivel; passando a ser, o
    residuo viraria erro exibido em documento que funciona.
    """
    fonte = inspect.getsource(ingestao)
    assert "'completed'" in fonte
    assert "- 'error'" in fonte, (
        "o UPDATE de sucesso voltou a nao limpar meta->>'error'"
    )

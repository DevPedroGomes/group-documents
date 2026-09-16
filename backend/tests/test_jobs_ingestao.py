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


# ---------------------------------------------------------------------------
# A causa da falha precisa chegar a quem subiu o arquivo
# ---------------------------------------------------------------------------

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


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


def test_a_api_projeta_a_causa_que_a_tela_le():
    """Cruza os dois lados.

    A causa era gravada em meta->>'error' desde sempre, mas o SELECT de
    /documents nunca a projetava: a tela mostrava um badge "Failed" sem causa,
    sem retry, sem saida. Se a tela le `doc.erro`, a API tem de mandar.
    """
    rota = (BACKEND / "app" / "api" / "routes" / "documents.py").read_text()
    tela = (FRONTEND / "components" / "KnowledgeHub.tsx").read_text()

    le_na_tela = "doc.erro" in tela
    # "AS erro" e do SELECT; procurar so por meta->>'error' casaria tambem com
    # o comentario logo acima dele, e o teste passaria com o SQL ja quebrado.
    projeta = "AS erro" in rota

    assert le_na_tela, "a tela deixou de mostrar a causa"
    assert projeta, "a tela le doc.erro mas a API nao projeta meta->>'error'"


def test_documento_preso_na_fila_e_marcado_como_tal():
    """Se a fila perde o job, o documento fica pending para sempre.

    Nao ha varredura de status em lugar nenhum do backend, e o browser fica
    fazendo polling a cada 3s indefinidamente. Em vez de um job novo, o proprio
    SELECT calcula se o documento esta parado ha tempo demais.
    """
    rota = (BACKEND / "app" / "api" / "routes" / "documents.py").read_text()
    tela = (FRONTEND / "components" / "KnowledgeHub.tsx").read_text()

    assert "AS preso" in rota, "o SELECT deixou de calcular documento preso"
    assert "doc.preso" in tela, "a tela ignora o sinal de documento preso"

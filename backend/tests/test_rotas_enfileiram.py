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
  primeiro.
"""

from pathlib import Path

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

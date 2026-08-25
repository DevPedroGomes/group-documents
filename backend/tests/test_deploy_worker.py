"""Prende o que o worker precisa do compose para funcionar de verdade.

Sao afirmacoes sobre arquivos de deploy, nao sobre codigo Python, e mesmo assim
moram na suite: cada uma delas ja tinha ou teria produzido um worker que PARECE
saudavel e nao processa nada — a falha mais cara desta arquitetura, porque o
upload continua respondendo 200 e o documento simplesmente nunca fica pronto.
"""

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((RAIZ / "docker-compose.yml").read_text("utf-8"))
WORKER = COMPOSE["services"]["worker"]
BACKEND = COMPOSE["services"]["backend"]


def _envs(servico: dict) -> dict[str, str]:
    return dict(item.split("=", 1) for item in servico["environment"])


def test_o_healthcheck_do_worker_pode_reprovar():
    # `CMD-SHELL` roda `/bin/sh -c "<comando>"`, e a linha de comando desse
    # shell contem o padrao procurado: `pgrep -f 'arq app.jobs.worker'` casava
    # com o proprio health check e NUNCA reprovava. (E `pgrep` vem do `procps`,
    # que a imagem `python:3.12-slim` nao traz e o Dockerfile nao instala.)
    teste = WORKER["healthcheck"]["test"]
    assert "pgrep" not in " ".join(teste)
    assert teste == ["CMD", "arq", "app.jobs.worker.WorkerSettings", "--check"]


def test_o_worker_recebe_o_jwt_secret():
    # `Settings.jwt_secret` e obrigatorio e sem default, e `app/db/engine.py`
    # instancia as settings no import — que `app/jobs/worker.py` faz. Sem a env,
    # o worker morre no import, antes de o arq subir.
    assert _envs(WORKER)["JWT_SECRET"] == "${JWT_SECRET}"


def test_as_duas_pontas_da_fila_veem_o_mesmo_teto_de_profundidade():
    # Explicito nos dois: o default do pacote (500) com `max_jobs = 4` e jobs de
    # ate 30 min e backlog de dias enquanto a API responde 200.
    assert int(_envs(BACKEND)["AGENT_OPS_PROFUNDIDADE_MAXIMA"]) <= 100
    assert (
        _envs(WORKER)["AGENT_OPS_PROFUNDIDADE_MAXIMA"]
        == _envs(BACKEND)["AGENT_OPS_PROFUNDIDADE_MAXIMA"]
    )


def test_o_worker_le_os_arquivos_que_a_api_gravou():
    assert any(v.endswith(":/app/uploads") for v in WORKER["volumes"])


def test_o_push_do_deploy_nao_nomeia_servicos():
    # Lista na mao e uma lista para esquecer de atualizar. Sem argumento, o
    # `push` cobre todo servico com `build:` e ignora os que nao tem.
    fluxo = yaml.safe_load((RAIZ / ".github" / "workflows" / "deploy.yml").read_text("utf-8"))
    comandos = [
        passo.get("run", "") for passo in fluxo["jobs"]["build"]["steps"]
    ]
    empurra = [c for c in comandos if "docker compose push" in c]
    assert empurra, "o build nao publica imagem nenhuma"
    for comando in empurra:
        for linha in comando.splitlines():
            linha = linha.strip()
            if linha.startswith("docker compose push"):
                assert linha == "docker compose push", (
                    f"push nomeando servicos: {linha!r}"
                )

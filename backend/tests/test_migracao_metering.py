"""Prende a migracao do teto de gasto para o nucleo compartilhado.

O que se prende aqui:
- o modulo local `app/core/budget.py` nao volta. Ele carregava um bug que
  esteve em producao: com `incrby` e `expire` no mesmo `try`, uma falha de
  rede entre as duas idas ao Redis levantava TetoAtingido SEM desfazer o
  incremento — chamada recusada cobrando cota do proximo visitante;
- as rotas separam 503 (backend de cota ilegivel) de 429 (teto do dia
  atingido). Antes as duas coisas eram 503, e nenhum painel conseguia
  distinguir saturacao de indisponibilidade;
- `panorama` recebe os limites, que e a unica quebra de contrato da migracao.

Sem rede, sem Redis, sem chave: o job `test` do deploy trava o build e roda
isolado.
"""

import asyncio
import inspect
from pathlib import Path

import pytest
from agent_ops import metering

from app.main import create_app  # noqa: F401  (garante que o app monta)

BACKEND = Path(__file__).resolve().parents[1]


def test_o_modulo_local_de_teto_nao_voltou():
    assert not (BACKEND / "app" / "core" / "budget.py").exists(), (
        "budget.py voltou; ele carregava o bug de cota que esteve em producao"
    )


def test_nenhum_arquivo_importa_o_budget_local():
    ofensores = []
    for py in (BACKEND / "app").rglob("*.py"):
        texto = py.read_text(encoding="utf-8")
        if "app.core.budget" in texto or "from app.core import budget" in texto:
            ofensores.append(str(py.relative_to(BACKEND)))
    assert ofensores == [], f"ainda importam o teto local: {ofensores}"


@pytest.mark.parametrize("erro,status", [
    (metering.TetoIndisponivel, 503),
    (metering.TetoAtingido, 429),
])
def test_a_cota_separa_indisponivel_de_teto_atingido(monkeypatch, erro, status):
    """`TetoIndisponivel` e subclasse de `TetoAtingido`: um `except` na ordem
    errada engole o 503 e todo Redis fora do ar vira "volte amanha". Pelas
    rotas em tests/test_rotas_documentos.py e tests/test_chat_pipeline.py."""
    from fastapi import HTTPException

    from app.api.dependencies import consumir_cota

    async def consumir(_tipo, _limite):
        raise erro("recusado")

    monkeypatch.setattr(metering, "consumir", consumir)

    with pytest.raises(HTTPException) as recusa:
        asyncio.run(consumir_cota("chat", 1))

    assert recusa.value.status_code == status
    assert ("Retry-After" in (recusa.value.headers or {})) is (status == 429)


def test_teto_indisponivel_e_subclasse_para_o_except_antigo_seguir_valendo():
    assert issubclass(metering.TetoIndisponivel, metering.TetoAtingido)


def test_panorama_recebe_os_limites():
    # A unica quebra de contrato da migracao: no modulo antigo `panorama()` lia
    # os limites das settings sozinha.
    assinatura = inspect.signature(metering.panorama)
    assert "limites" in assinatura.parameters

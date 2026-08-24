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

import ast
import inspect
from pathlib import Path

from agent_ops import metering

import importlib
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


ROTAS_COM_COTA = ("documents", "chat")


def test_as_rotas_separam_indisponivel_de_teto_atingido():
    # Varre TODOS os modulos de rota que consomem cota, nao so `documents`.
    # O plano original listava quatro call sites e havia cinco: `chat.py`
    # tambem consome, e so apareceu porque o grep que montou o plano estava
    # truncado. Um teste que olha um modulo so repetiria o mesmo erro.
    for nome in ROTAS_COM_COTA:
        modulo = importlib.import_module(f"app.api.routes.{nome}")
        fonte = inspect.getsource(modulo)
        arvore = ast.parse(fonte)

        handlers = [
            no
            for no in ast.walk(arvore)
            if isinstance(no, ast.ExceptHandler) and no.type is not None
        ]
        nomes = {ast.unparse(h.type) for h in handlers}

        assert "metering.TetoIndisponivel" in nomes, (
            f"{nome} nao trata TetoIndisponivel; 503 e 429 se misturam de novo"
        )
        assert "metering.TetoAtingido" in nomes, f"{nome} nao trata TetoAtingido"


def test_teto_indisponivel_e_subclasse_para_o_except_antigo_seguir_valendo():
    assert issubclass(metering.TetoIndisponivel, metering.TetoAtingido)


def test_a_ordem_dos_except_pega_o_subtipo_primeiro():
    # `TetoIndisponivel` e subclasse de `TetoAtingido`. Se o handler generico
    # vier antes, ele engole o especifico e todo 503 vira 429 — o erro fica
    # invisivel justamente porque o codigo "funciona".
    for nome in ROTAS_COM_COTA:
      modulo = importlib.import_module(f"app.api.routes.{nome}")
      arvore = ast.parse(inspect.getsource(modulo))

      for tentativa in [n for n in ast.walk(arvore) if isinstance(n, ast.Try)]:
        tipos = [
            ast.unparse(h.type)
            for h in tentativa.handlers
            if h.type is not None
        ]
        if "metering.TetoAtingido" in tipos and "metering.TetoIndisponivel" in tipos:
            assert tipos.index("metering.TetoIndisponivel") < tipos.index(
                "metering.TetoAtingido"
            ), "TetoAtingido vem antes e engole o TetoIndisponivel"


def test_panorama_recebe_os_limites():
    # A unica quebra de contrato da migracao: no modulo antigo `panorama()` lia
    # os limites das settings sozinha.
    assinatura = inspect.signature(metering.panorama)
    assert "limites" in assinatura.parameters

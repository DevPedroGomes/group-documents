"""Testes da conversa por voz sobre o acervo.

O que se prende aqui, e o porquê de cada um:

- **O isolamento entre inquilinos não pode depender do cliente.** Na voz o
  `function_call` é montado pelo modelo e repassado pelo NAVEGADOR. Se a busca
  aceitasse um identificador de usuário vindo do corpo, bastaria editar um JSON
  no console para ler o acervo de outra pessoa.
- **A tool tem que gravar a trilha.** É o motivo inteiro de a voz ter migrado do
  `voice_rag` para cá. Uma refatoração que devolva os trechos sem gravar deixa a
  demo "funcionando" e sem provar nada, que é a forma mais cara de quebrar.
- **As duas obrigações do prompt.** Avisar sobre divergência e dizer qual recorte
  no tempo usou são o que o acervo permite e o chat de provedor não faz. Elas
  vivem numa string, e string some numa edição distraída sem quebrar teste nenhum.
- **A ordem do teto.** Consumir depois de cunhar deixaria a credencial paga de pé
  mesmo com a cota estourada.

Nenhum toca a rede.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.api.routes import realtime as rt

FONTE = Path(inspect.getfile(rt)).read_text(encoding="utf-8")
ARVORE = ast.parse(FONTE)


def _funcao(nome: str) -> ast.AsyncFunctionDef:
    for no in ast.walk(ARVORE):
        if isinstance(no, ast.AsyncFunctionDef) and no.name == nome:
            return no
    raise AssertionError(f"{nome} não existe mais em realtime.py")


# ---------------------------------------------------------------------------
# 1. Isolamento entre inquilinos, por construção
# ---------------------------------------------------------------------------

def test_o_corpo_da_busca_nao_aceita_identificador_de_usuario():
    """O cliente não escolhe de quem é o acervo. Nem por engano, nem de propósito."""
    proibidos = {"user_id", "usuario", "tenant", "tenant_id", "owner", "email"}
    campos = set(rt.BuscaPedido.model_fields)
    assert not (campos & proibidos), f"campo perigoso em BuscaPedido: {campos & proibidos}"


def test_a_busca_usa_o_user_id_do_jwt_e_nao_o_do_corpo():
    fn = _funcao("executar_busca")

    # `user_id` nasce de `require_user`, e de nada mais.
    origens = [
        no for no in ast.walk(fn)
        if isinstance(no, ast.Assign)
        and any(getattr(a, "id", None) == "user_id" for a in no.targets)
    ]
    assert len(origens) == 1, "user_id deveria ter uma única origem"
    chamada = origens[0].value
    if isinstance(chamada, ast.Await):
        chamada = chamada.value
    assert isinstance(chamada, ast.Call)
    assert getattr(chamada.func, "id", None) == "require_user"

    # e chega ao retriever como a variável, nunca como algo tirado de `body`.
    for no in ast.walk(fn):
        if isinstance(no, ast.Call) and getattr(no.func, "id", None) == "retrieve_documents":
            kw = {k.arg: k.value for k in no.keywords}
            assert "user_id" in kw, "retrieve_documents sem user_id"
            assert getattr(kw["user_id"], "id", None) == "user_id"
            break
    else:
        raise AssertionError("executar_busca não chama retrieve_documents")


def test_as_duas_rotas_exigem_autenticacao():
    for nome in ("criar_sessao", "executar_busca"):
        fonte = ast.get_source_segment(FONTE, _funcao(nome)) or ""
        assert "require_user" in fonte, f"{nome} não exige autenticação"


# ---------------------------------------------------------------------------
# 2. A trilha, que é o motivo da migração
# ---------------------------------------------------------------------------

def test_a_busca_grava_a_trilha_de_decisao():
    fonte = ast.get_source_segment(FONTE, _funcao("executar_busca")) or ""
    assert "save_decision" in fonte, (
        "a tool voltou a só devolver trechos. Gravar a trilha é o motivo de a voz "
        "ter saído do voice_rag: sem isso este projeto vira o que ele substituiu."
    )


def test_a_trilha_recebe_latencia_conflito_e_recorte():
    """Os três campos que a voz acrescenta e que o painel mostra."""
    fn = _funcao("executar_busca")
    for no in ast.walk(fn):
        if isinstance(no, ast.Call) and getattr(no.func, "id", None) == "save_decision":
            passados = {k.arg for k in no.keywords}
            for campo in ("latency_ms", "conflict", "as_of", "user_id", "question"):
                assert campo in passados, f"save_decision sem {campo}"
            break
    else:
        raise AssertionError("save_decision não é chamada")


def test_o_detector_de_divergencia_roda_na_busca():
    fonte = ast.get_source_segment(FONTE, _funcao("executar_busca")) or ""
    assert "detectar_conflito" in fonte


# ---------------------------------------------------------------------------
# 3. As obrigações que vivem numa string
# ---------------------------------------------------------------------------

def test_o_prompt_manda_avisar_sobre_divergencia():
    texto = rt.INSTRUCOES.lower()
    assert "disagree" in texto, "o prompt não manda mais avisar quando as fontes divergem"
    assert "name both" in texto or "both" in texto, "o prompt não manda nomear as duas fontes"


def test_o_prompt_manda_dizer_o_recorte_no_tempo():
    assert "data_de_referencia" in rt.INSTRUCOES
    assert "cutoff" in rt.INSTRUCOES.lower()


def test_o_prompt_proibe_responder_de_memoria():
    texto = rt.INSTRUCOES.lower()
    assert "never answer from memory" in texto
    assert "not fill the gap" in texto, "sumiu a instrução de não preencher lacuna"


def test_o_prompt_lembra_que_a_resposta_e_falada():
    texto = rt.INSTRUCOES.lower()
    assert "no markdown" in texto or "no lists" in texto, (
        "sem isto o agente dita marcador de lista em voz alta"
    )


# ---------------------------------------------------------------------------
# 4. A tool declarada ao modelo
# ---------------------------------------------------------------------------

def test_a_tool_declara_o_recorte_no_tempo():
    """Sem o parâmetro declarado, `as_of` nunca é exercido pela voz."""
    props = rt.FERRAMENTA_BUSCA["parameters"]["properties"]
    assert "pergunta" in props
    assert "data_de_referencia" in props, "a tool não consegue mais perguntar sobre uma data"
    assert rt.FERRAMENTA_BUSCA["parameters"]["required"] == ["pergunta"]


def test_a_tool_nao_aceita_campo_extra():
    """`additionalProperties: false` impede o modelo de inventar argumento."""
    assert rt.FERRAMENTA_BUSCA["parameters"]["additionalProperties"] is False


# ---------------------------------------------------------------------------
# 5. Teto e degradação
# ---------------------------------------------------------------------------

def test_o_teto_e_consumido_antes_de_cunhar_a_credencial():
    """Ordem importa: cunhar antes deixaria a credencial paga de pé com cota estourada."""
    fonte = ast.get_source_segment(FONTE, _funcao("criar_sessao")) or ""
    assert "consumir" in fonte and "CLIENT_SECRETS_URL" in fonte
    assert fonte.index("consumir") < fonte.index("CLIENT_SECRETS_URL"), (
        "o teto passou a ser consumido depois de cunhar a credencial"
    )


def test_sem_chave_da_openai_a_voz_recusa_em_vez_de_quebrar():
    fonte = ast.get_source_segment(FONTE, _funcao("criar_sessao")) or ""
    assert "openai_api_key" in fonte and "503" in fonte


def test_resposta_vazia_e_valida_e_nao_erro():
    """É o que permite o agente dizer "não está nos seus documentos"."""
    r = rt.BuscaResposta(trechos=[], baixa_confianca=True)
    assert r.trechos == []
    assert r.baixa_confianca is True
    assert r.divergencia is None


@pytest.mark.parametrize("campo", ["texto", "arquivo", "pagina"])
def test_o_trecho_devolvido_nomeia_a_fonte(campo):
    """O agente cita o arquivo em voz alta: sem o nome, não há como citar."""
    assert campo in rt.Trecho.model_fields

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


@pytest.fixture
def voz(monkeypatch):
    """A rota da tool com os dubles do pipeline (tests/dubles_chat.py)."""
    from app.api.rate_limit import limiter
    from app.main import create_app
    from fastapi.testclient import TestClient

    from tests.dubles_chat import USUARIO, Cenario, instalar

    cenario = Cenario()
    instalar(monkeypatch, cenario)

    async def usuario(_request):
        return USUARIO

    monkeypatch.setattr(rt, "require_user", usuario)
    monkeypatch.setattr(rt, "save_decision", lambda **kw: cenario.decisoes.append(kw))
    limiter.enabled = False
    try:
        yield TestClient(create_app()), cenario
    finally:
        limiter.enabled = True


def test_a_busca_usa_o_user_id_do_jwt_e_grava_a_trilha_com_as_consultas(voz):
    """O cliente nao escolhe de quem e o acervo, e a tool grava a trilha: e o
    motivo inteiro de a voz ter saido do voice_rag."""
    from tests.dubles_chat import USUARIO, trecho

    cliente, cenario = voz
    cenario.acervo["qual o prazo?"] = [trecho("c1", "d1", "Politica"), trecho("c2", "d1", "Politica")]

    r = cliente.post(
        "/realtime/tool/buscar",
        json={"pergunta": "qual o prazo?", "data_de_referencia": "2025-03-01",
              "user_id": "00000000-0000-0000-0000-0000000000ff"},
        headers={"Authorization": "Bearer x"},
    )

    assert r.status_code == 200, r.text
    assert [t["arquivo"] for t in r.json()["trechos"]] == ["Politica", "Politica"]
    assert {b["user_id"] for b in cenario.buscas} == {USUARIO}
    assert {b["as_of"] for b in cenario.buscas} == {"2025-03-01"}
    (decisao,) = cenario.decisoes
    assert decisao["user_id"] == USUARIO
    assert decisao["question"] == "qual o prazo?"
    assert decisao["queries"] == ["qual o prazo?", "variante um", "variante dois"]
    assert decisao["as_of"] == "2025-03-01"
    assert isinstance(decisao["latency_ms"], int)
    assert "conflict" in decisao


def test_a_busca_devolve_a_divergencia_com_a_fonte_vigente(voz):
    """O prompt manda dizer o valor da fonte em vigor; sem `vigente` o agente
    teria de adivinhar qual das duas vale."""
    from tests.dubles_chat import trecho

    cliente, cenario = voz
    cenario.acervo["qual o prazo?"] = [
        trecho("c1", "d1", "Contrato", data="2023-05-10", texto="prazo de 30 dias"),
        trecho("c2", "d2", "Aditivo", data="2025-02-01", texto="prazo de 15 dias uteis"),
    ]
    cenario.resposta_conflito = (
        '{"conflict": true, "summary": "O prazo difere.", "sources": ["Contrato", "Aditivo"]}'
    )

    r = cliente.post("/realtime/tool/buscar", json={"pergunta": "qual o prazo?"},
                     headers={"Authorization": "Bearer x"})

    assert r.status_code == 200, r.text
    esperado = {"summary": "O prazo difere.", "sources": ["Contrato", "Aditivo"], "vigente": "Aditivo"}
    assert r.json()["divergencia"] == esperado
    assert cenario.decisoes[0]["conflict"] == esperado
    assert "vigente" in rt.INSTRUCOES


@pytest.mark.parametrize("rota,corpo", [
    ("/realtime/session", None),
    ("/realtime/tool/buscar", {"pergunta": "qual o prazo?"}),
])
@pytest.mark.parametrize("cabecalho", [
    {},
    {"Authorization": "Bearer abc.def.ghi"},
    {"Authorization": "Token sem-bearer"},
    "assinado-com-outro-segredo",
])
def test_as_duas_rotas_recusam_quem_nao_esta_autenticado(monkeypatch, rota, corpo, cabecalho):
    """Pela rota, com o `require_user` de verdade: sem token, token malformado
    ou assinado com outro segredo e 401, antes de qualquer busca ou credencial."""
    import jwt
    from fastapi.testclient import TestClient

    from app.api.rate_limit import limiter
    from app.main import create_app

    chamadas: list[str] = []
    monkeypatch.setattr(rt, "retrieve_documents", lambda **kw: chamadas.append("busca"))
    if cabecalho == "assinado-com-outro-segredo":
        token = jwt.encode({"sub": "00000000-0000-0000-0000-00000000000a"},
                           "outro-segredo-com-32-bytes-ou-mais!!", algorithm="HS256")
        cabecalho = {"Authorization": f"Bearer {token}"}
    limiter.enabled = False
    try:
        r = TestClient(create_app()).post(rota, json=corpo, headers=cabecalho)
    finally:
        limiter.enabled = True

    assert r.status_code == 401, r.text
    assert chamadas == []


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


def test_a_voz_le_a_mesma_chave_de_texto_que_a_busca_produz():
    """O bug que este teste existe para impedir.

    A rota de voz lia `t["content"]`, mas todo o caminho de busca devolve a
    chave `snippet`. O agente recebia o nome do arquivo e a pagina com o texto
    VAZIO e, como o prompt proibe responder de memoria, dizia "nao esta nos
    seus documentos" em 100% das perguntas. Nada na UI denunciava: ela mostra
    so a contagem de trechos, e a contagem estava certa.

    A suite nao pegou porque os testes de voz asseveram o texto-fonte. Este
    cruza duas fontes: as chaves que a busca PRODUZ e as que a voz LE.
    """
    import re

    from app.services import vector_store

    # Só o dict que hybrid_search DEVOLVE. Olhar o módulo inteiro afrouxa o
    # teste: "content" aparece lá em outro contexto e deixa o bug passar.
    produzidas = set(
        re.findall(r'"([a-z_]+)":', inspect.getsource(vector_store.hybrid_search))
    )
    lidas = set(re.findall(r't\.get\("([a-z_]+)"', inspect.getsource(rt)))

    assert lidas, "nenhuma leitura de trecho encontrada na rota de voz"
    faltando = lidas - produzidas
    assert not faltando, (
        f"a voz le chaves que a busca nunca devolve: {sorted(faltando)}"
    )


# ---------------------------------------------------------------------------
# A sessao precisa CONFIGURAR o que o navegador escuta
# ---------------------------------------------------------------------------

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def test_transcricao_da_entrada_e_configurada_porque_o_navegador_a_escuta():
    """Cruza os dois lados em vez de prender uma string.

    `audio.input.transcription` nasce null na API. Sem configurar, o evento
    `conversation.item.input_audio_transcription.completed` NUNCA e emitido — e
    `realtime-session.ts` tem um listener para ele. O resultado era um listener
    morto e metade da linha do tempo do painel vazia: so a fala do agente
    aparecia, a da pessoa nunca.
    """
    sessao = FRONTEND / "lib" / "realtime-session.ts"
    escuta = "input_audio_transcription.completed" in sessao.read_text()

    fonte = inspect.getsource(rt.criar_sessao)
    configura = '"transcription"' in fonte

    assert escuta, "o navegador deixou de escutar a transcricao da entrada"
    assert configura, (
        "o navegador escuta input_audio_transcription.completed, mas a sessao nao "
        "configura audio.input.transcription — o evento nunca sera emitido"
    )


def test_turn_detection_e_explicito_e_nao_herdado():
    """O default e threshold 0.5 / silencio 500ms, e ninguem sabia disso.

    Com a frase de preenchimento o agente fala muito mais, e 0.5 realimenta pelo
    alto-falante. 500ms tambem corta quem pausa para pensar.
    """
    fonte = inspect.getsource(rt.criar_sessao)
    assert '"turn_detection"' in fonte, "turn_detection voltou a ser herdado do default"
    assert '"silence_duration_ms"' in fonte
    assert '"threshold"' in fonte


def test_o_modelo_e_mandado_falar_antes_de_buscar():
    """A busca leva de 5 a 15s e a pessoa ouve silencio absoluto nesse intervalo.

    A obrigacao vive em DOIS lugares de proposito: no prompt e na descricao da
    tool. O modelo le a descricao no instante em que decide chamar, que e
    exatamente quando precisa saber que a chamada e lenta.
    """
    assert "holding phrase" in rt.INSTRUCOES
    assert "silence" in rt.FERRAMENTA_BUSCA["description"]


def test_erro_de_busca_e_campo_proprio_e_o_prompt_sabe_le_lo():
    """Lista vazia e "nao esta no acervo". `erro` e "nao consegui olhar".

    Sem separar, um 500 do backend fazia o agente afirmar com confianca que o
    documento da pessoa nao continha aquilo — falso, e justamente a falha que
    este acervo existe para impedir.
    """
    assert "erro" in rt.BuscaResposta.model_fields
    assert "`erro`" in rt.INSTRUCOES, "o contrato tem o campo, mas o prompt nao o le"


def test_a_busca_dispara_antes_do_fim_da_resposta():
    """Se o gatilho volta para `response.done`, a frase de preenchimento perde o efeito.

    Ela tocaria, terminaria, e so entao a busca comecaria: o silencio volta
    inteiro, apenas deslocado. O disparo em `function_call_arguments.done`
    sobrepoe a busca a fala.
    """
    sessao = (FRONTEND / "lib" / "realtime-session.ts").read_text()
    assert "response.function_call_arguments.done" in sessao
    assert "AbortSignal.timeout" in sessao, "fetch sem timeout e silencio sem fim"


# ---------------------------------------------------------------------------
# A conversa que cai, e a cota que some sem conversa nenhuma
# ---------------------------------------------------------------------------


def test_cota_volta_quando_a_credencial_nao_e_cunhada():
    """O teto e consumido ANTES do mint de proposito: uma conversa de voz e
    aberta, e sem isso um visitante segura a linha e gasta o dia sozinho. Essa
    decisao fica.

    O que este teste prende e o outro caso: quando NOS falhamos em criar a
    sessao, ela nunca existiu, e cobrar por ela gasta uma das 40 diarias sem
    ninguem ter falado. Mesmo padrao ja usado em documents.py e chat.py.
    """
    fonte = inspect.getsource(rt.criar_sessao)
    pos_consumo = fonte.index('consumir("realtime"')
    pos_devolucao = fonte.find('devolver("realtime"')

    assert pos_devolucao > 0, "a rota consome a cota e nunca devolve"
    assert pos_devolucao > pos_consumo, "a devolucao precisa vir depois do consumo"
    assert "httpx.HTTPError" in fonte[pos_consumo:pos_devolucao], (
        "a devolucao tem de estar no caminho de falha do mint, nao no caminho feliz"
    )


def test_a_queda_da_conexao_e_observada():
    """Uma falha de ICE mata o data channel.

    Com isso o handler de `error` do canal nunca dispara, e a tela fica em
    "Listening" com a bolinha verde pulsando para sempre. O estado do
    RTCPeerConnection e o unico lugar onde a queda e observavel.
    """
    sessao = (FRONTEND / "lib" / "realtime-session.ts").read_text()
    assert "onconnectionstatechange" in sessao, "a queda de conexao voltou a ser silenciosa"
    assert "'failed'" in sessao and "'disconnected'" in sessao


def test_a_validade_da_credencial_nao_e_descartada():
    """O backend calcula `expires_at` e devolve; o front ignorava.

    A credencial so autentica o POST de SDP inicial, entao expirar depois nao
    derruba a chamada. Mas se a pessoa demora a liberar o microfone ela expira
    ANTES, e o erro exibido era um generico "Could not open the voice
    connection" — que manda investigar a coisa errada.
    """
    sessao = (FRONTEND / "lib" / "realtime-session.ts").read_text()
    assert "expires_at" in sessao, "o front voltou a descartar a validade da credencial"

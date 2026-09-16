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

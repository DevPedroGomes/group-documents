"""Testes da trilha de decisao (migration 003).

O que se prende aqui:
- a trilha guarda METADADO, nunca o texto do trecho (a tabela nao pode virar
  uma segunda copia do acervo, e o texto ja vive em `chunks`);
- gravar a trilha nunca pode derrubar a resposta que o visitante ja recebeu;
- a trilha e gravada tambem quando a geracao FALHA, que e justamente o caso em
  que alguem vai querer saber ate onde o pipeline chegou;
- o payload explica a ESCALA do score. Mandar 0,03 sem dizer que e RRF foi o
  bug que fez toda resposta sair com aviso de baixa confianca.
"""

import ast
import inspect
from pathlib import Path

from app.api.routes import chat as chat_route


BACKEND = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Metadado, nao conteudo
# ---------------------------------------------------------------------------

def test_resumo_de_trechos_nao_carrega_o_texto():
    doc = {
        "id": "c1",
        "document_id": "d1",
        "document_title": "Contrato",
        "page": 3,
        "relevance_score": 0.4213,
        "score_scale": "cohere",
        "snippet": "clausula 4.2: o prazo de entrega e de 30 dias",
        "content": "texto inteiro do chunk que nao deve ser copiado",
    }
    (resumo,) = chat_route._resumo_trechos([doc])

    assert resumo["document_id"] == "d1"
    assert resumo["page"] == 3
    assert resumo["score_scale"] == "cohere"
    assert "snippet" not in resumo
    assert "content" not in resumo
    assert "clausula" not in str(resumo)


def test_resumo_assume_rrf_quando_a_escala_nao_veio():
    # Sem rerank o documento nao carrega `score_scale`, e a escala real e RRF.
    (resumo,) = chat_route._resumo_trechos([{"document_id": "d1", "relevance_score": 0.02}])
    assert resumo["score_scale"] == "rrf"


# ---------------------------------------------------------------------------
# 2. A trilha nunca derruba a resposta
# ---------------------------------------------------------------------------

def test_falha_ao_gravar_a_trilha_nao_propaga(monkeypatch):
    class EngineQuebrado:
        def begin(self):
            raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(chat_route, "engine", EngineQuebrado())

    # Nao levanta: o visitante ja recebeu o texto, perder a trilha e menos grave.
    chat_route.save_decision(
        user_id="00000000-0000-0000-0000-000000000001",
        thread_id="00000000-0000-0000-0000-000000000002",
        message_id=None,
        question="pergunta",
        retrieved=[],
        graded=[],
        web_used=False,
        low_confidence=False,
        answered=True,
        latency_ms=10,
    )


# ---------------------------------------------------------------------------
# 3. Gravada tambem quando a geracao falha
# ---------------------------------------------------------------------------

def _corpo_do_generate_sse() -> ast.AsyncFunctionDef:
    fonte = inspect.getsource(chat_route.chat)
    arvore = ast.parse(inspect.cleandoc(fonte))
    for no in ast.walk(arvore):
        if isinstance(no, ast.AsyncFunctionDef) and no.name == "generate_sse":
            return no
    raise AssertionError("generate_sse nao encontrado")


def test_trilha_e_gravada_no_finally_e_nao_no_caminho_feliz():
    gen = _corpo_do_generate_sse()
    tries = [n for n in ast.walk(gen) if isinstance(n, ast.Try) and n.finalbody]
    assert tries, "generate_sse precisa de um finally"

    chamadas_no_finally = {
        n.func.id
        for t in tries
        for corpo in t.finalbody
        for n in ast.walk(corpo)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    # Se sair do finally, resposta que quebrou no meio deixa de registrar
    # ate onde chegou, que e o caso mais util da trilha.
    assert "save_decision" in chamadas_no_finally


def test_answered_reflete_se_houve_texto():
    fonte = inspect.getsource(chat_route.chat)
    assert "answered=bool(full_answer)" in fonte.replace(" ", "").replace("\n", "")


# ---------------------------------------------------------------------------
# 4. O payload explica a escala do score
# ---------------------------------------------------------------------------

def _linha(escala: str) -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000003",
        "thread_id": None,
        "message_id": None,
        "question": "q",
        "retrieved": [],
        "graded": [],
        "considered": 3,
        "kept": 1,
        "score_scale": escala,
        "reranked": escala == "cohere",
        "low_confidence": False,
        "web_used": False,
        "answered": True,
        "latency_ms": 1200,
        "created_at": None,
    }


def test_payload_explica_a_escala_do_score():
    rrf = chat_route._decision_payload(_linha("rrf"))
    cohere = chat_route._decision_payload(_linha("cohere"))

    assert "RRF" in rrf["score_scale_hint"]
    assert rrf["reranked"] is False
    assert "0 a 1" in cohere["score_scale_hint"]
    assert cohere["reranked"] is True
    # A dica precisa existir sempre: numero sem escala e o que enganava.
    assert chat_route._decision_payload(_linha("qualquer"))["score_scale_hint"]


# ---------------------------------------------------------------------------
# 5. A resposta gravada devolve o id, senao a trilha fica orfa
# ---------------------------------------------------------------------------

def test_save_message_devolve_o_id():
    fonte = inspect.getsource(chat_route.save_message)
    assert "RETURNING id" in fonte
    assert "-> str | None" in fonte


# ---------------------------------------------------------------------------
# 6. A migration existe e isola por usuario
# ---------------------------------------------------------------------------

def test_migration_003_isola_por_usuario_e_indexa_a_leitura():
    sql = (BACKEND / "migrations" / "003_decisions.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS decisions" in sql
    assert "user_id      UUID NOT NULL" in sql
    assert "idx_decisions_user_created" in sql
    assert "idx_decisions_message" in sql


def test_leitura_da_trilha_filtra_por_usuario():
    # Filtrar so por message_id deixaria qualquer um ler a trilha de outro.
    for fn in (chat_route.get_decision, chat_route.list_decisions):
        fonte = inspect.getsource(fn)
        assert "user_id = CAST(:user_id AS uuid)" in fonte


# ---------------------------------------------------------------------------
# 7. Divergencia entre fontes: portao deterministico, aviso nunca decisao
# ---------------------------------------------------------------------------

from app.core.rag import conflict as conflict_mod


def _trecho(doc_id: str, titulo: str, texto: str) -> dict:
    return {"document_id": doc_id, "document_title": titulo, "page": 1, "snippet": texto}


def test_nao_chama_o_modelo_quando_ha_um_documento_so(monkeypatch):
    # Um documento nao diverge de si mesmo no escopo de uma resposta, e rodar a
    # checagem ali seria pagar por nada.
    def explode(**kwargs):
        raise AssertionError("nao devia chamar o modelo")

    monkeypatch.setattr(conflict_mod, "chat_complete", explode)
    trechos = [_trecho("d1", "Contrato", "prazo de 30 dias"),
               _trecho("d1", "Contrato", "entrega em Salvador")]
    assert conflict_mod.detectar_conflito(trechos) is None


def test_avisa_quando_o_modelo_aponta_divergencia(monkeypatch):
    monkeypatch.setattr(
        conflict_mod, "chat_complete",
        lambda **kw: '{"conflict": true, "summary": "O prazo difere entre os documentos.", "sources": ["Contrato", "Aditivo"]}',
    )
    aviso = conflict_mod.detectar_conflito([
        _trecho("d1", "Contrato", "prazo de 30 dias"),
        _trecho("d2", "Aditivo", "prazo de 15 dias uteis"),
    ])
    assert aviso["summary"].startswith("O prazo difere")
    assert aviso["sources"] == ["Contrato", "Aditivo"]


def test_falha_do_modelo_nao_derruba_a_resposta(monkeypatch):
    def explode(**kw):
        raise RuntimeError("provider fora do ar")

    monkeypatch.setattr(conflict_mod, "chat_complete", explode)
    assert conflict_mod.detectar_conflito([
        _trecho("d1", "A", "x"), _trecho("d2", "B", "y"),
    ]) is None


def test_conflito_sem_explicacao_nao_vira_aviso(monkeypatch):
    # Alarme sem conteudo treina o usuario a ignorar o alarme.
    monkeypatch.setattr(conflict_mod, "chat_complete",
                        lambda **kw: '{"conflict": true, "summary": "  ", "sources": []}')
    assert conflict_mod.detectar_conflito([
        _trecho("d1", "A", "x"), _trecho("d2", "B", "y"),
    ]) is None


def test_json_embrulhado_em_cerca_e_lido(monkeypatch):
    monkeypatch.setattr(
        conflict_mod, "chat_complete",
        lambda **kw: '```json\n{"conflict": true, "summary": "diverge", "sources": ["A"]}\n```',
    )
    aviso = conflict_mod.detectar_conflito([
        _trecho("d1", "A", "x"), _trecho("d2", "B", "y"),
    ])
    assert aviso and aviso["summary"] == "diverge"


def test_deteccao_pode_ser_desligada_por_configuracao(monkeypatch):
    class Fake:
        enable_conflict_detection = False
        fast_model = "x"

    monkeypatch.setattr(conflict_mod, "get_settings", lambda: Fake())
    monkeypatch.setattr(conflict_mod, "chat_complete",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("nao devia chamar")))
    assert conflict_mod.detectar_conflito([
        _trecho("d1", "A", "x"), _trecho("d2", "B", "y"),
    ]) is None


def test_o_aviso_nao_filtra_nem_reordena_as_fontes():
    # O invariante do projeto: o modelo redige, nao decide. A divergencia
    # aparece ao lado da resposta; nenhuma fonte e descartada por causa dela.
    fonte = inspect.getsource(chat_route.chat)
    trecho = fonte[fonte.find("detectar_conflito"):fonte.find("# Send sources")]
    assert "filtered_docs =" not in trecho
    assert "conflito = await" in trecho


# ---------------------------------------------------------------------------
# 8. Corte temporal: responder com o acervo como ele estava numa data
# ---------------------------------------------------------------------------

import pytest
from pydantic import ValidationError

from app.services import vector_store


def test_as_of_invalido_e_recusado_antes_de_chegar_no_banco():
    # String livre chegava no CAST do Postgres e virava 500 no meio do stream.
    with pytest.raises(ValidationError):
        chat_route.ChatBody(message="oi", as_of="mes passado")


@pytest.mark.parametrize("valor", ["2026-01-31", "2026-01-31T23:59:59Z", "2026-01-31T23:59:59+00:00"])
def test_as_of_aceita_formatos_iso(valor):
    assert chat_route.ChatBody(message="oi", as_of=valor).as_of


def test_as_of_vazio_vira_nulo():
    assert chat_route.ChatBody(message="oi", as_of="").as_of is None


def test_corte_temporal_vale_nas_duas_pernas_da_busca():
    # A busca e hibrida: filtrar so a perna semantica deixaria o recorte vazar
    # pela perna de palavra-chave, e o resultado misturaria as duas epocas.
    fonte = inspect.getsource(vector_store.hybrid_search)
    assert fonte.count("{data_filter}") == 2
    assert "d.created_at <= CAST(:as_of AS timestamptz)" in fonte


def test_corte_filtra_pelo_documento_e_nao_pelo_chunk():
    # Reprocessar um documento nao pode faze-lo aparecer num recorte anterior
    # a existencia dele.
    fonte = inspect.getsource(vector_store.hybrid_search)
    assert "c.created_at <= CAST(:as_of" not in fonte

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

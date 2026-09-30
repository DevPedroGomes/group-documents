"""O pipeline do chat de ponta a ponta, pela rota /chat, com dubles (tests/dubles_chat.py).

O que se prende aqui e o COMPORTAMENTO que a auditoria de 2026-09-30 achou
quebrado: a pergunta de seguimento buscava com o fragmento sozinho, o "CRAG"
nunca reconsultava o acervo, a web entrava como documento e a pergunta privada
ia para o Tavily, a divergencia rodava em serie antes do primeiro token, e o
`done` nao dizia qual mensagem tinha sido gravada.
"""

import pytest
from fastapi.testclient import TestClient

from tests.dubles_chat import Cenario, do_tipo, eventos, instalar, trecho


@pytest.fixture
def chat(monkeypatch):
    from app.api.rate_limit import limiter
    from app.main import create_app

    cenario = Cenario()
    instalar(monkeypatch, cenario)
    limiter.enabled = False
    try:
        yield TestClient(create_app()), cenario
    finally:
        limiter.enabled = True


def perguntar(cliente, mensagem: str, **corpo) -> list[dict]:
    r = cliente.post("/chat", json={"message": mensagem, **corpo},
                     headers={"Authorization": "Bearer x"})
    assert r.status_code == 200, r.text
    return eventos(r.text)


# ---------------------------------------------------------------------------
# Pergunta de seguimento: condensada para a busca, original para o gerador
# ---------------------------------------------------------------------------

HISTORICO = [
    {"role": "user", "content": "qual o prazo de entrega em 2024?"},
    {"role": "assistant", "content": "Em 2024 o prazo era de 30 dias (Politica 2024, p. 2)."},
]


def test_seguimento_busca_com_a_pergunta_autocontida(chat):
    cliente, cenario = chat
    cenario.historico = list(HISTORICO)
    autocontida = "qual o prazo de entrega em marco de 2025?"
    cenario.resposta_multi_query = f"{autocontida}\nprazo de entrega 2025\nentrega marco 2025"
    cenario.acervo[autocontida] = [trecho("c1", "d1", "Politica 2025"), trecho("c2", "d2", "Politica 2024")]

    evs = perguntar(cliente, "e em marco de 2025?")

    # O fragmento sozinho nao vai para a busca: a autocontida o substitui.
    assert [b["query_text"] for b in cenario.buscas] == [
        autocontida, "prazo de entrega 2025", "entrega marco 2025",
    ]
    assert cenario.embeddings == [[autocontida, "prazo de entrega 2025", "entrega marco 2025"]]
    # A condensacao viu a conversa.
    assert "qual o prazo de entrega em 2024?" in cenario.prompts_multi_query[0]
    # O gerador recebe a pergunta ORIGINAL e o historico.
    (geracao,) = cenario.geracoes
    assert geracao["messages"][-1]["content"].endswith("Question: e em marco de 2025?")
    assert [m["content"] for m in geracao["messages"][:-1]] == [m["content"] for m in HISTORICO]
    # A trilha grava as consultas usadas.
    (decisao,) = cenario.decisoes
    assert decisao["queries"] == [autocontida, "prazo de entrega 2025", "entrega marco 2025"]
    assert decisao["question"] == "e em marco de 2025?"
    assert do_tipo(evs, "done")


def test_sem_historico_busca_com_a_pergunta_e_as_variantes(chat):
    cliente, cenario = chat
    cenario.acervo["qual o prazo?"] = [trecho("c1", "d1", "Politica"), trecho("c2", "d1", "Politica")]

    perguntar(cliente, "qual o prazo?")

    assert [b["query_text"] for b in cenario.buscas] == ["qual o prazo?", "variante um", "variante dois"]
    assert "<conversation>" not in cenario.prompts_multi_query[0]
    assert cenario.decisoes[0]["queries"] == ["qual o prazo?", "variante um", "variante dois"]


def test_falha_da_condensacao_busca_com_a_pergunta_original(chat):
    cliente, cenario = chat
    cenario.historico = list(HISTORICO)
    cenario.resposta_multi_query = RuntimeError("provider fora do ar")

    perguntar(cliente, "e em marco de 2025?")

    assert [b["query_text"] for b in cenario.buscas] == ["e em marco de 2025?"]
    assert cenario.decisoes[0]["queries"] == ["e em marco de 2025?"]

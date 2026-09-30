"""Dubles do pipeline do chat: tudo que sai do processo e trocado e anotado.

LLM (multi-query, rewrite, divergencia e geracao), embedding, busca hibrida,
Tavily e as gravacoes no banco. A rota /chat roda de verdade pelo TestClient e
o teste le o SSE. Sem rede, sem banco, sem chave.
"""

from __future__ import annotations

import json
import sys
import threading
from types import SimpleNamespace

USUARIO = "00000000-0000-0000-0000-00000000000a"
THREAD = "00000000-0000-0000-0000-0000000000b0"


def trecho(ident: str, doc: str, titulo: str, data: str | None = "2025-01-01",
           score: float = 0.03, escala: str = "rrf", texto: str | None = None,
           pagina: int | None = 1) -> dict:
    """Um trecho como `hybrid_search`/reranker devolvem."""
    return {
        "id": ident, "document_id": doc, "document_title": titulo, "page": pagina,
        "snippet": texto or f"texto de {ident}", "document_date": data,
        "relevance_score": score, "score_scale": escala,
    }


class Cenario:
    """O que cada duble devolve e o que cada um recebeu."""

    def __init__(self) -> None:
        self.historico: list[dict] = []
        # consulta exata -> trechos que a busca hibrida devolve para ela
        self.acervo: dict[str, list[dict]] = {}
        self.buscas: list[dict] = []
        self.embeddings: list[list[str]] = []
        self.resposta_multi_query: str | Exception = "variante um\nvariante dois"
        self.prompts_multi_query: list[str] = []
        self.reescrita = "pergunta reescrita"
        self.reescritas: list[str] = []
        self.resposta_conflito = '{"conflict": false, "summary": "", "sources": []}'
        self.prompts_conflito: list[str] = []
        self.antes_de_responder_conflito = None  # callable: segura a checagem
        self.depois_de_responder_conflito = None
        # str vira token; Exception e levantada; callable roda entre tokens
        self.tokens: list = ["Resposta", " final."]
        self.geracoes: list[dict] = []
        self.resultados_tavily: list[dict] = [
            {"title": "Site externo", "url": "https://exemplo.com/prazo",
             "content": "Na web o prazo e de 10 dias.", "score": 0.8},
        ]
        self.tavily: list[str] = []
        self.mensagens: list[dict] = []
        self.decisoes: list[dict] = []
        self.devolvidos: list[str] = []
        self.consumidos: list[str] = []
        self.teto_erro: Exception | None = None
        # Ordem global: tipo de cada evento SSE montado e cada gravacao.
        self.log: list[str] = []
        self._trava = threading.Lock()

    def anotar(self, item: str) -> None:
        with self._trava:
            self.log.append(item)


def instalar(monkeypatch, cenario: Cenario) -> None:
    """Troca os pontos de saida do pipeline pelos dubles do cenario."""
    from app.api.routes import chat as chat_route
    from app.config.settings import get_settings
    from app.core.rag import conflict, generator, retriever, transformer

    settings = get_settings()
    monkeypatch.setattr(settings, "cohere_api_key", None)
    monkeypatch.setattr(settings, "tavily_api_key", None)
    monkeypatch.setattr(settings, "enable_conflict_detection", True)
    monkeypatch.setattr(settings, "enable_input_guardrails", True)
    monkeypatch.setattr(settings, "multi_query_count", 2)
    monkeypatch.setattr(settings, "relevance_threshold", 0.7)
    if "enable_web_fallback" in type(settings).model_fields:
        monkeypatch.setattr(settings, "enable_web_fallback", False)

    async def usuario(_request):
        return USUARIO

    async def consumir(tipo, _limite):
        cenario.consumidos.append(tipo)
        if cenario.teto_erro:
            raise cenario.teto_erro

    async def devolver(tipo):
        cenario.devolvidos.append(tipo)

    def save_message(thread_id, role, content, citations=None):
        cenario.anotar(f"save_message:{role}")
        cenario.mensagens.append(
            {"thread_id": thread_id, "role": role, "content": content, "citations": citations}
        )
        return f"msg-{len(cenario.mensagens)}"

    def save_decision(**kw):
        cenario.anotar("save_decision")
        cenario.decisoes.append(kw)

    sse_original = chat_route._sse

    def sse(tipo, dados):
        cenario.anotar(f"sse:{tipo}")
        return sse_original(tipo, dados)

    monkeypatch.setattr(chat_route, "require_user", usuario)
    monkeypatch.setattr(chat_route.metering, "consumir", consumir)
    monkeypatch.setattr(chat_route.metering, "devolver", devolver)
    monkeypatch.setattr(chat_route, "create_thread", lambda _u: THREAD)
    monkeypatch.setattr(chat_route, "validate_thread_ownership", lambda _t, _u: True)
    monkeypatch.setattr(chat_route, "get_thread_history", lambda *_a, **_k: list(cenario.historico))
    monkeypatch.setattr(chat_route, "save_message", save_message)
    monkeypatch.setattr(chat_route, "save_decision", save_decision)
    monkeypatch.setattr(chat_route, "_sse", sse)

    def multi_query(**kw):
        cenario.prompts_multi_query.append(kw["messages"][-1]["content"])
        if isinstance(cenario.resposta_multi_query, Exception):
            raise cenario.resposta_multi_query
        return cenario.resposta_multi_query

    def embeddings(textos):
        cenario.embeddings.append(list(textos))
        return [[0.0] * 4 for _ in textos]

    def busca(**kw):
        cenario.anotar(f"busca:{kw['query_text']}")
        cenario.buscas.append(kw)
        return [dict(t) for t in cenario.acervo.get(kw["query_text"], [])]

    def reescrever(**kw):
        cenario.reescritas.append(kw["messages"][-1]["content"])
        return cenario.reescrita

    def checar_conflito(**kw):
        cenario.prompts_conflito.append(kw["messages"][-1]["content"])
        if cenario.antes_de_responder_conflito:
            cenario.antes_de_responder_conflito()
        resposta = cenario.resposta_conflito
        if cenario.depois_de_responder_conflito:
            cenario.depois_de_responder_conflito()
        return resposta

    def gerar(**kw):
        cenario.geracoes.append(kw)
        for item in cenario.tokens:
            if isinstance(item, Exception):
                raise item
            if callable(item):
                item()
                continue
            yield item

    monkeypatch.setattr(retriever, "chat_complete", multi_query)
    monkeypatch.setattr(retriever, "get_query_embeddings", embeddings)
    monkeypatch.setattr(retriever, "hybrid_search", busca)
    monkeypatch.setattr(transformer, "chat_complete", reescrever)
    monkeypatch.setattr(conflict, "chat_complete", checar_conflito)
    monkeypatch.setattr(generator, "chat_stream", gerar)

    class TavilyFalso:
        def __init__(self, api_key=None):
            self.api_key = api_key

        def search(self, query, max_results=5, **_kw):
            cenario.anotar("tavily")
            cenario.tavily.append(query)
            return {"results": [dict(r) for r in cenario.resultados_tavily]}

    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=TavilyFalso))


def eventos(corpo: str) -> list[dict]:
    """Os eventos SSE do corpo, na ordem."""
    return [json.loads(l[6:]) for l in corpo.splitlines() if l.startswith("data: ")]


def do_tipo(evs: list[dict], tipo: str) -> list:
    return [e["data"] for e in evs if e["type"] == tipo]

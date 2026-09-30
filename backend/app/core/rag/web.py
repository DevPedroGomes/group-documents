"""Busca web do fallback corretivo (Tavily).

Resultado web NAO e documento do acervo: volta marcado com `kind: "web"`,
`document_id` nulo e a `url`, para virar citacao propria, entrar no prompt num
bloco separado e rotulado como externo, e aparecer na trilha com a escala do
Tavily. Antes entrava como "documento" de pagina 0, sumia das citacoes e o
prompt mandava responder so dos documentos.

Quem decide SE a busca roda e a rota (desligada por padrao, nunca com `as_of`).
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.config.settings import get_settings

# O gerador corta de novo; aqui e so para nao carregar pagina inteira na memoria.
_MAX_CHARS = 1500


def e_web(trecho: dict) -> bool:
    """Resultado web, no formato atual (`kind`) ou no legado (`document_id: "web"`)."""
    return trecho.get("kind") == "web" or trecho.get("document_id") == "web"


def _url_navegavel(url: str) -> bool:
    """So http(s): a url vira link clicavel na tela, e `javascript:` seria XSS."""
    partes = urlparse(url)
    return partes.scheme in ("http", "https") and bool(partes.netloc)


def buscar_na_web(consulta: str, max_resultados: int = 3) -> list[dict]:
    """Resultados do Tavily no formato de trecho, marcados como web.

    Levanta se o Tavily falhar: a rota mostra "indisponivel" no workflow.
    """
    from tavily import TavilyClient

    settings = get_settings()
    resposta = TavilyClient(api_key=settings.tavily_api_key).search(
        consulta, max_results=max_resultados
    )

    itens = []
    for r in (resposta or {}).get("results", []):
        url = str(r.get("url") or "").strip()
        conteudo = str(r.get("content") or "").strip()
        if not conteudo or not _url_navegavel(url):
            continue
        itens.append({
            "id": url,
            "kind": "web",
            "document_id": None,
            "document_title": str(r.get("title") or url)[:300],
            "page": None,
            "snippet": conteudo[:_MAX_CHARS],
            "document_date": None,
            "url": url,
            "relevance_score": float(r.get("score") or 0),
            # Criterio proprio do Tavily: nem RRF nem Cohere.
            "score_scale": "tavily",
        })
    return itens

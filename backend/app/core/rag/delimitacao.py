"""Trechos delimitados no prompt: o conteudo vai como DADO, nunca como instrucao.

Documento do acervo e pagina da web sao escritos por terceiros. Colados soltos
no prompt, "ignore as instrucoes anteriores" dentro de um PDF vira instrucao
(injecao indireta). Cada trecho vai entre tags com os metadados em atributos, e
o system prompt de quem usa diz que o que esta dentro das tags e dado.

Nome de tag dentro do conteudo e removido: sem isso, um trecho que contivesse
"</document>" fecharia o bloco e o resto dele sairia como texto solto.
"""

from __future__ import annotations

import html
import re

_TAGS_RESERVADAS = re.compile(
    r"</?\s*(?:documents|document|external_web_results|web_result)\b[^>]*>",
    re.IGNORECASE,
)


def neutralizar(texto: str) -> str:
    """O texto sem nenhuma tag que abra ou feche um bloco delimitado."""
    return _TAGS_RESERVADAS.sub(" ", texto or "")


def _atributo(valor) -> str:
    return html.escape(str(valor), quote=True)


def bloco(tag: str, conteudo: str, **atributos) -> str:
    """`<tag a="..." b="...">conteudo</tag>`, sem atributo nulo."""
    attrs = "".join(
        f' {nome}="{_atributo(v)}"' for nome, v in atributos.items() if v not in (None, "")
    )
    return f"<{tag}{attrs}>\n{neutralizar(conteudo).strip()}\n</{tag}>"


def trecho_do_acervo(indice: int, trecho: dict, max_chars: int | None = None) -> str:
    """Um trecho do acervo com titulo, pagina e data do documento."""
    texto = trecho.get("snippet") or ""
    if max_chars is not None:
        texto = texto[:max_chars]
    return bloco(
        "document",
        texto,
        index=indice,
        title=trecho.get("document_title") or "document",
        page=trecho.get("page"),
        date=trecho.get("document_date"),
    )

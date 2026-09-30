"""Quais chamadas pagas ja aconteceram dentro de um trecho medido.

Serve para devolver cota so quando nada foi gasto: uma busca que falha depois de
o provider ja ter cobrado nao devolve, senao o teto diario passa a contar menos
do que o gasto real.

Um ContextVar, e nao um contador global, porque requisicoes concorrentes nao
podem ver o gasto uma da outra. `asyncio.to_thread` copia o contexto para a
thread, entao o que o provider anota la dentro chega a lista de quem mediu; o
`run_in_executor` do loop NAO copia, e com ele nada seria anotado.

Anotam hoje: `llm_client.chat_complete`, `embedding.embed_sequences` e o rerank
da Cohere, que sao as chamadas pagas da busca.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_atual: ContextVar[list[str] | None] = ContextVar("chamadas_pagas", default=None)


def registrar(provider: str) -> None:
    """Anota que uma chamada paga a `provider` voltou com sucesso. Sem medicao ativa, nada."""
    anotadas = _atual.get()
    if anotadas is not None:
        anotadas.append(provider)


@contextmanager
def medir() -> Iterator[list[str]]:
    """Abre uma medicao; a lista devolvida recebe cada chamada paga feita dentro dela."""
    anotadas: list[str] = []
    token = _atual.set(anotadas)
    try:
        yield anotadas
    finally:
        _atual.reset(token)

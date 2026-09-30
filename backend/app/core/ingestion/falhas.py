"""Falha de ingestao: permanente ou transitoria, e o que a pessoa pode ler.

O worker retenta ate 5 vezes, e cada tentativa paga o enriquecimento de novo.
Retentar o que nao se conserta sozinho (arquivo corrompido, formato, limite,
provider recusando o conteudo com 4xx) so queima dinheiro: isso vai direto para
a dead-letter. Rede, timeout e 408/429/5xx sao transitorios e retentam.

A mensagem gravada no documento e uma categoria curta em ingles, que a tela
mostra. O erro cru do provider (corpo da resposta, link de billing, nome do
plano) fica so no log.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

ARQUIVO_ILEGIVEL = "The file could not be read."
PROVIDER_RECUSOU = "A provider refused to process the document."
PROVIDER_INDISPONIVEL = "A provider was temporarily unavailable. Try again later."
DESCONHECIDA = "The document could not be processed."


class FalhaPermanente(Exception):
    """Levantada por nos quando retentar nao muda nada. A mensagem vai para a tela."""

    def __init__(self, mensagem: str):
        super().__init__(mensagem)
        self.mensagem = mensagem


@dataclass(frozen=True)
class Falha:
    permanente: bool
    mensagem: str


def _status_http(exc: BaseException) -> int | None:
    """O status HTTP que o provider devolveu, quando a excecao carrega um.

    Anthropic e OpenAI expoem `status_code`, o Voyage `http_status` e o httpx
    `response.status_code`.
    """
    for valor in (
        getattr(exc, "status_code", None),
        getattr(exc, "http_status", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(valor, int):
            return valor
    return None


@lru_cache(maxsize=1)
def _transitorias() -> tuple[type[BaseException], ...]:
    import httpx
    from sqlalchemy.exc import InterfaceError, OperationalError

    tipos: list[type[BaseException]] = [
        TimeoutError, ConnectionError,
        httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError,
        # Banco fora do ar no meio do job: volta sozinho.
        OperationalError, InterfaceError,
    ]
    try:
        import anthropic

        tipos.append(anthropic.APIConnectionError)
    except ImportError:
        pass
    try:
        import openai

        tipos.append(openai.APIConnectionError)
    except ImportError:
        pass
    try:
        from voyageai import error as voyage

        tipos += [voyage.Timeout, voyage.APIConnectionError, voyage.TryAgain,
                  voyage.RateLimitError, voyage.ServiceUnavailableError,
                  voyage.ServerError, voyage.APIError]
    except ImportError:
        pass
    return tuple(tipos)


@lru_cache(maxsize=1)
def _recusadas() -> tuple[type[BaseException], ...]:
    """Recusa do provider que as vezes vem sem status: chave, pedido malformado."""
    try:
        from voyageai import error as voyage

        return (voyage.InvalidRequestError, voyage.AuthenticationError, voyage.MalformedRequestError)
    except ImportError:
        return ()


@lru_cache(maxsize=1)
def _ilegiveis() -> tuple[type[BaseException], ...]:
    from PIL import Image, UnidentifiedImageError
    from pypdf.errors import PyPdfError

    tipos: list[type[BaseException]] = [
        FileNotFoundError, UnicodeDecodeError, PyPdfError,
        UnidentifiedImageError, Image.DecompressionBombError,
    ]
    try:
        from voyageai import error as voyage

        tipos.append(voyage.VideoProcessingError)
    except ImportError:
        pass
    return tuple(tipos)


def classificar(exc: BaseException) -> Falha:
    """Permanente ou transitoria, com a mensagem que a pessoa ve."""
    if isinstance(exc, FalhaPermanente):
        return Falha(True, exc.mensagem)

    status = _status_http(exc)
    if status is not None:
        if status in (408, 429) or status >= 500:
            return Falha(False, PROVIDER_INDISPONIVEL)
        if 400 <= status < 500:
            return Falha(True, PROVIDER_RECUSOU)

    if isinstance(exc, _transitorias()):
        return Falha(False, PROVIDER_INDISPONIVEL)
    if isinstance(exc, _recusadas()):
        return Falha(True, PROVIDER_RECUSOU)
    if isinstance(exc, _ilegiveis()):
        return Falha(True, ARQUIVO_ILEGIVEL)

    # Desconhecida continua retentando, como antes: tratar como permanente
    # perderia o documento num soluco que ninguem previu aqui.
    return Falha(False, DESCONHECIDA)

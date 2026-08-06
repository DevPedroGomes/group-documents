"""Unified LLM client supporting Anthropic native SDK + OpenRouter (OpenAI-compat).

The 6 call sites in this codebase historically used
`anthropic.Anthropic().messages.create(...)` directly. To allow flipping
provider via env (`LLM_PROVIDER=openrouter`) without touching call sites,
they all now go through `chat_complete` / `chat_stream` here. The Anthropic
message shape (system, messages, max_tokens) is preserved as the public
interface; the OpenRouter path adapts internally.
"""
from __future__ import annotations

import logging
import re
from typing import Generator, Optional

from app.config.settings import get_settings


logger = logging.getLogger(__name__)


def _provider() -> str:
    return (get_settings().llm_provider or "anthropic").lower()


def _is_openrouter() -> bool:
    s = get_settings()
    return _provider() == "openrouter" and bool(s.openrouter_api_key)


def chat_complete(
    *,
    model: str,
    max_tokens: int,
    messages: list[dict],
    system: Optional[str] = None,
    temperature: float = 0.0,
) -> str:
    """Non-streaming completion. Returns the assistant text content.

    `messages` should be in [{role, content}] form (works for both providers).
    """
    if _is_openrouter():
        return _openrouter_complete(model, max_tokens, messages, system, temperature)
    return _anthropic_complete(model, max_tokens, messages, system, temperature)


def chat_stream(
    *,
    model: str,
    max_tokens: int,
    messages: list[dict],
    system: Optional[str] = None,
    temperature: float = 0.0,
) -> Generator[str, None, None]:
    """Streaming completion. Yields text deltas."""
    if _is_openrouter():
        yield from _openrouter_stream(model, max_tokens, messages, system, temperature)
        return
    yield from _anthropic_stream(model, max_tokens, messages, system, temperature)


# ─────────────────────────────────────────────────────────────────────────
# Anthropic native
# ─────────────────────────────────────────────────────────────────────────

def _anthropic_client():
    import anthropic
    s = get_settings()
    if not s.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY required for llm_provider=anthropic. "
            "Set it or switch to LLM_PROVIDER=openrouter."
        )
    return anthropic.Anthropic(api_key=s.anthropic_api_key)


# Modelos que RECUSAM parametro de sampling: mandar temperature/top_p/top_k
# devolve 400. Sonnet 4.6, Opus 4.6, Sonnet 4.5 e Haiku 4.5 ainda aceitam.
_RECUSA_SAMPLING = re.compile(
    r"^(anthropic/)?claude-(opus-5|opus-4-[78]|sonnet-5|fable-5|mythos-5)"
)

# Modelos onde o parametro `thinking` e valido. Haiku 4.5 nao aceita nenhuma
# forma dele — mandar qualquer valor devolve 400.
_ACEITA_THINKING = re.compile(
    r"^(anthropic/)?claude-(opus-5|opus-4-[678]|sonnet-5|sonnet-4-6|fable-5|mythos-5)"
)


def _kwargs_anthropic(model, max_tokens, messages, system, temperature) -> dict:
    """Monta o payload respeitando o que CADA modelo aceita.

    Este caminho e o de rollback (producao roda no OpenRouter), e ele nao
    sobreviveria a um modelo da familia 5 do jeito que estava:

    1. `temperature` — na familia 5 qualquer valor de sampling e 400. O jeito
       de guiar o comportamento passou a ser o prompt.
    2. `thinking` — na familia 5 pensar e o PADRAO, e `max_tokens` limita
       pensamento + resposta JUNTOS. Os call sites daqui tem teto apertado
       (150 a 2000) e o de geracao e STREAMING: com pensamento ligado o
       visitante veria uma pausa longa antes da primeira palavra e a resposta
       poderia truncar no meio. Este app faz RAG aterrado, nao raciocinio
       longo — desligar mantem latencia e custo onde estavam.
    """
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system
    if not _RECUSA_SAMPLING.match(model):
        kwargs["temperature"] = temperature
    if _ACEITA_THINKING.match(model):
        kwargs["thinking"] = {"type": "disabled"}
    return kwargs


def _primeiro_texto(content) -> str:
    """Primeiro bloco de TEXTO da resposta, nao o primeiro bloco.

    `content[0].text` quebra assim que o modelo pensa: o bloco de thinking vem
    antes do texto e nao tem `.text`. E um AttributeError que so aparece em
    producao, depois de alguem trocar o modelo.
    """
    for bloco in content:
        if bloco.type == "text":
            return bloco.text
    return ""


def _anthropic_complete(model, max_tokens, messages, system, temperature):
    client = _anthropic_client()
    resp = client.messages.create(
        **_kwargs_anthropic(model, max_tokens, messages, system, temperature)
    )
    return _primeiro_texto(resp.content)


def _anthropic_stream(model, max_tokens, messages, system, temperature):
    client = _anthropic_client()
    kwargs = _kwargs_anthropic(model, max_tokens, messages, system, temperature)
    with client.messages.stream(**kwargs) as stream:
        for text in stream.text_stream:
            yield text


# ─────────────────────────────────────────────────────────────────────────
# OpenRouter (OpenAI-compatible)
# ─────────────────────────────────────────────────────────────────────────

def _openrouter_client():
    from openai import OpenAI
    s = get_settings()
    if not s.openrouter_api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY required for llm_provider=openrouter."
        )
    return OpenAI(
        api_key=s.openrouter_api_key,
        base_url=s.openrouter_base_url,
        default_headers={
            "HTTP-Referer": "https://group-documents.pgdev.com.br",
            "X-Title": "qa-pgdev group-documents",
        },
    )


def _bloco_para_openai(bloco: dict) -> dict:
    """Traduz um bloco de conteudo do formato Anthropic para o OpenAI-compat.

    Os call sites deste modulo escrevem no formato Anthropic — e a interface
    publica declarada no topo do arquivo. O caminho do OpenRouter fala
    OpenAI-compat, onde imagem NAO e `{"type":"image","source":{...}}` e sim
    `{"type":"image_url","image_url":{"url":"data:<mime>;base64,<...>"}}`.

    Sem esta traducao o bloco de imagem seguia num formato que o outro lado nao
    reconhece e era simplesmente DESCARTADO: o modelo recebia so o texto
    "descreva esta imagem", sem imagem nenhuma, e respondia descrevendo uma
    imagem inventada. Nao dava erro em lugar nenhum — a descricao chegava
    plausivel e completamente errada, e ia direto para o indice.
    """
    if bloco.get("type") != "image":
        return bloco

    fonte = bloco.get("source") or {}
    if fonte.get("type") == "base64":
        mime = fonte.get("media_type", "image/png")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{fonte.get('data', '')}"},
        }
    if fonte.get("type") == "url":
        return {"type": "image_url", "image_url": {"url": fonte.get("url", "")}}
    return bloco


def _to_openai_messages(messages: list[dict], system: Optional[str]):
    out = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages:
        conteudo = msg.get("content")
        if isinstance(conteudo, list):
            msg = {**msg, "content": [_bloco_para_openai(b) if isinstance(b, dict) else b
                                      for b in conteudo]}
        out.append(msg)
    return out


def _openrouter_complete(model, max_tokens, messages, system, temperature):
    client = _openrouter_client()
    resp = client.chat.completions.create(
        model=model,
        messages=_to_openai_messages(messages, system),
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def _openrouter_stream(model, max_tokens, messages, system, temperature):
    client = _openrouter_client()
    stream = client.chat.completions.create(
        model=model,
        messages=_to_openai_messages(messages, system),
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta

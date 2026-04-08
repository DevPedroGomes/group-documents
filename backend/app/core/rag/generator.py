"""Answer generation using Claude Sonnet with streaming support."""

import logging
from typing import Generator, Optional

import anthropic

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def _format_context(documents: list[dict]) -> str:
    """Format retrieved documents as context for the LLM."""
    if not documents:
        return "No relevant documents found."

    parts = []
    for i, doc in enumerate(documents, 1):
        parts.append(
            f"[Source {i}] {doc['document_title']} (page {doc['page']}):\n"
            f"{doc['snippet']}"
        )
    return "\n\n".join(parts)


def generate_answer(
    question: str,
    documents: list[dict],
    history: Optional[list[dict]] = None,
) -> str:
    """Generate a complete answer (non-streaming)."""
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    context = _format_context(documents)

    system_prompt = (
        "You are a Team Hub Assistant that answers questions about shared documents. "
        "Answer EXCLUSIVELY based on the provided document context. "
        "ALWAYS cite the source (document name and page number) in your answers. "
        "If no relevant information is found, say so clearly. "
        "Respond in the same language as the user's question."
    )

    messages = []
    if history:
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            messages.append({"role": role, "content": msg["content"]})

    messages.append({
        "role": "user",
        "content": f"Context from documents:\n\n{context}\n\nQuestion: {question}",
    })

    response = client.messages.create(
        model=settings.generation_model,
        max_tokens=2000,
        system=system_prompt,
        messages=messages,
    )

    return response.content[0].text


def stream_answer(
    question: str,
    documents: list[dict],
    history: Optional[list[dict]] = None,
) -> Generator[str, None, None]:
    """Stream answer tokens using Claude Sonnet."""
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    context = _format_context(documents)

    system_prompt = (
        "You are a Team Hub Assistant that answers questions about shared documents. "
        "Answer EXCLUSIVELY based on the provided document context. "
        "ALWAYS cite the source (document name and page number) in your answers. "
        "If no relevant information is found, say so clearly. "
        "Respond in the same language as the user's question."
    )

    messages = []
    if history:
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            messages.append({"role": role, "content": msg["content"]})

    messages.append({
        "role": "user",
        "content": f"Context from documents:\n\n{context}\n\nQuestion: {question}",
    })

    with client.messages.stream(
        model=settings.generation_model,
        max_tokens=2000,
        system=system_prompt,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text

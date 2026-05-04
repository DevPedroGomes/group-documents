"""Answer generation through the configured LLM provider, with streaming support."""

import logging
from typing import Generator, Optional

from app.config.settings import get_settings
from app.core.llm_client import chat_complete, chat_stream

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are a BrainHub Assistant that answers questions about shared documents. "
    "Answer EXCLUSIVELY based on the provided document context. "
    "ALWAYS cite the source (document name and page number) in your answers. "
    "If no relevant information is found, say so clearly. "
    "Respond in the same language as the user's question."
)


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


def _build_messages(
    question: str,
    documents: list[dict],
    history: Optional[list[dict]],
) -> list[dict]:
    context = _format_context(documents)
    messages: list[dict] = []
    if history:
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            messages.append({"role": role, "content": msg["content"]})
    messages.append({
        "role": "user",
        "content": f"Context from documents:\n\n{context}\n\nQuestion: {question}",
    })
    return messages


def generate_answer(
    question: str,
    documents: list[dict],
    history: Optional[list[dict]] = None,
) -> str:
    """Generate a complete answer (non-streaming)."""
    settings = get_settings()
    return chat_complete(
        model=settings.generation_model,
        max_tokens=2000,
        system=_SYSTEM_PROMPT,
        messages=_build_messages(question, documents, history),
    )


def stream_answer(
    question: str,
    documents: list[dict],
    history: Optional[list[dict]] = None,
) -> Generator[str, None, None]:
    """Stream answer tokens through the configured LLM provider."""
    settings = get_settings()
    yield from chat_stream(
        model=settings.generation_model,
        max_tokens=2000,
        system=_SYSTEM_PROMPT,
        messages=_build_messages(question, documents, history),
    )

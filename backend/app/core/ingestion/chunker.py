"""
Semantic chunking with contextual enrichment.

Chunking: tiktoken-based recursive splitting with overlap.
Enrichment: Claude Haiku generates context per chunk (Anthropic's contextual retrieval technique).
"""

import logging
from typing import Optional

import tiktoken

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_encoder = tiktoken.encoding_for_model("gpt-4o")


def _token_count(text: str) -> int:
    return len(_encoder.encode(text))


def chunk_text(text: str, max_tokens: int = 500, overlap: int = 100) -> list[str]:
    """
    Split text into chunks of max_tokens with overlap.
    Uses sentence boundaries for semantic coherence.
    """
    import re

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current_chunk: list[str] = []
    current_tokens = 0

    for sent in sentences:
        sent_tokens = _token_count(sent)

        if current_tokens + sent_tokens > max_tokens and current_chunk:
            chunks.append(" ".join(current_chunk))

            # Keep overlap: walk backwards to find sentences that fit in overlap
            overlap_chunk: list[str] = []
            overlap_tokens = 0
            for s in reversed(current_chunk):
                s_tokens = _token_count(s)
                if overlap_tokens + s_tokens > overlap:
                    break
                overlap_chunk.insert(0, s)
                overlap_tokens += s_tokens

            current_chunk = overlap_chunk + [sent]
            current_tokens = overlap_tokens + sent_tokens
        else:
            current_chunk.append(sent)
            current_tokens += sent_tokens

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


def chunk_document_pages(
    pages: list[str],
) -> list[tuple[str, dict]]:
    """
    Chunk a document's pages into (text, metadata) tuples.
    Metadata includes page number and chunk index.
    """
    settings = get_settings()
    all_chunks = []
    global_idx = 0

    for page_num, page_text in enumerate(pages, start=1):
        if not page_text.strip():
            continue

        page_chunks = chunk_text(
            page_text,
            max_tokens=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )

        for chunk in page_chunks:
            metadata = {
                "page": page_num,
                "chunk_index": global_idx,
            }
            all_chunks.append((chunk, metadata))
            global_idx += 1

    return all_chunks


def enrich_chunks_with_context(
    chunks: list[tuple[str, dict]],
    full_document_text: str,
    document_title: str,
) -> list[tuple[str, dict]]:
    """
    Anthropic's Contextual Retrieval technique:
    Use Claude Haiku to generate 50-100 tokens of context per chunk,
    prepended before embedding.

    This improves retrieval quality by 35-67%.
    """
    import anthropic

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # Truncate document text if too long for context window (keep first 50k chars)
    doc_context = full_document_text[:50000]

    enriched = []
    for chunk_text_str, meta in chunks:
        try:
            response = client.messages.create(
                model=settings.fast_model,
                max_tokens=150,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"<document title=\"{document_title}\">\n{doc_context}\n</document>\n\n"
                            f"Here is a chunk from this document:\n<chunk>\n{chunk_text_str}\n</chunk>\n\n"
                            "Give a short succinct context (2-3 sentences) to situate this chunk "
                            "within the overall document. Answer only with the context, no preamble."
                        ),
                    }
                ],
            )
            context = response.content[0].text.strip()
            enriched_text = f"{context}\n\n{chunk_text_str}"
        except Exception as e:
            logger.warning(f"Contextual enrichment failed for chunk {meta['chunk_index']}: {e}")
            enriched_text = chunk_text_str

        enriched.append((enriched_text, meta))

    return enriched

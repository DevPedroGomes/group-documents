"""Score-based document grading — no LLM calls, uses reranker scores."""

import logging

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def grade_documents(
    documents: list[dict],
) -> tuple[list[dict], bool]:
    """
    Filter documents by relevance score threshold.

    Returns:
        (filtered_docs, needs_web_search)
        needs_web_search is True if >50% of docs were filtered out.
    """
    if not documents:
        return [], True

    settings = get_settings()
    threshold = settings.relevance_threshold

    filtered = [doc for doc in documents if doc.get("relevance_score", 0) >= threshold]

    needs_web_search = len(filtered) < len(documents) / 2

    # Safety net: always keep top 2 if all filtered
    if not filtered and documents:
        filtered = sorted(
            documents,
            key=lambda d: d.get("relevance_score", 0),
            reverse=True,
        )[:2]
        needs_web_search = True

    return filtered, needs_web_search

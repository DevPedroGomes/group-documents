"""Query transformation using a fast LLM for better retrieval."""

import logging

from app.config.settings import get_settings
from app.core.llm_client import chat_complete

logger = logging.getLogger(__name__)


def transform_query(question: str) -> str:
    """
    Rewrite a query for better retrieval.
    Used when initial retrieval doesn't find relevant documents.
    """
    settings = get_settings()
    try:
        text = chat_complete(
            model=settings.fast_model,
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Generate a search-optimized version of this question by analyzing "
                        "its core semantic meaning and intent.\n\n"
                        f"Original question: {question}\n\n"
                        "Instructions:\n"
                        "- Focus on the key concepts and entities\n"
                        "- Expand abbreviations if any\n"
                        "- Make it more specific for document retrieval\n"
                        "- Keep it as a question\n\n"
                        "Return only the improved question with no additional text."
                    ),
                }
            ],
        )
        return text.strip()
    except Exception as e:
        logger.error(f"Query transformation failed: {e}")
        return question

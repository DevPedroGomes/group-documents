"""Query transformation using a fast LLM for better retrieval."""

import logging

from app.config.settings import get_settings
from app.core.llm_client import chat_complete

logger = logging.getLogger(__name__)


def transform_query(question: str) -> str:
    """
    Rewrite a query for better retrieval.

    Passo corretivo: com baixa confianca, a reescrita reconsulta o ACERVO (ver
    `retriever.reconsultar`); a web so vem depois, se ligada. Mantem o idioma
    da pergunta, senao a perna de palavra-chave deixa de casar o documento.
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
                        "- Keep it as a question, in the same language as the original\n\n"
                        "Return only the improved question with no additional text."
                    ),
                }
            ],
        )
        # Resposta vazia reconsultaria o acervo com nada.
        return text.strip() or question
    except Exception as e:
        logger.error(f"Query transformation failed: {e}")
        return question

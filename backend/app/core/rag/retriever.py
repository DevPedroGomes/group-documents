"""
Two-stage retriever: Multi-query → Hybrid Search → Rerank.
Entry point for all document retrieval.
"""

import logging
from typing import Optional

from app.config.settings import get_settings
from app.core.llm_client import chat_complete
from app.services.embedding_cache import get_query_embedding
from app.services.vector_store import hybrid_search
from app.core.rag.reranker import rerank_documents

logger = logging.getLogger(__name__)


def generate_multi_queries(question: str) -> list[str]:
    """Generate multiple query variants for better recall."""
    settings = get_settings()

    try:
        text = chat_complete(
            model=settings.fast_model,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Generate {settings.multi_query_count} different search queries "
                        f"that would help find information to answer this question:\n\n"
                        f'"{question}"\n\n'
                        "Each query should approach the topic from a different angle "
                        "(synonyms, related concepts, specific aspects).\n"
                        "Return ONLY the queries, one per line, no numbering or bullets."
                    ),
                }
            ],
        )
        queries = [q.strip() for q in text.strip().split("\n") if q.strip()]
        return queries[:settings.multi_query_count]
    except Exception as e:
        logger.warning(f"Multi-query generation failed: {e}")
        return []


def retrieve_documents(
    question: str,
    user_id: str,
    document_ids: Optional[list[str]] = None,
    top_k: int = 5,
) -> list[dict]:
    """
    Full retrieval pipeline:
    1. Generate multi-query variants
    2. For each variant: embed → hybrid search
    3. Merge & deduplicate results
    4. Rerank with Cohere cross-encoder
    5. Return top_k

    `user_id` is REQUIRED for tenant isolation.
    """
    settings = get_settings()
    search_top_k = top_k * settings.search_candidates_multiplier

    # 1. Multi-query generation
    queries = generate_multi_queries(question)
    all_queries = [question] + queries  # Always include original

    # 2. Hybrid search per query variant
    all_results: dict[str, dict] = {}  # keyed by chunk id to deduplicate

    for q in all_queries:
        query_embedding = get_query_embedding(q)
        results = hybrid_search(
            query_embedding=query_embedding,
            query_text=q,
            user_id=user_id,
            top_k=search_top_k,
            document_ids=document_ids,
        )
        for r in results:
            rid = r["id"]
            if rid not in all_results or r["relevance_score"] > all_results[rid]["relevance_score"]:
                all_results[rid] = r

    # Sort by best score
    candidates = sorted(
        all_results.values(),
        key=lambda x: x["relevance_score"],
        reverse=True,
    )[:search_top_k]

    if not candidates:
        return []

    # 3. Rerank
    reranked = rerank_documents(
        query=question,
        documents=candidates,
        top_n=top_k,
    )

    return reranked

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "BrainHub Team API"
    debug: bool = False
    cors_origins: str = "http://localhost:3000"

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    supabase_jwt_secret: str
    supabase_db_url: str
    storage_bucket: str = "docs"

    # LLM (Anthropic Claude)
    anthropic_api_key: str
    generation_model: str = "claude-sonnet-4-20250514"
    fast_model: str = "claude-haiku-4-5-20251001"

    # Embedding (Voyage AI)
    voyage_api_key: str
    voyage_doc_model: str = "voyage-3-large"
    voyage_query_model: str = "voyage-3-lite"
    embedding_dimensions: int = 1536

    # Reranking (Cohere)
    cohere_api_key: Optional[str] = None
    cohere_rerank_model: str = "rerank-v3.5"
    enable_reranking: bool = True

    # RAG Pipeline
    chunk_size: int = 500
    chunk_overlap: int = 100
    similarity_top_k: int = 5
    search_candidates_multiplier: int = 3
    relevance_threshold: float = 0.7
    rrf_k: int = 60
    multi_query_count: int = 3

    # Cache (Redis)
    redis_url: str = "redis://localhost:6379"
    embedding_cache_ttl: int = 3600
    semantic_cache_ttl: int = 3600
    semantic_cache_threshold: float = 0.85

    # Web Search Fallback (Tavily)
    tavily_api_key: Optional[str] = None

    # Multimodal (Google Gemini)
    google_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-flash-preview-04-17"

    # Rate Limiting
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60

    # Guardrails
    enable_input_guardrails: bool = True

    # Observability (Langfuse)
    langfuse_enabled: bool = False
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # File limits
    max_file_size: int = 20 * 1024 * 1024  # 20MB

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()

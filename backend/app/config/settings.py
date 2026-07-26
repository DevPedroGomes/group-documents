from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    app_name: str = "BrainHub Team API"
    debug: bool = False
    cors_origins: str = "http://localhost:3000"

    # Database
    database_url: str

    # Auth (local JWT)
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    # File storage
    uploads_path: str = "/app/uploads"

    # LLM provider — "anthropic" (native SDK) | "openrouter" (OpenAI-compat aggregator)
    # When llm_provider="openrouter", `generation_model` and `fast_model` should be
    # OpenRouter ids (e.g. "anthropic/claude-haiku-4.5", "deepseek/deepseek-chat").
    # Default kept as "anthropic" for backward-compat.
    llm_provider: str = "anthropic"
    anthropic_api_key: Optional[str] = None
    generation_model: str = "claude-sonnet-4-20250514"
    fast_model: str = "claude-haiku-4-5-20251001"

    # OpenRouter (used when llm_provider="openrouter")
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Embedding (Voyage AI)
    voyage_api_key: str
    # Doc e query DEVEM usar o mesmo modelo: `input_type` ("document"/"query")
    # e o que diferencia os dois lados no Voyage. Modelos distintos produzem
    # espacos vetoriais nao-comparaveis — e voyage-3-lite (512 dims) contra
    # voyage-3-large (1024) nem chega a rodar: pgvector rejeita a comparacao.
    voyage_doc_model: str = "voyage-3-large"
    voyage_query_model: str = "voyage-3-large"
    # Precisa bater com o vector(N) das migrations. Ver migrations/002.
    embedding_dimensions: int = 1024

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

    # Observability: o pacote `langfuse` foi removido do requirements por ser
    # dependencia morta — 3 MB na imagem, nenhum import no codigo e
    # `langfuse_enabled` nunca lido. Se for reinstrumentar, adicione langfuse>=4
    # e escreva contra a API nova; a v2 que estava pinada e incompativel com ela.
    # Envs LANGFUSE_* remanescentes sao ignoradas (model_config extra="ignore").

    # File limits
    max_file_size: int = 20 * 1024 * 1024  # 20MB

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()

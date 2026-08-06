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
    # Producao roda no OpenRouter (ver backend/.env), entao estes defaults valem
    # para o caminho de rollback LLM_PROVIDER=anthropic. Saiu de
    # claude-sonnet-4-20250514, que a Anthropic retira em 15/06/2026.
    generation_model: str = "claude-sonnet-5"
    fast_model: str = "claude-haiku-4-5"

    # OpenRouter (used when llm_provider="openrouter")
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Embedding: LOCAL, sem provider.
    #
    # `jinaai/jina-clip-v1` via FastEmbed/ONNX. Duas torres do MESMO modelo,
    # entao texto e imagem ficam no mesmo espaco vetorial — e o que permite uma
    # pergunta escrita recuperar uma figura. Apache 2.0 (o jina-clip-v2 e
    # CC-BY-NC e proibiria uso comercial). Ver services/embedding.py.
    #
    # Saiu da Voyage porque embedding era a unica dependencia com lock-in real:
    # trocar de modelo obriga a re-embedar o corpus inteiro. LLM e roteador e
    # transcricao e endpoint isolado — essas se trocam numa tarde.
    #
    # Precisa bater com o vector(N) das migrations. Ver migrations/003.
    embedding_dimensions: int = 768
    # Teto de embeddings concorrentes: 2 nucleos, e as outras demos do
    # portfolio rodam na mesma maquina.
    embedding_max_concurrency: int = 2

    # Abaixo disto uma pagina de PDF e tratada como escaneada e vai para o
    # caminho visual em vez do textual. `pypdf` devolve string vazia (ou quase)
    # em pagina que e so imagem — e ate hoje essas paginas simplesmente
    # sumiam do indice, sem erro nenhum.
    pdf_min_chars_por_pagina: int = 120
    # DPI do render da pagina escaneada. 150 e legivel para o encoder de visao
    # sem gerar imagem grande demais para uma maquina de 2 nucleos.
    pdf_render_dpi: int = 150

    # Reranking (Cohere)
    cohere_api_key: Optional[str] = None
    # `rerank-v3.5` foi aposentado em 1/jul/2026 e desde 1/ago/2026 a Cohere
    # serve `rerank-4-fast` no lugar dele automaticamente. Pedir o sucessor
    # pelo nome em vez de depender da troca silenciosa do fornecedor — e o
    # score dele tem distribuicao diferente, entao `relevance_threshold` so
    # vale depois de recalibrado contra ele (ver core/rag/grader.py).
    cohere_rerank_model: str = "rerank-4-fast"
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
    # `semantic_cache_ttl` e `semantic_cache_threshold` sairam junto com a
    # tabela `semantic_cache` do models.py: nenhum codigo lia nenhum dos tres.
    # A tabela continua existindo no banco, vazia; pode ser dropada numa
    # migration quando alguem estiver mexendo no schema por outro motivo.

    # Web Search Fallback (Tavily)
    tavily_api_key: Optional[str] = None

    # Multimodal
    #
    # O Gemini saiu inteiro. Ele era usado para transformar imagem, audio e
    # video em texto antes de indexar. Dois problemas: o pacote
    # `google-generativeai` foi descontinuado pelo Google, e o id configurado
    # (`gemini-2.5-flash-preview-04-17`) e endpoint de preview ja retirado —
    # ou seja, o caminho multimodal estava morto por dois motivos ao mesmo
    # tempo, alem da chave vazia.
    #
    # Hoje: imagem e embedada DIRETO pela torre de visao local (jina-clip-v1),
    # e a descricao textual — que serve ao BM25 e ao gerador, nao a busca
    # vetorial — sai do Claude, que ja esta configurado e pago.
    #
    # AUDIO e VIDEO vao para o Deepgram, que aceita os dois containers e
    # devolve a fala transcrita. Limitacao assumida: o CONTEUDO VISUAL do video
    # nao e indexado, so o que e falado nele. Indexar frames exigiria ffmpeg e
    # amostragem, e video e a modalidade mais rara num app de documentos de
    # time — nao paga o peso na imagem.
    deepgram_api_key: Optional[str] = None
    deepgram_model: str = "nova-3"

    # Rate Limiting
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60
    # Cadastro e aberto e gratuito, entao o rate limit por IP e a unica coisa
    # entre um script e uma fila infinita de contas novas.
    auth_rate_limit: str = "5/minute"

    # Tetos diarios GLOBAIS (ver core/budget.py). Os rate limits acima limitam
    # UM chamador; nenhum deles limita todos juntos nem alguem criando contas.
    daily_chat_limit: int = 300
    daily_ingest_limit: int = 100

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

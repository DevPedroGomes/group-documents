# RAG Pipeline Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the group-documents RAG pipeline from vector-only search with Agno to a Corrective RAG system with hybrid search, reranking, contextual enrichment, and streaming — orchestrated by LangGraph, powered by Claude + Voyage AI.

**Architecture:** Replace the Agno agent with a LangGraph state machine implementing a 6-node Corrective RAG pipeline (Retrieve → Rerank → Grade → Transform → Web Search → Generate). Ingestion gains semantic chunking with contextual enrichment. Frontend gains SSE streaming with workflow step visualization.

**Tech Stack:** Claude Sonnet 4 + Haiku 4.5 (Anthropic), Voyage 4 (embeddings), Cohere Rerank v3.5, LangGraph, PostgreSQL pgvector + tsvector, Redis, Tavily, FastAPI, Next.js 14

---

## File Structure

### New files to create

| File | Responsibility |
|------|---------------|
| `backend/app/config/settings.py` | Centralized Pydantic BaseSettings |
| `backend/app/db/engine.py` | SQLAlchemy engine + session factory |
| `backend/app/db/models.py` | SQLAlchemy table definitions (moved from app/models.py) |
| `backend/app/api/routes/documents.py` | /ingest, /documents, /document/{id}/preview routes |
| `backend/app/api/routes/chat.py` | /chat (SSE streaming), /threads routes |
| `backend/app/api/dependencies.py` | require_user() auth dependency |
| `backend/app/services/embedding.py` | Voyage 4 embedding generation |
| `backend/app/services/embedding_cache.py` | Redis query embedding cache |
| `backend/app/services/vector_store.py` | pgvector hybrid search (semantic + keyword + RRF) |
| `backend/app/services/supabase_client.py` | Supabase storage (moved, unchanged) |
| `backend/app/core/ingestion/pdf_processor.py` | PDF text extraction |
| `backend/app/core/ingestion/chunker.py` | Semantic chunking + contextual enrichment |
| `backend/app/core/ingestion/multimodal.py` | Gemini processing (moved, unchanged) |
| `backend/app/core/rag/retriever.py` | Multi-query → hybrid search → rerank orchestration |
| `backend/app/core/rag/reranker.py` | Cohere cross-encoder reranking |
| `backend/app/core/rag/grader.py` | Score threshold filtering |
| `backend/app/core/rag/transformer.py` | Query rewriting with Claude Haiku |
| `backend/app/core/rag/generator.py` | Answer generation with Claude Sonnet (streaming) |
| `backend/app/core/workflow/corrective_flow.py` | LangGraph state machine |
| `backend/app/core/guardrails/input_validator.py` | Prompt injection detection |
| `backend/app/middleware/rate_limit.py` | Redis sliding window rate limiter |
| `sql/migration_rag_upgrade.sql` | Schema changes (tsvector, summary, semantic_cache) |
| `frontend/hooks/useChatStream.ts` | SSE streaming chat hook (replaces useChat) |
| `frontend/lib/types.ts` | Shared TypeScript interfaces |

### Files to delete after migration

| File | Reason |
|------|--------|
| `backend/app/agno_agent.py` | Replaced by LangGraph workflow |
| `backend/app/rag.py` | Replaced by services/embedding.py |
| `backend/app/ingest.py` | Replaced by core/ingestion/chunker.py + pdf_processor.py |
| `backend/app/models.py` | Moved to db/models.py |
| `backend/app/auth.py` | Moved to api/dependencies.py |
| `backend/app/observability.py` | Replaced by Langfuse integration in settings |

---

## Task 1: Database Schema Migration

**Files:**
- Create: `sql/migration_rag_upgrade.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- ==========================================
-- RAG UPGRADE MIGRATION
-- Run in Supabase SQL Editor after schema_complete.sql
-- ==========================================

-- 1. Add full-text search vector to chunks (for hybrid search)
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector tsvector
  GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;

CREATE INDEX IF NOT EXISTS chunks_search_vector_idx ON chunks USING gin(search_vector);

-- 2. Tune HNSW index with explicit parameters
DROP INDEX IF EXISTS chunks_embedding_idx;
CREATE INDEX chunks_embedding_idx ON chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- 3. Add composite index for filtered searches
CREATE INDEX IF NOT EXISTS chunks_doc_chunk_idx ON chunks (document_id, chunk_index);

-- 4. Add summary and chunk_count to documents
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_count integer DEFAULT 0;

-- 5. Create semantic cache table
CREATE TABLE IF NOT EXISTS semantic_cache (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  query_hash text NOT NULL,
  query_embedding vector(1536),
  query_text text NOT NULL,
  response_text text NOT NULL,
  citations jsonb,
  document_ids uuid[],
  created_at timestamptz DEFAULT now(),
  expires_at timestamptz DEFAULT now() + interval '1 hour'
);

CREATE INDEX IF NOT EXISTS semantic_cache_embedding_idx
  ON semantic_cache USING hnsw (query_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS semantic_cache_expires_idx
  ON semantic_cache (expires_at);

-- RLS for semantic_cache (private per implicit - no user_id, accessed via backend service role)

-- 6. Add trigger for threads.updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS threads_updated_at ON threads;
CREATE TRIGGER threads_updated_at
  BEFORE UPDATE ON threads FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

- [ ] **Step 2: Verify migration is idempotent**

Run: Read through the SQL and confirm every statement uses `IF NOT EXISTS` or `DROP IF EXISTS` before create. This ensures it can be re-run safely.

- [ ] **Step 3: Commit**

```bash
git add sql/migration_rag_upgrade.sql
git commit -m "feat: add RAG upgrade migration (tsvector, semantic_cache, HNSW tuning)"
```

---

## Task 2: Update Dependencies

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Rewrite requirements.txt**

```
# Core
fastapi==0.115.0
uvicorn[standard]==0.30.0
python-multipart==0.0.9
sqlalchemy==2.0.32
psycopg2-binary==2.9.9
pgvector==0.2.5
pydantic==2.8.2
pydantic-settings>=2.0.0
pyjwt==2.9.0
python-dotenv==1.0.1
httpx>=0.28

# LLM & RAG
anthropic>=0.40.0
voyageai>=0.3.0
cohere>=5.0
langgraph>=0.2.0
langchain-core>=0.3.0
tiktoken>=0.7.0

# Cache
redis>=5.0

# Web Search Fallback
tavily-python>=0.5.0

# Ingestion
pypdf==5.0.1
python-magic==0.4.27
google-generativeai>=0.8.0

# Observability
langfuse==2.39.0

# Supabase
supabase==2.4.0
```

- [ ] **Step 2: Install dependencies in venv**

Run:
```bash
cd backend && source .venv/bin/activate && pip install -r requirements.txt
```

Expected: All packages install successfully. Note any conflicts and resolve.

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "feat: update dependencies for RAG upgrade (anthropic, voyage, langgraph, cohere, redis)"
```

---

## Task 3: Centralized Settings

**Files:**
- Create: `backend/app/config/__init__.py`
- Create: `backend/app/config/settings.py`
- Modify: `backend/.env.example`

- [ ] **Step 1: Create config package**

Create `backend/app/config/__init__.py`:
```python
```

(Empty `__init__.py`)

- [ ] **Step 2: Write settings.py**

Create `backend/app/config/settings.py`:
```python
from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "HubDocs API"
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


@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: Update .env.example**

```
# Supabase
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=
SUPABASE_DB_URL=
STORAGE_BUCKET=docs

# Anthropic (Claude)
ANTHROPIC_API_KEY=
GENERATION_MODEL=claude-sonnet-4-20250514
FAST_MODEL=claude-haiku-4-5-20251001

# Voyage AI (Embeddings)
VOYAGE_API_KEY=
VOYAGE_DOC_MODEL=voyage-3-large
VOYAGE_QUERY_MODEL=voyage-3-lite

# Cohere (Reranking) - optional
COHERE_API_KEY=
ENABLE_RERANKING=true

# Redis (Cache)
REDIS_URL=redis://localhost:6379

# Tavily (Web Search Fallback) - optional
TAVILY_API_KEY=

# Google (Gemini Multimodal) - optional
GOOGLE_API_KEY=

# RAG Pipeline
CHUNK_SIZE=500
CHUNK_OVERLAP=100
SIMILARITY_TOP_K=5
RELEVANCE_THRESHOLD=0.7

# Rate Limiting
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_WINDOW_SECONDS=60

# CORS
CORS_ORIGINS=http://localhost:3000

# Langfuse (Observability) - optional
LANGFUSE_ENABLED=false
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/config/ backend/.env.example
git commit -m "feat: add centralized Pydantic settings and update .env.example"
```

---

## Task 4: Database Engine & Models

**Files:**
- Create: `backend/app/db/__init__.py`
- Create: `backend/app/db/engine.py`
- Create: `backend/app/db/models.py`

- [ ] **Step 1: Create db package**

Create `backend/app/db/__init__.py`:
```python
```

- [ ] **Step 2: Write engine.py**

Create `backend/app/db/engine.py`:
```python
from sqlalchemy import create_engine
from .models import metadata

from app.config.settings import get_settings


def get_engine():
    settings = get_settings()
    return create_engine(settings.supabase_db_url, pool_pre_ping=True)


engine = get_engine()
```

- [ ] **Step 3: Write models.py (moved + extended)**

Create `backend/app/db/models.py`:
```python
from sqlalchemy import (
    Table, Column, String, Integer, Text, JSON, TIMESTAMP,
    ForeignKey, MetaData, Float,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
import uuid

metadata = MetaData()

documents = Table(
    "documents", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("title", Text, nullable=False),
    Column("mime", Text),
    Column("storage_path", Text, nullable=False),
    Column("meta", JSON),
    Column("status", String, default="pending"),
    Column("summary", Text),
    Column("chunk_count", Integer, default=0),
    Column("uploaded_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

chunks = Table(
    "chunks", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("page", Integer),
    Column("chunk_index", Integer),
    Column("text", Text, nullable=False),
    Column("embedding", Vector(1536)),
    # search_vector is a GENERATED column in PostgreSQL, not defined here
)

threads = Table(
    "threads", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("title", Text),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

messages = Table(
    "messages", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("thread_id", UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False),
    Column("role", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("meta", JSON),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
)

semantic_cache = Table(
    "semantic_cache", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("query_hash", Text, nullable=False),
    Column("query_embedding", Vector(1536)),
    Column("query_text", Text, nullable=False),
    Column("response_text", Text, nullable=False),
    Column("citations", JSON),
    Column("document_ids", ARRAY(UUID(as_uuid=True))),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now()),
    Column("expires_at", TIMESTAMP(timezone=True)),
)
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/db/
git commit -m "feat: add db engine and extended models (semantic_cache, summary, chunk_count)"
```

---

## Task 5: Voyage Embedding Service

**Files:**
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/embedding.py`

- [ ] **Step 1: Create services package**

Create `backend/app/services/__init__.py`:
```python
```

- [ ] **Step 2: Write embedding.py**

Create `backend/app/services/embedding.py`:
```python
import voyageai

from app.config.settings import get_settings

_client = None


def _get_client() -> voyageai.Client:
    global _client
    if _client is None:
        settings = get_settings()
        _client = voyageai.Client(api_key=settings.voyage_api_key)
    return _client


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document chunks using Voyage 4 Large (optimized for documents)."""
    if not texts:
        return []
    settings = get_settings()
    client = _get_client()
    result = client.embed(
        texts,
        model=settings.voyage_doc_model,
        input_type="document",
    )
    vectors = result.embeddings
    if len(vectors) != len(texts):
        raise RuntimeError(f"Embedding count mismatch: expected {len(texts)}, got {len(vectors)}")
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a search query using Voyage 4 Lite (optimized for queries)."""
    settings = get_settings()
    client = _get_client()
    result = client.embed(
        [text],
        model=settings.voyage_query_model,
        input_type="query",
    )
    return result.embeddings[0]
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/
git commit -m "feat: add Voyage 4 embedding service (doc + query models)"
```

---

## Task 6: Redis Embedding Cache

**Files:**
- Create: `backend/app/services/embedding_cache.py`

- [ ] **Step 1: Write embedding_cache.py**

Create `backend/app/services/embedding_cache.py`:
```python
import hashlib
import json
import logging
from typing import Optional

import redis

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_redis_client = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _cache_key(query: str) -> str:
    query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()
    return f"emb:{query_hash}"


def get_cached_embedding(query: str) -> Optional[list[float]]:
    """Return cached embedding or None."""
    try:
        r = _get_redis()
        data = r.get(_cache_key(query))
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        logger.warning(f"Redis get error: {e}")
        return None


def cache_embedding(query: str, embedding: list[float]) -> None:
    """Cache an embedding with TTL."""
    try:
        settings = get_settings()
        r = _get_redis()
        r.setex(
            _cache_key(query),
            settings.embedding_cache_ttl,
            json.dumps(embedding),
        )
    except Exception as e:
        logger.warning(f"Redis set error: {e}")


def get_query_embedding(query: str) -> list[float]:
    """Get query embedding with cache-through pattern."""
    cached = get_cached_embedding(query)
    if cached is not None:
        return cached

    from app.services.embedding import embed_query
    vector = embed_query(query)
    cache_embedding(query, vector)
    return vector
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/embedding_cache.py
git commit -m "feat: add Redis embedding cache with SHA256 key hashing"
```

---

## Task 7: Hybrid Search (Vector Store)

**Files:**
- Create: `backend/app/services/vector_store.py`

- [ ] **Step 1: Write vector_store.py**

Create `backend/app/services/vector_store.py`:
```python
"""
Hybrid search combining semantic (pgvector HNSW) + keyword (tsvector GIN)
with Reciprocal Rank Fusion (RRF).
"""

import logging
import uuid as uuid_mod
from typing import Optional

from sqlalchemy import insert, text as sqltext

from app.config.settings import get_settings
from app.db.engine import engine
from app.services.embedding import embed_documents

logger = logging.getLogger(__name__)


def add_chunks(
    texts: list[str],
    embeddings: list[list[float]],
    user_id: str,
    document_id: str,
    pages: list[int],
    chunk_indices: list[int],
) -> int:
    """Insert chunks with embeddings into pgvector. Returns count inserted."""
    from app.db.models import chunks

    recs = []
    for i, (txt, emb) in enumerate(zip(texts, embeddings)):
        recs.append({
            "user_id": user_id,
            "document_id": document_id,
            "page": pages[i],
            "chunk_index": chunk_indices[i],
            "text": txt,
            "embedding": emb,
        })

    with engine.begin() as conn:
        conn.execute(insert(chunks), recs)

    return len(recs)


def hybrid_search(
    query_embedding: list[float],
    query_text: str,
    top_k: int = 5,
    document_ids: Optional[list[str]] = None,
) -> list[dict]:
    """
    Hybrid search: semantic (pgvector) + keyword (tsvector) fused with RRF.
    Returns top_k results sorted by combined RRF score.
    """
    settings = get_settings()
    prefetch = top_k * settings.search_candidates_multiplier

    qvec_str = "[" + ",".join(map(str, query_embedding)) + "]"

    # Build WHERE clause for optional document filtering
    doc_filter = ""
    params = {
        "qvec": qvec_str,
        "query_text": query_text,
        "limit": prefetch,
    }

    if document_ids:
        valid_ids = []
        for did in document_ids:
            try:
                uuid_mod.UUID(did)
                valid_ids.append(did)
            except (ValueError, AttributeError):
                continue
        if valid_ids:
            params["document_ids"] = "{" + ",".join(valid_ids) + "}"
            doc_filter = "AND c.document_id = ANY(CAST(:document_ids AS uuid[]))"

    # 1. Semantic search (pgvector HNSW)
    semantic_sql = sqltext(f"""
        SELECT c.id, c.document_id, d.title as document_title, c.page,
               left(c.text, 500) as snippet,
               1 - (c.embedding <=> CAST(:qvec AS vector)) as score
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE 1 - (c.embedding <=> CAST(:qvec AS vector)) >= 0.1
        {doc_filter}
        ORDER BY c.embedding <=> CAST(:qvec AS vector)
        LIMIT :limit
    """)

    # 2. Keyword search (tsvector GIN)
    keyword_sql = sqltext(f"""
        SELECT c.id, c.document_id, d.title as document_title, c.page,
               left(c.text, 500) as snippet,
               ts_rank(c.search_vector, plainto_tsquery('english', :query_text)) as score
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE c.search_vector @@ plainto_tsquery('english', :query_text)
        {doc_filter}
        ORDER BY score DESC
        LIMIT :limit
    """)

    with engine.begin() as conn:
        semantic_rows = conn.execute(semantic_sql, params).mappings().all()
        keyword_rows = conn.execute(keyword_sql, params).mappings().all()

    # 3. Reciprocal Rank Fusion (RRF)
    k = settings.rrf_k
    scores: dict[str, dict] = {}

    for rank, row in enumerate(semantic_rows):
        rid = str(row["id"])
        rrf = 1.0 / (k + rank + 1)
        if rid not in scores:
            scores[rid] = {"score": 0.0, "data": dict(row)}
        scores[rid]["score"] += rrf

    for rank, row in enumerate(keyword_rows):
        rid = str(row["id"])
        rrf = 1.0 / (k + rank + 1)
        if rid not in scores:
            scores[rid] = {"score": 0.0, "data": dict(row)}
        scores[rid]["score"] += rrf

    # Sort by combined RRF score
    sorted_results = sorted(scores.values(), key=lambda x: x["score"], reverse=True)

    results = []
    for item in sorted_results[:top_k]:
        data = item["data"]
        results.append({
            "id": str(data["id"]),
            "document_id": str(data["document_id"]),
            "document_title": data["document_title"],
            "page": data["page"],
            "snippet": data["snippet"],
            "relevance_score": item["score"],
        })

    return results
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/vector_store.py
git commit -m "feat: add hybrid search with RRF fusion (semantic + keyword)"
```

---

## Task 8: Ingestion — PDF Processor + Semantic Chunker

**Files:**
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/ingestion/__init__.py`
- Create: `backend/app/core/ingestion/pdf_processor.py`
- Create: `backend/app/core/ingestion/chunker.py`

- [ ] **Step 1: Create package files**

Create `backend/app/core/__init__.py`:
```python
```

Create `backend/app/core/ingestion/__init__.py`:
```python
```

- [ ] **Step 2: Write pdf_processor.py**

Create `backend/app/core/ingestion/pdf_processor.py`:
```python
"""PDF text extraction with page-level tracking."""

import io
import re

from pypdf import PdfReader


def extract_pages_from_pdf(data: bytes) -> list[str]:
    """Extract text from each page of a PDF. Returns list of page texts."""
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = re.sub(r"\s+", " ", text).strip()
        pages.append(text)
    return pages
```

- [ ] **Step 3: Write chunker.py**

Create `backend/app/core/ingestion/chunker.py`:
```python
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
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/
git commit -m "feat: add PDF processor and semantic chunker with contextual enrichment"
```

---

## Task 9: RAG Core — Reranker, Grader, Transformer

**Files:**
- Create: `backend/app/core/rag/__init__.py`
- Create: `backend/app/core/rag/reranker.py`
- Create: `backend/app/core/rag/grader.py`
- Create: `backend/app/core/rag/transformer.py`

- [ ] **Step 1: Create rag package**

Create `backend/app/core/rag/__init__.py`:
```python
```

- [ ] **Step 2: Write reranker.py**

Create `backend/app/core/rag/reranker.py`:
```python
"""Cohere cross-encoder reranking for precision improvement."""

import logging
from typing import Optional

import cohere

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_client: Optional[cohere.Client] = None


def _get_client() -> cohere.Client:
    global _client
    if _client is None:
        settings = get_settings()
        _client = cohere.Client(api_key=settings.cohere_api_key)
    return _client


def rerank_documents(
    query: str,
    documents: list[dict],
    top_n: int = 5,
) -> list[dict]:
    """
    Rerank documents using Cohere cross-encoder.
    Falls back to original order if Cohere is unavailable.
    """
    settings = get_settings()

    if not settings.enable_reranking or not settings.cohere_api_key:
        return documents[:top_n]

    if len(documents) <= 1:
        return documents

    try:
        client = _get_client()
        texts = [doc["snippet"] for doc in documents]

        response = client.rerank(
            model=settings.cohere_rerank_model,
            query=query,
            documents=texts,
            top_n=min(top_n, len(documents)),
            return_documents=False,
        )

        reranked = []
        for result in response.results:
            doc = documents[result.index].copy()
            doc["relevance_score"] = result.relevance_score
            reranked.append(doc)

        return reranked

    except Exception as e:
        logger.error(f"Cohere rerank failed, using original order: {e}")
        return documents[:top_n]
```

- [ ] **Step 3: Write grader.py**

Create `backend/app/core/rag/grader.py`:
```python
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
```

- [ ] **Step 4: Write transformer.py**

Create `backend/app/core/rag/transformer.py`:
```python
"""Query transformation using Claude Haiku for better retrieval."""

import logging

import anthropic

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def transform_query(question: str) -> str:
    """
    Rewrite a query for better retrieval.
    Used when initial retrieval doesn't find relevant documents.
    """
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        response = client.messages.create(
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
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Query transformation failed: {e}")
        return question
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/rag/
git commit -m "feat: add reranker, grader, and query transformer"
```

---

## Task 10: RAG Core — Retriever (Multi-query + Hybrid + Rerank)

**Files:**
- Create: `backend/app/core/rag/retriever.py`

- [ ] **Step 1: Write retriever.py**

Create `backend/app/core/rag/retriever.py`:
```python
"""
Two-stage retriever: Multi-query → Hybrid Search → Rerank.
Entry point for all document retrieval.
"""

import logging
from typing import Optional

import anthropic

from app.config.settings import get_settings
from app.services.embedding_cache import get_query_embedding
from app.services.vector_store import hybrid_search
from app.core.rag.reranker import rerank_documents

logger = logging.getLogger(__name__)


def generate_multi_queries(question: str) -> list[str]:
    """Generate multiple query variants for better recall."""
    settings = get_settings()

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
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
        queries = [
            q.strip()
            for q in response.content[0].text.strip().split("\n")
            if q.strip()
        ]
        return queries[:settings.multi_query_count]
    except Exception as e:
        logger.warning(f"Multi-query generation failed: {e}")
        return []


def retrieve_documents(
    question: str,
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
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/core/rag/retriever.py
git commit -m "feat: add multi-query retriever with hybrid search and reranking"
```

---

## Task 11: RAG Core — Generator (Claude Sonnet Streaming)

**Files:**
- Create: `backend/app/core/rag/generator.py`

- [ ] **Step 1: Write generator.py**

Create `backend/app/core/rag/generator.py`:
```python
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
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/core/rag/generator.py
git commit -m "feat: add Claude Sonnet answer generator with streaming support"
```

---

## Task 12: LangGraph Corrective RAG Workflow

**Files:**
- Create: `backend/app/core/workflow/__init__.py`
- Create: `backend/app/core/workflow/corrective_flow.py`

- [ ] **Step 1: Create workflow package**

Create `backend/app/core/workflow/__init__.py`:
```python
```

- [ ] **Step 2: Write corrective_flow.py**

Create `backend/app/core/workflow/corrective_flow.py`:
```python
"""
LangGraph Corrective RAG workflow.

Pipeline: Retrieve → Rerank → Grade → [Transform → Web Search] → Generate
"""

import logging
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, END

from app.core.rag.retriever import retrieve_documents
from app.core.rag.grader import grade_documents
from app.core.rag.transformer import transform_query
from app.core.rag.generator import generate_answer

logger = logging.getLogger(__name__)


class WorkflowStep(TypedDict):
    step: str
    status: str
    details: str


class GraphState(TypedDict):
    question: str
    original_question: str
    document_ids: Optional[list[str]]
    user_id: str
    history: Optional[list[dict]]
    documents: list[dict]
    answer: str
    citations: list[dict]
    workflow: list[WorkflowStep]
    needs_web_search: bool
    used_web_search: bool
    was_corrected: bool


# --- Nodes ---

def retrieve_node(state: GraphState) -> dict:
    """Retrieve documents using multi-query hybrid search + reranking."""
    documents = retrieve_documents(
        question=state["question"],
        document_ids=state.get("document_ids"),
        top_k=5,
    )

    workflow = state["workflow"] + [{
        "step": "retrieve",
        "status": "completed",
        "details": f"Found {len(documents)} relevant chunks",
    }]

    return {"documents": documents, "workflow": workflow}


def grade_node(state: GraphState) -> dict:
    """Grade documents by relevance score threshold."""
    filtered, needs_web_search = grade_documents(state["documents"])

    workflow = state["workflow"] + [{
        "step": "grade",
        "status": "completed",
        "details": f"Kept {len(filtered)}/{len(state['documents'])} documents"
            + (" — triggering web search" if needs_web_search else ""),
    }]

    return {
        "documents": filtered,
        "needs_web_search": needs_web_search,
        "workflow": workflow,
    }


def transform_node(state: GraphState) -> dict:
    """Transform query for better retrieval."""
    transformed = transform_query(state["question"])

    workflow = state["workflow"] + [{
        "step": "transform",
        "status": "completed",
        "details": f"Rewrote query: {transformed[:100]}",
    }]

    return {
        "question": transformed,
        "was_corrected": True,
        "workflow": workflow,
    }


def web_search_node(state: GraphState) -> dict:
    """Search the web as fallback when documents are insufficient."""
    workflow_step: WorkflowStep = {
        "step": "web_search",
        "status": "completed",
        "details": "Web search not configured — answering from available documents",
    }

    try:
        from app.config.settings import get_settings
        settings = get_settings()

        if settings.tavily_api_key:
            from tavily import TavilyClient
            client = TavilyClient(api_key=settings.tavily_api_key)
            results = client.search(state["question"], max_results=3)

            web_docs = []
            for r in results.get("results", []):
                web_docs.append({
                    "id": "web",
                    "document_id": "web",
                    "document_title": r.get("title", "Web Result"),
                    "page": 0,
                    "snippet": r.get("content", "")[:500],
                    "relevance_score": r.get("score", 0.5),
                })

            workflow_step["details"] = f"Found {len(web_docs)} web results"
            return {
                "documents": state["documents"] + web_docs,
                "used_web_search": True,
                "workflow": state["workflow"] + [workflow_step],
            }
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        workflow_step["details"] = f"Web search failed: {e}"

    return {
        "used_web_search": False,
        "workflow": state["workflow"] + [workflow_step],
    }


def generate_node(state: GraphState) -> dict:
    """Generate answer from documents using Claude Sonnet."""
    answer = generate_answer(
        question=state["original_question"],
        documents=state["documents"],
        history=state.get("history"),
    )

    # Extract citations from top documents
    citations = []
    for doc in state["documents"][:5]:
        if doc.get("document_id") != "web":
            citations.append({
                "document_id": doc["document_id"],
                "document_title": doc["document_title"],
                "page": doc["page"],
                "snippet": doc["snippet"][:200],
            })

    workflow = state["workflow"] + [{
        "step": "generate",
        "status": "completed",
        "details": f"Generated answer ({len(answer)} chars)",
    }]

    return {"answer": answer, "citations": citations, "workflow": workflow}


# --- Routing ---

def should_transform(state: GraphState) -> str:
    if state["needs_web_search"]:
        return "transform"
    return "generate"


# --- Graph ---

def build_graph() -> StateGraph:
    workflow = StateGraph(GraphState)

    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("grade", grade_node)
    workflow.add_node("transform", transform_node)
    workflow.add_node("web_search", web_search_node)
    workflow.add_node("generate", generate_node)

    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "grade")
    workflow.add_conditional_edges("grade", should_transform, {
        "transform": "transform",
        "generate": "generate",
    })
    workflow.add_edge("transform", "web_search")
    workflow.add_edge("web_search", "generate")
    workflow.add_edge("generate", END)

    return workflow.compile()


# Compiled graph (singleton)
corrective_rag_graph = build_graph()


def run_corrective_rag(
    question: str,
    user_id: str,
    document_ids: Optional[list[str]] = None,
    history: Optional[list[dict]] = None,
) -> dict:
    """Run the full Corrective RAG pipeline. Returns answer + citations + workflow."""
    initial_state: GraphState = {
        "question": question,
        "original_question": question,
        "document_ids": document_ids,
        "user_id": user_id,
        "history": history,
        "documents": [],
        "answer": "",
        "citations": [],
        "workflow": [],
        "needs_web_search": False,
        "used_web_search": False,
        "was_corrected": False,
    }

    final_state = corrective_rag_graph.invoke(initial_state)

    return {
        "answer": final_state["answer"],
        "citations": final_state["citations"],
        "workflow": final_state["workflow"],
        "was_corrected": final_state["was_corrected"],
        "used_web_search": final_state["used_web_search"],
    }
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/workflow/
git commit -m "feat: add LangGraph Corrective RAG workflow (5-node state machine)"
```

---

## Task 13: Input Guardrails

**Files:**
- Create: `backend/app/core/guardrails/__init__.py`
- Create: `backend/app/core/guardrails/input_validator.py`

- [ ] **Step 1: Create guardrails package**

Create `backend/app/core/guardrails/__init__.py`:
```python
```

- [ ] **Step 2: Write input_validator.py**

Create `backend/app/core/guardrails/input_validator.py`:
```python
"""Heuristic-based input validation for prompt injection detection."""

import re
import logging

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"ignore\s+(all\s+)?above",
        r"you\s+are\s+now",
        r"new\s+instructions",
        r"system\s+prompt",
        r"reveal\s+your\s+(instructions|prompt|system)",
        r"forget\s+(everything|all|previous)",
        r"act\s+as\s+(a|an)\s+",
        r"pretend\s+(to\s+be|you\s+are)",
        r"jailbreak",
        r"DAN\s+mode",
    ]
]


def validate_input(question: str) -> tuple[bool, str]:
    """
    Validate user input. Returns (is_valid, reason).
    Heuristic-based — no LLM calls.
    """
    settings = get_settings()
    if not settings.enable_input_guardrails:
        return True, ""

    if not question or len(question.strip()) < 3:
        return False, "Question too short"

    if len(question) > 10000:
        return False, "Question exceeds 10000 characters"

    for pattern in INJECTION_PATTERNS:
        if pattern.search(question):
            logger.warning(f"Injection pattern detected in query: {question[:100]}")
            return False, "Contains disallowed content"

    return True, ""
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/guardrails/
git commit -m "feat: add input validation guardrails for prompt injection detection"
```

---

## Task 14: Auth Dependency + Supabase Client (move)

**Files:**
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/dependencies.py`
- Move: `backend/app/supabase_client.py` → `backend/app/services/supabase_client.py`
- Move: `backend/app/multimodal.py` → `backend/app/core/ingestion/multimodal.py`

- [ ] **Step 1: Create api package**

Create `backend/app/api/__init__.py`:
```python
```

- [ ] **Step 2: Write dependencies.py (auth from old auth.py)**

Create `backend/app/api/dependencies.py`:
```python
"""FastAPI dependencies: authentication, database access."""

import jwt
from fastapi import Request, HTTPException

from app.config.settings import get_settings


async def require_user(request: Request) -> str:
    """Validate JWT token and extract user_id."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")

    token = auth.split(" ", 1)[1].strip()
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

    user_id = payload.get("sub") or payload.get("user_id") or payload.get("uid")
    if not user_id:
        raise HTTPException(401, "No user in token")

    request.state.user_id = user_id
    request.state.user_token = token
    return user_id
```

- [ ] **Step 3: Move supabase_client.py**

Copy `backend/app/supabase_client.py` to `backend/app/services/supabase_client.py`, updating imports:

```python
import os
import httpx
from supabase import create_client, Client

from app.config.settings import get_settings

_svc: Client | None = None
_anon: Client | None = None


def svc_client() -> Client:
    global _svc
    if _svc is None:
        settings = get_settings()
        _svc = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _svc


def anon_client() -> Client:
    global _anon
    if _anon is None:
        settings = get_settings()
        _anon = create_client(settings.supabase_url, settings.supabase_anon_key)
    return _anon


def create_signed_url(path: str, expires: int = 600) -> str:
    settings = get_settings()
    sb = svc_client()
    res = sb.storage.from_(settings.storage_bucket).create_signed_url(path, expires)
    return res.get("signedUrl") or res.get("signedURL")
```

- [ ] **Step 4: Move multimodal.py**

Copy `backend/app/multimodal.py` to `backend/app/core/ingestion/multimodal.py`. Update the import of `genai.configure` to use settings:

```python
import os
import tempfile
import time
import logging

import google.generativeai as genai

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_configured = False


def _ensure_configured():
    global _configured
    if not _configured:
        settings = get_settings()
        if settings.google_api_key:
            genai.configure(api_key=settings.google_api_key)
            _configured = True
        else:
            raise RuntimeError("GOOGLE_API_KEY not configured")


def process_media_with_gemini(
    data: bytes, mime_type: str, prompt: str = "Describe this content in detail.", filename: str | None = None
) -> str:
    _ensure_configured()
    temp_path = None
    try:
        suffix = ".bin"
        if filename:
            ext = os.path.splitext(filename)[1]
            if ext:
                suffix = ext
        if suffix == ".bin":
            if "image" in mime_type: suffix = ".jpg"
            elif "audio" in mime_type: suffix = ".mp3"
            elif "video" in mime_type: suffix = ".mp4"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            temp_path = tmp.name

        uploaded_file = genai.upload_file(temp_path, mime_type=mime_type)

        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = genai.get_file(uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            raise Exception("Gemini File Upload Failed")

        model = genai.GenerativeModel("gemini-3-pro-preview")
        response = model.generate_content(
            [uploaded_file, prompt],
            request_options={"timeout": 600},
        )
        return response.text

    except Exception as e:
        logger.error(f"Gemini processing error: {e}")
        return f"Error processing media: {str(e)}"
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def process_image(image_bytes: bytes) -> str:
    return process_media_with_gemini(image_bytes, "image/jpeg", "Describe this image in extreme detail for search and analysis purposes. Include text, objects, colors, and sentiments.")


def process_audio(audio_bytes: bytes, filename: str = "audio.mp3") -> str:
    return process_media_with_gemini(audio_bytes, "audio/mp3", "Transcribe this audio completely and generate a summary of the main points.", filename=filename)


def process_video(video_bytes: bytes) -> str:
    return process_media_with_gemini(video_bytes, "video/mp4", "Watch this video. 1. Transcribe what is spoken. 2. Describe what happens visually frame by frame at key moments.")
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/ backend/app/services/supabase_client.py backend/app/core/ingestion/multimodal.py
git commit -m "feat: add auth dependency, move supabase_client and multimodal to new structure"
```

---

## Task 15: API Routes — Documents

**Files:**
- Create: `backend/app/api/routes/__init__.py`
- Create: `backend/app/api/routes/documents.py`

- [ ] **Step 1: Create routes package**

Create `backend/app/api/routes/__init__.py`:
```python
```

- [ ] **Step 2: Write documents.py**

Create `backend/app/api/routes/documents.py`:
```python
"""Document management routes: ingest, list, preview."""

import re
import logging
import asyncio
from typing import Optional

import httpx
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import insert, text as sqltext

from app.config.settings import get_settings
from app.db.engine import engine
from app.db.models import documents, chunks
from app.api.dependencies import require_user
from app.services.supabase_client import create_signed_url
from app.services.embedding import embed_documents
from app.core.ingestion.pdf_processor import extract_pages_from_pdf
from app.core.ingestion.chunker import chunk_document_pages, enrich_chunks_with_context

logger = logging.getLogger(__name__)

router = APIRouter()


def validate_storage_path(path: str) -> bool:
    if not path:
        return False
    if ".." in path or path.startswith("/"):
        return False
    pattern = r"^[a-f0-9-]+/docs/[^/]+\.(pdf|png|jpg|jpeg|gif|webp|mp3|mp4|wav|webm)$"
    return bool(re.match(pattern, path, re.IGNORECASE))


class IngestBody(BaseModel):
    storage_path: str
    title: str
    mime: str

    class Config:
        str_strip_whitespace = True


@router.post("/ingest")
async def ingest(request: Request, body: IngestBody, background_tasks: BackgroundTasks):
    user_id = await require_user(request)

    if not body.title or len(body.title) > 500:
        raise HTTPException(400, "Invalid title (max 500 characters)")

    if not body.storage_path or not validate_storage_path(body.storage_path):
        raise HTTPException(400, "Invalid file path")

    path_user_id = body.storage_path.split("/")[0]
    if path_user_id != user_id:
        raise HTTPException(403, "Storage path does not belong to this user")

    try:
        with engine.begin() as conn:
            doc_id = conn.execute(
                insert(documents).values(
                    user_id=user_id,
                    title=body.title,
                    mime=body.mime,
                    storage_path=body.storage_path,
                    status="pending",
                ).returning(documents.c.id)
            ).scalar_one()
    except Exception as e:
        logger.error(f"DB error creating document: {e}")
        raise HTTPException(500, "Error creating document record")

    background_tasks.add_task(process_ingestion, str(doc_id), user_id, body.storage_path)
    return {"document_id": str(doc_id), "status": "pending"}


async def process_ingestion(doc_id: str, user_id: str, storage_path: str):
    """Background task: download → chunk → enrich → embed → store."""
    settings = get_settings()
    loop = asyncio.get_running_loop()

    try:
        with engine.begin() as conn:
            mime = conn.execute(
                sqltext("SELECT mime FROM documents WHERE id = :id"), {"id": doc_id}
            ).scalar()
            conn.execute(
                sqltext("UPDATE documents SET status = 'processing' WHERE id = :id"), {"id": doc_id}
            )

        # Download
        url = create_signed_url(storage_path, 600)
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                data_chunks = []
                total = 0
                async for chunk in r.aiter_bytes(chunk_size=8192):
                    total += len(chunk)
                    if total > settings.max_file_size:
                        raise Exception(f"File too large (>{settings.max_file_size // (1024*1024)}MB)")
                    data_chunks.append(chunk)
                data = b"".join(data_chunks)

        # Process by type
        text_chunks: list[tuple[str, dict]] = []

        if mime == "application/pdf":
            pages = await loop.run_in_executor(None, extract_pages_from_pdf, data)
            if not pages:
                raise Exception("No content extracted from PDF")

            text_chunks = chunk_document_pages(pages)

            # Contextual enrichment
            full_text = " ".join(p for p in pages if p.strip())
            doc_title = ""
            with engine.begin() as conn:
                doc_title = conn.execute(
                    sqltext("SELECT title FROM documents WHERE id = :id"), {"id": doc_id}
                ).scalar() or ""

            text_chunks = await loop.run_in_executor(
                None, enrich_chunks_with_context, text_chunks, full_text, doc_title
            )

            # Generate summary
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
                summary_resp = client.messages.create(
                    model=settings.fast_model,
                    max_tokens=300,
                    messages=[{
                        "role": "user",
                        "content": f"Summarize this document in 2-3 sentences:\n\n{full_text[:10000]}",
                    }],
                )
                summary = summary_resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"Summary generation failed: {e}")
                summary = None

        elif mime and mime.startswith("image/"):
            from app.core.ingestion.multimodal import process_image
            text = await loop.run_in_executor(None, process_image, data)
            if not text:
                raise Exception("No content extracted from image")
            from app.core.ingestion.chunker import chunk_text
            for ci, ck in enumerate(chunk_text(text)):
                text_chunks.append((ck, {"page": 1, "chunk_index": ci}))
            summary = None

        elif mime and mime.startswith("audio/"):
            from app.core.ingestion.multimodal import process_audio
            filename = storage_path.split("/")[-1]
            text = await loop.run_in_executor(None, lambda: process_audio(data, filename=filename))
            if not text:
                raise Exception("No content extracted from audio")
            from app.core.ingestion.chunker import chunk_text
            for ci, ck in enumerate(chunk_text(text)):
                text_chunks.append((ck, {"page": 1, "chunk_index": ci}))
            summary = None

        elif mime and mime.startswith("video/"):
            from app.core.ingestion.multimodal import process_video
            text = await loop.run_in_executor(None, process_video, data)
            if not text:
                raise Exception("No content extracted from video")
            from app.core.ingestion.chunker import chunk_text
            for ci, ck in enumerate(chunk_text(text)):
                text_chunks.append((ck, {"page": 1, "chunk_index": ci}))
            summary = None

        else:
            raise Exception(f"Unsupported format: {mime}")

        if not text_chunks:
            raise Exception("No chunks generated")

        # Embed
        texts = [t for t, _ in text_chunks]
        batch_size = 64
        all_vectors = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            vecs = await loop.run_in_executor(None, embed_documents, batch)
            all_vectors.extend(vecs)

        # Store
        from app.services.vector_store import add_chunks
        pages_list = [m["page"] for _, m in text_chunks]
        indices_list = [m["chunk_index"] for _, m in text_chunks]
        add_chunks(texts, all_vectors, user_id, doc_id, pages_list, indices_list)

        # Update document
        with engine.begin() as conn:
            update_sql = "UPDATE documents SET status = 'completed', chunk_count = :count"
            update_params: dict = {"id": doc_id, "count": len(text_chunks)}
            if summary:
                update_sql += ", summary = :summary"
                update_params["summary"] = summary
            update_sql += " WHERE id = :id"
            conn.execute(sqltext(update_sql), update_params)

        logger.info(f"Ingestion completed for {doc_id}: {len(text_chunks)} chunks")

    except Exception as e:
        logger.error(f"Ingestion failed for {doc_id}: {e}")
        with engine.begin() as conn:
            conn.execute(
                sqltext(
                    "UPDATE documents SET status = 'failed', meta = jsonb_build_object('error', :err) WHERE id = :id"
                ),
                {"id": doc_id, "err": str(e)},
            )


@router.get("/documents")
async def list_documents(request: Request, query: Optional[str] = None, semantic_query: Optional[str] = None):
    user_id = await require_user(request)

    relevant_ids = None
    if semantic_query:
        from app.services.embedding_cache import get_query_embedding

        qvec = get_query_embedding(semantic_query)
        qvec_str = "[" + ",".join(map(str, qvec)) + "]"

        with engine.begin() as conn:
            sql = sqltext("""
                SELECT document_id, MAX(1 - (embedding <=> CAST(:qvec AS vector))) as max_score
                FROM chunks
                WHERE 1 - (embedding <=> CAST(:qvec AS vector)) > 0.15
                GROUP BY document_id
                ORDER BY max_score DESC
                LIMIT 50
            """)
            rows = conn.execute(sql, {"qvec": qvec_str}).fetchall()
            relevant_ids = [str(r[0]) for r in rows]
            if not relevant_ids:
                return {"items": []}

    with engine.begin() as conn:
        base_sql = "SELECT id, title, mime, status, summary, chunk_count FROM documents"
        params: dict = {}

        if relevant_ids is not None:
            base_sql += " WHERE id = ANY(:ids)"
            params["ids"] = relevant_ids
        else:
            base_sql += " ORDER BY uploaded_at DESC"

        rows = conn.execute(sqltext(base_sql), params).mappings().all()

    items = [{
        "id": str(r["id"]),
        "title": r["title"],
        "mime": r["mime"],
        "status": r["status"],
        "summary": r.get("summary"),
        "chunk_count": r.get("chunk_count", 0),
    } for r in rows]

    if query:
        ql = query.lower()
        items = [i for i in items if ql in i["title"].lower()]

    if relevant_ids:
        order_map = {rid: i for i, rid in enumerate(relevant_ids)}
        items.sort(key=lambda x: order_map.get(x["id"], 999))

    return {"items": items}


@router.get("/document/{doc_id}/preview")
async def preview(request: Request, doc_id: str):
    await require_user(request)
    with engine.begin() as conn:
        row = conn.execute(
            sqltext("SELECT storage_path FROM documents WHERE id=:id"), {"id": doc_id}
        ).first()
    if not row:
        raise HTTPException(404, "Document not found")
    url = create_signed_url(row[0], 600)
    return {"signed_url": url}
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/routes/
git commit -m "feat: add document routes with contextual enrichment and auto-summary"
```

---

## Task 16: API Routes — Chat (SSE Streaming)

**Files:**
- Create: `backend/app/api/routes/chat.py`

- [ ] **Step 1: Write chat.py**

Create `backend/app/api/routes/chat.py`:
```python
"""Chat routes with SSE streaming and thread management."""

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import insert, text as sqltext

from app.config.settings import get_settings
from app.db.engine import engine
from app.db.models import threads, messages
from app.api.dependencies import require_user
from app.core.guardrails.input_validator import validate_input
from app.core.workflow.corrective_flow import run_corrective_rag
from app.core.rag.generator import stream_answer
from app.core.rag.retriever import retrieve_documents
from app.core.rag.grader import grade_documents

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatBody(BaseModel):
    message: str
    document_ids: list[str] | None = None
    thread_id: str | None = None

    class Config:
        str_strip_whitespace = True


# --- Thread management ---

def create_thread(user_id: str) -> str:
    with engine.begin() as conn:
        thread_id = conn.execute(
            insert(threads).values(user_id=user_id).returning(threads.c.id)
        ).scalar_one()
    return str(thread_id)


def validate_thread_ownership(thread_id: str, user_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            sqltext("SELECT user_id FROM threads WHERE id = :thread_id"),
            {"thread_id": thread_id},
        ).first()
    if not result:
        return False
    return str(result[0]) == user_id


def get_thread_history(thread_id: str, user_id: str, limit: int = 20) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            sqltext("""
                SELECT m.role, m.content, m.meta, m.created_at
                FROM messages m
                JOIN threads t ON m.thread_id = t.id
                WHERE m.thread_id = :thread_id AND t.user_id = :user_id
                ORDER BY m.created_at ASC
                LIMIT :limit
            """),
            {"thread_id": thread_id, "user_id": user_id, "limit": limit},
        ).mappings().all()

    return [
        {
            "role": r["role"],
            "content": r["content"],
            "citations": r["meta"].get("citations") if r["meta"] else None,
        }
        for r in rows
    ]


def save_message(thread_id: str, role: str, content: str, citations: list | None = None):
    meta = json.dumps({"citations": citations}) if citations else None
    with engine.begin() as conn:
        conn.execute(
            sqltext("""
                INSERT INTO messages (id, thread_id, role, content, meta, created_at)
                VALUES (gen_random_uuid(), :thread_id, :role, :content, :meta, NOW())
            """),
            {"thread_id": thread_id, "role": role, "content": content, "meta": meta},
        )


# --- Chat endpoint (SSE streaming) ---

@router.post("/chat")
async def chat(request: Request, body: ChatBody):
    user_id = await require_user(request)

    # Input validation
    is_valid, reason = validate_input(body.message)
    if not is_valid:
        raise HTTPException(400, reason)

    # Thread management
    thread_id = body.thread_id
    if thread_id:
        if not validate_thread_ownership(thread_id, user_id):
            raise HTTPException(403, "Thread does not belong to this user")
    else:
        thread_id = create_thread(user_id)

    history = get_thread_history(thread_id, user_id)

    # Save user message
    save_message(thread_id, "user", body.message)

    async def generate_sse() -> AsyncGenerator[str, None]:
        """Generate SSE stream with workflow steps + streamed answer."""
        full_answer = ""
        citations = []

        try:
            # Step 1: Retrieve
            yield _sse("workflow", [{"step": "retrieve", "status": "in_progress", "details": "Searching documents..."}])

            documents = retrieve_documents(
                question=body.message,
                document_ids=body.document_ids,
                top_k=5,
            )

            workflow = [{"step": "retrieve", "status": "completed", "details": f"Found {len(documents)} chunks"}]
            yield _sse("workflow", workflow)

            # Step 2: Grade
            workflow.append({"step": "grade", "status": "in_progress", "details": "Analyzing relevance..."})
            yield _sse("workflow", workflow)

            filtered_docs, needs_web = grade_documents(documents)

            workflow[-1] = {
                "step": "grade",
                "status": "completed",
                "details": f"Kept {len(filtered_docs)}/{len(documents)} documents",
            }
            yield _sse("workflow", workflow)

            # Send sources
            if filtered_docs:
                sources = [
                    {
                        "document_id": d["document_id"],
                        "document_title": d["document_title"],
                        "page": d["page"],
                        "snippet": d["snippet"][:200],
                    }
                    for d in filtered_docs
                    if d.get("document_id") != "web"
                ]
                citations = sources
                yield _sse("sources", sources)

            # Step 3: Generate (streaming)
            workflow.append({"step": "generate", "status": "in_progress", "details": "Generating answer..."})
            yield _sse("workflow", workflow)

            for token in stream_answer(
                question=body.message,
                documents=filtered_docs,
                history=history,
            ):
                full_answer += token
                yield _sse("chunk", token)

            workflow[-1] = {"step": "generate", "status": "completed", "details": "Done"}
            yield _sse("workflow", workflow)

            # Done
            yield _sse("done", {"thread_id": thread_id})

        except Exception as e:
            logger.error(f"SSE generation error: {e}")
            yield _sse("error", {"message": str(e)})

        finally:
            # Save assistant message
            if full_answer:
                save_message(thread_id, "assistant", full_answer, citations)

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/threads")
async def list_threads(request: Request):
    """List user's conversation threads."""
    user_id = await require_user(request)

    with engine.begin() as conn:
        rows = conn.execute(
            sqltext("""
                SELECT t.id, t.title, t.updated_at,
                       (SELECT content FROM messages WHERE thread_id = t.id ORDER BY created_at ASC LIMIT 1) as first_message
                FROM threads t
                WHERE t.user_id = :user_id
                ORDER BY t.updated_at DESC
                LIMIT 50
            """),
            {"user_id": user_id},
        ).mappings().all()

    return {
        "threads": [
            {
                "id": str(r["id"]),
                "title": r["title"] or (r["first_message"][:50] + "..." if r["first_message"] and len(r["first_message"]) > 50 else r["first_message"]),
                "updated_at": str(r["updated_at"]),
            }
            for r in rows
        ]
    }


@router.get("/threads/{thread_id}/messages")
async def get_messages(request: Request, thread_id: str):
    """Get messages for a thread."""
    user_id = await require_user(request)

    if not validate_thread_ownership(thread_id, user_id):
        raise HTTPException(403, "Thread does not belong to this user")

    history = get_thread_history(thread_id, user_id, limit=100)
    return {"messages": history, "thread_id": thread_id}


def _sse(event_type: str, data) -> str:
    """Format a Server-Sent Event."""
    return f"data: {json.dumps({'type': event_type, 'data': data})}\n\n"
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/routes/chat.py
git commit -m "feat: add chat routes with SSE streaming and thread management"
```

---

## Task 17: New main.py (App Factory)

**Files:**
- Rewrite: `backend/app/main.py`

- [ ] **Step 1: Rewrite main.py**

Replace the entire content of `backend/app/main.py` with:

```python
"""HubDocs API — FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title=settings.app_name)

    # CORS
    cors_origins = [o.strip() for o in settings.cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Include routers
    from app.api.routes.documents import router as documents_router
    from app.api.routes.chat import router as chat_router

    app.include_router(documents_router)
    app.include_router(chat_router)

    return app


app = create_app()
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/main.py
git commit -m "refactor: rewrite main.py as app factory (~35 lines, was 486)"
```

---

## Task 18: Clean Up Old Files

**Files:**
- Delete: `backend/app/agno_agent.py`
- Delete: `backend/app/rag.py`
- Delete: `backend/app/ingest.py`
- Delete: `backend/app/models.py`
- Delete: `backend/app/auth.py`
- Delete: `backend/app/observability.py`
- Delete: `backend/app/supabase_client.py`
- Delete: `backend/app/multimodal.py`

- [ ] **Step 1: Remove old files**

```bash
cd /Users/peugomes/projects/doing/group-documents
git rm backend/app/agno_agent.py backend/app/rag.py backend/app/ingest.py backend/app/models.py backend/app/auth.py backend/app/observability.py backend/app/supabase_client.py backend/app/multimodal.py
```

- [ ] **Step 2: Commit**

```bash
git commit -m "refactor: remove old files replaced by modular architecture"
```

---

## Task 19: Frontend — SSE Chat Hook

**Files:**
- Create: `frontend/lib/types.ts`
- Create: `frontend/hooks/useChatStream.ts`

- [ ] **Step 1: Write types.ts**

Create `frontend/lib/types.ts`:
```typescript
export interface Citation {
  document_id: string
  document_title: string
  page: number
  snippet: string
}

export interface WorkflowStep {
  step: string
  status: 'in_progress' | 'completed'
  details: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  timestamp: Date
}

export interface ThreadSummary {
  id: string
  title: string
  updated_at: string
}

export interface SSEEvent {
  type: 'workflow' | 'sources' | 'chunk' | 'done' | 'error'
  data: any
}
```

- [ ] **Step 2: Write useChatStream.ts**

Create `frontend/hooks/useChatStream.ts`:
```typescript
'use client'

import { useState, useCallback, useRef } from 'react'
import type { Message, Citation, WorkflowStep, SSEEvent } from '@/lib/types'

interface UseChatStreamOptions {
  getToken: () => Promise<string | undefined>
  documentIds?: string[]
  onError?: (error: string) => void
}

export function useChatStream({ getToken, documentIds, onError }: UseChatStreamOptions) {
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [threadId, setThreadId] = useState<string | null>(null)
  const [workflowSteps, setWorkflowSteps] = useState<WorkflowStep[]>([])
  const [currentCitations, setCurrentCitations] = useState<Citation[]>([])
  const abortControllerRef = useRef<AbortController | null>(null)

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim() || isLoading) return

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: content.trim(),
      timestamp: new Date(),
    }

    setMessages(prev => [...prev, userMessage])
    setIsLoading(true)
    setWorkflowSteps([])
    setCurrentCitations([])

    // Add placeholder assistant message for streaming
    const assistantId = crypto.randomUUID()
    setMessages(prev => [...prev, {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
    }])

    try {
      const token = await getToken()
      abortControllerRef.current = new AbortController()

      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: content.trim(),
          document_ids: documentIds,
          thread_id: threadId,
        }),
        signal: abortControllerRef.current.signal,
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to send message')
      }

      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''
      let finalCitations: Citation[] = []

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const jsonStr = line.slice(6)
          if (!jsonStr.trim()) continue

          try {
            const event: SSEEvent = JSON.parse(jsonStr)

            switch (event.type) {
              case 'workflow':
                setWorkflowSteps(event.data)
                break

              case 'sources':
                finalCitations = event.data
                setCurrentCitations(event.data)
                break

              case 'chunk':
                setMessages(prev =>
                  prev.map(m =>
                    m.id === assistantId
                      ? { ...m, content: m.content + event.data }
                      : m
                  )
                )
                break

              case 'done':
                if (event.data.thread_id && !threadId) {
                  setThreadId(event.data.thread_id)
                }
                // Attach citations to final message
                setMessages(prev =>
                  prev.map(m =>
                    m.id === assistantId
                      ? { ...m, citations: finalCitations }
                      : m
                  )
                )
                break

              case 'error':
                onError?.(event.data.message || 'An error occurred')
                break
            }
          } catch {
            // skip malformed JSON
          }
        }
      }
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        return
      }
      const errorMessage = error instanceof Error ? error.message : 'Something went wrong'
      onError?.(errorMessage)
      // Remove empty assistant message on error
      setMessages(prev => prev.filter(m => m.id !== assistantId || m.content))
    } finally {
      setIsLoading(false)
      abortControllerRef.current = null
    }
  }, [getToken, documentIds, threadId, isLoading, onError])

  const resetChat = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    setMessages([])
    setThreadId(null)
    setIsLoading(false)
    setWorkflowSteps([])
    setCurrentCitations([])
  }, [])

  const stopGeneration = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      setIsLoading(false)
    }
  }, [])

  return {
    messages,
    isLoading,
    threadId,
    workflowSteps,
    currentCitations,
    sendMessage,
    resetChat,
    stopGeneration,
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/types.ts frontend/hooks/useChatStream.ts
git commit -m "feat: add SSE streaming chat hook with workflow step tracking"
```

---

## Task 20: Frontend — Update Chat Page to Use Streaming

**Files:**
- Modify: `frontend/app/chat/page.tsx`

- [ ] **Step 1: Update chat page imports and hook**

In `frontend/app/chat/page.tsx`, replace the `useChat` import and usage with `useChatStream`:

Replace:
```typescript
import { useChat } from '@/hooks/useChat'
```
with:
```typescript
import { useChatStream } from '@/hooks/useChatStream'
```

Replace:
```typescript
import type { Citation } from '@/hooks/useChat'
```
with:
```typescript
import type { Citation } from '@/lib/types'
```

Replace the hook call:
```typescript
  const {
    messages,
    isLoading: isSending,
    sendMessage,
    resetChat,
    stopGeneration,
  } = useChat({
    getToken: async () => (await supabase.auth.getSession()).data.session?.access_token,
    documentIds: documentIds.length > 0 ? documentIds : undefined,
    onError: setError,
  })
```
with:
```typescript
  const {
    messages,
    isLoading: isSending,
    workflowSteps,
    sendMessage,
    resetChat,
    stopGeneration,
  } = useChatStream({
    getToken: async () => (await supabase.auth.getSession()).data.session?.access_token,
    documentIds: documentIds.length > 0 ? documentIds : undefined,
    onError: setError,
  })
```

- [ ] **Step 2: Add workflow steps indicator**

In the same file, add a workflow steps display above the chat input. After the `{isSending && <ThinkingMessage key="thinking" />}` line, add:

```tsx
              {isSending && workflowSteps.length > 0 && (
                <motion.div
                  key="workflow"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="px-4 py-2"
                >
                  <div className="flex flex-col gap-1 text-xs text-zinc-500">
                    {workflowSteps.map((step, i) => (
                      <div key={i} className="flex items-center gap-2">
                        {step.status === 'in_progress' ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <div className="h-3 w-3 rounded-full bg-emerald-500" />
                        )}
                        <span>{step.details}</span>
                      </div>
                    ))}
                  </div>
                </motion.div>
              )}
```

Add `Loader2` to the lucide-react imports if not already present.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/chat/page.tsx
git commit -m "feat: update chat page to use SSE streaming with workflow steps"
```

---

## Task 21: Frontend — Update API Proxy for SSE

**Files:**
- Modify: `frontend/lib/api-proxy.ts` (if using Next.js API route)

- [ ] **Step 1: Check current proxy behavior**

Read `frontend/lib/api-proxy.ts`. If the `/api/chat` route proxies to the backend, it needs to support streaming. The simplest approach: make the frontend call the backend directly (via `NEXT_PUBLIC_API_URL`) for the chat endpoint, bypassing the proxy.

If the frontend already calls the backend directly (check `fetch('/api/chat')` in the hook), then the Next.js API route at `app/api/chat/route.ts` needs to stream the response through.

Create or update `frontend/app/api/chat/route.ts`:

```typescript
import { NextRequest } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function POST(request: NextRequest) {
  const body = await request.json()
  const authorization = request.headers.get('Authorization') || ''

  const response = await fetch(`${API_URL}/chat`, {
    method: 'POST',
    headers: {
      'Authorization': authorization,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })

  // Stream the response through
  return new Response(response.body, {
    status: response.status,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    },
  })
}
```

- [ ] **Step 2: Add threads API route**

Create `frontend/app/api/threads/route.ts`:

```typescript
import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function GET(request: NextRequest) {
  const authorization = request.headers.get('Authorization') || ''

  const response = await fetch(`${API_URL}/threads`, {
    headers: { 'Authorization': authorization },
  })

  const data = await response.json()
  return NextResponse.json(data, { status: response.status })
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app/api/
git commit -m "feat: update API proxy to support SSE streaming for chat"
```

---

## Task 22: Backend Smoke Test

- [ ] **Step 1: Verify all imports resolve**

Run:
```bash
cd /Users/peugomes/projects/doing/group-documents/backend
source .venv/bin/activate
python -c "from app.main import app; print('OK: app created')"
```

Expected: `OK: app created`

If import errors occur, fix the specific import paths.

- [ ] **Step 2: Verify uvicorn starts**

Run:
```bash
cd /Users/peugomes/projects/doing/group-documents/backend
source .venv/bin/activate
timeout 5 uvicorn app.main:app --port 8000 2>&1 || true
```

Expected: Server starts (may fail on missing env vars, but should not crash on import errors).

- [ ] **Step 3: Fix any issues found and commit**

```bash
git add -A
git commit -m "fix: resolve import issues from refactoring"
```

---

## Task 23: Frontend Build Verification

- [ ] **Step 1: Install dependencies**

Run:
```bash
cd /Users/peugomes/projects/doing/group-documents/frontend
npm install
```

- [ ] **Step 2: Verify build**

Run:
```bash
cd /Users/peugomes/projects/doing/group-documents/frontend
npm run build
```

Expected: Build completes without TypeScript errors.

- [ ] **Step 3: Fix any issues found and commit**

```bash
git add -A
git commit -m "fix: resolve frontend build issues"
```

---

## Summary of Tasks

| Task | Description | P |
|------|------------|---|
| 1 | Database schema migration (tsvector, semantic_cache, HNSW tuning) | P0 |
| 2 | Update Python dependencies | P0 |
| 3 | Centralized Pydantic Settings | P0 |
| 4 | Database engine + extended models | P0 |
| 5 | Voyage embedding service | P0 |
| 6 | Redis embedding cache | P0 |
| 7 | Hybrid search vector store (semantic + keyword + RRF) | P0 |
| 8 | PDF processor + semantic chunker + contextual enrichment | P0 |
| 9 | Reranker, grader, query transformer | P0 |
| 10 | Multi-query retriever | P0 |
| 11 | Claude Sonnet generator (streaming) | P0 |
| 12 | LangGraph Corrective RAG workflow | P0 |
| 13 | Input guardrails | P1 |
| 14 | Auth dependency + move files | P0 |
| 15 | Document API routes | P0 |
| 16 | Chat API routes (SSE streaming) | P0 |
| 17 | Rewrite main.py as app factory | P0 |
| 18 | Clean up old files | P0 |
| 19 | Frontend SSE chat hook + types | P0 |
| 20 | Update chat page for streaming | P0 |
| 21 | Update API proxy for SSE | P0 |
| 22 | Backend smoke test | P0 |
| 23 | Frontend build verification | P0 |

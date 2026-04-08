# RAG Pipeline Upgrade — State-of-the-Art 2026

**Date**: 2026-04-07
**Status**: Approved
**Scope**: Backend RAG refactor + Frontend streaming + Database schema evolution

---

## 1. Overview

Upgrade the group-documents RAG pipeline from first-generation (vector-only search, no reranking, basic chunking) to a production-grade Corrective RAG system using the latest techniques as of April 2026.

### Stack

| Layer | Technology |
|-------|-----------|
| **LLM Generation** | Claude Sonnet 4 (Anthropic) |
| **Fast LLM** | Claude Haiku 4.5 (enrichment, multi-query, grading) |
| **Embeddings** | Voyage 4 Large (docs) + Voyage 4 Lite (queries) |
| **Reranking** | Cohere Rerank v3.5 |
| **Orchestration** | LangGraph (replaces Agno) |
| **Vector DB** | PostgreSQL pgvector (HNSW, cosine) |
| **Full-text** | PostgreSQL tsvector (GIN index) |
| **Cache** | Redis (embedding cache + semantic cache) |
| **Web Search** | Tavily (fallback) |
| **Auth** | Supabase JWT (unchanged) |
| **Storage** | Supabase Storage (unchanged) |
| **Frontend** | Next.js 14, React 18, Tailwind (unchanged) |

---

## 2. Ingestion Pipeline

```
PDF Upload → Supabase Storage → Streaming Download (20MB max)
  → PDF Text Extraction (pypdf, per-page)
  → Semantic Chunking
     - 500 tokens target, 100 overlap
     - tiktoken for precise token counting
     - Context-aware boundaries (cosine distance between sentences)
     - Metadata: page, chunk_index, document_id
  → Contextual Enrichment (Anthropic technique)
     - Claude Haiku generates 50-100 tokens of context per chunk
     - Prepended to chunk before embedding
     - Uses prompt caching for efficiency
  → Embedding (Voyage 4 Large, 1536-dim)
     - Batch of 64
  → Store in pgvector + auto-generate tsvector
  → Auto-summary (Claude Haiku summarizes full document → documents.summary)
```

### Contextual Enrichment Detail

For each chunk, Claude Haiku receives the full document text (cached) and generates context like:
"This chunk is from Section 3 (Payment Terms) of the Lease Agreement between Company X and Company Y, discussing monthly payment deadlines."

This context is prepended to the chunk text before embedding. Result: 35-67% improvement in retrieval quality (per Anthropic's published benchmarks).

---

## 3. Query Pipeline (LangGraph State Machine)

### Graph State

```python
class GraphState(TypedDict):
    question: str
    original_question: str
    document_ids: Optional[List[str]]
    user_id: str
    documents: List[SourceDocument]
    answer: str
    workflow: List[WorkflowStep]
    needs_web_search: bool
    used_web_search: bool
    was_corrected: bool
```

### Nodes

1. **RETRIEVE** — Multi-query generation (3 variants via Haiku) → Hybrid search (semantic + keyword) per variant → RRF fusion (k=60) → Return top_k * 3 candidates
2. **RERANK** — Cohere cross-encoder reranks candidates → Return top_k (5)
3. **GRADE** — Score threshold filtering (0.7). If >50% filtered → set needs_web_search flag. Safety net: always keep top 2.
4. **TRANSFORM** — Claude Haiku rewrites query for better retrieval (only if grading failed)
5. **WEB_SEARCH** — Tavily API (only if grading failed)
6. **GENERATE** — Claude Sonnet 4 generates answer with streaming SSE. Citations inline.

### Flow

```
RETRIEVE → RERANK → GRADE → [docs_ok?]
                                ├─ yes → GENERATE
                                └─ no  → TRANSFORM → WEB_SEARCH → GENERATE
```

### Semantic Cache

Before entering the pipeline, check semantic_cache table:
- Embed query with Voyage Lite
- Search cached queries with cosine similarity >= 0.85
- If HIT and same document_ids scope → return cached response
- If MISS → run full pipeline → cache result (1h TTL)

---

## 4. Database Schema Changes

### Alter `chunks` table

```sql
ALTER TABLE chunks ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;

CREATE INDEX chunks_search_vector_idx ON chunks USING gin(search_vector);

DROP INDEX IF EXISTS chunks_embedding_idx;
CREATE INDEX chunks_embedding_idx ON chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE INDEX chunks_doc_chunk_idx ON chunks (document_id, chunk_index);
```

### Alter `documents` table

```sql
ALTER TABLE documents ADD COLUMN summary text;
ALTER TABLE documents ADD COLUMN chunk_count integer DEFAULT 0;
```

### New `semantic_cache` table

```sql
CREATE TABLE semantic_cache (
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

CREATE INDEX semantic_cache_embedding_idx
  ON semantic_cache USING hnsw (query_embedding vector_cosine_ops);
CREATE INDEX semantic_cache_expires_idx ON semantic_cache (expires_at);
```

### Add trigger for `threads.updated_at`

```sql
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

CREATE TRIGGER threads_updated_at
  BEFORE UPDATE ON threads FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

---

## 5. Backend Structure

```
backend/app/
├── main.py                          # App factory (~40 lines)
├── config/
│   └── settings.py                  # Pydantic BaseSettings
├── api/
│   ├── routes/
│   │   ├── documents.py             # /ingest, /documents, /document/{id}/preview
│   │   └── chat.py                  # /chat (SSE), /threads
│   └── dependencies.py              # require_user(), get_db()
├── core/
│   ├── rag/
│   │   ├── retriever.py             # multi-query → hybrid → rerank
│   │   ├── reranker.py              # Cohere v3.5
│   │   ├── grader.py                # Score threshold
│   │   ├── transformer.py           # Query rewrite (Haiku)
│   │   └── generator.py             # Answer generation (Sonnet, streaming)
│   ├── workflow/
│   │   └── corrective_flow.py       # LangGraph state machine
│   ├── ingestion/
│   │   ├── chunker.py               # Semantic chunking + contextual enrichment
│   │   ├── pdf_processor.py         # PDF text extraction
│   │   └── multimodal.py            # Gemini (images, audio, video)
│   └── guardrails/
│       └── input_validator.py       # Injection detection
├── services/
│   ├── vector_store.py              # pgvector: add, hybrid_search
│   ├── embedding.py                 # Voyage 4 (doc + query)
│   ├── embedding_cache.py           # Redis embedding cache
│   ├── semantic_cache.py            # Query→response cache
│   ├── web_search.py                # Tavily
│   └── supabase_client.py           # Storage, signed URLs
├── db/
│   ├── models.py                    # SQLAlchemy models
│   └── engine.py                    # Engine + session
└── middleware/
    └── rate_limit.py                # Redis sliding window
```

### Dependencies

```
# Core
fastapi==0.115.0
uvicorn[standard]==0.30.0
sqlalchemy==2.0.32
psycopg2-binary==2.9.9
pgvector==0.2.5
pydantic==2.8.2
pyjwt==2.9.0
python-dotenv==1.0.1
httpx>=0.28

# LLM & RAG
anthropic>=0.40.0
langchain-anthropic>=0.3.0
langchain-core>=0.3.0
langchain-cohere>=0.3.0
langgraph>=0.2.0
voyageai>=0.3.0
cohere>=5.0
tiktoken>=0.7.0

# Cache & Search
redis>=5.0
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

---

## 6. Frontend Changes

### Chat Streaming (SSE)

Refactor `useChat` hook to consume Server-Sent Events:

**SSE Message Types:**
- `workflow` — pipeline step updates (retrieve, rerank, grade, generate)
- `sources` — citation documents
- `chunk` — streamed LLM response tokens
- `done` — completion with metadata

**UI:** Progress steps visible during generation, tokens appear progressively, citations rendered after completion.

### Chat History Sidebar

- List user's threads (GET /threads)
- Resume thread on click (loads messages)
- "New Chat" button
- Grouped by date (Today, Yesterday, This Week)

### Auth Context

- Create `AuthProvider` context wrapping the app
- Eliminates prop drilling of `getToken`
- Exposes `user`, `session`, `getToken` via hook

### TypeScript Improvements

- `strict: true` in tsconfig
- Typed interfaces for all API responses
- Eliminate `useState<any>`

---

## 7. Graceful Degradation

| Service | If Unavailable |
|---------|---------------|
| Cohere | Skip reranking, use hybrid search ranking |
| Redis | Skip caches, compute everything on-the-fly |
| Tavily | Skip web search, answer "not found in documents" |
| Gemini | Skip multimodal, accept only text PDFs |
| Voyage | Fallback to OpenAI text-embedding-3-small (if configured) |

---

## 8. Expected Results

| Metric | Current | After |
|--------|---------|-------|
| Retrieval precision | ~40% | ~85% |
| API cost per query | baseline | -40% (caching) |
| Perceived latency | 3-5s (full response) | <1s (first token via streaming) |
| Chunk context quality | raw text | +35-67% (contextual enrichment) |
| Keyword match recall | 0% (vector-only) | full (hybrid search) |

---

## 9. Out of Scope

- Workspaces/Teams multi-tenancy
- Granular permissions
- Billing (Stripe)
- SSO (SAML/OIDC)
- Graph RAG
- ColBERT/late interaction
- Landing page / marketing site

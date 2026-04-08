# BrainHub Team

A collaborative document Q&A platform for teams. Upload PDFs, images, audio, and video files into a shared workspace. The system extracts content, generates vector embeddings, and enables AI-powered question answering with automatic source citations -- powered by a Corrective RAG pipeline with hybrid search, cross-encoder reranking, and real-time streaming.

Built for small-to-medium teams (5-50 people) that need a shared knowledge base where every member can ask questions about any document and get precise, cited answers.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **LLM** | Claude Sonnet 4 (generation), Claude Haiku 4.5 (enrichment, multi-query, transform) |
| **Embeddings** | Voyage 4 Large (documents), Voyage 4 Lite (queries) |
| **Reranking** | Cohere Rerank v3.5 cross-encoder |
| **Orchestration** | LangGraph (Corrective RAG state machine) |
| **Backend** | FastAPI, SQLAlchemy, Python 3.12 |
| **Database** | PostgreSQL + pgvector (HNSW), tsvector (GIN) via Supabase |
| **Cache** | Redis (embedding cache, 1h TTL) |
| **Frontend** | Next.js 14 (App Router), React 18, TypeScript, Tailwind CSS |
| **Auth** | Supabase Auth (email/password + Google OAuth) |
| **Storage** | Supabase Storage (file uploads) |
| **Multimodal** | Google Gemini (image, audio, video processing) |
| **Web Search** | Tavily (fallback when documents are insufficient) |

## How It Works

### RAG Pipeline Algorithm

The system implements a **Corrective RAG** architecture -- a multi-stage retrieval pipeline that self-corrects when initial results are poor.

**Ingestion (upload time):**

1. User uploads a file to Supabase Storage
2. Backend downloads the file and extracts text (pypdf for PDFs, Gemini for images/audio/video)
3. Text is split into chunks using sentence-boundary splitting with tiktoken-based token counting (500 tokens, 100 overlap)
4. Each chunk is enriched using Anthropic's **Contextual Retrieval** technique: Claude Haiku reads the full document and generates 2-3 sentences of context per chunk (e.g., "This section discusses payment terms from the lease agreement between Company X and Y"). This context is prepended to the chunk before embedding. Published benchmarks show 35-67% improvement in retrieval quality.
5. Enriched chunks are embedded using Voyage 4 Large (1536 dimensions, optimized for documents)
6. Chunks + vectors are stored in PostgreSQL with pgvector. A tsvector column is auto-generated for full-text keyword search.
7. Claude Haiku generates a 2-3 sentence summary of the full document.

**Query (chat time):**

1. **Input validation** -- heuristic prompt injection detection (11 regex patterns)
2. **Multi-query generation** -- Claude Haiku generates 3 query variants approaching the question from different angles (synonyms, related concepts, specific aspects)
3. **Hybrid search** -- for each query variant:
   - Semantic search: pgvector HNSW index with cosine distance
   - Keyword search: PostgreSQL tsvector with GIN index and `ts_rank`
   - Results merged using **Reciprocal Rank Fusion** (RRF, k=60)
4. **Deduplication** -- results across all query variants are merged, keeping the best score per chunk
5. **Reranking** -- Cohere cross-encoder reranks the top candidates (top_k * 3 candidates narrowed to top_k). Cross-encoders are 3-5x more precise than bi-encoder similarity alone.
6. **Grading** -- score threshold filtering (0.7). If more than 50% of documents are filtered out, the pipeline triggers self-correction:
   - **Query transformation** -- Claude Haiku rewrites the question for better retrieval
   - **Web search fallback** -- Tavily searches the web for supplementary information
7. **Generation** -- Claude Sonnet 4 generates the answer from retrieved context, streamed via SSE with real-time workflow step indicators

```
RETRIEVE (multi-query + hybrid) --> RERANK (Cohere) --> GRADE (threshold)
                                                            |
                                              docs OK?  ----+---- docs poor?
                                                |                    |
                                            GENERATE             TRANSFORM (rewrite query)
                                          (Claude Sonnet)            |
                                                               WEB SEARCH (Tavily)
                                                                     |
                                                                 GENERATE
```

### Why Hybrid Search + Reranking

Vector-only search misses exact keyword matches (error codes, product names, legal terms). Keyword-only search misses semantic synonyms. Hybrid search with RRF catches both. The cross-encoder reranker then re-scores candidates with full query-document attention -- it can only reorder what was retrieved, so the hybrid stage ensures nothing important is missed.

### Why Contextual Enrichment

Raw text chunks lose context. A chunk saying "the deadline is 30 days" is ambiguous without knowing which contract or section it belongs to. By prepending contextual information before embedding, the vector representation captures both the content and its position within the document.

## Architecture

```
backend/app/
|-- main.py                          # FastAPI app factory (35 lines)
|-- config/
|   +-- settings.py                  # Pydantic BaseSettings (all env vars)
|-- api/
|   |-- dependencies.py              # JWT auth middleware
|   +-- routes/
|       |-- documents.py             # /ingest, /documents, /document/{id}/preview
|       +-- chat.py                  # /chat (SSE), /threads, /threads/{id}/messages
|-- core/
|   |-- ingestion/
|   |   |-- pdf_processor.py         # pypdf text extraction
|   |   |-- chunker.py              # Semantic chunking + contextual enrichment
|   |   +-- multimodal.py           # Gemini image/audio/video processing
|   |-- rag/
|   |   |-- retriever.py            # Multi-query -> hybrid search -> rerank
|   |   |-- reranker.py             # Cohere cross-encoder
|   |   |-- grader.py               # Score threshold filtering
|   |   |-- transformer.py          # Query rewriting (Claude Haiku)
|   |   +-- generator.py            # Answer generation (Claude Sonnet, streaming)
|   |-- workflow/
|   |   +-- corrective_flow.py      # LangGraph state machine (unused in SSE path)
|   +-- guardrails/
|       +-- input_validator.py       # Prompt injection detection
|-- services/
|   |-- embedding.py                 # Voyage 4 (doc + query models)
|   |-- embedding_cache.py           # Redis cache with SHA256 keys
|   |-- vector_store.py              # Hybrid search (pgvector + tsvector + RRF)
|   +-- supabase_client.py           # Storage signed URLs
+-- db/
    |-- engine.py                    # SQLAlchemy engine
    +-- models.py                    # Table definitions

frontend/
|-- app/
|   |-- page.tsx                     # Auth + KnowledgeHub
|   |-- chat/page.tsx                # Chat with SSE streaming + history sidebar
|   +-- api/                         # Proxy routes to backend
|       |-- chat/route.ts            # SSE passthrough
|       |-- threads/route.ts         # Thread listing
|       +-- threads/[id]/messages/   # Thread message history
|-- components/
|   |-- KnowledgeHub.tsx             # Document grid, upload, semantic search
|   +-- chat/
|       |-- ChatHistory.tsx          # Conversation history sidebar
|       |-- ChatMessage.tsx          # Message with markdown + citations
|       |-- ChatInput.tsx            # Auto-expanding textarea
|       +-- ChatHeader.tsx           # Navigation
|-- hooks/
|   |-- useChatStream.ts            # SSE streaming with workflow steps
|   +-- useDebounce.ts              # Search debounce
+-- lib/
    |-- types.ts                     # TypeScript interfaces
    +-- api-proxy.ts                 # Proxy helper (SSE-aware)
```

### Database Schema

Four tables with Row Level Security:

| Table | Scope | Purpose |
|-------|-------|---------|
| `documents` | Shared (BrainHub Team) | File metadata, status, auto-generated summary, chunk count |
| `chunks` | Shared (BrainHub Team) | Text chunks with pgvector embeddings (1536d) + auto-generated tsvector |
| `threads` | Private per user | Chat conversations |
| `messages` | Private per user | Messages with role, content, citations in JSONB |

Additional table:
- `semantic_cache` -- query-to-response cache with vector similarity matching (unused, reserved for future)

Indexes:
- HNSW on `chunks.embedding` (vector cosine ops, m=16, ef_construction=64)
- GIN on `chunks.search_vector` (full-text)
- B-tree on user_id, document_id, status, uploaded_at, thread_id, created_at

## Data Flow

```mermaid
flowchart TD
    subgraph Ingestion
        A[User uploads file] --> B[Supabase Storage]
        B --> C[POST /ingest]
        C --> D[Background task]
        D --> E{File type}
        E -->|PDF| F[pypdf per page]
        E -->|Image/Audio/Video| G[Gemini processing]
        F --> H[Semantic chunking - 500 tokens, 100 overlap]
        G --> H
        H --> I[Contextual enrichment - Claude Haiku]
        I --> J[Voyage 4 Large embedding]
        J --> K[Store in PostgreSQL + pgvector]
        K --> L[Generate summary - Claude Haiku]
    end

    subgraph Query
        M[User sends message] --> N[Input validation]
        N --> O[Multi-query generation - 3 variants]
        O --> P[Hybrid search per variant]
        P --> Q[Semantic: pgvector HNSW]
        P --> R[Keyword: tsvector GIN]
        Q --> S[RRF fusion - k=60]
        R --> S
        S --> T[Cohere reranking]
        T --> U{Score grading}
        U -->|OK| V[Claude Sonnet generation]
        U -->|Poor| W[Query transform + Web search]
        W --> V
        V --> X[SSE stream to frontend]
    end
```

## Use Cases

**Legal teams** -- upload contracts, policies, and agreements. Ask "What are the termination clauses across all our vendor contracts?" and get cited answers referencing specific documents and page numbers.

**Product teams** -- upload PRDs, research reports, and user feedback. Ask "What did users say about the onboarding flow?" across dozens of research documents.

**Engineering teams** -- upload architecture docs, RFCs, and runbooks. Ask "How does the authentication system handle token refresh?" and get answers from your own documentation.

**Consulting firms** -- upload client deliverables and industry reports. New team members can onboard by asking questions about project history.

**Academic research groups** -- upload papers and datasets. Ask "What methods have been used for X?" across your reading list.

## Setup

### Prerequisites

- Python 3.12+
- Node.js 18+
- Supabase project (free tier works)
- Anthropic API key (Claude)
- Voyage AI API key (embeddings)
- Optional: Cohere API key (reranking), Redis, Tavily API key (web search), Google API key (multimodal)

### 1. Database

Run both SQL files in the Supabase SQL Editor, in order:

```bash
sql/schema_complete.sql          # Base tables, indexes, RLS policies
sql/migration_rag_upgrade.sql    # tsvector column, HNSW tuning, semantic_cache table
```

Create a `docs` bucket in Supabase Storage (private) with RLS policies for authenticated users.

### 2. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `backend/.env`:

```
# Required
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
SUPABASE_JWT_SECRET=your-jwt-secret
SUPABASE_DB_URL=postgresql://postgres:password@db.xxx.supabase.co:5432/postgres
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...

# Optional (graceful degradation if missing)
COHERE_API_KEY=                  # Reranking (falls back to hybrid search ranking)
REDIS_URL=redis://localhost:6379 # Embedding cache (falls back to compute every time)
TAVILY_API_KEY=                  # Web search fallback (skipped if not set)
GOOGLE_API_KEY=                  # Multimodal processing (only PDFs work without it)

# Defaults (override if needed)
GENERATION_MODEL=claude-sonnet-4-20250514
FAST_MODEL=claude-haiku-4-5-20251001
VOYAGE_DOC_MODEL=voyage-3-large
VOYAGE_QUERY_MODEL=voyage-3-lite
CHUNK_SIZE=500
CHUNK_OVERLAP=100
RELEVANCE_THRESHOLD=0.7
CORS_ORIGINS=http://localhost:3000
```

### 3. Frontend

```bash
cd frontend
npm install
```

Create `frontend/.env.local`:

```
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 4. Run

```bash
# Terminal 1 - Backend
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000

# Terminal 2 - Frontend
cd frontend && npm run dev
```

Open `http://localhost:3000`.

### 5. Optional: Redis

```bash
# Docker
docker run -d --name redis-brainhub -p 6379:6379 redis:alpine

# Or install locally (macOS)
brew install redis && brew services start redis
```

Redis is optional. Without it, every query computes a fresh embedding instead of checking cache first. The application works identically, just with slightly higher API costs.

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/ingest` | JWT | Start document processing. Returns `{document_id, status: "pending"}`. |
| GET | `/documents` | JWT | List all documents. Optional `semantic_query` for vector-ranked results. Returns `{items: [...]}`. |
| GET | `/document/{id}/preview` | JWT | Get signed URL for file preview. |
| POST | `/chat` | JWT | Send message. Returns SSE stream with events: `workflow`, `sources`, `chunk`, `done`, `error`. |
| GET | `/threads` | JWT | List user's conversation threads. |
| GET | `/threads/{id}/messages` | JWT | Get messages for a thread. |

### SSE Event Types

The `/chat` endpoint returns a `text/event-stream` with these event types:

| Event | Data | When |
|-------|------|------|
| `workflow` | `WorkflowStep[]` | Each pipeline step starts/completes |
| `sources` | `Citation[]` | After grading, before generation |
| `chunk` | `string` | Each token from Claude Sonnet |
| `done` | `{thread_id: string}` | Generation complete |
| `error` | `{message: string}` | Pipeline error |

## Graceful Degradation

Every external service except Supabase and Anthropic is optional:

| Service | If Missing |
|---------|-----------|
| Cohere | Reranking skipped, uses hybrid search ranking directly |
| Redis | Embedding cache skipped, computes fresh every time |
| Tavily | Web search fallback skipped, answers from documents only |
| Google Gemini | Only PDFs with extractable text are supported |
| Voyage AI | Required -- no fallback (embeddings are core) |

## Key Design Decisions

**BrainHub Team model.** All documents shared, chat threads private. Enforced at both application and database (RLS) layers.

**Background ingestion.** POST /ingest returns 202 immediately. Processing runs in FastAPI BackgroundTasks. Frontend polls status every 3 seconds.

**SSE streaming with workflow steps.** Users see "Searching documents..." / "Analyzing relevance..." / "Generating answer..." in real time. Each token is streamed as it arrives from Claude.

**Contextual enrichment at ingestion, not query time.** The LLM call to generate context happens once per chunk during upload, not on every query. This is a one-time cost that improves every future search.

**Separate embedding models for documents and queries.** Voyage 4 Large for documents (higher quality, runs once at ingestion) and Voyage 4 Lite for queries (faster, cheaper, runs on every search). Both models share the same embedding space.

**Proxy architecture.** All frontend API routes proxy to the backend. This keeps the backend URL out of client-side code and avoids CORS issues in production.

**Score-based grading, not LLM-based.** Document relevance is determined by the reranker score (threshold 0.7), not by an additional LLM call. This saves cost and latency.

## Production Deployment Checklist

### Infrastructure

- [ ] PostgreSQL with pgvector extension (Supabase, or self-hosted PostgreSQL 15+ with `CREATE EXTENSION vector`)
- [ ] Redis instance (Upstash, AWS ElastiCache, or self-hosted)
- [ ] Backend hosting (Railway, Render, AWS ECS, or any Docker-capable platform)
- [ ] Frontend hosting (Vercel recommended for Next.js)

### Database

- [ ] Run `sql/schema_complete.sql` on production database
- [ ] Run `sql/migration_rag_upgrade.sql` on production database
- [ ] Create `docs` storage bucket with authenticated RLS policies
- [ ] Verify HNSW index is created: `SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'`

### Backend Deployment

- [ ] Set all required environment variables (see Setup section)
- [ ] Set `CORS_ORIGINS` to your frontend production URL
- [ ] Run with production ASGI server: `gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000`
- [ ] Configure health check endpoint (add `GET /health` returning 200)
- [ ] Set up persistent task queue (replace FastAPI BackgroundTasks with Celery + Redis for reliable ingestion)
- [ ] Add rate limiting middleware (Redis-based, currently defined but not wired)
- [ ] Configure logging to a centralized service (e.g., Datadog, Grafana)

### Frontend Deployment

- [ ] Set `NEXT_PUBLIC_API_URL` to backend production URL
- [ ] Set `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- [ ] Deploy to Vercel: `vercel --prod`
- [ ] Configure custom domain

### Security

- [ ] Verify all API keys are in environment variables, not in code
- [ ] Enable HTTPS on both frontend and backend
- [ ] Set Supabase JWT secret to a strong value (minimum 32 characters)
- [ ] Review RLS policies match your access model (BrainHub Team = shared docs, private threads)
- [ ] Set `CORS_ORIGINS` to exact production domain (not `*`)
- [ ] Consider adding rate limiting before production traffic

### Monitoring

- [ ] Set up Langfuse for RAG observability (`LANGFUSE_ENABLED=true`)
- [ ] Monitor embedding cache hit rate (Redis key pattern `emb:*`)
- [ ] Track document processing failures (query `documents WHERE status = 'failed'`)
- [ ] Set up alerts for API error rates

### Cost Optimization

- [ ] Enable Redis to cache embeddings (50-70% cost reduction on Voyage API)
- [ ] Monitor Cohere reranking usage (optional, can disable via `ENABLE_RERANKING=false`)
- [ ] Consider batch ingestion during off-peak hours for large document uploads
- [ ] Track token usage per model via Langfuse or Anthropic dashboard

## Supported File Types

| Type | Processing | Notes |
|------|-----------|-------|
| PDF | pypdf text extraction, page-aware chunking | Primary use case. Contextual enrichment applied. |
| Images (JPEG, PNG, WebP) | Gemini vision description | Converts visual content to searchable text |
| Audio (MP3, WAV, MP4) | Gemini transcription + summary | Full transcription with key points |
| Video (MP4, WebM) | Gemini transcription + visual description | Audio transcription + visual key moments |

All files limited to 20MB. Content chunked at sentence boundaries with 500-token max and 100-token overlap.

## License

MIT

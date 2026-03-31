# Document Hub

A collaborative document Q&A system using Retrieval-Augmented Generation. Users upload PDFs, images, audio, and video files. The system extracts content, generates vector embeddings, and enables semantic search with AI-powered question answering that always cites its sources.

## Tech Stack

**Backend:** FastAPI, Agno Framework 2.2.13, OpenAI (GPT-4o-mini for chat, text-embedding-3-small for embeddings), Google Gemini (multimodal processing), Supabase (PostgreSQL + pgvector), SQLAlchemy, Langfuse (optional observability)

**Frontend:** Next.js 14 (App Router), React 18, TypeScript, Tailwind CSS, Framer Motion, Supabase Auth + Storage

**Database:** PostgreSQL with pgvector extension, HNSW indexing for vector similarity search, Row Level Security policies

**Design:** Genlabs visual language -- Inter font, zinc color palette, glass morphism panels, orange accent system, layered shadow system

## Architecture

```
frontend/                         backend/
Next.js 14 (App Router)           FastAPI

app/page.tsx ---- Auth -----> Supabase Auth (JWT)
                                    |
components/                         v
  KnowledgeHub.tsx               main.py
    Upload --> Supabase Storage     |-- POST /ingest
    Search --> GET /documents       |-- GET  /documents
    Chat   --> POST /chat           |-- POST /chat
                                    |-- GET  /document/{id}/preview
app/chat/page.tsx                   |
  useChat hook                      v
  ChatMessage                    agno_agent.py
  ChatInput                        Agno Agent (GPT-4o-mini)
                                   +-- search_tool (pgvector)
                                   +-- list_tool (SQL)
                                        |
                                        v
                                   PostgreSQL + pgvector
                                   (documents, chunks, threads, messages)
```

The frontend communicates with the backend through Next.js API routes that proxy requests to FastAPI. This avoids CORS issues in production and keeps the backend URL server-side.

### Backend Modules

| Module | Responsibility |
|--------|---------------|
| `main.py` | FastAPI app, all HTTP endpoints, document ingestion pipeline, thread/message management |
| `agno_agent.py` | Agno Agent configuration, `search_tool` (semantic search via pgvector), `list_tool` (document listing), DB engine |
| `rag.py` | OpenAI embedding generation with error propagation |
| `ingest.py` | PDF text extraction (pypdf) and sentence-boundary chunking |
| `multimodal.py` | Image/audio/video processing via Google Gemini API |
| `auth.py` | Supabase JWT validation and user extraction |
| `models.py` | SQLAlchemy table definitions (documents, chunks, threads, messages) |
| `supabase_client.py` | Singleton Supabase clients, signed URL generation |
| `observability.py` | Langfuse tracing integration (optional) |

### Frontend Structure

| Path | Responsibility |
|------|---------------|
| `app/page.tsx` | Root page -- auth form (email/password + Google OAuth) or app shell |
| `app/chat/page.tsx` | Chat interface with document context |
| `app/auth/callback/route.ts` | OAuth callback handler |
| `app/api/*/route.ts` | Proxy routes to backend (chat, ingest, documents, preview) |
| `components/KnowledgeHub.tsx` | Document grid, upload, semantic search, preview modal |
| `components/Topbar.tsx` | Glass-panel navigation bar |
| `components/chat/*` | ChatHeader, ChatMessage, ChatInput |
| `components/ui/*` | Design system primitives (button, card, badge, input, etc.) |
| `hooks/useChat.ts` | Chat state management with thread persistence and abort |
| `lib/api-proxy.ts` | Shared proxy helper for API routes |
| `lib/supabase/client.ts` | Supabase browser client |

### Database Schema

Four tables with RLS policies:

- **documents** -- uploaded files metadata, status tracking (pending/processing/completed/failed)
- **chunks** -- text chunks with 1536-dimension vector embeddings, linked to documents via CASCADE
- **threads** -- chat conversations, private per user
- **messages** -- chat messages with role (user/assistant/system) and metadata (citations)

Documents and chunks are shared across all authenticated users (Team Hub mode). Threads and messages are private per user.

## Data Flow

```mermaid
flowchart TD
    A[User uploads file] --> B[Frontend sends to Supabase Storage]
    B --> C[Frontend calls POST /ingest]
    C --> D[Backend creates document record - status: pending]
    D --> E[Background task starts - status: processing]

    E --> F{File type?}
    F -->|PDF| G[pypdf extracts text per page]
    F -->|Image| H[Gemini describes image]
    F -->|Audio| I[Gemini transcribes audio]
    F -->|Video| J[Gemini transcribes + describes video]

    G --> K[Chunk text by sentence boundaries - max 700 tokens]
    H --> K
    I --> K
    J --> K

    K --> L[OpenAI generates embeddings - batch of 64]
    L --> M[Store chunks + vectors in PostgreSQL]
    M --> N[Update document status: completed]

    style A fill:#fff,stroke:#d4d4d8
    style N fill:#d1fae5,stroke:#6ee7b7
```

```mermaid
flowchart TD
    A[User sends chat message] --> B[POST /chat with message + document_ids + thread_id]
    B --> C{Thread exists?}
    C -->|No| D[Create new thread]
    C -->|Yes| E[Validate thread ownership]
    D --> F[Load thread history]
    E --> F

    F --> G[Save user message to thread]
    G --> H[Agno Agent receives message + history]

    H --> I[Agent calls search_tool]
    I --> J[Generate query embedding via OpenAI]
    J --> K[pgvector cosine similarity search]
    K --> L[Return top matching chunks with scores]

    L --> M[Agent generates answer using GPT-4o-mini]
    M --> N[Extract citations from search results]
    N --> O[Save assistant message + citations to thread]
    O --> P[Return answer + citations + thread_id]

    style A fill:#fff,stroke:#d4d4d8
    style P fill:#d1fae5,stroke:#6ee7b7
```

```mermaid
flowchart LR
    A[User types in search bar] --> B[Debounce 400ms]
    B --> C[GET /documents?semantic_query=...]
    C --> D[Generate query embedding]
    D --> E[pgvector finds chunks above threshold]
    E --> F[Group by document_id, rank by max similarity]
    F --> G[Return ordered document list]
    G --> H[Frontend displays ranked results]

    style A fill:#fff,stroke:#d4d4d8
    style H fill:#d1fae5,stroke:#6ee7b7
```

## Setup

### Prerequisites

- Node.js 18+
- Python 3.12+
- Supabase project
- OpenAI API key
- Google API key (for multimodal processing)

### 1. Database

Run `sql/schema_complete.sql` in the Supabase SQL Editor. This creates all tables, indexes (including HNSW for vector search), RLS policies, and the `search_similar_chunks` RPC function.

Create a `docs` bucket in Supabase Storage (private) with RLS policies allowing authenticated users to upload, download, and delete.

### 2. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `backend/.env`:

```
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
SUPABASE_JWT_SECRET=your-jwt-secret
SUPABASE_DB_URL=postgresql://postgres:password@db.xxx.supabase.co:5432/postgres
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AI...
MODEL=gpt-4o-mini
EMB_MODEL=text-embedding-3-small
SIM_THRESHOLD=0.2
CORS_ORIGINS=http://localhost:3000
STORAGE_BUCKET=docs
LANGFUSE_ENABLED=false
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

Terminal 1:

```bash
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
```

Terminal 2:

```bash
cd frontend && npm run dev
```

The app is available at `http://localhost:3000`.

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/ingest` | JWT | Upload document for processing. Accepts `storage_path`, `title`, `mime`. Returns 202 with `document_id`. |
| GET | `/documents` | JWT | List all hub documents. Optional `semantic_query` param for vector-ranked results. |
| POST | `/chat` | JWT | Send message to RAG agent. Accepts `message`, optional `document_ids` and `thread_id`. Returns `answer`, `citations`, `thread_id`. |
| GET | `/document/{id}/preview` | JWT | Get signed URL for document preview. |

All endpoints require a valid Supabase JWT in the `Authorization: Bearer <token>` header.

## Key Design Decisions

**Team Hub model.** All documents are shared among authenticated users. Chat threads remain private. This is enforced at both the application layer and the database layer via RLS policies.

**Background ingestion.** Document processing (download, text extraction, embedding generation) runs in FastAPI BackgroundTasks. The endpoint returns 202 immediately. The frontend polls document status every 3 seconds until processing completes.

**Structured chat history.** Chat history is passed to the Agno Agent as structured message objects with explicit role boundaries, preventing prompt injection attacks where users could manipulate the agent by crafting messages that mimic history delimiters.

**Error propagation over silent failure.** The embedding module raises exceptions on failure rather than returning empty results. This ensures documents are correctly marked as "failed" instead of silently completing with missing embeddings.

**Proxy architecture.** Frontend API routes proxy all requests to the backend. This keeps the backend URL out of client-side code and sidesteps CORS configuration in production.

**Single DB engine.** One SQLAlchemy engine instance in `agno_agent.py`, imported by `main.py`. Avoids duplicate connection pools.

## Supported File Types

| Type | Processing | Notes |
|------|-----------|-------|
| PDF | pypdf text extraction, page-aware chunking | Primary use case |
| Images (JPEG, PNG, WebP) | Gemini vision description | Converts visual content to searchable text |
| Audio (MP3, WAV, MP4) | Gemini transcription + summary | Full transcription with key points |
| Video (MP4, WebM) | Gemini transcription + visual description | Audio transcription + frame-by-frame key moments |

All files are limited to 20MB. Content is chunked at sentence boundaries with a maximum of 700 tokens per chunk.

## License

MIT

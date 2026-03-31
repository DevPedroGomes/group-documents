# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Hub Agno + Supabase is a document Q&A system using RAG (Retrieval-Augmented Generation). Users upload PDFs, which are processed into embeddings, enabling semantic search and AI-powered question answering with automatic citations.

## Tech Stack

**Backend:** FastAPI, Agno Framework (v2.2.13), OpenAI (GPT-4o-mini, text-embedding-3-small), Supabase (PostgreSQL + pgvector), SQLAlchemy

**Frontend:** Next.js 14 (App Router), React 18, Supabase Auth + Storage, Tailwind CSS, TypeScript

## Development Commands

```bash
# Run both frontend and backend (from frontend directory)
cd frontend && npm run dev

# Run backend only
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000

# Run frontend only
cd frontend && npm run dev

# Install backend dependencies
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Install frontend dependencies
cd frontend && npm install

# Build frontend
cd frontend && npm run build
```

## Architecture

### Backend (`backend/app/`)

| Module | Purpose |
|--------|---------|
| `main.py` | FastAPI endpoints: `/ingest`, `/documents`, `/chat`, `/document/{id}/preview` |
| `agno_agent.py` | Agno Agent with custom tools (`search_tool`, `list_tool`) for RAG queries |
| `rag.py` | OpenAI embedding generation |
| `ingest.py` | PDF text extraction and chunking |
| `auth.py` | Supabase JWT validation |
| `models.py` | SQLAlchemy models (documents, chunks) |
| `supabase_client.py` | Supabase client and signed URL generation |

### Frontend (`frontend/`)

| Path | Purpose |
|------|---------|
| `app/page.tsx` | Main page (login redirect or KnowledgeHub) |
| `app/login/page.tsx` | Authentication page |
| `components/KnowledgeHub.tsx` | Core UI: document upload + chat interface |
| `lib/api-proxy.ts` | API client for backend communication |

### Database Schema

- `documents` - User documents with storage path reference
- `chunks` - Document text chunks with pgvector embeddings (1536 dimensions)
- `threads` / `messages` - Reserved for future chat history persistence

## Data Flow

1. **Upload:** Frontend uploads PDF to Supabase Storage, then calls `/ingest`
2. **Ingest:** Backend downloads PDF, extracts text, chunks it, generates embeddings via OpenAI, stores in PostgreSQL
3. **Chat:** `/chat` triggers Agno Agent which uses `search_tool` for semantic search (cosine similarity on pgvector), returns answer with citations

## Environment Variables

**Backend (`backend/.env`):**
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`
- `SUPABASE_DB_URL` - PostgreSQL connection string
- `OPENAI_API_KEY`, `MODEL` (default: gpt-4o-mini), `EMB_MODEL` (default: text-embedding-3-small)
- `SIM_THRESHOLD` (default: 0.2) - Minimum cosine similarity for search results
- `CORS_ORIGINS` (default: http://localhost:3000)

**Frontend (`frontend/.env.local`):**
- `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `NEXT_PUBLIC_API_URL` (default: http://localhost:8000)

## Supabase Setup

Key steps:
1. Create project and obtain credentials
2. Run `sql/schema_complete.sql` in SQL Editor (creates tables, indexes, RLS policies, and RPC functions)
3. Create `docs` bucket in Storage with RLS policies for authenticated users (see instructions in schema_complete.sql)

## Key Patterns

- **Team Hub Mode:** All authenticated users can view/edit all documents (shared workspace). Chat threads remain private per user.
- **Atomic transactions:** Document creation and embedding generation use rollback on failure
- **Agno tools:** `search_tool` performs vector search across all documents, `list_tool` lists all hub documents
- **Input validation:** Max file size 20MB, PDF/images/audio/video supported, message length limits enforced
- **Security:** Storage path validation, streaming downloads with size limits, SQL injection prevention via parameterized queries

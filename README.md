# BrainHub

[![CI](https://github.com/DevPedroGomes/group-documents/actions/workflows/deploy.yml/badge.svg)](https://github.com/DevPedroGomes/group-documents/actions/workflows/deploy.yml)

BrainHub answers questions over a private document library (PDF, images, audio, video, web pages) and cites the document, page and date behind each answer. It also warns when the retrieved sources contradict each other and names the one with the most recent document date, answers "as of" a past date using each document's effective date, and stores a decision trail for every answer. A voice mode runs the same retrieval as a tool call and writes the same trail.

Demo: https://group-documents.pgdev.com.br. Visitors register with an email and a 12+ character password; a new account starts empty, and the library offers a one-click sample PDF.

Stack: FastAPI (Python 3.12), Postgres with pgvector, Redis, an arq worker for ingestion, Next.js 16 with React 19.

## What it does beyond citing

- **Disagreement warning.** When the passages kept for an answer come from two or more documents, a fast-model check runs in parallel with generation and reports whether they disagree on a fact. The result is a warning next to the answer; nothing is filtered or reordered. The "current" source (`vigente`) is chosen in code, not by the model: among the sources the check cites, the one with the latest document date. On a tie or a missing date there is none.
- **Answer as of a date.** `as_of` (YYYY-MM-DD, inclusive) limits both search legs to documents dated on or before that day. A document's date is its `effective_date`, entered at upload or crawl, or else the UTC day it was uploaded. The web fallback never runs with `as_of`.
- **Decision trail.** Every answer writes a `decisions` row with the queries actually searched (condensed follow-up and corrective rewrite included), each retrieved and kept passage with title, page, score, score scale and document date, whether the reranker ran, low confidence, web use, the disagreement result, `as_of` and latency. The row is written before the `done` event, and also when the stream fails or the client disconnects. The chat panel reloads it after each answer.
- **Voice.** The browser talks to OpenAI Realtime over WebRTC with a short-lived key minted by the backend, so the real key stays on the server. Retrieval is a function call that the browser forwards to `POST /realtime/tool/buscar`. That route runs retrieval, grading and the disagreement check with the user id from the JWT, honours the chat's document selection, and writes the same trail.

## Architecture

```mermaid
flowchart LR
    B(["Browser"])
    N["Next.js 16: pages + /api route handlers"]
    A["FastAPI, one uvicorn process"]
    W["arq worker: ingestion"]
    P[("Postgres + pgvector")]
    R[("Redis: embedding cache, rate limits, daily quotas, arq queue")]
    U[("uploads volume")]
    O["OpenAI Realtime"]
    subgraph X["Providers"]
        L["LLM: Anthropic or OpenRouter"]
        V["Voyage multimodal embeddings"]
        C["Cohere rerank, optional"]
        D["Deepgram, audio"]
        T["Tavily web search, off by default"]
    end
    B --> N
    N -->|"internal network, visitor IP in X-Forwarded-For"| A
    B -.->|"login and register on the API host"| A
    B -.->|"WebRTC audio"| O
    A -->|"mints short-lived key"| O
    A --> P
    A --> R
    A --> U
    W --> P
    W --> R
    W --> U
    A --> X
    W --> X
```

- Frontend routes: `/` (landing, server-rendered), `/library` (upload, crawl, document list), `/chat`. The `/api/*` route handlers proxy to FastAPI over the Docker network (`API_INTERNAL_URL`) and forward Traefik's `X-Real-Ip` as `X-Forwarded-For`, so per-IP rate limits count each visitor separately.
- Ingestion is queued in Redis and run by a separate container from the same image (`arq app.jobs.worker.WorkerSettings`).
- The API runs a single uvicorn process. Blocking work (SQL, bcrypt, provider calls, disk) runs in threads so it does not hold the event loop.

## Retrieval pipeline (`POST /chat`, server-sent events)

1. Input check (length, plus a regex filter for obvious injection phrasing in English and Portuguese), thread ownership, then one unit of the daily chat quota.
2. **Queries.** The fast model writes 3 variants of the question. When the thread has history, it first rewrites a follow-up as a standalone question from the last 6 messages; that becomes the main query, and the original question is kept as an extra variant.
3. **Embedding.** All queries go to Voyage `voyage-multimodal-3.5` (1024 dimensions) in one call, through a Redis cache keyed by model, input type and text hash.
4. **Hybrid search per query.** Each leg has `LIMIT 45` and filters by `user_id`, the optional document selection and the optional `as_of`:
   - semantic: cosine similarity on an HNSW index, with `hnsw.ef_search` raised inside the transaction so post-scan filters do not drop neighbours;
   - keyword: a `tsvector` built with two configurations from migration 008 (`busca_portugues`, with accent folding when `unaccent` exists, and `busca_ingles`), each dropping both languages' stopwords. Query terms are OR-ed, and function words the stoplists miss (unaccented or informal Portuguese such as "nao" or "pra") are removed from the question first;
   - the two legs are fused with reciprocal rank fusion (k = 60).
5. Results are merged across queries, then Cohere `rerank-v4.0-fast` reranks them against the main query down to 5 passages. Without a Cohere key the fused order is kept.
6. **Grading.** With Cohere scores, passages at 0.7 or above are kept; if none clears the bar, the best 2 go on and the answer is marked low confidence. With fused scores (no reranker) there is no cut, and fewer than 2 passages means low confidence.
7. **Corrective step.** On low confidence the fast model rewrites the main query and the library is searched once more. Both result sets are merged, reranked against the main query when Cohere is on, and graded again.
8. **Web, opt-in.** Runs only if confidence is still low, `ENABLE_WEB_FALLBACK=true`, a Tavily key is set and there is no `as_of`. Web results are cited separately with `kind: "web"`.
9. **Generation.** Passages reach the generation model inside `<document title page date>` tags that the system prompt declares as data, not instructions. On low confidence the prompt says the passages may not answer the question.
10. Events: `workflow`, `sources`, `conflict` (at any point before `done`), `chunk`, `done {thread_id, message_id, low_confidence}`, `error`.

The generator gets the raw chunk text. The short context that ingestion writes for each chunk is used only for the embedding and the keyword index.

## Keyword-leg evaluation

`backend/scripts/avaliar_busca_textual.py` measures the keyword leg on its own: a deterministic synthetic library (500 templated documents plus 15 informal notes; an English tenant adds the sample handbook and 60 distractor tickets) goes through the real text path, without the LLM chunk context, into a throwaway database. Metric: MRR of the first relevant passage in the top 45 (the production `LIMIT`), with ties counted against. "Before" is the original setup: the `english` configuration with every term required (AND).

| Question group | n | Origin | Before | Now |
|---|---:|---|---:|---:|
| Literal template question | 5 | original | 1.00 | 1.00 |
| Paraphrase reusing the template's words | 20 | original | 0.00 | 0.95 |
| Other document types | 15 | original | 0.00 | 1.00 |
| English: handbook + 60 distractors, 1 relevant passage | 10 | original | 0.30 | 0.90 |
| Typed without accents | 6 | added after the first run | 0.00 | 1.00 |
| Chat style without accents ("nao", "ate", "pra") | 5 | added later | 0.00 | 1.00 |
| Different words from the document | 20 | added later | 0.00 | 0.01 |

What this does not show:

- The corpus is synthetic and templated. In most groups the question contains a term that only the relevant documents have, so OR reduces the task to finding a rare term.
- When the question uses other words, the keyword leg finds nothing useful: 0.01, against 0.18 for a random order. The semantic leg is expected to cover that case; this is not measured.
- It does not measure hybrid search, the reranker, chunks with their LLM context, or answer quality.
- The chat-style row needs the function-word removal: without it the same configuration scores 0.82. Without `unaccent`, the rows about missing accents do not apply.

Reproduce (a Postgres with pgvector and `unaccent` where the role can `CREATE DATABASE`; the script creates and drops its own database and needs no API key):

```bash
cd backend
.venv/bin/python -m scripts.avaliar_busca_textual --database-url postgresql://localhost/postgres [--json out.json]
```

## Ingestion

- `POST /upload` takes multipart `file`, `title` and an optional `effective_date`. The body is counted while it is read and rejected with 413 above 20 MB. The type is sniffed with libmagic against an allowlist (PDF; PNG, JPEG, GIF, WebP; MP3, WAV, WebM audio; MP4, WebM video), and a declared type must agree with it. `POST /crawl` fetches a web page as text.
- The daily ingest quota is consumed before anything is written and returned if saving, the database insert or queueing fails. Files are stored as `{user_id}/docs/{uuid4}{ext}`; the client file name is discarded.
- In the worker, PDFs are read page by page with pypdf. Pages with under 120 characters of text are rendered with PyMuPDF at 150 DPI, described by the fast model and embedded as images. PDFs over 300 pages fail before any paid call. Text becomes 500-token chunks with 100 tokens of overlap, and the fast model writes a short context for each chunk, with the document prompt-cached per 50,000-character window. Images and video are embedded directly by the multimodal model; audio is transcribed by Deepgram.
- Failures are classified. Our own limits, provider 4xx responses and unreadable files are permanent and not retried. Network errors, 408, 429, 5xx and unclassified errors are retried, up to 5 attempts. The document list shows a short error category, never the raw provider error.

## Security and isolation

- Every chunk query (both search legs and the library's semantic search) filters by `user_id`. Decisions, threads, previews and deletes are scoped to the owner, and a preview of someone else's document answers 404.
- Local JWT (HS256, 60 minutes), bcrypt, 12-character minimum password, and an `is_active` lookup on every authenticated request.
- Crawler SSRF guard: http(s) only, deny-listed hosts, private, reserved and cloud-metadata IP ranges and sensitive ports blocked, the connection pinned to the validated IP, every redirect revalidated (at most 5), a 5 MB body cap.
- Document passages and web results are delimited as data in the prompts, and tag names inside their content are stripped. The regex input filter only adds friction.
- Containers run as non-root with `cap_drop: ALL` and `no-new-privileges`. Traefik adds HSTS, frame-deny, nosniff and referrer-policy headers to both hosts, plus a CSP on the frontend. `/docs` and `/openapi.json` exist only with `DEBUG=true`.

## Limits and cost

Daily caps are global (all users together), counted in Redis per UTC day, and answered with 429 and `Retry-After` until midnight UTC. `GET /demo-limits` reports what is left.

| Setting | Default | Consumed by |
|---|---:|---|
| `DAILY_CHAT_LIMIT` | 300 | each chat answer, and each semantic search in the library list |
| `DAILY_INGEST_LIMIT` | 100 | upload, crawl, ingest |
| `DAILY_REALTIME_LIMIT` | 40 | voice sessions opened |
| `DAILY_REALTIME_TOOL_LIMIT` | 400 | voice searches, across all sessions |

- Per-IP rate limits (slowapi on Redis): chat and upload 30/min, crawl 10/min, library semantic search 20/min, voice session 10/min, voice search 60/min, register and login 5/min.
- New ingestions get 429 while the queue holds more than `AGENT_OPS_PROFUNDIDADE_MAXIMA` jobs (50 in the compose file). A quota unit is given back when a request fails before its main paid work: a chat answer before the first generated token, a voice search when no provider was called, an upload when storing or queueing fails. `AGENT_OPS_KILL_SWITCH=true` refuses every quota-consuming call.
- Paid calls per chat answer: one fast-model call for the query variants, one Voyage call (none if every query is cached), one Cohere rerank if configured, the disagreement check (fast model, at most 220 output tokens) when two or more documents remain, and the generation. Low confidence adds a rewrite, one embedding call and one rerank.

## Running locally

Needs Python 3.12, Node 22, git (pip installs one dependency from GitHub), a Postgres with pgvector, and Redis. The database role must be able to `CREATE EXTENSION vector` (pgvector is not a trusted extension). The `unaccent` contrib extension is optional.

```bash
docker run -d --name bh-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 pgvector/pgvector:pg16
docker run -d --name bh-redis -p 6379:6379 redis:7-alpine

cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
# edit .env: DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres,
# REDIS_URL=redis://localhost:6379, JWT_SECRET, VOYAGE_API_KEY, ANTHROPIC_API_KEY
.venv/bin/uvicorn app.main:app --port 8000      # applies pending migrations, then serves
.venv/bin/arq app.jobs.worker.WorkerSettings    # second terminal: the ingestion worker

cd ../frontend
npm ci && cp .env.local.example .env.local      # NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev                                     # open http://localhost:3000
```

- To start, the API needs `DATABASE_URL`, `JWT_SECRET`, `VOYAGE_API_KEY` and a reachable Redis (the queue pool opens at startup). Answers also need an LLM key: `ANTHROPIC_API_KEY`, or `LLM_PROVIDER=openrouter` with `OPENROUTER_API_KEY`. Cohere, Deepgram, OpenAI and Tavily keys are optional.
- `AGENT_OPS_*` variables are read from the process environment, not from `backend/.env`; the default `redis://localhost:6379` fits a local Redis.
- Migrations: `backend/app/db/migrate.py` applies `backend/migrations/NNN_*.sql` at API startup, in order, each file in its own transaction, under an advisory lock. A failed migration stops the boot. There is no separate migrate command.
- The `docker-compose.yml` in the repository is the production file and will not start on its own: Postgres is the external `central-db` on the `databases` network, and Traefik comes from the external `proxy` network with its file-defined middlewares.

Tests:

```bash
cd backend
.venv/bin/python -m pytest -q        # unit: no network, no keys, no database
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
TEST_REDIS_URL=redis://localhost:6379/0 \
  .venv/bin/python -m pytest -q -m integration
cd ../frontend && npx tsc --noEmit && npm run build
```

Integration tests run real SQL and are skipped without `TEST_DATABASE_URL`. Each one creates and drops its own `gd_test_<uuid>` database, so the role needs `CREATEDB` and permission to create the extensions.

## Configuration

Backend variables come from `backend/.env` (see `backend/.env.example`, which a test keeps in sync with `Settings`). Defaults in parentheses.

| Group | Variables | Notes |
|---|---|---|
| Required | `DATABASE_URL`, `JWT_SECRET`, `VOYAGE_API_KEY` | No default. A `postgresql+asyncpg://` URL is rewritten for psycopg2. |
| LLM | `LLM_PROVIDER` (`anthropic`), `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `GENERATION_MODEL` (`claude-sonnet-5`), `FAST_MODEL` (`claude-haiku-4-5`) | With `openrouter`, model names are OpenRouter ids. |
| Embeddings | `VOYAGE_DOC_MODEL` (`voyage-multimodal-3.5`), `EMBEDDING_DIMENSIONS` (1024), `EMBEDDING_CACHE_TTL` (3600) | The dimension must match `vector(1024)` in the migrations. |
| Rerank | `COHERE_API_KEY`, `COHERE_RERANK_MODEL` (`rerank-v4.0-fast`), `ENABLE_RERANKING` (true) | Without a key, or with reranking off: fused order and no 0.7 cut. |
| Retrieval | `CHUNK_SIZE` (500), `CHUNK_OVERLAP` (100), `SEARCH_CANDIDATES_MULTIPLIER` (3), `RELEVANCE_THRESHOLD` (0.7), `RRF_K` (60), `MULTI_QUERY_COUNT` (3), `ENABLE_CONFLICT_DETECTION` (true), `ENABLE_INPUT_GUARDRAILS` (true) | |
| Web search | `ENABLE_WEB_FALLBACK` (false), `TAVILY_API_KEY` | Enabling it sends the rewritten question to a third party. |
| Files | `UPLOADS_PATH` (`/app/uploads`), `MAX_FILE_SIZE` (20 MB), `MAX_PDF_PAGES` (300), `PDF_MIN_CHARS_POR_PAGINA` (120), `PDF_RENDER_DPI` (150) | |
| Audio and voice | `DEEPGRAM_API_KEY`, `DEEPGRAM_MODEL` (`nova-3`), `ENABLE_REALTIME` (true), `OPENAI_API_KEY`, `REALTIME_MODEL` (`gpt-realtime-2.1`), `REALTIME_VOICE` (`marin`), `REALTIME_TRANSCRIBE_MODEL` (`gpt-4o-mini-transcribe`) | Without Deepgram, audio ingestion fails; without OpenAI, voice answers 503. |
| App and auth | `APP_NAME`, `DEBUG` (false), `CORS_ORIGINS` (`http://localhost:3000`), `JWT_ALGORITHM` (`HS256`), `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (60), `AUTH_RATE_LIMIT` (`5/minute`) | |
| Redis and quotas | `REDIS_URL` (`redis://localhost:6379`), `DAILY_CHAT_LIMIT`, `DAILY_INGEST_LIMIT`, `DAILY_REALTIME_LIMIT`, `DAILY_REALTIME_TOOL_LIMIT` | Quota defaults in the table above. |
| Queue and metering | `AGENT_OPS_REDIS_URL` (`redis://localhost:6379`), `AGENT_OPS_PROJETO` (`default`), `AGENT_OPS_PROFUNDIDADE_MAXIMA` (500), `AGENT_OPS_KILL_SWITCH` (false) | Process environment only. Metering fails closed (503) when this Redis is unreachable. |
| Seed script | `ACERVO_DEMO_SENHA` | Password for `scripts/semear_acervo_demo.py`, at least 16 characters; there is no default. |
| Frontend | `NEXT_PUBLIC_API_URL` (build time), `API_INTERNAL_URL` | The browser calls the public URL for login, register and `/auth/me`. The `/api/*` handlers use `API_INTERNAL_URL`, then `NEXT_PUBLIC_API_URL`, then `http://localhost:8000`. |
| Tests | `TEST_DATABASE_URL`, `TEST_REDIS_URL` | |

## Deployment

- CI (`.github/workflows/deploy.yml`, on push to `main`) runs the unit tests, then the integration tests against `pgvector/pgvector:pg16` and `redis:7-alpine` service containers. Only after both pass does it run `docker compose build` and `docker compose push` to GHCR (`ghcr.io/devpedrogomes/group-documents/backend` and `/frontend`; the worker reuses the backend image). The server pulls images and never builds them; the pull runs on the host, outside this repository.
- Compose services: `redis`, `backend`, `worker`, `frontend`. `DB_PASSWORD` and `JWT_SECRET` are interpolated from the host environment; everything else comes from `backend/.env`. Hosts: `group-documents.pgdev.com.br` (frontend) and `group-documents-api.pgdev.com.br` (API), with Let's Encrypt certificates through Traefik.
- Migration 008 rewrites every `chunks` row once (`UPDATE chunks SET content = content`) to rebuild the search vectors, holding row locks while it runs; its cost grows with the table, since every row's GIN and HNSW entries are written again. If the database role cannot create `unaccent`, 008 logs a WARNING at boot with the manual recovery steps and builds the Portuguese configuration without accent folding instead of failing.

## Known limitations

- There is no end-to-end evaluation of answers: faithfulness, citation correctness, hybrid retrieval and the reranker are not measured. The only numbers are the keyword-leg evaluation above, on a synthetic corpus.
- The disagreement check is one fast-model call over at most 6 passages. It can miss a disagreement or report one that is not there; it is shown as a warning and changes nothing else.
- "Current" is decided by document date only. A document uploaded without `effective_date` is dated by its upload day, so "most recent" can mean "uploaded last".
- No guest or demo mode: visitors must register before trying anything.
- The web fallback is off by default. When enabled, it sends the rewritten question to Tavily.
- The list of function words removed from keyword queries is kept by hand. It covers the unaccented forms of Postgres's Portuguese stopwords and a few informal words, not every colloquialism.
- Without `unaccent`, keyword search does not fold accents, and installing the extension later needs a manual `ALTER TEXT SEARCH CONFIGURATION` and a reindex.
- The API is one uvicorn process; blocking work moves to threads, not to more processes.
- Video is indexed by its embedding and file name only, with no transcript. Voice searches skip the corrective step and the web.
- `GET /graph` (documents, questions, and edges between documents that disagreed) has no UI yet.
- A thread's document selection is remembered per browser (localStorage), not on the server.

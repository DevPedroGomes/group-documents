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
from app.core.rag.generator import stream_answer
from app.core.rag.transformer import transform_query
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

            # Step 3: Transform + Web Search (if needed)
            if needs_web:
                workflow.append({"step": "transform", "status": "in_progress", "details": "Rewriting query..."})
                yield _sse("workflow", workflow)

                transformed_query = transform_query(body.message)

                workflow[-1] = {
                    "step": "transform",
                    "status": "completed",
                    "details": f"Rewrote: {transformed_query[:80]}...",
                }
                yield _sse("workflow", workflow)

                # Web search fallback
                settings = get_settings()
                if settings.tavily_api_key:
                    workflow.append({"step": "web_search", "status": "in_progress", "details": "Searching the web..."})
                    yield _sse("workflow", workflow)

                    try:
                        from tavily import TavilyClient
                        tavily_client = TavilyClient(api_key=settings.tavily_api_key)
                        web_results = tavily_client.search(transformed_query, max_results=3)

                        for r in web_results.get("results", []):
                            filtered_docs.append({
                                "document_id": "web",
                                "document_title": r.get("title", "Web Result"),
                                "page": 0,
                                "snippet": r.get("content", "")[:500],
                                "relevance_score": r.get("score", 0.5),
                            })

                        workflow[-1] = {"step": "web_search", "status": "completed", "details": f"Found {len(web_results.get('results', []))} web results"}
                        yield _sse("workflow", workflow)
                    except Exception as e:
                        logger.warning(f"Web search failed: {e}")
                        workflow[-1] = {"step": "web_search", "status": "completed", "details": "Web search unavailable"}
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

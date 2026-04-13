"""Document management routes: upload, ingest, list, preview."""

import os
import re
import logging
import asyncio
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import insert, text as sqltext

from app.config.settings import get_settings
from app.db.engine import engine
from app.db.models import documents, chunks
from app.api.dependencies import require_user
from app.services.file_storage import save_file, get_file
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


@router.post("/upload")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
):
    """Upload a file and trigger ingestion."""
    user_id = await require_user(request)
    settings = get_settings()

    if not title or len(title) > 500:
        raise HTTPException(400, "Invalid title (max 500 characters)")

    # Read file data
    data = await file.read()
    if len(data) > settings.max_file_size:
        raise HTTPException(400, f"File too large (max {settings.max_file_size // (1024*1024)}MB)")

    # Determine mime type
    mime = file.content_type or "application/octet-stream"

    # Save to local storage
    storage_path = save_file(user_id, file.filename, data)

    # Create document record
    try:
        with engine.begin() as conn:
            doc_id = conn.execute(
                insert(documents).values(
                    user_id=user_id,
                    title=title,
                    mime=mime,
                    storage_path=storage_path,
                    status="pending",
                ).returning(documents.c.id)
            ).scalar_one()
    except Exception as e:
        logger.error(f"DB error creating document: {e}")
        raise HTTPException(500, "Error creating document record")

    # Trigger background ingestion
    background_tasks.add_task(process_ingestion, str(doc_id), user_id, storage_path)

    return {"document_id": str(doc_id), "status": "pending"}


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
    """Background task: read file → chunk → enrich → embed → store."""
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

        # Read file from local storage
        data = get_file(storage_path)

        if len(data) > settings.max_file_size:
            raise Exception(f"File too large (>{settings.max_file_size // (1024*1024)}MB)")

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
    """Return a temporary download URL or serve the file directly."""
    await require_user(request)
    with engine.begin() as conn:
        row = conn.execute(
            sqltext("SELECT storage_path FROM documents WHERE id=:id"), {"id": doc_id}
        ).first()
    if not row:
        raise HTTPException(404, "Document not found")

    storage_path = row[0]
    settings = get_settings()
    abs_path = os.path.join(settings.uploads_path, storage_path)

    if not os.path.isfile(abs_path):
        raise HTTPException(404, "File not found on disk")

    from fastapi.responses import FileResponse
    return FileResponse(abs_path, filename=os.path.basename(storage_path))

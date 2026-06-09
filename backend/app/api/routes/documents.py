"""Document management routes: upload, ingest, list, preview, delete."""

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
from app.services.file_storage import save_file, get_file, get_file_abspath, delete_file
from app.api.rate_limit import limiter
from app.services.embedding import embed_documents
from app.core.ingestion.pdf_processor import extract_pages_from_pdf
from app.core.ingestion.chunker import chunk_document_pages, enrich_chunks_with_context

logger = logging.getLogger(__name__)

router = APIRouter()


# Allowed MIME types — must match the regex in validate_storage_path.
ALLOWED_MIMES: dict[str, set[str]] = {
    "application/pdf": {"pdf"},
    "image/png": {"png"},
    "image/jpeg": {"jpg", "jpeg"},
    "image/gif": {"gif"},
    "image/webp": {"webp"},
    "audio/mpeg": {"mp3"},
    "audio/mp3": {"mp3"},
    "audio/wav": {"wav"},
    "audio/x-wav": {"wav"},
    "audio/webm": {"webm"},
    "video/mp4": {"mp4"},
    "video/webm": {"webm"},
    # text/plain is reserved for content extracted from crawled URLs (the user
    # cannot upload a raw .txt file via /upload — libmagic sniff will reject it).
    "text/plain": {"txt"},
}


def _sniff_mime(data: bytes) -> str:
    """Sniff MIME type from file bytes using libmagic."""
    try:
        import magic
        return magic.from_buffer(data, mime=True) or "application/octet-stream"
    except Exception as e:
        logger.warning(f"libmagic sniff failed: {e}")
        return "application/octet-stream"


def validate_storage_path(path: str) -> bool:
    if not path:
        return False
    if ".." in path or path.startswith("/"):
        return False
    pattern = r"^[a-f0-9-]+/docs/[^/]+\.(pdf|png|jpg|jpeg|gif|webp|mp3|mp4|wav|webm|txt)$"
    return bool(re.match(pattern, path, re.IGNORECASE))


class IngestBody(BaseModel):
    storage_path: str
    title: str
    mime: str

    class Config:
        str_strip_whitespace = True


@router.post("/upload")
@limiter.limit("30/minute")
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
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > settings.max_file_size:
        raise HTTPException(400, f"File too large (max {settings.max_file_size // (1024*1024)}MB)")

    # MIME allowlist + magic-byte sniffing. The client-declared content-type is
    # advisory only — we trust libmagic.
    sniffed_mime = _sniff_mime(data).lower()
    # text/plain is reserved for /crawl ingestion only — direct .txt uploads
    # are rejected so the crawl provenance (source URL stored in `meta`) is the
    # only path that produces a text document.
    if sniffed_mime == "text/plain" or sniffed_mime not in ALLOWED_MIMES:
        logger.info(f"Rejected upload: sniffed mime={sniffed_mime}")
        raise HTTPException(415, f"Unsupported file type: {sniffed_mime}")

    # If the client declared a content-type, it must agree with sniffed mime
    # (or at least be in ALLOWED_MIMES with a compatible extension set).
    declared = (file.content_type or "").lower()
    if declared and declared in ALLOWED_MIMES:
        if ALLOWED_MIMES[declared] != ALLOWED_MIMES[sniffed_mime]:
            raise HTTPException(415, "Declared content-type does not match file contents")

    # Save to local storage with UUID-based filename derived from sniffed mime
    storage_path = save_file(user_id, sniffed_mime, data)

    # Create document record
    try:
        with engine.begin() as conn:
            doc_id = conn.execute(
                insert(documents).values(
                    user_id=user_id,
                    title=title,
                    mime=sniffed_mime,
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


class CrawlBody(BaseModel):
    url: str
    title: Optional[str] = None

    class Config:
        str_strip_whitespace = True


@router.post("/crawl")
@limiter.limit("10/minute")
async def crawl_url(request: Request, body: CrawlBody, background_tasks: BackgroundTasks):
    """Fetch a URL, extract its text, and ingest it as a document.

    SSRF defenses: scheme allowlist, hostname/IP deny-list, DNS resolution
    re-validated on every redirect hop. See `app.core.ingestion.url_crawler`.
    """
    from app.core.ingestion.url_crawler import fetch_and_extract, is_safe_url

    user_id = await require_user(request)

    if not body.url:
        raise HTTPException(400, "URL is required")
    if len(body.url) > 2048:
        raise HTTPException(400, "URL too long (max 2048 characters)")

    ok, err = is_safe_url(body.url)
    if not ok:
        raise HTTPException(400, f"URL blocked: {err}")

    # Synchronous fetch (cheap relative to embeddings; keeps API contract
    # simple — caller knows immediately whether the URL was reachable).
    try:
        text, page_title = await asyncio.get_running_loop().run_in_executor(
            None, fetch_and_extract, body.url
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Crawl failed for {body.url}: {e}")
        raise HTTPException(502, "Failed to fetch URL")

    title = (body.title or page_title or body.url)[:500]

    storage_path = save_file(user_id, "text/plain", text.encode("utf-8"))

    try:
        with engine.begin() as conn:
            doc_id = conn.execute(
                insert(documents).values(
                    user_id=user_id,
                    title=title,
                    mime="text/plain",
                    storage_path=storage_path,
                    status="pending",
                    meta={"source_url": body.url},
                ).returning(documents.c.id)
            ).scalar_one()
    except Exception as e:
        logger.error(f"DB error creating document: {e}")
        raise HTTPException(500, "Error creating document record")

    background_tasks.add_task(process_ingestion, str(doc_id), user_id, storage_path)
    return {"document_id": str(doc_id), "status": "pending", "title": title}


@router.post("/ingest")
@limiter.limit("30/minute")
async def ingest(request: Request, body: IngestBody, background_tasks: BackgroundTasks):
    user_id = await require_user(request)

    if not body.title or len(body.title) > 500:
        raise HTTPException(400, "Invalid title (max 500 characters)")

    if not body.storage_path or not validate_storage_path(body.storage_path):
        raise HTTPException(400, "Invalid file path")

    path_user_id = body.storage_path.split("/")[0]
    if path_user_id != user_id:
        raise HTTPException(403, "Storage path does not belong to this user")

    mime_lower = (body.mime or "").lower()
    if mime_lower == "text/plain" or mime_lower not in ALLOWED_MIMES:
        raise HTTPException(415, f"Unsupported file type: {body.mime}")

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

        # Process by type. We carry both the original chunk text (`raw_chunks`)
        # and the contextually-enriched text (`enriched_chunks`) into the store.
        raw_chunks: list[tuple[str, dict]] = []
        enriched_chunks: list[tuple[str, dict]] = []
        summary = None

        if mime == "application/pdf":
            pages = await loop.run_in_executor(None, extract_pages_from_pdf, data)
            if not pages:
                raise Exception("No content extracted from PDF")

            raw_chunks = chunk_document_pages(pages)

            # Contextual enrichment
            full_text = " ".join(p for p in pages if p.strip())
            doc_title = ""
            with engine.begin() as conn:
                doc_title = conn.execute(
                    sqltext("SELECT title FROM documents WHERE id = :id"), {"id": doc_id}
                ).scalar() or ""

            enriched_chunks = await loop.run_in_executor(
                None, enrich_chunks_with_context, raw_chunks, full_text, doc_title
            )

            # Generate summary
            try:
                from app.core.llm_client import chat_complete
                summary = chat_complete(
                    model=settings.fast_model,
                    max_tokens=300,
                    messages=[{
                        "role": "user",
                        "content": f"Summarize this document in 2-3 sentences:\n\n{full_text[:10000]}",
                    }],
                ).strip()
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
                raw_chunks.append((ck, {"page": 1, "chunk_index": ci}))
            enriched_chunks = list(raw_chunks)

        elif mime and mime.startswith("audio/"):
            from app.core.ingestion.multimodal import process_audio
            filename = storage_path.split("/")[-1]
            text = await loop.run_in_executor(None, lambda: process_audio(data, filename=filename))
            if not text:
                raise Exception("No content extracted from audio")
            from app.core.ingestion.chunker import chunk_text
            for ci, ck in enumerate(chunk_text(text)):
                raw_chunks.append((ck, {"page": 1, "chunk_index": ci}))
            enriched_chunks = list(raw_chunks)

        elif mime and mime.startswith("video/"):
            from app.core.ingestion.multimodal import process_video
            text = await loop.run_in_executor(None, process_video, data)
            if not text:
                raise Exception("No content extracted from video")
            from app.core.ingestion.chunker import chunk_text
            for ci, ck in enumerate(chunk_text(text)):
                raw_chunks.append((ck, {"page": 1, "chunk_index": ci}))
            enriched_chunks = list(raw_chunks)

        elif mime == "text/plain":
            text = data.decode("utf-8", errors="replace")
            if not text.strip():
                raise Exception("No content extracted from URL")
            from app.core.ingestion.chunker import chunk_text
            for ci, ck in enumerate(chunk_text(text)):
                raw_chunks.append((ck, {"page": 1, "chunk_index": ci}))
            enriched_chunks = list(raw_chunks)

            try:
                from app.core.llm_client import chat_complete
                summary = chat_complete(
                    model=settings.fast_model,
                    max_tokens=300,
                    messages=[{
                        "role": "user",
                        "content": f"Summarize this web page in 2-3 sentences:\n\n{text[:10000]}",
                    }],
                ).strip()
            except Exception as e:
                logger.warning(f"Summary generation failed: {e}")
                summary = None

        else:
            raise Exception(f"Unsupported format: {mime}")

        if not raw_chunks:
            raise Exception("No chunks generated")

        # Embed using the enriched text (better retrieval per Anthropic's contextual
        # retrieval technique). Fall back to raw text if enrichment is missing.
        if not enriched_chunks or len(enriched_chunks) != len(raw_chunks):
            enriched_chunks = list(raw_chunks)

        texts_for_embedding = [t for t, _ in enriched_chunks]
        raw_texts = [t for t, _ in raw_chunks]

        batch_size = 64
        all_vectors = []
        for i in range(0, len(texts_for_embedding), batch_size):
            batch = texts_for_embedding[i : i + batch_size]
            vecs = await loop.run_in_executor(None, embed_documents, batch)
            all_vectors.extend(vecs)

        # Store: keep raw content in `content`, enriched in `enriched_content`.
        from app.services.vector_store import add_chunks
        pages_list = [m["page"] for _, m in raw_chunks]
        indices_list = [m["chunk_index"] for _, m in raw_chunks]
        add_chunks(
            texts=raw_texts,
            enriched_texts=texts_for_embedding,
            embeddings=all_vectors,
            user_id=user_id,
            document_id=doc_id,
            pages=pages_list,
            chunk_indices=indices_list,
        )

        # Update document
        with engine.begin() as conn:
            update_sql = "UPDATE documents SET status = 'completed', chunk_count = :count"
            update_params: dict = {"id": doc_id, "count": len(raw_chunks)}
            if summary:
                update_sql += ", summary = :summary"
                update_params["summary"] = summary
            update_sql += " WHERE id = :id"
            conn.execute(sqltext(update_sql), update_params)

        logger.info(f"Ingestion completed for {doc_id}: {len(raw_chunks)} chunks")

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
                WHERE user_id = CAST(:user_id AS uuid)
                  AND 1 - (embedding <=> CAST(:qvec AS vector)) > 0.15
                GROUP BY document_id
                ORDER BY max_score DESC
                LIMIT 50
            """)
            rows = conn.execute(sql, {"qvec": qvec_str, "user_id": user_id}).fetchall()
            relevant_ids = [str(r[0]) for r in rows]
            if not relevant_ids:
                return {"items": []}

    with engine.begin() as conn:
        base_sql = "SELECT id, title, mime, status, summary, chunk_count FROM documents WHERE user_id = CAST(:user_id AS uuid)"
        params: dict = {"user_id": user_id}

        if relevant_ids is not None:
            base_sql += " AND id = ANY(:ids)"
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
    """Return the file content for an owned document. 404 (not 403) on mismatch."""
    user_id = await require_user(request)

    with engine.begin() as conn:
        row = conn.execute(
            sqltext("SELECT storage_path FROM documents WHERE id = :id AND user_id = CAST(:uid AS uuid)"),
            {"id": doc_id, "uid": user_id},
        ).first()
    if not row:
        # Hide existence — same response whether the doc doesn't exist or
        # belongs to someone else.
        raise HTTPException(404, "Document not found")

    storage_path = row[0]

    try:
        abs_path = get_file_abspath(storage_path)
    except FileNotFoundError:
        raise HTTPException(404, "File not found on disk")

    from fastapi.responses import FileResponse
    return FileResponse(abs_path, filename=os.path.basename(storage_path))


@router.delete("/documents/{doc_id}")
async def delete_document(request: Request, doc_id: str):
    """Delete a document (and its chunks via FK CASCADE) plus the file on disk.

    Enforces ownership; returns 404 if the document doesn't belong to the caller.
    """
    user_id = await require_user(request)

    with engine.begin() as conn:
        row = conn.execute(
            sqltext("SELECT storage_path FROM documents WHERE id = :id AND user_id = CAST(:uid AS uuid)"),
            {"id": doc_id, "uid": user_id},
        ).first()
        if not row:
            raise HTTPException(404, "Document not found")
        storage_path = row[0]

        # FK CASCADE removes chunks rows. Explicitly delete the document.
        conn.execute(
            sqltext("DELETE FROM documents WHERE id = :id AND user_id = CAST(:uid AS uuid)"),
            {"id": doc_id, "uid": user_id},
        )

    # Remove the file from disk best-effort.
    try:
        delete_file(storage_path)
    except Exception as e:
        logger.warning(f"File deletion failed for {storage_path}: {e}")

    return {"deleted": True, "id": doc_id}

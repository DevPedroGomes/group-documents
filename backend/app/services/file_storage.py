"""Local file storage service for uploaded documents."""

import os
import uuid as uuid_mod
import logging

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


# Map MIME → safe extension. Validated by the upload route via libmagic.
MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "text/plain": ".txt",
}


def ext_for_mime(mime: str) -> str:
    """Return a safe extension for the given (already-validated) mime type."""
    return MIME_TO_EXT.get((mime or "").lower(), ".bin")


def save_file(user_id: str, mime: str, data: bytes) -> str:
    """Save uploaded file to local filesystem under a UUID-based name.

    The original client-supplied filename is intentionally discarded — we
    derive the on-disk name from `uuid4()` plus an extension determined by
    the validated MIME type. This eliminates path-traversal and metadata
    leakage from filenames.

    Returns the relative storage path: `{user_id}/docs/{uuid4()}{ext}`.
    """
    settings = get_settings()
    ext = ext_for_mime(mime)
    safe_name = f"{uuid_mod.uuid4()}{ext}"
    rel_path = os.path.join(user_id, "docs", safe_name)
    abs_path = os.path.join(settings.uploads_path, rel_path)

    # Defense-in-depth: refuse to write outside the uploads root.
    uploads_root = os.path.realpath(settings.uploads_path)
    target_real = os.path.realpath(abs_path)
    if not (target_real == uploads_root or target_real.startswith(uploads_root + os.sep)):
        raise ValueError("Refusing to write outside uploads root")

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(abs_path, "wb") as f:
        f.write(data)

    logger.info(f"Saved file: {rel_path} ({len(data)} bytes)")
    return rel_path


def _resolve_in_uploads(storage_path: str) -> str:
    """Resolve `storage_path` under uploads_path and verify containment.

    Raises FileNotFoundError if the resolved path escapes the uploads root.
    """
    settings = get_settings()
    uploads_root = os.path.realpath(settings.uploads_path)
    candidate = os.path.realpath(os.path.join(settings.uploads_path, storage_path))
    if not (candidate == uploads_root or candidate.startswith(uploads_root + os.sep)):
        raise FileNotFoundError(f"Path escapes uploads root: {storage_path}")
    return candidate


def get_file(storage_path: str) -> bytes:
    """Read file from local filesystem by relative storage path."""
    abs_path = _resolve_in_uploads(storage_path)

    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {storage_path}")

    with open(abs_path, "rb") as f:
        return f.read()


def get_file_abspath(storage_path: str) -> str:
    """Return the validated absolute path of a stored file (for FileResponse)."""
    abs_path = _resolve_in_uploads(storage_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {storage_path}")
    return abs_path


def delete_file(storage_path: str) -> bool:
    """Delete file from local filesystem. Returns True if deleted."""
    try:
        abs_path = _resolve_in_uploads(storage_path)
    except FileNotFoundError:
        logger.warning(f"Refused to delete out-of-root path: {storage_path}")
        return False

    if os.path.isfile(abs_path):
        os.remove(abs_path)
        logger.info(f"Deleted file: {storage_path}")
        return True

    logger.warning(f"File not found for deletion: {storage_path}")
    return False

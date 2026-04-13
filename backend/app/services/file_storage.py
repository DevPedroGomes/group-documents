"""Local file storage service for uploaded documents."""

import os
import logging

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def save_file(user_id: str, filename: str, data: bytes) -> str:
    """Save uploaded file to local filesystem.

    Returns the relative storage path: {user_id}/docs/{filename}
    """
    settings = get_settings()
    rel_path = os.path.join(user_id, "docs", filename)
    abs_path = os.path.join(settings.uploads_path, rel_path)

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(abs_path, "wb") as f:
        f.write(data)

    logger.info(f"Saved file: {rel_path} ({len(data)} bytes)")
    return rel_path


def get_file(storage_path: str) -> bytes:
    """Read file from local filesystem by relative storage path."""
    settings = get_settings()
    abs_path = os.path.join(settings.uploads_path, storage_path)

    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {storage_path}")

    with open(abs_path, "rb") as f:
        return f.read()


def delete_file(storage_path: str) -> bool:
    """Delete file from local filesystem. Returns True if deleted."""
    settings = get_settings()
    abs_path = os.path.join(settings.uploads_path, storage_path)

    if os.path.isfile(abs_path):
        os.remove(abs_path)
        logger.info(f"Deleted file: {storage_path}")
        return True

    logger.warning(f"File not found for deletion: {storage_path}")
    return False

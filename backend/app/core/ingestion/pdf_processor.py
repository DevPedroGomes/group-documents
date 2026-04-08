"""PDF text extraction with page-level tracking."""

import io
import re

from pypdf import PdfReader


def extract_pages_from_pdf(data: bytes) -> list[str]:
    """Extract text from each page of a PDF. Returns list of page texts."""
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = re.sub(r"\s+", " ", text).strip()
        pages.append(text)
    return pages

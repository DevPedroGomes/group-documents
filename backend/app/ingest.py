from pypdf import PdfReader
import re
MAX_TOKENS = 700

def extract_pages_from_pdf(data: bytes) -> list[str]:
    import io
    r = PdfReader(io.BytesIO(data))
    pages = []
    for p in r.pages:
        txt = p.extract_text() or ''
        txt = re.sub(r'\s+',' ',txt).strip()
        pages.append(txt)
    return pages

def chunk_text(text: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    sents = re.split(r'(?<=[.!?])\s+', text)
    chunks, buf, count = [], [], 0
    for s in sents:
        ln = len(s.split())
        if count + ln > max_tokens and buf:
            chunks.append(' '.join(buf)); buf, count = [s], ln
        else:
            buf.append(s); count += ln
    if buf: chunks.append(' '.join(buf))
    return chunks

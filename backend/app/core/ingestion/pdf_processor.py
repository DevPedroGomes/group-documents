"""Extracao de PDF com rastreio por pagina e caminho visual para escaneados.

O buraco que isto fecha: `pypdf` so le a camada de TEXTO do PDF. Pagina que e
imagem — contrato escaneado, fatura fotografada, slide exportado como bitmap —
devolve string vazia. Antes essas paginas simplesmente sumiam do indice, sem
erro e sem aviso, e "o documento nao diz" era a resposta para algo que estava
escrito na pagina 3.

Agora cada pagina e classificada: se rendeu texto, segue pelo caminho textual,
que e barato. Se nao rendeu, e renderizada como imagem e vai para o caminho
visual, embedada direto pelo modelo multimodal. E o meio termo entre "so texto"
(rapido, cego para escaneado) e "toda pagina como imagem" (caro em
armazenamento e busca, e desnecessario num PDF nativo).
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from pypdf import PdfReader

from app.config.settings import get_settings
from app.core.ingestion.falhas import FalhaPermanente

logger = logging.getLogger(__name__)


@dataclass
class Pagina:
    numero: int          # 1-based
    texto: str           # o que a camada de texto rendeu, normalizado
    escaneada: bool = False
    """Texto curto demais: a pagina vai para o caminho visual, com o texto junto."""


def _normalizar(texto: str) -> str:
    """Colapsa espacos e tabs repetidos, mas preserva a quebra de linha.

    Colapsar todo whitespace num espaco so juntava as linhas de uma tabela numa
    linha unica: cada valor perdia a linha (e o rotulo) a que pertencia.
    """
    linhas = [re.sub(r"[ \t\f\v\u00a0]+", " ", linha).strip() for linha in texto.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()


def extract_pages_from_pdf(data: bytes) -> list[str]:
    """Texto de cada pagina. Mantida para quem so precisa do texto."""
    reader = PdfReader(io.BytesIO(data))
    return [_normalizar(page.extract_text() or "") for page in reader.pages]


def renderizar_paginas(data: bytes, numeros: Iterable[int], dpi: int) -> Iterator[tuple[int, object | None]]:
    """Renderiza as paginas pedidas (1-based) UMA POR VEZ, sob demanda.

    Antes todas as escaneadas viravam PIL.Image juntas, antes de qualquer uma
    ser processada: 300 paginas a 150 DPI sao ~1,9 GB de RAM no worker. Como
    gerador, so existe em memoria a pagina da vez e o que quem consome segurar.
    Pagina que nao renderiza vem com imagem None, e quem chama decide. Import
    tardio: PyMuPDF so entra em jogo quando existe pagina escaneada.
    """
    numeros = list(numeros)
    if not numeros:
        return
    try:
        import pymupdf
        from PIL import Image
    except ImportError as exc:
        logger.warning("PyMuPDF/Pillow indisponivel, paginas escaneadas sem render: %s", exc)
        for numero in numeros:
            yield numero, None
        return

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        logger.warning("Nao consegui abrir o PDF para render: %s", exc)
        for numero in numeros:
            yield numero, None
        return

    try:
        escala = dpi / 72.0
        matriz = pymupdf.Matrix(escala, escala)
        for numero in numeros:
            try:
                pix = doc[numero - 1].get_pixmap(matrix=matriz)
                imagem = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            except Exception as exc:
                logger.warning("Render da pagina %d falhou: %s", numero, exc)
                imagem = None
            yield numero, imagem
    finally:
        doc.close()


def extrair_paginas(data: bytes) -> list[Pagina]:
    """Paginas classificadas entre textual e escaneada. Nao renderiza nada.

    PDF com mais paginas que `max_pdf_pages` falha aqui, como permanente,
    antes de qualquer chamada paga.
    """
    settings = get_settings()
    minimo = settings.pdf_min_chars_por_pagina

    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    if total > settings.max_pdf_pages:
        raise FalhaPermanente(f"The PDF has more pages than the limit ({settings.max_pdf_pages}).")

    paginas = []
    for i, page in enumerate(reader.pages):
        texto = _normalizar(page.extract_text() or "")
        paginas.append(Pagina(numero=i + 1, texto=texto, escaneada=len(texto) < minimo))

    escaneadas = sum(p.escaneada for p in paginas)
    if escaneadas:
        logger.info("PDF: %d de %d paginas sem texto util vao para o caminho visual", escaneadas, total)
    return paginas

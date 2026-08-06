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
from dataclasses import dataclass
from typing import Optional

from pypdf import PdfReader

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Pagina:
    numero: int          # 1-based
    texto: str           # vazio quando a pagina e escaneada
    imagem: object | None = None   # PIL.Image quando escaneada

    @property
    def escaneada(self) -> bool:
        return self.imagem is not None


def extract_pages_from_pdf(data: bytes) -> list[str]:
    """Texto de cada pagina. Mantida para quem so precisa do texto."""
    reader = PdfReader(io.BytesIO(data))
    paginas = []
    for page in reader.pages:
        texto = page.extract_text() or ""
        paginas.append(re.sub(r"\s+", " ", texto).strip())
    return paginas


def _renderizar(data: bytes, indices: list[int], dpi: int) -> dict[int, object]:
    """Renderiza APENAS as paginas pedidas. Import tardio: PyMuPDF so entra em
    jogo quando existe pagina escaneada, e um PDF nativo nunca paga por ele."""
    if not indices:
        return {}
    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except ImportError as exc:
        logger.warning("PyMuPDF/Pillow indisponivel, paginas escaneadas serao puladas: %s", exc)
        return {}

    saida: dict[int, object] = {}
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        logger.warning("Nao consegui abrir o PDF para render: %s", exc)
        return {}

    try:
        escala = dpi / 72.0
        matriz = fitz.Matrix(escala, escala)
        for i in indices:
            try:
                pix = doc[i].get_pixmap(matrix=matriz)
                saida[i] = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            except Exception as exc:
                logger.warning("Render da pagina %d falhou: %s", i + 1, exc)
    finally:
        doc.close()

    return saida


def extrair_paginas(data: bytes) -> list[Pagina]:
    """Paginas classificadas entre textual e escaneada."""
    settings = get_settings()
    minimo = settings.pdf_min_chars_por_pagina

    reader = PdfReader(io.BytesIO(data))
    brutas: list[str] = []
    for page in reader.pages:
        texto = page.extract_text() or ""
        brutas.append(re.sub(r"\s+", " ", texto).strip())

    candidatas = [i for i, t in enumerate(brutas) if len(t) < minimo]
    renderizadas = _renderizar(data, candidatas, settings.pdf_render_dpi) if candidatas else {}

    if candidatas:
        logger.info(
            "PDF: %d de %d paginas sem texto util; %d renderizadas para o caminho visual",
            len(candidatas), len(brutas), len(renderizadas),
        )

    paginas: list[Pagina] = []
    for i, texto in enumerate(brutas):
        imagem = renderizadas.get(i)
        # Pagina vazia que nao pode ser renderizada e descartada mais adiante
        # por nao ter conteudo nenhum — nao vira chunk fantasma.
        paginas.append(Pagina(numero=i + 1, texto=texto, imagem=imagem))
    return paginas

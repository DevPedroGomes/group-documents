"""Ingestao de imagem, video e audio.

O que mudou e por que.

Antes, toda modalidade virava TEXTO antes de ser indexada: o Gemini escrevia
uma legenda da imagem e so a legenda era embedada. Isso tem um limite duro —
tudo que a legenda nao mencionou deixa de existir para a busca. Uma planta
baixa, um grafico ou uma tabela viram tres frases, e a pergunta que dependia do
numero no canto do grafico nunca encontra nada.

Alem disso o caminho estava morto de duas formas: o pacote
`google-generativeai` foi descontinuado pelo Google, e o modelo configurado
(`gemini-2.5-flash-preview-04-17`) e endpoint de preview ja retirado.

Agora:

- IMAGEM e VIDEO sao embedados DIRETO pelo `voyage-multimodal-3.5`, no mesmo
  espaco vetorial do texto. Uma pergunta escrita pode recuperar uma figura
  porque os dois vivem no mesmo espaco, nao porque alguem descreveu a figura.
- A descricao textual continua existindo, mas com outro papel: alimentar o
  BM25 (que so sabe casar palavra) e dar ao gerador algo legivel para citar.
  Ela nao e mais o que a busca vetorial enxerga. Sai do Claude, que ja esta
  configurado e pago — uma dependencia a menos.
- AUDIO o Voyage nao cobre. Vai para o Deepgram, a mesma chave que o
  Transcripts ja usa.

Cada funcao devolve `(texto, imagem_ou_none)`. Quem chama decide o que fazer:
com imagem, embeda a imagem; sem, embeda o texto.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Optional

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


# Formatos que o Claude aceita como bloco de imagem.
_MIMES_VISAO = {"image/png", "image/jpeg", "image/gif", "image/webp"}

_PROMPT_DESCRICAO = (
    "Describe this image for a document search index. Include any text visible "
    "in the image verbatim, plus the objects, chart values, table contents and "
    "layout that someone might search for later. Be factual and dense. No "
    "preamble."
)


def _abrir_imagem(data: bytes):
    """bytes -> PIL.Image, em RGB.

    Import tardio porque o Pillow so e necessario no caminho multimodal, e
    manter o import no topo faria o modulo inteiro falhar num ambiente que so
    processa PDF.
    """
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    # O Voyage rejeita alguns modos exoticos; RGB e o denominador comum.
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def descrever_imagem(data: bytes, mime: str) -> str:
    """Descricao textual da imagem, para BM25 e para o gerador citar.

    NAO e o que a busca vetorial usa — a imagem e embedada direto. Se falhar, a
    ingestao continua: perder a descricao custa recall no BM25, nao a imagem.
    """
    from app.core.llm_client import chat_complete

    settings = get_settings()
    mime = (mime or "").lower()
    if mime not in _MIMES_VISAO:
        # gif/webp exoticos ou mime desconhecido: converte para PNG.
        try:
            buf = io.BytesIO()
            _abrir_imagem(data).save(buf, format="PNG")
            data, mime = buf.getvalue(), "image/png"
        except Exception as exc:
            logger.warning("Nao consegui normalizar a imagem para descricao: %s", exc)
            return ""

    try:
        return chat_complete(
            model=settings.fast_model,
            max_tokens=400,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": base64.b64encode(data).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": _PROMPT_DESCRICAO},
                    ],
                }
            ],
        ).strip()
    except Exception as exc:
        logger.warning("Descricao da imagem falhou: %s", exc)
        return ""


def processar_imagem(data: bytes, mime: str) -> tuple[str, object]:
    """(descricao, PIL.Image). A imagem e o que vai para o embedding."""
    imagem = _abrir_imagem(data)
    return descrever_imagem(data, mime), imagem


def processar_video(data: bytes, filename: Optional[str] = None) -> tuple[str, object]:
    """(descricao, Video). O Voyage embeda o video direto, com os frames.

    Limite do provider: 20 MB por video — que coincide com o `max_file_size` do
    app, entao nao ha caso em que um upload aceito aqui seja recusado la.
    """
    from voyageai.video_utils import Video

    settings = get_settings()
    import tempfile
    import os

    sufixo = os.path.splitext(filename or "")[1] or ".mp4"
    caminho = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=sufixo) as tmp:
            tmp.write(data)
            caminho = tmp.name
        video = Video.from_path(caminho, model=settings.voyage_doc_model)
        # Descricao textual de video exigiria amostrar frames e mandar ao
        # Claude. Nao vale o custo: o vetor do video ja e o que a busca usa, e
        # o nome do arquivo cobre o caso de alguem procurar pelo titulo.
        return (filename or "video"), video
    finally:
        if caminho and os.path.exists(caminho):
            try:
                os.unlink(caminho)
            except OSError:
                pass


def transcrever_audio(data: bytes, mime: str) -> tuple[str, None]:
    """(transcricao, None). Audio nao tem embedding proprio: vira texto mesmo.

    O Voyage multimodal cobre texto, imagem e video, nao audio. Deepgram e a
    escolha aqui porque ja e o provider de audio do portfolio e a integracao ja
    esta provada no Transcripts.
    """
    settings = get_settings()
    if not settings.deepgram_api_key:
        raise RuntimeError(
            "DEEPGRAM_API_KEY nao configurada — ingestao de audio indisponivel"
        )

    resposta = httpx.post(
        "https://api.deepgram.com/v1/listen",
        params={
            "model": settings.deepgram_model,
            "smart_format": "true",
            "punctuate": "true",
            "detect_language": "true",
        },
        headers={
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": mime or "audio/mpeg",
        },
        content=data,
        timeout=300.0,
    )
    resposta.raise_for_status()
    corpo = resposta.json()

    try:
        return corpo["results"]["channels"][0]["alternatives"][0]["transcript"], None
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Resposta inesperada do Deepgram: {exc}") from exc

"""Ingestao de um documento: ler → chunk → enriquecer → embedar → gravar.

Movida de `api/routes/documents.py`. O caminho mais caro do app: o
enriquecimento contextual chama o LLM uma vez por chunk.

CONTRATO DE FALHA: esta funcao grava `documents.status = 'failed'` com uma
mensagem saneada em `meta.error` e RE-LANCA. Ela nao decide estado terminal de
job — isso e do envelope em `jobs/worker.py`, que sabe se ainda ha tentativa e
se a falha e permanente (`core/ingestion/falhas.py`).

Tudo que e sincrono e demorado (disco, SQL, LLM, embedding, render) vai para
thread com `asyncio.to_thread`: o worker roda varios jobs no mesmo event loop,
e uma chamada sincrona ali para todos eles, e o health check junto.
"""

import asyncio
import logging

from sqlalchemy import text as sqltext

from app.config.settings import get_settings
from app.db.engine import engine
from app.services.embedding import embed_sequences
from app.services.file_storage import get_file
from app.services.vector_store import add_chunks
from app.core.llm_client import chat_complete
from app.core.ingestion.falhas import FalhaPermanente, classificar
from app.core.ingestion.multimodal import descrever_imagem
from app.core.ingestion.chunker import chunk_document_pages, enrich_chunks_with_context

logger = logging.getLogger(__name__)

# Paginas escaneadas embedadas por vez: e quantas imagens ficam em memoria.
_LOTE_VISUAL = 8
# Lote do embedding final. Imagem pesa muito mais que texto no teto de 320k
# tokens por requisicao do Voyage.
_LOTE_EMBEDDING = 32


def _png(imagem) -> bytes:
    """PIL.Image -> bytes PNG. O Claude recebe bytes; o Voyage recebe a Image."""
    import io

    buf = io.BytesIO()
    imagem.save(buf, format="PNG")
    return buf.getvalue()


def _item_de_texto(texto: str, pagina: int, indice: int, enriquecido: str | None = None) -> dict:
    enr = enriquecido or texto
    return {"texto": texto, "enriquecido": enr, "sequencia": [enr], "vetor": None,
            "page": pagina, "chunk_index": indice}


def _marcar_processando(doc_id: str) -> tuple[str | None, str]:
    """Marca `processing`, limpa os chunks antigos e devolve (mime, titulo)."""
    with engine.begin() as conn:
        mime = conn.execute(
            sqltext("SELECT mime FROM documents WHERE id = :id"), {"id": doc_id}
        ).scalar()
        titulo = conn.execute(
            sqltext("SELECT title FROM documents WHERE id = :id"), {"id": doc_id}
        ).scalar()
        conn.execute(
            sqltext("UPDATE documents SET status = 'processing' WHERE id = :id"), {"id": doc_id}
        )
        # A fila e at-least-once e este job pode reexecutar (retentativa,
        # SIGTERM no meio do deploy). `add_chunks` e INSERT puro e nao ha
        # unique em (document_id, chunk_index), entao sem esta limpeza uma
        # reexecucao ANEXA um segundo conjunto completo de chunks: citacoes
        # duplicadas, `chunk_count` mentindo, e embedding pago duas vezes.
        # Fica na MESMA transacao que marca `processing`: ou o documento
        # entra em reprocessamento com o indice ja limpo, ou nao entra.
        conn.execute(sqltext("DELETE FROM chunks WHERE document_id = :id"), {"id": doc_id})
    return mime, titulo or ""


def _marcar_concluido(doc_id: str, total: int, summary: str | None) -> None:
    with engine.begin() as conn:
        # `meta - 'error'` apaga a causa da tentativa ANTERIOR. Sem isto o erro
        # fica grudado num documento que depois foi reprocessado com sucesso,
        # e a tela acusaria erro em documento que funcionou.
        update_sql = (
            "UPDATE documents SET status = 'completed', chunk_count = :count, "
            "meta = COALESCE(meta, '{}'::jsonb) - 'error'"
        )
        params: dict = {"id": doc_id, "count": total}
        if summary:
            update_sql += ", summary = :summary"
            params["summary"] = summary
        conn.execute(sqltext(update_sql + " WHERE id = :id"), params)


def _marcar_falha(doc_id: str, mensagem: str) -> None:
    # MERGE em `meta`, e nao troca: sobrescrever apagava o `source_url` de um
    # documento vindo de URL, e a origem dele sumia na primeira falha.
    with engine.begin() as conn:
        conn.execute(
            sqltext(
                "UPDATE documents SET status = 'failed', "
                "meta = COALESCE(meta, '{}'::jsonb) || jsonb_build_object('error', CAST(:err AS text)) "
                "WHERE id = :id"
            ),
            {"id": doc_id, "err": mensagem},
        )


async def _resumo(texto: str, o_que: str) -> str | None:
    """Resumo de 2-3 frases; falha vira resumo nenhum, sem derrubar a ingestao."""
    if not texto.strip():
        return None
    settings = get_settings()
    try:
        resposta = await asyncio.to_thread(
            chat_complete,
            model=settings.fast_model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f"Summarize this {o_que} in 2-3 sentences:\n\n{texto[:10000]}",
            }],
        )
        return resposta.strip() or None
    except Exception as e:
        logger.warning(f"Summary generation failed: {e}")
        return None


async def _itens_de_texto(brutos: list[tuple[str, dict]], texto_completo: str, titulo: str) -> list[dict]:
    """Chunks de texto com o contexto do enriquecimento, em todo fluxo de texto.

    Antes so o PDF era enriquecido; pagina da web e transcricao entravam crus,
    com a recuperacao pior justamente onde o texto e mais solto.
    """
    if not brutos:
        return []
    enriquecidos = await asyncio.to_thread(enrich_chunks_with_context, brutos, texto_completo, titulo)
    return [
        _item_de_texto(bruto, meta["page"], meta["chunk_index"], enr)
        for (bruto, meta), (enr, _) in zip(brutos, enriquecidos)
    ]


def _itens_visuais(data: bytes, paginas: list, primeiro_indice: int, dpi: int) -> list[dict]:
    """Paginas escaneadas: render, descricao e embedding, uma pagina por vez.

    Roda inteiro numa thread. As imagens sao embedadas em lotes de
    `_LOTE_VISUAL` e soltas em seguida: antes todas as paginas viravam imagem
    ao mesmo tempo e so eram embedadas no fim, com todas em memoria.
    """
    from app.core.ingestion.pdf_processor import renderizar_paginas

    por_numero = {p.numero: p for p in paginas}
    itens: list[dict] = []
    lote: list[dict] = []

    def embedar_lote() -> None:
        vetores = embed_sequences([it["sequencia"] for it in lote], "document")
        for item, vetor in zip(lote, vetores):
            item["vetor"] = vetor
            item["sequencia"] = None  # solta a imagem
        lote.clear()

    for numero, imagem in renderizar_paginas(data, list(por_numero), dpi):
        pagina = por_numero[numero]
        if imagem is None:
            # Sem render, o pouco texto que a pagina tinha ainda entra.
            if pagina.texto:
                itens.append(_item_de_texto(pagina.texto, numero, primeiro_indice + len(itens)))
            continue
        descricao = descrever_imagem(_png(imagem), "image/png")
        # A imagem e o que vai para o vetor; o texto curto da pagina e a
        # descricao entram junto para o BM25 ter palavra com que casar.
        texto = "\n\n".join(t for t in (pagina.texto, descricao) if t)
        item = {
            "texto": texto or f"[scanned page {numero}]",
            "enriquecido": texto or f"[scanned page {numero}]",
            "sequencia": [texto, imagem] if texto else [imagem],
            "vetor": None,
            "page": numero,
            "chunk_index": primeiro_indice + len(itens),
        }
        itens.append(item)
        lote.append(item)
        if len(lote) >= _LOTE_VISUAL:
            embedar_lote()
    if lote:
        embedar_lote()
    return itens


async def _itens_do_pdf(data: bytes, titulo: str) -> tuple[list[dict], str | None]:
    from app.core.ingestion.pdf_processor import extrair_paginas

    settings = get_settings()
    paginas = await asyncio.to_thread(extrair_paginas, data)
    if not paginas:
        raise FalhaPermanente("The PDF has no pages.")

    # Pagina com texto suficiente vai pelo caminho textual, que e barato; a
    # curta vai SO pelo visual, levando o texto dela junto. Antes ela entrava
    # pelos dois e virava dois chunks da mesma pagina.
    textuais = [p.texto if not p.escaneada else "" for p in paginas]
    texto_completo = "\n\n".join(p.texto for p in paginas if p.texto)
    brutos = await asyncio.to_thread(chunk_document_pages, textuais)
    itens = await _itens_de_texto(brutos, texto_completo, titulo)

    escaneadas = [p for p in paginas if p.escaneada]
    if escaneadas:
        itens += await asyncio.to_thread(
            _itens_visuais, data, escaneadas, len(itens), settings.pdf_render_dpi
        )

    return itens, await _resumo(texto_completo, "document")


def _tem_conteudo(item: dict) -> bool:
    if item["vetor"] is not None:
        return True
    return any(not isinstance(x, str) or x.strip() for x in item["sequencia"] or [])


async def process_ingestion(doc_id: str, user_id: str, storage_path: str):
    """Background task: read file → chunk → enrich → embed → store."""
    settings = get_settings()

    try:
        mime, titulo = await asyncio.to_thread(_marcar_processando, doc_id)
        mime = mime or ""

        data = await asyncio.to_thread(get_file, storage_path)
        if len(data) > settings.max_file_size:
            raise FalhaPermanente(
                f"The file is larger than the limit ({settings.max_file_size // (1024 * 1024)}MB)."
            )

        # Cada item vira uma linha em `chunks`. `sequencia` e o que o modelo
        # multimodal embeda: [texto] para texto, [descricao, imagem] para
        # imagem, [video] para video; `vetor` ja vem pronto quando o item foi
        # embedado no caminho (paginas escaneadas). Manter isso por item e o
        # que deixa pagina textual e escaneada conviverem no mesmo indice.
        itens: list[dict] = []
        summary = None

        if mime == "application/pdf":
            itens, summary = await _itens_do_pdf(data, titulo)

        elif mime.startswith("image/"):
            from app.core.ingestion.multimodal import processar_imagem

            descricao, imagem = await asyncio.to_thread(processar_imagem, data, mime)
            # A imagem SEMPRE entra no indice, mesmo se a descricao falhar: o
            # vetor vem dela, nao do texto.
            itens.append({
                "texto": descricao or f"[image] {storage_path.split('/')[-1]}",
                "enriquecido": descricao or "",
                "sequencia": ([descricao, imagem] if descricao else [imagem]),
                "vetor": None,
                "page": 1,
                "chunk_index": 0,
            })

        elif mime.startswith("audio/"):
            from app.core.ingestion.multimodal import transcrever_audio

            texto, _ = await asyncio.to_thread(transcrever_audio, data, mime)
            if not texto.strip():
                raise FalhaPermanente("No speech was found in the audio.")
            brutos = await asyncio.to_thread(chunk_document_pages, [texto])
            itens = await _itens_de_texto(brutos, texto, titulo)

        elif mime.startswith("video/"):
            from app.core.ingestion.multimodal import processar_video

            filename = storage_path.split("/")[-1]
            rotulo, video = await asyncio.to_thread(processar_video, data, filename)
            itens.append({
                "texto": rotulo,
                "enriquecido": rotulo,
                "sequencia": [video],
                "vetor": None,
                "page": 1,
                "chunk_index": 0,
            })

        elif mime == "text/plain":
            text = data.decode("utf-8", errors="replace")
            if not text.strip():
                raise FalhaPermanente("No text could be extracted from the page.")
            brutos = await asyncio.to_thread(chunk_document_pages, [text])
            itens = await _itens_de_texto(brutos, text, titulo)
            summary = await _resumo(text, "web page")

        else:
            raise FalhaPermanente("This file type is not supported.")

        # Descarta item sem conteudo nenhum para nao gravar chunk fantasma.
        itens = [i for i in itens if _tem_conteudo(i)]
        if not itens:
            raise FalhaPermanente("No content could be extracted from the document.")

        pendentes = [it for it in itens if it["vetor"] is None]
        for i in range(0, len(pendentes), _LOTE_EMBEDDING):
            lote = pendentes[i : i + _LOTE_EMBEDDING]
            vetores = await asyncio.to_thread(embed_sequences, [it["sequencia"] for it in lote], "document")
            for item, vetor in zip(lote, vetores):
                item["vetor"] = vetor

        # Store: keep raw content in `content`, enriched in `enriched_content`.
        await asyncio.to_thread(
            add_chunks,
            texts=[it["texto"] for it in itens],
            enriched_texts=[it["enriquecido"] for it in itens],
            embeddings=[it["vetor"] for it in itens],
            user_id=user_id,
            document_id=doc_id,
            pages=[it["page"] for it in itens],
            chunk_indices=[it["chunk_index"] for it in itens],
        )
        await asyncio.to_thread(_marcar_concluido, doc_id, len(itens), summary)

        logger.info(f"Ingestion completed for {doc_id}: {len(itens)} chunks")

    except Exception as e:
        falha = classificar(e)
        # O detalhe cru (corpo do provider, link de billing) fica so no log; o
        # documento recebe a categoria que a tela mostra.
        logger.error(
            "Ingestion failed for %s (%s): %s: %s",
            doc_id, "permanente" if falha.permanente else "transitoria", type(e).__name__, e,
        )
        try:
            await asyncio.to_thread(_marcar_falha, doc_id, falha.mensagem)
        except Exception:
            logger.exception("ingestao.marcar_falha_falhou doc_id=%s", doc_id)
        # Re-lanca de proposito: quem decide se isto e uma retentativa ou o fim
        # da linha e o envelope em `jobs/worker.py`, nao esta funcao. Engolir
        # aqui fazia o job terminar gravando `concluido` em `job_progress` para
        # um documento marcado como `failed`.
        raise

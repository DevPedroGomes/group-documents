"""Ingestao de um documento: ler → chunk → enriquecer → embedar → gravar.

Movida de `api/routes/documents.py`. O caminho mais caro do app: o
enriquecimento contextual chama o LLM uma vez por chunk.

CONTRATO DE FALHA: esta funcao grava `documents.status = 'failed'` e RE-LANCA.
Ela nao decide estado terminal de job — isso e do envelope em `jobs/worker.py`,
que e quem sabe se ainda ha tentativa sobrando. Enquanto a excecao morria aqui,
o envelope via sucesso e gravava `concluido` para um documento `failed`.
"""

import asyncio
import logging

from sqlalchemy import text as sqltext

from app.config.settings import get_settings
from app.db.engine import engine
from app.services.file_storage import get_file
from app.core.ingestion.multimodal import descrever_imagem
from app.core.ingestion.chunker import chunk_document_pages, enrich_chunks_with_context

logger = logging.getLogger(__name__)


def _png(imagem) -> bytes:
    """PIL.Image -> bytes PNG. O Claude recebe bytes; o Voyage recebe a Image."""
    import io

    buf = io.BytesIO()
    imagem.save(buf, format="PNG")
    return buf.getvalue()


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
            # A fila e at-least-once e este job pode reexecutar (retentativa,
            # SIGTERM no meio do deploy). `add_chunks` e INSERT puro e nao ha
            # unique em (document_id, chunk_index), entao sem esta limpeza uma
            # reexecucao ANEXA um segundo conjunto completo de chunks: citacoes
            # duplicadas, `chunk_count` mentindo, e embedding pago duas vezes.
            # Fica na MESMA transacao que marca `processing`: ou o documento
            # entra em reprocessamento com o indice ja limpo, ou nao entra.
            conn.execute(sqltext("DELETE FROM chunks WHERE document_id = :id"), {"id": doc_id})

        # Read file from local storage
        data = get_file(storage_path)

        if len(data) > settings.max_file_size:
            raise Exception(f"File too large (>{settings.max_file_size // (1024*1024)}MB)")

        # Cada item vira uma linha em `chunks`. `sequencia` e o que o modelo
        # multimodal embeda: [texto] para texto, [descricao, imagem] para
        # imagem, [video] para video. Manter isso explicito por item e o que
        # permite um mesmo documento ter pagina textual e pagina escaneada
        # convivendo no mesmo indice, no mesmo espaco vetorial.
        itens: list[dict] = []
        summary = None

        def _texto(texto: str, pagina: int, indice: int, enriquecido: str | None = None) -> dict:
            enr = enriquecido or texto
            return {"texto": texto, "enriquecido": enr, "sequencia": [enr],
                    "page": pagina, "chunk_index": indice}

        if mime == "application/pdf":
            from app.core.ingestion.pdf_processor import extrair_paginas

            paginas = await loop.run_in_executor(None, extrair_paginas, data)
            if not paginas:
                raise Exception("No content extracted from PDF")

            textuais = [p for p in paginas if p.texto]
            escaneadas = [p for p in paginas if p.escaneada]

            doc_title = ""
            with engine.begin() as conn:
                doc_title = conn.execute(
                    sqltext("SELECT title FROM documents WHERE id = :id"), {"id": doc_id}
                ).scalar() or ""

            # --- paginas com texto: caminho barato de sempre
            raw_chunks = chunk_document_pages(
                [p.texto if p.texto else "" for p in paginas]
            )
            full_text = " ".join(p.texto for p in textuais)

            enriched_chunks = raw_chunks
            if raw_chunks and full_text:
                enriched_chunks = await loop.run_in_executor(
                    None, enrich_chunks_with_context, raw_chunks, full_text, doc_title
                )

            for (bruto, meta), (enr, _) in zip(raw_chunks, enriched_chunks):
                itens.append(_texto(bruto, meta["page"], meta["chunk_index"], enr))

            # --- paginas escaneadas: caminho visual
            for pagina in escaneadas:
                descricao = await loop.run_in_executor(
                    None, descrever_imagem, _png(pagina.imagem), "image/png"
                )
                itens.append({
                    "texto": descricao or f"[scanned page {pagina.numero}]",
                    "enriquecido": descricao or f"[scanned page {pagina.numero}]",
                    # A imagem e o que vai para o vetor; a descricao entra junto
                    # para o BM25 ter palavra com que casar.
                    "sequencia": ([descricao, pagina.imagem] if descricao else [pagina.imagem]),
                    "page": pagina.numero,
                    "chunk_index": len(itens),
                })

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
            from app.core.ingestion.multimodal import processar_imagem

            descricao, imagem = await loop.run_in_executor(
                None, processar_imagem, data, mime
            )
            # A imagem SEMPRE entra no indice, mesmo se a descricao falhar: o
            # vetor vem dela, nao do texto. Antes, sem legenda nao havia
            # documento nenhum — a imagem era descartada.
            itens.append({
                "texto": descricao or f"[image] {storage_path.split('/')[-1]}",
                "enriquecido": descricao or "",
                "sequencia": ([descricao, imagem] if descricao else [imagem]),
                "page": 1,
                "chunk_index": 0,
            })

        elif mime and mime.startswith("audio/"):
            from app.core.ingestion.multimodal import transcrever_audio

            texto, _ = await loop.run_in_executor(
                None, transcrever_audio, data, mime
            )
            if not texto.strip():
                raise Exception("No speech detected in audio")
            from app.core.ingestion.chunker import chunk_text
            for ci, ck in enumerate(chunk_text(texto)):
                itens.append(_texto(ck, 1, ci))

        elif mime and mime.startswith("video/"):
            from app.core.ingestion.multimodal import processar_video

            filename = storage_path.split("/")[-1]
            rotulo, video = await loop.run_in_executor(
                None, processar_video, data, filename
            )
            itens.append({
                "texto": rotulo,
                "enriquecido": rotulo,
                "sequencia": [video],
                "page": 1,
                "chunk_index": 0,
            })

        elif mime == "text/plain":
            text = data.decode("utf-8", errors="replace")
            if not text.strip():
                raise Exception("No content extracted from URL")
            from app.core.ingestion.chunker import chunk_text
            for ci, ck in enumerate(chunk_text(text)):
                itens.append(_texto(ck, 1, ci))

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

        # Descarta item sem conteudo nenhum (pagina em branco que tambem nao
        # pode ser renderizada) para nao gravar chunk fantasma.
        itens = [i for i in itens if i["sequencia"] and (i["texto"] or len(i["sequencia"]) > 1
                 or not isinstance(i["sequencia"][0], str))]
        if not itens:
            raise Exception("No chunks generated")

        # Uma chamada de embedding cobre texto, imagem e video juntos: cada
        # item ja carrega a propria sequencia. Lote de 32 porque imagem pesa
        # muito mais que texto no teto de 320k tokens por requisicao.
        from app.services.embedding import embed_sequences

        batch_size = 32
        all_vectors: list[list[float]] = []
        for i in range(0, len(itens), batch_size):
            lote = [it["sequencia"] for it in itens[i : i + batch_size]]
            vecs = await loop.run_in_executor(None, embed_sequences, lote, "document")
            all_vectors.extend(vecs)

        # Store: keep raw content in `content`, enriched in `enriched_content`.
        from app.services.vector_store import add_chunks
        add_chunks(
            texts=[it["texto"] for it in itens],
            enriched_texts=[it["enriquecido"] for it in itens],
            embeddings=all_vectors,
            user_id=user_id,
            document_id=doc_id,
            pages=[it["page"] for it in itens],
            chunk_indices=[it["chunk_index"] for it in itens],
        )

        # Update document
        with engine.begin() as conn:
            update_sql = "UPDATE documents SET status = 'completed', chunk_count = :count"
            update_params: dict = {"id": doc_id, "count": len(itens)}
            if summary:
                update_sql += ", summary = :summary"
                update_params["summary"] = summary
            update_sql += " WHERE id = :id"
            conn.execute(sqltext(update_sql), update_params)

        logger.info(f"Ingestion completed for {doc_id}: {len(itens)} chunks")

    except Exception as e:
        logger.error(f"Ingestion failed for {doc_id}: {e}")
        with engine.begin() as conn:
            conn.execute(
                sqltext(
                    "UPDATE documents SET status = 'failed', meta = jsonb_build_object('error', :err) WHERE id = :id"
                ),
                {"id": doc_id, "err": str(e)},
            )
        # Re-lanca de proposito: quem decide se isto e uma retentativa ou o fim
        # da linha e o envelope em `jobs/worker.py`, nao esta funcao. Engolir
        # aqui — contrato correto sob `BackgroundTasks`, de onde este bloco veio
        # sem alteracao — tornava `tentar_de_novo` e `descartar` codigo morto
        # (`descartado` era inalcancavel), e fazia o job terminar gravando
        # `concluido` em `job_progress` para um documento marcado como `failed`:
        # duas fontes duraveis de verdade discordando, com a errada sendo a nova.
        raise

"""Document management routes: upload, ingest, list, preview, delete.

Toda rota aqui espera `require_user`, entao e `async def`; o SQL, o disco e o
embedding sao sincronos e vao por `run_in_threadpool`. No event loop (o uvicorn
roda um worker so) cada um deles congelaria todos os outros requests do app.
"""

import os
import re
import uuid
import logging
import asyncio
from datetime import date
from collections.abc import AsyncIterator
from typing import Optional

from fastapi import APIRouter, Query, Request, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import insert, text as sqltext
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
# `parse_options_header` pelo starlette, que resolve o nome do pacote
# python-multipart conforme a versao instalada.
from starlette.formparsers import MultiPartException, MultiPartParser, parse_options_header

from app.config.settings import get_settings
from app.db.engine import engine
from app.db.models import documents
from app.api.dependencies import consumir_cota, require_user
from app.services.file_storage import save_file, get_file_abspath, delete_file
from app.api.rate_limit import limiter
from agent_ops import metering
from agent_ops.decisions import digerir
from agent_ops.queue import (
    FilaCheia,
    FilaIndisponivel,
    enfileirar,
    job_id_de,
)

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


def _data_efetiva(valor: Optional[str]) -> Optional[date]:
    """`effective_date` opcional: a data em que o documento vale (emissao), que
    o recorte `as_of` usa no lugar da data de upload. So aceita YYYY-MM-DD e
    dia que existe; vazio vira None. Invalido levanta ValueError."""
    if valor is None or not valor.strip():
        return None
    valor = valor.strip()
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
            raise ValueError
        return date.fromisoformat(valor)
    except ValueError:
        raise ValueError(f"effective_date precisa ser uma data YYYY-MM-DD valida, recebido {valor!r}") from None


def validate_storage_path(path: str) -> bool:
    if not path:
        return False
    if ".." in path or path.startswith("/"):
        return False
    pattern = r"^[a-f0-9-]+/docs/[^/]+\.(pdf|png|jpg|jpeg|gif|webp|mp3|mp4|wav|webm|txt)$"
    return bool(re.match(pattern, path, re.IGNORECASE))


def _apagar_linha(doc_id) -> None:
    with engine.begin() as conn:
        conn.execute(sqltext("DELETE FROM documents WHERE id = :id"), {"id": doc_id})


def _inserir_documento(**valores) -> str:
    """Cria a linha do documento e devolve o id."""
    with engine.begin() as conn:
        return conn.execute(
            insert(documents).values(**valores).returning(documents.c.id)
        ).scalar_one()


async def _consumir_cota_de_ingestao() -> None:
    """Teto diario global de ingestoes, consumido ANTES de gravar arquivo ou linha.

    A ingestao e o caminho MAIS caro do app: o enriquecimento contextual chama o
    LLM uma vez por chunk. Consumir antes de gravar faz a recusa nao deixar nada
    para tras: nem documento fantasma no banco, nem arquivo orfao no volume.
    """
    await consumir_cota("ingest", get_settings().daily_ingest_limit)


async def _desfazer(storage_path: str | None, doc_id=None) -> None:
    """Devolve a cota e apaga o que ja foi gravado: a linha, se houver, e o arquivo.

    Nunca levanta: cada passo e melhor-esforco e registrado no log. Uma
    devolucao de cota perdida custa um pouco de folga; trocar o erro original
    por um erro do desfazimento esconderia do cliente o que aconteceu.
    """
    # Cota primeiro: e o unico que custa dinheiro ao proximo visitante.
    try:
        await metering.devolver("ingest")
    except Exception:
        logger.exception("ingest.devolucao_de_cota_falhou doc_id=%s", doc_id)

    if doc_id is not None:
        try:
            await run_in_threadpool(_apagar_linha, doc_id)
        except Exception:
            logger.exception("ingest.remocao_do_documento_falhou doc_id=%s", doc_id)

    if storage_path:
        try:
            await run_in_threadpool(delete_file, storage_path)
        except Exception:
            logger.warning(f"File deletion failed for {storage_path}", exc_info=True)


async def _gravar_e_registrar(user_id: str, mime: str, dados: bytes, **linha) -> tuple[str, str]:
    """Grava o arquivo e cria a linha `pending`, com a cota JA consumida.

    Devolve `(doc_id, storage_path)`. Se gravar ou registrar falhar, desfaz o
    que ja aconteceu (cota e arquivo) antes do 500.
    """
    storage_path = None
    try:
        storage_path = await run_in_threadpool(save_file, user_id, mime, dados)
        doc_id = await run_in_threadpool(
            _inserir_documento,
            user_id=user_id, mime=mime, storage_path=storage_path, status="pending", **linha,
        )
    except Exception as e:
        logger.error(f"Error storing document: {e}")
        await _desfazer(storage_path)
        raise HTTPException(500, "Error creating document record")
    return doc_id, storage_path


async def _enfileirar(request: Request, doc_id, user_id: str, storage_path: str) -> str:
    """Enfileira a ingestao e devolve o `job_id`; se a fila falhar, desfaz tudo.

    O digest identifica ESTA ingestao (a linha criada + o arquivo gravado), nao
    o CONTEUDO do arquivo: `doc_id` e `storage_path` sao novos a cada
    requisicao, entao reenviar o mesmo arquivo roda de novo. Dedup real exigiria
    hash do conteudo consultado ANTES do `consumir`, fora de escopo aqui. O que
    o digest entrega e um `job_id` deterministico, para o cliente acompanhar o
    proprio upload mesmo quando `enfileirar` devolve None.
    """
    digest = digerir(f"{doc_id}:{storage_path}")
    job_id = job_id_de(digest, tenant=user_id)
    try:
        await enfileirar(
            request.app.state.fila,
            "ingerir",
            str(doc_id), user_id, storage_path,
            digest=digest, tenant=user_id,
        )
    except Exception as exc:
        await _recusar_e_desfazer(doc_id, storage_path, exc)
    return job_id


async def _recusar_e_desfazer(doc_id, storage_path: str, exc: Exception) -> None:
    """Desfaz os efeitos ja aplicados quando a fila RECUSA o trabalho, e vira HTTP.

    A ordem das rotas e: consome a cota -> grava o arquivo -> cria a linha
    `pending` -> enfileira. Se o enfileiramento recusa, os TRES primeiros ja
    aconteceram, e nada os desfazia: a cota do dia era gasta por um trabalho que
    nunca rodou, o arquivo ficava orfao no volume, e a linha ficava `pending`
    para sempre — com o frontend consultando aquele documento a cada 3s, para
    sempre.

    O que o chamador recebe e o erro da FILA: trocar o 429 por um 500 esconderia
    o unico fato acionavel, que e "tente de novo". `FilaIndisponivel` (Redis
    ilegivel) vira 503 e `FilaCheia` vira 429: a fila cheia tem prazo para
    voltar, a queda de infra nao tem.
    """
    await _desfazer(storage_path, doc_id)

    if isinstance(exc, FilaIndisponivel):
        raise HTTPException(status_code=503, detail=exc.mensagem) from exc
    if isinstance(exc, FilaCheia):
        raise HTTPException(
            status_code=429,
            detail=exc.mensagem,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    # Qualquer outra falha ao enfileirar (Redis caindo no meio do
    # `enqueue_job`, por exemplo) desfaz igual; o detalhe fica no log.
    logger.error("ingest.enfileiramento_falhou doc_id=%s: %s", doc_id, exc, exc_info=exc)
    raise HTTPException(status_code=503, detail="The document could not be queued. Try again.") from exc


class IngestBody(BaseModel):
    storage_path: str
    title: str
    mime: str

    model_config = ConfigDict(str_strip_whitespace=True)


# Folga para o envelope multipart em volta do arquivo: boundaries, titulo, data.
_FOLGA_MULTIPART = 64 * 1024
_BLOCO_DE_LEITURA = 1024 * 1024


def _grande_demais(limite: int) -> HTTPException:
    return HTTPException(413, f"File too large (max {limite // (1024 * 1024)}MB)")


async def _contado(fluxo: AsyncIterator[bytes], teto: int, limite: int) -> AsyncIterator[bytes]:
    """Repassa o corpo e levanta 413 assim que ele passa de `teto` bytes.

    O parser do multipart grava o arquivo num temporario enquanto le; sem este
    contador, um upload sem Content-Length (chunked), ou com um Content-Length
    que mente, ia inteiro para o disco antes de qualquer checagem de tamanho.
    """
    lidos = 0
    async for bloco in fluxo:
        lidos += len(bloco)
        if lidos > teto:
            raise _grande_demais(limite)
        yield bloco


async def _ler_formulario(request: Request, limite: int) -> FormData:
    """O multipart do upload, lido com o corpo limitado a `limite` + a folga."""
    tipo, _ = parse_options_header(request.headers.get("content-type", ""))
    if tipo != b"multipart/form-data":
        raise HTTPException(422, "Expected multipart/form-data with 'file' and 'title'")
    parser = MultiPartParser(
        request.headers,
        _contado(request.stream(), limite + _FOLGA_MULTIPART, limite),
        max_files=1,
        max_fields=10,
    )
    try:
        return await parser.parse()
    except MultiPartException as exc:
        raise HTTPException(400, exc.message) from exc


async def _ler_ate_o_limite(arquivo: UploadFile, limite: int) -> bytes:
    """Le em blocos e desiste em `limite + 1` bytes, com 413.

    `await file.read()` trazia o arquivo inteiro para a memoria antes de olhar o
    tamanho, entao um upload sem Content-Length (ou com folga dentro dele)
    ocupava a RAM que o limite existia para proteger.
    """
    partes: list[bytes] = []
    lidos = 0
    while True:
        bloco = await arquivo.read(min(_BLOCO_DE_LEITURA, limite + 1 - lidos))
        if not bloco:
            return b"".join(partes)
        partes.append(bloco)
        lidos += len(bloco)
        if lidos > limite:
            raise _grande_demais(limite)


@router.post("/upload")
@limiter.limit("30/minute")
async def upload_file(request: Request):
    """Upload a file and trigger ingestion.

    Multipart com `file`, `title` e `effective_date` (opcional). O corpo e lido
    AQUI, e nao declarado como `File(...)`/`Form(...)`: o FastAPI leria e
    gravaria o multipart inteiro antes de chamar a rota, e nem o JWT nem o
    tamanho conseguiriam barrar um upload gigante antes de ele chegar. O
    Content-Length recusa cedo; o corpo contado recusa quem nao o manda.
    """
    user_id = await require_user(request)
    limite = get_settings().max_file_size

    declarado = request.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > limite + _FOLGA_MULTIPART:
        raise _grande_demais(limite)

    form = await _ler_formulario(request, limite)
    try:
        file, title, effective_date = form.get("file"), form.get("title"), form.get("effective_date")
        if not isinstance(file, UploadFile):
            raise HTTPException(422, "Field 'file' is required")
        if not isinstance(title, str):
            raise HTTPException(422, "Field 'title' is required")
        if not title or len(title) > 500:
            raise HTTPException(400, "Invalid title (max 500 characters)")
        if effective_date is not None and not isinstance(effective_date, str):
            raise HTTPException(422, "effective_date precisa ser uma data YYYY-MM-DD")
        # Antes de gravar arquivo ou gastar cota: data invalida nao deixa rastro.
        try:
            data_efetiva = _data_efetiva(effective_date)
        except ValueError as e:
            raise HTTPException(422, str(e))

        data = await _ler_ate_o_limite(file, limite)
        declared = (file.content_type or "").lower()
    finally:
        await form.close()

    if not data:
        raise HTTPException(400, "Empty file")

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
    if declared and declared in ALLOWED_MIMES:
        if ALLOWED_MIMES[declared] != ALLOWED_MIMES[sniffed_mime]:
            raise HTTPException(415, "Declared content-type does not match file contents")

    await _consumir_cota_de_ingestao()
    # Arquivo gravado com nome UUID e extensao do mime detectado.
    doc_id, storage_path = await _gravar_e_registrar(
        user_id, sniffed_mime, data, title=title, effective_date=data_efetiva,
    )

    job_id = await _enfileirar(request, doc_id, user_id, storage_path)

    return {"document_id": str(doc_id), "job_id": job_id, "status": "pending"}


class CrawlBody(BaseModel):
    url: str
    title: Optional[str] = None
    effective_date: Optional[date] = None

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("effective_date", mode="before")
    @classmethod
    def _valida_effective_date(cls, v):
        # O `date` do pydantic aceita mais formatos (numero, data e hora); o
        # contrato e so YYYY-MM-DD, igual ao upload.
        if v is None or isinstance(v, str):
            return _data_efetiva(v)
        raise ValueError("effective_date precisa ser uma data YYYY-MM-DD")


@router.post("/crawl")
@limiter.limit("10/minute")
async def crawl_url(request: Request, body: CrawlBody):
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

    # Resolve DNS (getaddrinfo), que bloqueia: em thread, como o fetch abaixo.
    ok, err = await run_in_threadpool(is_safe_url, body.url)
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

    await _consumir_cota_de_ingestao()
    doc_id, storage_path = await _gravar_e_registrar(
        user_id, "text/plain", text.encode("utf-8"),
        title=title, meta={"source_url": body.url}, effective_date=body.effective_date,
    )

    job_id = await _enfileirar(request, doc_id, user_id, storage_path)

    return {"document_id": str(doc_id), "job_id": job_id, "status": "pending", "title": title}


@router.post("/ingest")
@limiter.limit("30/minute")
async def ingest(request: Request, body: IngestBody):
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

    await _consumir_cota_de_ingestao()
    try:
        doc_id = await run_in_threadpool(
            _inserir_documento,
            user_id=user_id,
            title=body.title,
            mime=body.mime,
            storage_path=body.storage_path,
            status="pending",
        )
    except Exception as e:
        logger.error(f"DB error creating document: {e}")
        # O arquivo e da pessoa e ja existia antes desta rota: so a cota volta.
        await _desfazer(None)
        raise HTTPException(500, "Error creating document record")

    job_id = await _enfileirar(request, doc_id, user_id, body.storage_path)

    return {"document_id": str(doc_id), "job_id": job_id, "status": "pending"}


def _documentos_parecidos(qvec: list[float], user_id: str) -> list[str]:
    """Ids dos documentos com algum chunk perto da consulta, do mais parecido ao menos."""
    qvec_str = "[" + ",".join(map(str, qvec)) + "]"
    with engine.begin() as conn:
        rows = conn.execute(
            sqltext("""
                SELECT document_id, MAX(1 - (embedding <=> CAST(:qvec AS vector))) as max_score
                FROM chunks
                WHERE user_id = CAST(:user_id AS uuid)
                  AND 1 - (embedding <=> CAST(:qvec AS vector)) > 0.15
                GROUP BY document_id
                ORDER BY max_score DESC
                LIMIT 50
            """),
            {"qvec": qvec_str, "user_id": user_id},
        ).fetchall()
    return [str(r[0]) for r in rows]


def _listar_documentos(user_id: str, relevant_ids: list[str] | None):
    with engine.begin() as conn:
        # `erro` vinha sendo gravado em meta->>'error' e nunca projetado: a tela
        # mostrava um badge "Failed" sem causa e sem saida. `preso` cobre o
        # outro silencio — se a fila perde o job, o documento fica em
        # pending/processing para sempre e o browser faz polling indefinido.
        base_sql = (
            "SELECT id, title, mime, status, summary, chunk_count, effective_date, "
            "meta->>'error' AS erro, "
            "(status IN ('pending','processing') "
            " AND uploaded_at < now() - interval '30 minutes') AS preso "
            "FROM documents WHERE user_id = CAST(:user_id AS uuid)"
        )
        params: dict = {"user_id": user_id}

        if relevant_ids is not None:
            base_sql += " AND id = ANY(CAST(:ids AS uuid[]))"
            params["ids"] = relevant_ids
        else:
            base_sql += " ORDER BY uploaded_at DESC"

        return conn.execute(sqltext(base_sql), params).mappings().all()


async def _busca_semantica(consulta: str, user_id: str) -> list[str]:
    """Ids dos documentos parecidos com a consulta.

    Cada consulta e uma chamada paga ao Voyage, entao passa pelo teto diario do
    chat (o mesmo orcamento de busca do app); se o embedding falhar, nada foi
    cobrado e a cota volta.
    """
    from app.services.embedding_cache import get_query_embedding

    await consumir_cota("chat", get_settings().daily_chat_limit)
    try:
        qvec = await run_in_threadpool(get_query_embedding, consulta)
    except Exception as exc:
        await metering.devolver("chat")
        logger.warning("busca semantica da lista falhou: %s", exc)
        raise HTTPException(503, "Semantic search is temporarily unavailable") from exc
    return await run_in_threadpool(_documentos_parecidos, qvec, user_id)


def _sem_busca_semantica(request: Request) -> bool:
    # O limite vale so para a busca semantica: a lista pura e consultada a cada
    # 3s enquanto ha documento processando, e nao chama provider nenhum.
    return not (request.query_params.get("semantic_query") or "").strip()


@router.get("/documents")
@limiter.limit("20/minute", exempt_when=_sem_busca_semantica)
async def list_documents(
    request: Request,
    query: Optional[str] = None,
    semantic_query: Optional[str] = Query(None, max_length=500),
):
    user_id = await require_user(request)

    relevant_ids = None
    if semantic_query and semantic_query.strip():
        relevant_ids = await _busca_semantica(semantic_query.strip(), user_id)
        if not relevant_ids:
            return {"items": []}

    rows = await run_in_threadpool(_listar_documentos, user_id, relevant_ids)

    # O SELECT ja trazia `erro` e `preso` e o dict os descartava: o badge
    # "Failed"/"Stalled" da tela nunca tinha o que mostrar.
    items = [{
        "id": str(r["id"]),
        "title": r["title"],
        "mime": r["mime"],
        "status": r["status"],
        "summary": r.get("summary"),
        "chunk_count": r.get("chunk_count", 0),
        "erro": r.get("erro"),
        "preso": bool(r.get("preso")),
        "effective_date": r["effective_date"].isoformat() if r.get("effective_date") else None,
    } for r in rows]

    if query:
        ql = query.lower()
        items = [i for i in items if ql in i["title"].lower()]

    if relevant_ids:
        order_map = {rid: i for i, rid in enumerate(relevant_ids)}
        items.sort(key=lambda x: order_map.get(x["id"], 999))

    return {"items": items}


def _e_uuid(valor: str) -> bool:
    try:
        uuid.UUID(str(valor))
        return True
    except ValueError:
        return False


def _caminho_do_documento(doc_id: str, user_id: str) -> str | None:
    with engine.begin() as conn:
        row = conn.execute(
            sqltext("SELECT storage_path FROM documents WHERE id = :id AND user_id = CAST(:uid AS uuid)"),
            {"id": doc_id, "uid": user_id},
        ).first()
    return row[0] if row else None


@router.get("/document/{doc_id}/preview")
async def preview(request: Request, doc_id: str):
    """Return the file content for an owned document. 404 (not 403) on mismatch."""
    user_id = await require_user(request)

    # Id que nao e UUID nao e documento de ninguem: 404 igual ao de um id alheio.
    if not _e_uuid(doc_id):
        raise HTTPException(404, "Document not found")

    storage_path = await run_in_threadpool(_caminho_do_documento, doc_id, user_id)
    if not storage_path:
        # Hide existence — same response whether the doc doesn't exist or
        # belongs to someone else.
        raise HTTPException(404, "Document not found")

    try:
        abs_path = await run_in_threadpool(get_file_abspath, storage_path)
    except FileNotFoundError:
        raise HTTPException(404, "File not found on disk")

    from fastapi.responses import FileResponse
    return FileResponse(abs_path, filename=os.path.basename(storage_path))


def _apagar_documento(doc_id: str, user_id: str) -> str | None:
    """Apaga a linha (e os chunks, por FK CASCADE); devolve o arquivo dela, ou None."""
    with engine.begin() as conn:
        row = conn.execute(
            sqltext("DELETE FROM documents WHERE id = :id AND user_id = CAST(:uid AS uuid) "
                    "RETURNING storage_path"),
            {"id": doc_id, "uid": user_id},
        ).first()
    return row[0] if row else None


@router.delete("/documents/{doc_id}")
async def delete_document(request: Request, doc_id: str):
    """Delete a document (and its chunks via FK CASCADE) plus the file on disk.

    Enforces ownership; returns 404 if the document doesn't belong to the caller.
    """
    user_id = await require_user(request)

    storage_path = await run_in_threadpool(_apagar_documento, doc_id, user_id)
    if not storage_path:
        raise HTTPException(404, "Document not found")

    # Remove the file from disk best-effort.
    try:
        await run_in_threadpool(delete_file, storage_path)
    except Exception as e:
        logger.warning(f"File deletion failed for {storage_path}: {e}")

    return {"deleted": True, "id": doc_id}

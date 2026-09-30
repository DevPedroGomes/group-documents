"""Chat routes with SSE streaming and thread management."""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import insert, text as sqltext
from starlette.concurrency import iterate_in_threadpool

from app.config.settings import get_settings
from app.db.engine import engine
from app.db.models import threads, messages
from app.api.dependencies import require_user
from app.api.rate_limit import limiter
from agent_ops import metering
from app.core.guardrails.input_validator import validate_input
from app.core.rag.generator import stream_answer
from app.core.rag.transformer import transform_query
from app.core.rag.retriever import reconsultar, retrieve_documents
from app.core.rag.grader import grade_documents
from app.core.rag.conflict import detectar_conflito
from app.core.rag.web import buscar_na_web

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatBody(BaseModel):
    message: str
    document_ids: list[str] | None = None
    thread_id: str | None = None
    # Recorte temporal: responde com o acervo como ele estava nesta data.
    # Consulta vira auditoria quando da para perguntar "e em janeiro?".
    as_of: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("as_of")
    @classmethod
    def _valida_as_of(cls, v: str | None) -> str | None:
        """Data invalida tem que virar 422 aqui, nao erro de CAST no Postgres."""
        if not v:
            return None
        from datetime import datetime

        texto = v.replace("Z", "+00:00")
        try:
            datetime.fromisoformat(texto)
        except ValueError:
            raise ValueError("as_of precisa ser uma data ISO, como 2026-01-31 ou 2026-01-31T23:59:59Z")
        return texto


# --- Thread management ---

def create_thread(user_id: str) -> str:
    with engine.begin() as conn:
        thread_id = conn.execute(
            insert(threads).values(user_id=user_id).returning(threads.c.id)
        ).scalar_one()
    return str(thread_id)


def validate_thread_ownership(thread_id: str, user_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            sqltext("SELECT user_id FROM threads WHERE id = :thread_id"),
            {"thread_id": thread_id},
        ).first()
    if not result:
        return False
    return str(result[0]) == user_id


def get_thread_history(thread_id: str, user_id: str, limit: int = 20) -> list[dict]:
    """As `limit` mensagens mais RECENTES da thread, em ordem cronologica.

    O corte e feito do fim para o comeco e so depois reordenado: com `ASC LIMIT`
    a conversa longa entregava as primeiras mensagens, e o modelo deixava de
    ver justamente os turnos que a pergunta atual retoma.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            sqltext("""
                SELECT role, content, citations, created_at
                FROM (
                    SELECT m.id, m.role, m.content, m.citations, m.created_at
                    FROM messages m
                    JOIN threads t ON m.thread_id = t.id
                    WHERE m.thread_id = :thread_id AND t.user_id = CAST(:user_id AS uuid)
                    ORDER BY m.created_at DESC, m.id DESC
                    LIMIT :limit
                ) recentes
                ORDER BY created_at ASC, id ASC
            """),
            {"thread_id": thread_id, "user_id": user_id, "limit": limit},
        ).mappings().all()

    return [
        {
            "role": r["role"],
            "content": r["content"],
            "citations": r["citations"] if r["citations"] else None,
        }
        for r in rows
    ]


def save_message(
    thread_id: str, role: str, content: str, citations: list | None = None
) -> str | None:
    """Grava a mensagem e devolve o id dela.

    O id volta porque a trilha de decisao aponta para a resposta que produziu:
    sem ele a decisao ficaria orfa e o "por que ele respondeu isso?" nao teria
    ancora na conversa.
    """
    citations_json = json.dumps(citations) if citations else None
    with engine.begin() as conn:
        row = conn.execute(
            sqltext("""
                INSERT INTO messages (id, thread_id, role, content, citations, created_at)
                VALUES (gen_random_uuid(), :thread_id, :role, :content, CAST(:citations AS jsonb), NOW())
                RETURNING id
            """),
            {"thread_id": thread_id, "role": role, "content": content, "citations": citations_json},
        ).first()
        # Na mesma transacao: a lista de threads ordena por `updated_at`, e sem
        # isso a conversa mais recente nao subiria ao topo.
        conn.execute(
            sqltext("UPDATE threads SET updated_at = NOW() WHERE id = :thread_id"),
            {"thread_id": thread_id},
        )
    return str(row[0]) if row else None


def _e_web(d: dict) -> bool:
    return d.get("kind") == "web" or d.get("document_id") == "web"


def _resumo_trechos(docs: list[dict]) -> list[dict]:
    """Metadado dos trechos para a trilha: sem o texto, que ja vive em `chunks`.

    Resultado web entra com `document_id` nulo e a `url`: nao e documento do
    acervo, mas foi para o gerador e precisa aparecer no caminho da resposta.
    """
    return [
        {
            "document_id": None if _e_web(d) or not d.get("document_id") else str(d["document_id"]),
            "document_title": d.get("document_title"),
            "page": d.get("page"),
            "score": round(float(d.get("relevance_score", 0) or 0), 6),
            "score_scale": d.get("score_scale", "rrf"),
            "document_date": d.get("document_date"),
            "url": d.get("url"),
        }
        for d in docs
    ]


def _citacao(d: dict) -> dict:
    """Citacao no contrato do SSE `sources` (e em `messages.citations`)."""
    web = _e_web(d)
    return {
        "kind": "web" if web else "document",
        "document_id": None if web or not d.get("document_id") else str(d["document_id"]),
        "document_title": d.get("document_title") or "",
        "page": None if web else d.get("page"),
        "snippet": (d.get("snippet") or "")[:300],
        "document_date": None if web else d.get("document_date"),
        "url": d.get("url") if web else None,
    }


def save_decision(
    *,
    user_id: str,
    thread_id: str,
    message_id: str | None,
    question: str,
    retrieved: list[dict],
    graded: list[dict],
    web_used: bool,
    low_confidence: bool,
    answered: bool,
    latency_ms: int,
    conflict: dict | None = None,
    as_of: str | None = None,
    queries: list[str] | None = None,
) -> None:
    """Persiste o caminho que produziu uma resposta.

    Nunca derruba a resposta: se a gravacao da trilha falhar, o visitante ja
    recebeu o texto e perder a trilha e menos grave que devolver erro.
    """
    # A escala da decisao e a dos trechos do ACERVO: o score do Tavily entra em
    # `graded` com a propria etiqueta, mas nao diz se o rerank rodou.
    escala = next(
        (d.get("score_scale", "rrf") for d in [*graded, *retrieved] if not _e_web(d)),
        "rrf",
    )
    try:
        with engine.begin() as conn:
            conn.execute(
                sqltext("""
                    INSERT INTO decisions (
                        user_id, thread_id, message_id, question,
                        retrieved, graded, considered, kept,
                        score_scale, reranked, low_confidence, web_used,
                        answered, latency_ms, conflict, as_of, queries
                    ) VALUES (
                        :user_id, :thread_id, :message_id, :question,
                        CAST(:retrieved AS jsonb), CAST(:graded AS jsonb), :considered, :kept,
                        :score_scale, :reranked, :low_confidence, :web_used,
                        :answered, :latency_ms, CAST(:conflict AS jsonb),
                        CAST(:as_of AS timestamptz), CAST(:queries AS jsonb)
                    )
                """),
                {
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "message_id": message_id,
                    "question": question[:4000],
                    "retrieved": json.dumps(_resumo_trechos(retrieved)),
                    "graded": json.dumps(_resumo_trechos(graded)),
                    "considered": len(retrieved),
                    # Quantos trechos do acervo o grader manteve; web nao passa por ele.
                    "kept": sum(1 for d in graded if not _e_web(d)),
                    "score_scale": escala,
                    "reranked": escala == "cohere",
                    "low_confidence": low_confidence,
                    "web_used": web_used,
                    "answered": answered,
                    "latency_ms": latency_ms,
                    "conflict": json.dumps(conflict) if conflict else None,
                    "as_of": as_of,
                    "queries": json.dumps(list(queries or [])),
                },
            )
    except Exception:
        logger.warning("nao consegui gravar a trilha de decisao", exc_info=True)


# --- Chat endpoint (SSE streaming) ---

@router.post("/chat")
@limiter.limit("30/minute")
async def chat(request: Request, body: ChatBody):
    user_id = await require_user(request)

    # Input validation
    is_valid, reason = validate_input(body.message)
    if not is_valid:
        raise HTTPException(400, reason)

    # Thread management
    thread_id = body.thread_id
    if thread_id:
        if not validate_thread_ownership(thread_id, user_id):
            raise HTTPException(403, "Thread does not belong to this user")
    else:
        thread_id = create_thread(user_id)

    history = get_thread_history(thread_id, user_id)

    # Teto diario global, consumido ANTES de qualquer chamada paga. Vem depois
    # da validacao e da checagem de posse da thread, que sao gratis: pergunta
    # invalida nao deve gastar a cota do proximo visitante.
    try:
        await metering.consumir("chat", get_settings().daily_chat_limit)
    except metering.TetoIndisponivel as exc:
        # Backend de cota ilegivel: e indisponibilidade, nao limite atingido.
        # Sem `Retry-After`, porque ninguem sabe quando o Redis volta.
        raise HTTPException(status_code=503, detail=exc.mensagem) from exc
    except metering.TetoAtingido as exc:
        raise HTTPException(
            status_code=429,
            detail=exc.mensagem,
            headers={"Retry-After": str(metering.segundos_ate_meia_noite_utc())},
        ) from exc

    # Save user message
    save_message(thread_id, "user", body.message)

    async def generate_sse() -> AsyncGenerator[str, None]:
        """Generate SSE stream with workflow steps + streamed answer."""
        full_answer = ""
        citations = []

        # A trilha da decisao. Ate aqui esses numeros so existiam dentro do
        # painel de workflow, que some quando a pagina recarrega.
        iniciado_em = time.monotonic()
        recuperados: list[dict] = []
        aprovados: list[dict] = []
        baixa_confianca = False
        usou_web = False
        conflito: dict | None = None
        consultas: list[str] = []
        resultados_web: list[dict] = []
        reescrita: str | None = None

        # Todo passo caro daqui para baixo e sincrono (LLM, embedding, SQL) e
        # roda em thread, nunca no event loop. Com 1 worker do uvicorn, uma
        # unica pergunta segurava o loop por vezes dezenas de segundos e
        # congelava TODOS os outros requests do app: login, lista de
        # documentos, ate o /healthz. Mesmo padrao de process_ingestion.
        loop = asyncio.get_running_loop()

        try:
            # Step 1: Retrieve
            yield _sse("workflow", [{"step": "retrieve", "status": "in_progress", "details": "Searching documents..."}])

            # O historico vai junto para condensar pergunta de seguimento ("e em
            # marco de 2025?"): a busca usa a pergunta autocontida, o gerador
            # continua recebendo a original.
            recuperacao = await loop.run_in_executor(
                None,
                lambda: retrieve_documents(
                    question=body.message,
                    user_id=user_id,
                    document_ids=body.document_ids,
                    as_of=body.as_of,
                    top_k=5,
                    history=history,
                ),
            )
            documents = recuperacao.documents
            consultas = list(recuperacao.queries)

            recuperados = list(documents)
            workflow = [{"step": "retrieve", "status": "completed", "details": f"Found {len(documents)} chunks"}]
            yield _sse("workflow", workflow)

            # Step 2: Grade
            workflow.append({"step": "grade", "status": "in_progress", "details": "Analyzing relevance..."})
            yield _sse("workflow", workflow)

            filtered_docs, baixa_confianca = await loop.run_in_executor(
                None, grade_documents, documents
            )
            aprovados = list(filtered_docs)

            workflow[-1] = {
                "step": "grade",
                "status": "completed",
                "details": f"Kept {len(filtered_docs)}/{len(documents)} documents",
            }
            yield _sse("workflow", workflow)

            # Step 3: passo corretivo. Com baixa confianca, a pergunta e
            # reescrita e o ACERVO e reconsultado com ela, antes de qualquer
            # coisa sair do app. Antes a reescrita so alimentava o Tavily e o
            # acervo nunca era consultado de novo.
            if baixa_confianca:
                workflow.append({"step": "transform", "status": "in_progress", "details": "Rewriting query..."})
                yield _sse("workflow", workflow)

                reescrita = await loop.run_in_executor(None, transform_query, consultas[0])

                if reescrita.strip().casefold() == consultas[0].strip().casefold():
                    # Rewrite que devolve a mesma pergunta nao acha nada novo.
                    workflow[-1] = {
                        "step": "transform",
                        "status": "completed",
                        "details": "Rewrite gave the same question; kept the first search",
                    }
                    yield _sse("workflow", workflow)
                else:
                    workflow[-1] = {
                        "step": "transform",
                        "status": "completed",
                        "details": f"Rewrote: {reescrita[:80]}",
                    }
                    workflow.append({"step": "requery", "status": "in_progress", "details": "Searching documents again..."})
                    yield _sse("workflow", workflow)

                    documents = await loop.run_in_executor(
                        None,
                        lambda: reconsultar(
                            consulta=reescrita,
                            pergunta=consultas[0],
                            anteriores=recuperados,
                            user_id=user_id,
                            document_ids=body.document_ids,
                            as_of=body.as_of,
                            top_k=5,
                        ),
                    )
                    consultas.append(reescrita)
                    recuperados = list(documents)
                    workflow[-1] = {
                        "step": "requery",
                        "status": "completed",
                        "details": f"{len(documents)} chunks after merging both searches",
                    }
                    workflow.append({"step": "regrade", "status": "in_progress", "details": "Analyzing relevance again..."})
                    yield _sse("workflow", workflow)

                    filtered_docs, baixa_confianca = await loop.run_in_executor(
                        None, grade_documents, documents
                    )
                    aprovados = list(filtered_docs)
                    workflow[-1] = {
                        "step": "regrade",
                        "status": "completed",
                        "details": f"Kept {len(filtered_docs)}/{len(documents)} documents",
                    }
                    yield _sse("workflow", workflow)

            # Step 4: web, so se o acervo continua sem resposta DEPOIS da
            # reconsulta, e so com opt-in explicito: a pergunta sai do app para
            # um terceiro. Com `as_of` nunca: a web de hoje nao diz como era.
            settings = get_settings()
            if (
                baixa_confianca
                and settings.enable_web_fallback
                and settings.tavily_api_key
                and not body.as_of
            ):
                workflow.append({"step": "web_search", "status": "in_progress", "details": "Searching the web..."})
                yield _sse("workflow", workflow)

                try:
                    resultados_web = await loop.run_in_executor(
                        None, buscar_na_web, reescrita or consultas[0]
                    )
                    usou_web = bool(resultados_web)
                    detalhe = f"Found {len(resultados_web)} web results"
                except Exception as e:
                    logger.warning(f"Web search failed: {e}")
                    detalhe = "Web search unavailable"
                workflow[-1] = {"step": "web_search", "status": "completed", "details": detalhe}
                yield _sse("workflow", workflow)

            # Passo: as fontes divergem entre si?
            #
            # Roda depois do grade porque so interessa o que de fato sobrou, e
            # antes do generate porque o aviso acompanha a resposta na tela. O
            # portao e deterministico (dois ou mais documentos distintos), a
            # checagem e do modelo, e o resultado e AVISO: nada e filtrado.
            if len({d.get("document_id") for d in filtered_docs if d.get("document_id") != "web"}) >= 2:
                workflow.append({"step": "conflict", "status": "in_progress", "details": "Comparing sources..."})
                yield _sse("workflow", workflow)

                conflito = await loop.run_in_executor(None, detectar_conflito, filtered_docs)

                workflow[-1] = {
                    "step": "conflict",
                    "status": "completed",
                    "details": "Sources disagree" if conflito else "No disagreement found",
                }
                yield _sse("workflow", workflow)

                if conflito:
                    yield _sse("conflict", conflito)

            # Fontes: do acervo e da web, cada uma com o seu `kind`. Antes a web
            # ia para o gerador e ficava fora das citacoes.
            citations = [_citacao(d) for d in [*filtered_docs, *resultados_web]]
            if citations:
                yield _sse("sources", citations)

            # Step 3: Generate (streaming)
            workflow.append({"step": "generate", "status": "in_progress", "details": "Generating answer..."})
            yield _sse("workflow", workflow)

            async for token in iterate_in_threadpool(
                stream_answer(
                    question=body.message,
                    documents=filtered_docs,
                    history=history,
                    low_confidence=baixa_confianca,
                    web_results=resultados_web,
                )
            ):
                full_answer += token
                yield _sse("chunk", token)

            workflow[-1] = {"step": "generate", "status": "completed", "details": "Done"}
            yield _sse("workflow", workflow)

            # Done
            yield _sse("done", {"thread_id": thread_id, "low_confidence": baixa_confianca})

        except Exception as e:
            # O erro cru do provider NAO vai para a tela. Foi assim que uma
            # mensagem de rate limit da Voyage, com link do dashboard de
            # billing e nome do plano, apareceu para o visitante no meio do
            # stream. O detalhe fica no log, onde serve para depurar.
            logger.error(f"SSE generation error: {e}", exc_info=True)
            yield _sse("error", {"message": "Something went wrong answering that. Please try again."})

            # Nada foi gerado, entao nenhuma chamada paga de geracao aconteceu:
            # devolve a cota em vez de cobra-la do proximo visitante. Se ja
            # havia texto, o modelo rodou e o gasto foi real — nao devolve.
            if not full_answer:
                await metering.devolver("chat")

        finally:
            # Save assistant message
            message_id = None
            if full_answer:
                message_id = save_message(thread_id, "assistant", full_answer, citations)

            # A trilha e gravada mesmo quando a geracao falhou: saber ate onde
            # o pipeline chegou antes de quebrar e justamente o que se procura
            # depois. Nesse caso `message_id` fica nulo e `answered` falso.
            save_decision(
                user_id=user_id,
                thread_id=thread_id,
                message_id=message_id,
                question=body.message,
                retrieved=recuperados,
                # Web entra na trilha junto do que foi aprovado: foi ao gerador.
                graded=[*aprovados, *resultados_web],
                web_used=usou_web,
                low_confidence=baixa_confianca,
                answered=bool(full_answer),
                conflict=conflito,
                as_of=body.as_of,
                queries=consultas,
                latency_ms=int((time.monotonic() - iniciado_em) * 1000),
            )

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Trilha de decisao ---
#
# Responder com a fonte citada e o padrao do mercado. O que quase nenhum RAG
# mostra e o CAMINHO: quantos trechos foram considerados, quantos sobreviveram
# ao grader, em que escala esta o score, se o rerank rodou e se a resposta saiu
# da rede de seguranca. Sem isso, "por que ele respondeu isso?" nao tem resposta
# depois que a pagina recarrega.


@router.get("/decisions/{message_id}")
@limiter.limit("60/minute")
async def get_decision(request: Request, message_id: str):
    """Devolve a trilha que produziu uma resposta especifica."""
    user_id = await require_user(request)

    with engine.begin() as conn:
        row = conn.execute(
            sqltext("""
                SELECT id, thread_id, message_id, question, retrieved, graded,
                       considered, kept, score_scale, reranked, low_confidence,
                       web_used, answered, latency_ms, conflict, as_of, queries, created_at
                FROM decisions
                WHERE message_id = CAST(:message_id AS uuid) AND user_id = CAST(:user_id AS uuid)
            """),
            {"message_id": message_id, "user_id": user_id},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="No decision trail for that message")

    return _decision_payload(row)


@router.get("/decisions")
@limiter.limit("60/minute")
async def list_decisions(request: Request, thread_id: str | None = None, limit: int = 50):
    """Lista as trilhas do usuario, da mais recente para a mais antiga."""
    user_id = await require_user(request)
    limit = max(1, min(limit, 200))

    sql = """
        SELECT id, thread_id, message_id, question, retrieved, graded,
               considered, kept, score_scale, reranked, low_confidence,
               web_used, answered, latency_ms, conflict, as_of, queries, created_at
        FROM decisions
        WHERE user_id = CAST(:user_id AS uuid)
    """
    params: dict = {"user_id": user_id, "limit": limit}
    if thread_id:
        sql += " AND thread_id = CAST(:thread_id AS uuid)"
        params["thread_id"] = thread_id
    sql += " ORDER BY created_at DESC LIMIT :limit"

    with engine.begin() as conn:
        rows = conn.execute(sqltext(sql), params).mappings().all()

    return {"decisions": [_decision_payload(r) for r in rows]}


def _decision_payload(row) -> dict:
    """Formata a linha, explicando a escala do score em vez de so devolver o numero.

    Um score 0,03 e otimo em RRF e pessimo em Cohere. Mandar o numero cru para a
    tela sem dizer a escala foi exatamente o bug que fazia toda resposta sair
    com aviso de baixa confianca.
    """
    escala = row["score_scale"] or "rrf"
    # Em ingles porque e texto de tela, e o painel e em ingles.
    explicacao = {
        "cohere": "Calibrated reranker relevance, from 0 to 1.",
        "rrf": (
            "Rank fusion score (RRF) of the semantic and keyword searches. "
            "It stays around 0.01 to 0.03 even when the excerpt is a good match."
        ),
        "tavily": "Web search engine score, on its own scale.",
    }.get(escala, "Unknown score scale.")

    return {
        "id": str(row["id"]),
        "thread_id": str(row["thread_id"]) if row["thread_id"] else None,
        "message_id": str(row["message_id"]) if row["message_id"] else None,
        "question": row["question"],
        "retrieved": row["retrieved"],
        "graded": row["graded"],
        "considered": row["considered"],
        "kept": row["kept"],
        "score_scale": escala,
        "score_scale_hint": explicacao,
        "reranked": row["reranked"],
        "low_confidence": row["low_confidence"],
        "web_used": row["web_used"],
        "answered": row["answered"],
        "conflict": row["conflict"],
        "queries": row["queries"] or [],
        "as_of": row["as_of"].isoformat() if row["as_of"] else None,
        "latency_ms": row["latency_ms"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


# --- O acervo como grafo ---
#
# POR QUE ISTO E UM ENDPOINT E NAO UM BANCO DE GRAFO: a tentacao aqui e trocar
# Postgres por Neo4j. Nao vale. As arestas que interessam ja existem como
# relacao no schema (uma decisao usou um documento; dois documentos divergiram;
# uma pergunta puxou os mesmos arquivos que outra), e montar isso em SQL custa
# uma query. O valor do grafo esta na TELA, em ver o acervo se conectando; nao
# no motor. Trocar de banco traria multi-tenancy, backup e operacao novos para
# resolver um problema que o Postgres ja resolve neste tamanho.


def _montar_grafo(linhas) -> tuple[list[dict], list[dict], list[dict]]:
    """Nos de documento, nos de pergunta e arestas, a partir das decisoes.

    A aresta DIVERGE liga SO os documentos cujos titulos o aviso citou em
    `conflict.sources`, mapeados para `document_id` pelos trechos de `graded`
    da mesma decisao. Antes ligava todo par de documentos usados na resposta,
    e o grafo acusava de contradicao arquivos que nem estavam em causa. Titulo
    que nao mapeia (ou que mapeia para mais de um documento) nao gera aresta.
    """
    documentos: dict[str, dict] = {}
    perguntas: list[dict] = []
    arestas: list[dict] = []

    for linha in linhas:
        did_pergunta = f"q:{linha['id']}"
        perguntas.append({
            "id": did_pergunta,
            "type": "question",
            "label": (linha["question"] or "")[:90],
            "low_confidence": bool(linha["low_confidence"]),
            "created_at": linha["created_at"].isoformat() if linha["created_at"] else None,
        })

        usados = set()
        ids_por_titulo: dict[str, set[str]] = {}
        for trecho in (linha["graded"] or []):
            doc_id = trecho.get("document_id")
            # Resultado web nao e documento do acervo e nao vira no.
            if not doc_id or doc_id == "web" or trecho.get("url"):
                continue
            titulo = " ".join(str(trecho.get("document_title") or "").split()).casefold()
            ids_por_titulo.setdefault(titulo, set()).add(doc_id)
            no = documentos.setdefault(doc_id, {
                "id": f"d:{doc_id}",
                "type": "document",
                "label": trecho.get("document_title") or "documento",
                "uses": 0,
                "conflicts": 0,
            })
            if doc_id not in usados:
                no["uses"] += 1
                usados.add(doc_id)
                arestas.append({
                    "source": did_pergunta,
                    "target": no["id"],
                    "type": "USOU",
                    "score": trecho.get("score"),
                })

        # A divergencia liga documento a documento, e e o par que o usuario
        # precisa abrir: sao os dois arquivos que dizem coisas diferentes.
        conflito = linha["conflict"] or {}
        if not conflito.get("summary"):
            continue
        citados: list[str] = []
        for fonte in conflito.get("sources") or []:
            ids = ids_por_titulo.get(" ".join(str(fonte).split()).casefold(), set())
            if len(ids) == 1 and next(iter(ids)) not in citados:
                citados.append(next(iter(ids)))
        for i in range(len(citados)):
            for j in range(i + 1, len(citados)):
                arestas.append({
                    "source": f"d:{citados[i]}",
                    "target": f"d:{citados[j]}",
                    "type": "DIVERGE",
                    "summary": conflito["summary"][:200],
                })
                for k in (citados[i], citados[j]):
                    documentos[k]["conflicts"] += 1

    return list(documentos.values()), perguntas, arestas


@router.get("/graph")
@limiter.limit("30/minute")
async def knowledge_graph(request: Request, days: int = 30, limit: int = 300):
    """Monta o grafo do acervo a partir do que a trilha de decisao ja registrou.

    Nos:   documento (tamanho = quantas vezes foi usado numa resposta)
           pergunta  (uma por decisao)
    Arestas:
      pergunta -> documento : USOU      (o trecho sobreviveu ao grader)
      documento -> documento: DIVERGE   (a checagem apontou contradicao)
    """
    user_id = await require_user(request)
    days = max(1, min(days, 365))
    limit = max(10, min(limit, 1000))

    with engine.begin() as conn:
        linhas = conn.execute(
            sqltext("""
                SELECT id, question, graded, conflict, low_confidence, created_at
                FROM decisions
                WHERE user_id = CAST(:user_id AS uuid)
                  AND created_at >= NOW() - CAST(:janela AS interval)
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "janela": f"{days} days", "limit": limit},
        ).mappings().all()

    documentos, perguntas, arestas = _montar_grafo(linhas)

    return {
        "nodes": documentos + perguntas,
        "edges": arestas,
        "window_days": days,
        "legend": {
            "document": "Documento do seu acervo. Quanto maior, mais respostas ele sustentou.",
            "question": "Uma pergunta feita ao acervo.",
            "USOU": "A resposta se apoiou neste documento.",
            "DIVERGE": "Estes dois documentos se contradisseram numa resposta.",
        },
    }


@router.get("/threads")
async def list_threads(request: Request):
    """List user's conversation threads."""
    user_id = await require_user(request)

    with engine.begin() as conn:
        rows = conn.execute(
            sqltext("""
                SELECT t.id, t.title, t.updated_at,
                       (SELECT content FROM messages WHERE thread_id = t.id ORDER BY created_at ASC LIMIT 1) as first_message
                FROM threads t
                WHERE t.user_id = :user_id
                ORDER BY t.updated_at DESC
                LIMIT 50
            """),
            {"user_id": user_id},
        ).mappings().all()

    return {
        "threads": [
            {
                "id": str(r["id"]),
                "title": r["title"] or (r["first_message"][:50] + "..." if r["first_message"] and len(r["first_message"]) > 50 else r["first_message"]),
                "updated_at": str(r["updated_at"]),
            }
            for r in rows
        ]
    }


@router.get("/threads/{thread_id}/messages")
async def get_messages(request: Request, thread_id: str):
    """Get messages for a thread."""
    user_id = await require_user(request)

    if not validate_thread_ownership(thread_id, user_id):
        raise HTTPException(403, "Thread does not belong to this user")

    history = get_thread_history(thread_id, user_id, limit=100)
    return {"messages": history, "thread_id": thread_id}


@router.delete("/threads/{thread_id}")
async def delete_thread(request: Request, thread_id: str):
    """Delete a thread and its messages (LGPD compliance). Ownership enforced."""
    user_id = await require_user(request)

    with engine.begin() as conn:
        row = conn.execute(
            sqltext("SELECT id FROM threads WHERE id = :id AND user_id = CAST(:uid AS uuid)"),
            {"id": thread_id, "uid": user_id},
        ).first()
        if not row:
            raise HTTPException(404, "Thread not found")

        # FK CASCADE removes message rows.
        conn.execute(
            sqltext("DELETE FROM threads WHERE id = :id AND user_id = CAST(:uid AS uuid)"),
            {"id": thread_id, "uid": user_id},
        )

    return {"deleted": True, "id": thread_id}


def _sse(event_type: str, data) -> str:
    """Format a Server-Sent Event."""
    return f"data: {json.dumps({'type': event_type, 'data': data})}\n\n"

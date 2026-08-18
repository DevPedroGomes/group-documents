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
from app.core import budget
from app.core.guardrails.input_validator import validate_input
from app.core.rag.generator import stream_answer
from app.core.rag.transformer import transform_query
from app.core.rag.retriever import retrieve_documents
from app.core.rag.grader import grade_documents
from app.core.rag.conflict import detectar_conflito

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
    with engine.begin() as conn:
        rows = conn.execute(
            sqltext("""
                SELECT m.role, m.content, m.citations, m.created_at
                FROM messages m
                JOIN threads t ON m.thread_id = t.id
                WHERE m.thread_id = :thread_id AND t.user_id = CAST(:user_id AS uuid)
                ORDER BY m.created_at ASC
                LIMIT :limit
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
    return str(row[0]) if row else None


def _resumo_trechos(docs: list[dict]) -> list[dict]:
    """Metadado dos trechos para a trilha: sem o texto, que ja vive em `chunks`."""
    return [
        {
            "document_id": str(d.get("document_id", "")),
            "document_title": d.get("document_title"),
            "page": d.get("page"),
            "score": round(float(d.get("relevance_score", 0) or 0), 6),
            "score_scale": d.get("score_scale", "rrf"),
        }
        for d in docs
    ]


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
) -> None:
    """Persiste o caminho que produziu uma resposta.

    Nunca derruba a resposta: se a gravacao da trilha falhar, o visitante ja
    recebeu o texto e perder a trilha e menos grave que devolver erro.
    """
    escala = (graded or retrieved or [{}])[0].get("score_scale", "rrf")
    try:
        with engine.begin() as conn:
            conn.execute(
                sqltext("""
                    INSERT INTO decisions (
                        user_id, thread_id, message_id, question,
                        retrieved, graded, considered, kept,
                        score_scale, reranked, low_confidence, web_used,
                        answered, latency_ms, conflict, as_of
                    ) VALUES (
                        :user_id, :thread_id, :message_id, :question,
                        CAST(:retrieved AS jsonb), CAST(:graded AS jsonb), :considered, :kept,
                        :score_scale, :reranked, :low_confidence, :web_used,
                        :answered, :latency_ms, CAST(:conflict AS jsonb),
                        CAST(:as_of AS timestamptz)
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
                    "kept": len(graded),
                    "score_scale": escala,
                    "reranked": escala == "cohere",
                    "low_confidence": low_confidence,
                    "web_used": web_used,
                    "answered": answered,
                    "latency_ms": latency_ms,
                    "conflict": json.dumps(conflict) if conflict else None,
                    "as_of": as_of,
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
        await budget.consumir("chat", get_settings().daily_chat_limit)
    except budget.TetoAtingido as exc:
        raise HTTPException(
            status_code=503,
            detail=exc.mensagem,
            headers={"Retry-After": str(budget.segundos_ate_meia_noite_utc())},
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

        # Todo passo caro daqui para baixo e sincrono (LLM, embedding, SQL) e
        # roda em thread, nunca no event loop. Com 1 worker do uvicorn, uma
        # unica pergunta segurava o loop por vezes dezenas de segundos e
        # congelava TODOS os outros requests do app: login, lista de
        # documentos, ate o /healthz. Mesmo padrao de process_ingestion.
        loop = asyncio.get_running_loop()

        try:
            # Step 1: Retrieve
            yield _sse("workflow", [{"step": "retrieve", "status": "in_progress", "details": "Searching documents..."}])

            documents = await loop.run_in_executor(
                None,
                lambda: retrieve_documents(
                    question=body.message,
                    user_id=user_id,
                    document_ids=body.document_ids,
                    as_of=body.as_of,
                    top_k=5,
                ),
            )

            recuperados = list(documents)
            workflow = [{"step": "retrieve", "status": "completed", "details": f"Found {len(documents)} chunks"}]
            yield _sse("workflow", workflow)

            # Step 2: Grade
            workflow.append({"step": "grade", "status": "in_progress", "details": "Analyzing relevance..."})
            yield _sse("workflow", workflow)

            filtered_docs, needs_web = await loop.run_in_executor(
                None, grade_documents, documents
            )
            # `needs_web` do grader significa "o lote recuperado foi ruim": e a
            # mesma condicao que sinaliza baixa confianca na resposta.
            aprovados = list(filtered_docs)
            baixa_confianca = bool(needs_web)

            workflow[-1] = {
                "step": "grade",
                "status": "completed",
                "details": f"Kept {len(filtered_docs)}/{len(documents)} documents",
            }
            yield _sse("workflow", workflow)

            # Step 3: Transform + Web Search (if needed)
            #
            # O rewrite so serve de entrada para a busca web. Sem TAVILY_API_KEY
            # o resultado dele era calculado, mostrado no painel e jogado fora:
            # uma chamada paga de LLM por pergunta, sem efeito nenhum na
            # resposta. Nao vale pagar por um rewrite que nao tem para onde ir.
            settings = get_settings()
            if needs_web and settings.tavily_api_key:
                workflow.append({"step": "transform", "status": "in_progress", "details": "Rewriting query..."})
                yield _sse("workflow", workflow)

                transformed_query = await loop.run_in_executor(
                    None, transform_query, body.message
                )

                workflow[-1] = {
                    "step": "transform",
                    "status": "completed",
                    "details": f"Rewrote: {transformed_query[:80]}...",
                }
                yield _sse("workflow", workflow)

                # Web search fallback
                workflow.append({"step": "web_search", "status": "in_progress", "details": "Searching the web..."})
                yield _sse("workflow", workflow)

                try:
                    from tavily import TavilyClient
                    tavily_client = TavilyClient(api_key=settings.tavily_api_key)
                    web_results = await loop.run_in_executor(
                        None,
                        lambda: tavily_client.search(transformed_query, max_results=3),
                    )

                    for r in web_results.get("results", []):
                        filtered_docs.append({
                            "document_id": "web",
                            "document_title": r.get("title", "Web Result"),
                            "page": 0,
                            "snippet": r.get("content", "")[:500],
                            "relevance_score": r.get("score", 0.5),
                            # Score do Tavily, nem RRF nem Cohere. Marcado para
                            # nao ser confundido com nenhuma das duas escalas.
                            "score_scale": "tavily",
                        })

                    usou_web = True
                    workflow[-1] = {"step": "web_search", "status": "completed", "details": f"Found {len(web_results.get('results', []))} web results"}
                    yield _sse("workflow", workflow)
                except Exception as e:
                    logger.warning(f"Web search failed: {e}")
                    workflow[-1] = {"step": "web_search", "status": "completed", "details": "Web search unavailable"}
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

            # Send sources
            if filtered_docs:
                sources = [
                    {
                        "document_id": d["document_id"],
                        "document_title": d["document_title"],
                        "page": d["page"],
                        "snippet": d["snippet"][:200],
                    }
                    for d in filtered_docs
                    if d.get("document_id") != "web"
                ]
                citations = sources
                yield _sse("sources", sources)

            # Step 3: Generate (streaming)
            workflow.append({"step": "generate", "status": "in_progress", "details": "Generating answer..."})
            yield _sse("workflow", workflow)

            async for token in iterate_in_threadpool(
                stream_answer(
                    question=body.message,
                    documents=filtered_docs,
                    history=history,
                )
            ):
                full_answer += token
                yield _sse("chunk", token)

            workflow[-1] = {"step": "generate", "status": "completed", "details": "Done"}
            yield _sse("workflow", workflow)

            # Done
            yield _sse("done", {"thread_id": thread_id})

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
                await budget.devolver("chat")

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
                graded=aprovados,
                web_used=usou_web,
                low_confidence=baixa_confianca,
                answered=bool(full_answer),
                conflict=conflito,
                as_of=body.as_of,
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
                       web_used, answered, latency_ms, conflict, as_of, created_at
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
               web_used, answered, latency_ms, conflict, as_of, created_at
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
    explicacao = {
        "cohere": "Score calibrado do reranker, de 0 a 1.",
        "rrf": "Score de fusao das buscas (RRF). Fica na casa de 0,01 a 0,03 mesmo quando o trecho e bom.",
        "tavily": "Score do buscador web, criterio proprio.",
    }.get(escala, "Escala nao identificada.")

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
        for trecho in (linha["graded"] or []):
            doc_id = trecho.get("document_id")
            if not doc_id or doc_id == "web":
                continue
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
        if conflito.get("summary"):
            ids = [d for d in usados]
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = f"d:{ids[i]}", f"d:{ids[j]}"
                    arestas.append({
                        "source": a,
                        "target": b,
                        "type": "DIVERGE",
                        "summary": conflito["summary"][:200],
                    })
                    for k in (ids[i], ids[j]):
                        documentos[k]["conflicts"] += 1

    return {
        "nodes": list(documentos.values()) + perguntas,
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

"""Conversa por voz sobre o acervo. O RAG vira uma tool, não o meio do pipeline.

DE ONDE ISTO VEIO
-----------------
A camada de tempo real foi construída no repositório `voice_rag` e migrada para
cá em 06/09/2026. Lá ela estava certa e não tinha por quê: um chat de provedor
faz voz sobre um PDF melhor do que nós. Aqui ela tem, porque aqui existe acervo,
isolamento por usuário e trilha de decisão. Ver `voice_rag/docs/EXTRACAO-PARA-BRAINHUB.md`.

O DESENHO
---------
    navegador ⟷ WebRTC ⟷ OpenAI Realtime
                              ↓ function_call
                    buscar_no_acervo() aqui dentro
                    retrieve → grade → conflito → decisions

O modelo fala e escuta ao mesmo tempo, e chama a busca no meio da frase. O ganho
não é só latência: a pessoa interrompe, muda de assunto e volta, e a conversa
continua, porque não existe mais "um turno" a concluir.

O QUE MUDA EM RELAÇÃO AO ORIGINAL, E É O MOTIVO DA MIGRAÇÃO
------------------------------------------------------------
Lá a tool devolvia os trechos e acabava. Aqui ela **grava em `decisions`**: quais
trechos entraram, quantos o grader manteve, se houve divergência entre fontes,
qual recorte no tempo foi usado, e quanto demorou.

Consequência: a trilha passa a ser escrita **enquanto a pessoa fala**, e o painel
ao lado se preenche sozinho. Um chat de provedor devolve a resposta; este devolve
a resposta e o caminho até ela.

POR QUE O ÁUDIO NÃO PASSA POR AQUI
----------------------------------
Proxiar mídia em tempo real numa KVM de 2 vCPU seria o gargalo, e o WebRTC já
resolve jitter, perda de pacote e eco. O backend entra só onde precisa mandar:
cunhar a credencial efêmera e executar a busca. A chave real nunca sai daqui.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert

from agent_ops import metering
from app.api.dependencies import consumir_cota, require_user
from app.api.rate_limit import limiter
from app.api.routes.chat import (
    MAX_DOCUMENTOS_SELECIONADOS,
    save_decision,
    validar_data_iso,
    validar_ids_de_documento,
    validate_thread_ownership,
)
from app.config.settings import get_settings
from app.core import chamadas_pagas
from app.core.guardrails.input_validator import validate_input
from app.core.rag.conflict import detectar_conflito
from app.core.rag.grader import grade_documents
from app.core.rag.retriever import retrieve_documents
from app.db.engine import engine
from app.db.models import threads

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/realtime", tags=["realtime"])

CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"

FERRAMENTA_BUSCA = {
    "type": "function",
    "name": "buscar_no_acervo",
    "description": (
        "Search this person's document archive and return the passages that answer "
        "their question. Call this whenever the answer depends on what is in their "
        "documents — never answer from memory. Rephrase what they said into a clear "
        "search question: spoken language is terse and the documents are not. "
        "Pass `data_de_referencia` only when the person asks about a specific point "
        "in time, like 'what was the policy in March 2025'. "
        # O orcamento de latencia vive AQUI, e nao so no prompt: o modelo le a
        # descricao no instante em que decide chamar, que e quando precisa saber.
        "This search takes several seconds to run, and the person hears silence the "
        "whole time — always say a short holding phrase out loud before calling it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pergunta": {
                "type": "string",
                "description": "The question to search for, as a full sentence.",
            },
            "data_de_referencia": {
                "type": "string",
                "description": "ISO date (YYYY-MM-DD) to answer as of that date. Omit for today.",
            },
        },
        "required": ["pergunta"],
        "additionalProperties": False,
    },
}

# As duas obrigações do meio são o que o acervo permite e o chat de provedor não:
# ele recupera as duas versões de uma política, escolhe uma e soa confiante.
INSTRUCOES = """\
You are a voice assistant that answers strictly from this person's document \
archive. You are speaking out loud — there is no screen.

Language: answer in the language the person speaks to you. If they switch, switch \
with them, without commenting on it.

Before answering anything about the archive, call buscar_no_acervo. Never answer \
from memory, and never guess a number, name or date that did not come back from \
the search.

WHEN SOURCES DISAGREE: the search may come back with a `divergencia` field. When \
it does, say out loud that the documents disagree, name both, and give the value \
from the one that is currently in force: `divergencia.vigente` names it (the source \
with the most recent document date). If `vigente` is empty, say you cannot tell \
which one is current. Do not silently pick one — that a client would never find \
out is exactly the failure this archive exists to prevent.

WHEN THE QUESTION IS ABOUT A DATE: pass `data_de_referencia` and say which cutoff \
you used, so the person knows the answer is about that date and not about today.

BEFORE EVERY SEARCH, SAY SOMETHING FIRST. Always speak a short, neutral holding \
phrase out loud — "Let me check that", "One moment", "Let me look that up" — and \
then call buscar_no_acervo in the same turn. Never call the search in silence: it \
takes several seconds and the person hears nothing at all in the meantime. Vary the \
phrase, and never let it hint at whether you will find the answer or not.

IF THE SEARCH IS TAKING LONG and the person speaks again, say you are still \
looking. Never leave them in silence for more than ten seconds.

When the search comes back empty, say plainly that it is not in the documents. Do \
not fill the gap.

IF THE RESULT CARRIES AN `erro` FIELD, the archive could not be reached. Say the \
search is temporarily unavailable and offer to try again. Do NOT say it is not in \
the documents — that is a different thing, and saying it would be false.

You are being heard, not read. Two or three sentences. No lists, no markdown, no \
headings, no citation markers — if the source matters, name the file out loud. \
Being interrupted is normal: stop talking and listen.
"""


class SessaoResposta(BaseModel):
    """Credencial efêmera que o navegador usa para abrir o WebRTC.

    `client_secret` é um `ek_...` de vida curta, já amarrado às instruções e à
    tool: a chave real da OpenAI nunca sai do backend, e o cliente não consegue
    reconfigurar a sessão para outra coisa.
    """

    client_secret: str
    expires_at: int
    model: str
    thread_id: str
    """A conversa de voz tem thread própria, para a trilha ficar agrupada por ela."""


class BuscaPedido(BaseModel):
    pergunta: str = Field(min_length=1, max_length=1000)
    data_de_referencia: str | None = None
    thread_id: str | None = None
    document_ids: list[str] | None = Field(default=None, max_length=MAX_DOCUMENTOS_SELECIONADOS)
    """A mesma selecao de documentos do chat: a busca fica restrita a eles."""

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("data_de_referencia")
    @classmethod
    def _valida_data(cls, v: str | None) -> str | None:
        return validar_data_iso(v, "data_de_referencia")

    @field_validator("document_ids")
    @classmethod
    def _valida_ids(cls, v: list[str] | None) -> list[str] | None:
        return validar_ids_de_documento(v)


class Trecho(BaseModel):
    texto: str
    arquivo: str
    pagina: int | None = None


class BuscaResposta(BaseModel):
    """Volta ao modelo como `function_call_output`.

    Lista vazia com `baixa_confianca` é resposta VÁLIDA, não erro: é o que permite
    o agente dizer "não está nos seus documentos" em vez de inventar.
    """

    trechos: list[Trecho]
    baixa_confianca: bool
    divergencia: dict | None = None
    """Preenchido quando duas ou mais fontes respondem diferente à mesma pergunta."""
    erro: str | None = None
    """A busca NAO rodou (backend fora do ar, timeout).

    Campo proprio, e nao `baixa_confianca`, porque os dois casos sao diferentes e
    o prompt trata cada um de um jeito: vazio significa "nao esta no acervo";
    `erro` significa "nao consegui olhar". Sem essa separacao, um 500 fazia o
    agente afirmar com confianca que o documento da pessoa nao continha aquilo."""


def _criar_thread_de_voz(user_id: str) -> str:
    # SQL sincrono: a rota o chama por thread, fora do event loop.
    with engine.begin() as conn:
        return str(conn.execute(
            insert(threads).values(user_id=user_id, title="Conversa por voz").returning(threads.c.id)
        ).scalar_one())


@router.post("/session", response_model=SessaoResposta)
@limiter.limit("10/minute")
async def criar_sessao(request: Request) -> SessaoResposta:
    user_id = await require_user(request)
    settings = get_settings()

    if not settings.enable_realtime:
        raise HTTPException(503, "Realtime voice is disabled")
    if not settings.openai_api_key:
        raise HTTPException(503, "Realtime voice is not configured")

    # Teto consumido ANTES de cunhar: uma conversa de voz é aberta, e sem isto um
    # visitante segura a linha e gasta o dia inteiro sozinho. Mesmo motivo pelo
    # qual a ingestão consome antes de criar a linha do documento.
    try:
        await metering.consumir("realtime", settings.daily_realtime_limit)
    except metering.TetoIndisponivel as exc:
        raise HTTPException(503, exc.mensagem) from exc
    except metering.TetoAtingido as exc:
        raise HTTPException(
            429,
            exc.mensagem,
            headers={"Retry-After": str(metering.segundos_ate_meia_noite_utc())},
        ) from exc

    thread_id = await asyncio.to_thread(_criar_thread_de_voz, user_id)

    corpo = {
        "session": {
            "type": "realtime",
            "model": settings.realtime_model,
            "instructions": INSTRUCOES,
            "audio": {
                "input": {
                    # Sem isto a transcricao da entrada nasce null e o evento
                    # `input_audio_transcription.completed` nunca chega: metade
                    # da linha do tempo do painel ficava vazia. Sem `language` de
                    # proposito — o prompt manda o agente trocar de idioma junto
                    # com a pessoa, e pinar um idioma mataria isso.
                    "transcription": {"model": settings.realtime_transcribe_model},
                    "noise_reduction": {"type": "near_field"},
                    # Explicito em vez de herdado. O default e threshold 0.5 e
                    # silencio de 500ms; com a frase de preenchimento o agente
                    # fala muito mais e 0.5 realimenta pelo alto-falante. 500ms
                    # tambem corta quem pausa para pensar — portugues falado tem
                    # pausa mais longa que isso.
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.65,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 700,
                        "create_response": True,
                    },
                },
                "output": {"voice": settings.realtime_voice},
            },
            "tools": [FERRAMENTA_BUSCA],
            "tool_choice": "auto",
        }
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                CLIENT_SECRETS_URL,
                json=corpo,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
            r.raise_for_status()
            dados = r.json()
    except httpx.HTTPError as e:
        # A sessao nao chegou a existir: cobrar por ela gastaria uma das 40
        # diarias sem ninguem ter falado. O teto continua sendo consumido ANTES
        # do mint de proposito (ver acima) — o que muda aqui e so o caso em que
        # NOS falhamos em cria-la. Mesmo padrao de documents.py e chat.py.
        await metering.devolver("realtime")
        logger.warning("realtime: falha ao cunhar credencial: %s", e)
        raise HTTPException(503, "Realtime voice is unavailable") from e

    return SessaoResposta(
        client_secret=dados["value"],
        expires_at=dados["expires_at"],
        model=settings.realtime_model,
        thread_id=str(thread_id),
    )


@router.post("/tool/buscar", response_model=BuscaResposta)
@limiter.limit("60/minute")
async def executar_busca(request: Request, body: BuscaPedido) -> BuscaResposta:
    """Executa a tool que o modelo chamou, e grava a trilha.

    O navegador recebe o `function_call` pelo data channel e bate aqui; a resposta
    volta ao modelo como `function_call_output`. Deliberadamente NÃO é o navegador
    que decide o que é relevante: quem filtra é o grader, aqui dentro, com o
    `user_id` do JWT. Um cliente que mentisse sobre o que recuperou não
    conseguiria ver documento de outra pessoa.
    """
    user_id = await require_user(request)
    settings = get_settings()

    if not settings.enable_realtime:
        raise HTTPException(503, "Realtime voice is disabled")

    # O mesmo filtro de entrada do chat: a pergunta vem do modelo de voz, que
    # repete o que a pessoa falou, e o texto chega ao gerador da mesma forma.
    valida, motivo = validate_input(body.pergunta)
    if not valida:
        raise HTTPException(400, motivo)

    # A trilha grava `thread_id`; sem esta checagem, qualquer um penduraria
    # decisoes na conversa de outra pessoa.
    if body.thread_id and not await asyncio.to_thread(
        validate_thread_ownership, body.thread_id, user_id
    ):
        raise HTTPException(403, "Thread does not belong to this user")

    # Teto proprio, consumido depois das checagens gratis e antes de qualquer
    # chamada paga. Sem ele, uma sessao de voz aberta disparava buscas sem fim.
    await consumir_cota("realtime_busca", settings.daily_realtime_tool_limit)

    iniciado_em = time.monotonic()

    # Todo passo caro daqui e sincrono (LLM, embedding, SQL) e vai para thread,
    # nunca para o event loop: segurar o loop atrasa o `function_call_output`,
    # e o agente fica mudo no meio da frase. `to_thread`, e nao
    # `run_in_executor`, porque ele leva o contexto que `medir` observa.
    with chamadas_pagas.medir() as pagas:
        try:
            recuperacao = await asyncio.to_thread(
                retrieve_documents,
                question=body.pergunta,
                user_id=user_id,
                document_ids=body.document_ids,
                as_of=body.data_de_referencia,
                top_k=5,
            )
            recuperados = recuperacao.documents
            aprovados, baixa_confianca = await asyncio.to_thread(grade_documents, recuperados)

            divergencia = None
            if aprovados:
                divergencia = await asyncio.to_thread(detectar_conflito, aprovados)
        except Exception as exc:
            logger.exception("realtime: a busca da tool falhou")
            # Cota so volta se nenhum provider chegou a cobrar.
            if not pagas:
                await metering.devolver("realtime_busca")
            # O navegador transforma o erro no campo `erro` do resultado, que o
            # prompt manda falar como "busca indisponivel", nao "nao esta la".
            raise HTTPException(503, "Search is temporarily unavailable") from exc

    await asyncio.to_thread(
        save_decision,
        user_id=user_id,
        thread_id=body.thread_id,
        message_id=None,  # a fala não vira linha em `messages`: ela vive na sessão da OpenAI
        question=body.pergunta,
        retrieved=recuperados,
        graded=aprovados,
        web_used=False,  # a conversa de voz não cai para busca web: seria longa demais para falar
        low_confidence=baixa_confianca,
        answered=bool(aprovados),
        latency_ms=int((time.monotonic() - iniciado_em) * 1000),
        conflict=divergencia,
        as_of=body.data_de_referencia,
        queries=recuperacao.queries,
    )

    return BuscaResposta(
        trechos=[
            Trecho(
                texto=t.get("snippet") or "",
                arquivo=t.get("document_title") or "",
                pagina=t.get("page"),
            )
            for t in aprovados
        ],
        baixa_confianca=baixa_confianca,
        divergencia=divergencia,
    )

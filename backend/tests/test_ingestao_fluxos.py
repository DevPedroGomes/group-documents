"""A ingestao EXECUTADA, fluxo por fluxo, com providers e banco em dubles.

O que se prende aqui:
- todo fluxo de texto (PDF, pagina da web, transcricao) passa pelo
  enriquecimento contextual, e nenhuma chamada paga roda no event loop do worker;
- pagina curta de PDF entra no indice UMA vez, pelo caminho visual, com o texto;
- pagina escaneada e embedada em lotes enquanto renderiza, sem todas em memoria;
- erro permanente (limite, arquivo ilegivel, 4xx do provider) nao e retentado,
  e o que vai para o documento e a categoria, nunca o corpo do provider.

Sem rede, sem Redis e sem Postgres (o merge em `meta` roda com SQL de verdade
em tests/test_integracao_ingestao.py).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from arq.worker import Retry

from app.core.ingestion import falhas
from app.jobs import ingestao, worker
from tests.fixtures import pdf_com_texto, pdf_escaneado
from tests.motor_falso import MotorFalso, status_do_documento

LONGO = "O prazo de entrega e de quinze dias uteis a partir da confirmacao. " * 6


def _no_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@pytest.fixture
def ingestao_falsa(monkeypatch):
    """Providers, disco e banco da ingestao trocados por dubles que anotam."""
    from app.core import llm_client

    motor = MotorFalso()
    r = SimpleNamespace(motor=motor, arquivo=b"", pagas=[], lotes=[], gravado=None,
                        erro_embedding=None, descricao="descricao da pagina")

    def llm(**kw):
        conteudo = kw["messages"][-1]["content"]
        tipo = "contexto" if isinstance(conteudo, list) else "resumo"
        r.pagas.append((tipo, _no_event_loop()))
        return "contexto do trecho" if tipo == "contexto" else "resumo do documento"

    def embed(sequencias, _tipo="document"):
        r.pagas.append(("embedding", _no_event_loop()))
        if r.erro_embedding:
            raise r.erro_embedding
        r.lotes.append(len(sequencias))
        return [[0.1] * 4 for _ in sequencias]

    def descrever(_png, _mime):
        r.pagas.append(("visao", _no_event_loop()))
        return r.descricao

    def gravar(**kw):
        r.gravado = kw
        return len(kw["texts"])

    monkeypatch.setattr(ingestao, "engine", motor)
    monkeypatch.setattr(worker, "engine", motor)
    monkeypatch.setattr(ingestao, "get_file", lambda _caminho: r.arquivo)
    monkeypatch.setattr(ingestao, "chat_complete", llm)
    monkeypatch.setattr(llm_client, "chat_complete", llm)
    monkeypatch.setattr(ingestao, "embed_sequences", embed)
    monkeypatch.setattr(ingestao, "descrever_imagem", descrever)
    monkeypatch.setattr(ingestao, "add_chunks", gravar)
    monkeypatch.setattr(worker.queue, "marcar", lambda *a, **k: None)
    r.descartes = []
    monkeypatch.setattr(worker.queue, "descartar", lambda *a, **k: r.descartes.append(k))
    return r


def _ingerir(r, mime: str, dados: bytes) -> None:
    r.motor.escalar = mime
    r.arquivo = dados
    asyncio.run(ingestao.process_ingestion("doc-1", "u", "u/docs/arquivo"))


def _erro_gravado(r) -> str | None:
    for _, sql, params in r.motor.executados:
        if "status = 'failed'" in sql:
            return params.get("err")
    return None


# ---------------------------------------------------------------------------
# Enriquecimento em todo fluxo de texto, e nada pago no event loop
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mime", ["text/plain", "audio/mpeg"])
def test_pagina_da_web_e_transcricao_passam_pelo_enriquecimento(ingestao_falsa, monkeypatch, mime):
    """Antes so o PDF era enriquecido: web e audio entravam crus."""
    from app.core.ingestion import multimodal

    r = ingestao_falsa
    texto = " ".join(f"Frase numero {i} sobre o prazo de entrega." for i in range(200))
    monkeypatch.setattr(multimodal, "transcrever_audio", lambda _d, _m: (texto, None))

    _ingerir(r, mime, texto.encode())

    enriquecidos = r.gravado["enriched_texts"]
    assert len(enriquecidos) > 1
    assert all(e.startswith("contexto do trecho\n\n") for e in enriquecidos)
    assert status_do_documento(r.motor) == ["processing", "completed"]


def test_nenhuma_chamada_paga_da_ingestao_roda_no_event_loop(ingestao_falsa):
    """O worker roda varios jobs no mesmo loop: uma chamada sincrona ali para
    todos eles, e o health check junto."""
    r = ingestao_falsa

    _ingerir(r, "application/pdf", pdf_com_texto([LONGO, "Capitulo 1"]))

    tipos = {tipo for tipo, _ in r.pagas}
    assert {"contexto", "resumo", "embedding", "visao"} <= tipos
    assert [p for p in r.pagas if p[1]] == [], "chamada paga rodou no event loop"


# ---------------------------------------------------------------------------
# PDF: pagina curta uma vez so, escaneadas em lotes
# ---------------------------------------------------------------------------

def test_pagina_curta_entra_uma_vez_so_com_o_texto_dela(ingestao_falsa):
    """Pagina com 1 a 119 caracteres tinha texto E ia para o caminho visual:
    virava dois chunks da mesma pagina."""
    r = ingestao_falsa

    _ingerir(r, "application/pdf", pdf_com_texto([LONGO, "Capitulo 1"]))

    paginas = r.gravado["pages"]
    assert paginas.count(2) == 1, f"a pagina curta entrou {paginas.count(2)} vezes"
    texto_da_curta = r.gravado["texts"][paginas.index(2)]
    assert "Capitulo 1" in texto_da_curta and "descricao da pagina" in texto_da_curta
    assert 1 in paginas


def test_escaneadas_sao_embedadas_em_lotes_enquanto_renderizam(ingestao_falsa, monkeypatch):
    import pymupdf

    from app.config.settings import get_settings

    r = ingestao_falsa
    monkeypatch.setattr(get_settings(), "pdf_render_dpi", 30)
    renderizadas: list[int] = []
    original = pymupdf.Page.get_pixmap

    def espiao(self, *a, **k):
        renderizadas.append(self.number)
        return original(self, *a, **k)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", espiao)
    no_primeiro_lote: list[int] = []
    embed = ingestao.embed_sequences

    def embed_espiao(sequencias, tipo="document"):
        if not no_primeiro_lote:
            no_primeiro_lote.append(len(renderizadas))
        return embed(sequencias, tipo)

    monkeypatch.setattr(ingestao, "embed_sequences", embed_espiao)

    _ingerir(r, "application/pdf", pdf_escaneado(10))

    assert no_primeiro_lote == [ingestao._LOTE_VISUAL], "todas as paginas viraram imagem antes de embedar"
    assert r.lotes == [ingestao._LOTE_VISUAL, 10 - ingestao._LOTE_VISUAL]
    assert sorted(r.gravado["pages"]) == list(range(1, 11))


# ---------------------------------------------------------------------------
# Erro permanente nao e retentado; a mensagem e saneada
# ---------------------------------------------------------------------------

def _job(r, mime: str, dados: bytes, tentativa: int = 1) -> None:
    r.motor.escalar = mime
    r.arquivo = dados
    asyncio.run(worker.ingerir({"job_id": "j", "job_try": tentativa}, "doc-1", "u", "u/docs/x"))


def test_pdf_acima_do_teto_falha_sem_pagar_nada_e_sem_retentar(ingestao_falsa, monkeypatch):
    from app.config.settings import get_settings

    r = ingestao_falsa
    monkeypatch.setattr(get_settings(), "max_pdf_pages", 1)

    _job(r, "application/pdf", pdf_com_texto([LONGO, LONGO]))  # tentativa 1 de 5: nao levanta Retry

    assert r.pagas == []
    assert r.descartes, "erro permanente nao foi para a dead-letter"
    assert _erro_gravado(r) == "The PDF has more pages than the limit (1)."
    assert status_do_documento(r.motor)[-1] == "failed"


def test_recusa_do_provider_nao_retenta_e_nao_vaza_o_corpo(ingestao_falsa):
    from voyageai import error as voyage

    r = ingestao_falsa
    r.erro_embedding = voyage.InvalidRequestError(
        "You have not yet added your payment method. https://dash.voyageai.com/billing",
        http_status=400,
    )

    _job(r, "text/plain", LONGO.encode())

    assert r.descartes and status_do_documento(r.motor)[-1] == "failed"
    assert _erro_gravado(r) == falhas.PROVIDER_RECUSOU
    assert not any("billing" in str(p) for _, _, p in r.motor.executados)


def test_provider_fora_do_ar_retenta(ingestao_falsa):
    r = ingestao_falsa
    r.erro_embedding = httpx.ConnectError("conexao recusada")

    with pytest.raises(Retry):
        _job(r, "text/plain", LONGO.encode())

    assert r.descartes == []
    assert _erro_gravado(r) == falhas.PROVIDER_INDISPONIVEL
    assert status_do_documento(r.motor)[-1] == "processing"


def _http(status: int) -> httpx.HTTPStatusError:
    pedido = httpx.Request("POST", "https://api.exemplo.com")
    return httpx.HTTPStatusError("erro", request=pedido, response=httpx.Response(status, request=pedido))


class _ErroComStatus(Exception):
    """O formato dos erros de Anthropic e OpenAI: `status_code` no proprio erro."""

    def __init__(self, status_code: int):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize("erro,permanente,mensagem", [
    (falhas.FalhaPermanente("The PDF has no pages."), True, "The PDF has no pages."),
    (_http(400), True, falhas.PROVIDER_RECUSOU),
    (_ErroComStatus(401), True, falhas.PROVIDER_RECUSOU),
    (_http(408), False, falhas.PROVIDER_INDISPONIVEL),
    (_http(429), False, falhas.PROVIDER_INDISPONIVEL),
    (_ErroComStatus(529), False, falhas.PROVIDER_INDISPONIVEL),
    (httpx.ReadTimeout("lento"), False, falhas.PROVIDER_INDISPONIVEL),
    (TimeoutError(), False, falhas.PROVIDER_INDISPONIVEL),
    (FileNotFoundError("sumiu"), True, falhas.ARQUIVO_ILEGIVEL),
    (RuntimeError("inesperado"), False, falhas.DESCONHECIDA),
])
def test_classificacao_da_falha(erro, permanente, mensagem):
    assert falhas.classificar(erro) == falhas.Falha(permanente, mensagem)


def test_pdf_corrompido_e_arquivo_ilegivel_e_nao_retenta():
    from app.core.ingestion.pdf_processor import extrair_paginas
    from tests.fixtures import pdf_corrompido

    with pytest.raises(Exception) as erro:
        extrair_paginas(pdf_corrompido())

    assert falhas.classificar(erro.value) == falhas.Falha(True, falhas.ARQUIVO_ILEGIVEL)


@pytest.fixture
def sem_cache_de_tipos():
    caches = (falhas._transitorias, falhas._recusadas, falhas._ilegiveis)
    for f in caches:
        f.cache_clear()
    yield
    for f in caches:
        f.cache_clear()


def test_sdk_sem_alguma_excecao_nao_quebra_a_classificacao(monkeypatch, sem_cache_de_tipos):
    """SDK do Voyage mais velho, sem `VideoProcessingError` nem
    `MalformedRequestError`: o getattr cru levantava AttributeError dentro do
    `except` do worker, e o documento ficava em `processing` sem dead-letter."""
    from voyageai import error as voyage

    monkeypatch.delattr(voyage, "VideoProcessingError")
    monkeypatch.delattr(voyage, "MalformedRequestError")

    assert falhas.classificar(FileNotFoundError("x")) == falhas.Falha(True, falhas.ARQUIVO_ILEGIVEL)
    assert falhas.classificar(voyage.AuthenticationError("chave")) == falhas.Falha(True, falhas.PROVIDER_RECUSOU)


def test_classificar_nunca_levanta(monkeypatch):
    def quebrado():
        raise AttributeError("module has no attribute")

    monkeypatch.setattr(falhas, "_transitorias", quebrado)

    assert falhas.classificar(RuntimeError("x")) == falhas.Falha(False, falhas.DESCONHECIDA)


def test_classificacao_quebrada_ainda_leva_a_ultima_tentativa_para_a_dead_letter(ingestao_falsa, monkeypatch):
    r = ingestao_falsa

    def quebrado():
        raise AttributeError("module has no attribute")

    monkeypatch.setattr(falhas, "_transitorias", quebrado)
    r.erro_embedding = RuntimeError("inesperado")

    _job(r, "text/plain", LONGO.encode(), tentativa=5)

    assert r.descartes, "o documento ficou em processing sem dead-letter"
    assert status_do_documento(r.motor)[-1] == "failed"

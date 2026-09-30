"""As rotas de documento, pela rota, com cota, fila, disco e banco em dubles.

O que se prende aqui:
- o upload grande e recusado ANTES de o corpo ser lido (Content-Length) e, se
  passar por ele, na leitura em blocos, sem gastar cota nem gravar arquivo;
- a cota e consumida ANTES de gravar o arquivo: um 429 nao deixa arquivo orfao
  no volume, e uma falha ao registrar devolve a cota e apaga o arquivo;
- as tres rotas de ingestao enfileiram com o tenant e devolvem o `job_id`, e uma
  recusa da fila desfaz cota, linha e arquivo.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_ops.decisions import digerir
from agent_ops.queue import FilaCheia, FilaIndisponivel, job_id_de
from fastapi.testclient import TestClient

from tests.fixtures import pdf_com_texto

USUARIO = "00000000-0000-0000-0000-00000000000a"
CAMINHO = f"{USUARIO}/docs/arquivo.pdf"


@pytest.fixture
def docs(monkeypatch):
    from agent_ops import metering

    from app.api.rate_limit import limiter
    from app.api.routes import documents as rotas
    from app.core.ingestion import url_crawler
    from app.main import create_app

    r = SimpleNamespace(eventos=[], enfileirados=[], linha=None,
                        teto=None, erro_ao_inserir=None, erro_da_fila=None)

    async def usuario(_request):
        return USUARIO

    async def consumir(tipo, _limite):
        r.eventos.append(f"consumir:{tipo}")
        if r.teto:
            raise r.teto

    async def devolver(tipo):
        r.eventos.append(f"devolver:{tipo}")

    def gravar(uid, mime, dados):
        r.eventos.append("save_file")
        return f"{uid}/docs/arquivo.pdf" if mime != "text/plain" else f"{uid}/docs/pagina.txt"

    def inserir(**valores):
        r.eventos.append("insert")
        if r.erro_ao_inserir:
            raise r.erro_ao_inserir
        r.linha = valores
        return "doc-1"

    async def enfileirar(_fila, funcao, *args, **kw):
        r.eventos.append("enfileirar")
        r.enfileirados.append((funcao, args, kw))
        if r.erro_da_fila:
            raise r.erro_da_fila

    monkeypatch.setattr(rotas, "require_user", usuario)
    monkeypatch.setattr(metering, "consumir", consumir)
    monkeypatch.setattr(metering, "devolver", devolver)
    monkeypatch.setattr(rotas, "save_file", gravar)
    monkeypatch.setattr(rotas, "_inserir_documento", inserir)
    monkeypatch.setattr(rotas, "_apagar_linha", lambda doc_id: r.eventos.append(f"apagar_linha:{doc_id}"))
    monkeypatch.setattr(rotas, "delete_file", lambda caminho: r.eventos.append(f"apagar_arquivo:{caminho}"))
    monkeypatch.setattr(rotas, "enfileirar", enfileirar)
    monkeypatch.setattr(url_crawler, "is_safe_url", lambda url: (True, None))
    monkeypatch.setattr(url_crawler, "fetch_and_extract", lambda url: ("Texto da pagina.", "Pagina"))
    monkeypatch.setattr(limiter, "enabled", False)

    app = create_app()
    app.state.fila = None
    return TestClient(app, headers={"Authorization": "Bearer x"}), r


def _upload(cliente, conteudo: bytes, **campos):
    return cliente.post("/upload", files={"file": ("manual.pdf", conteudo, "application/pdf")},
                        data={"title": "Manual", **campos})


def _limite(monkeypatch, limite: int) -> None:
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "max_file_size", limite)


# ---------------------------------------------------------------------------
# Upload grande: recusado sem ler, sem cota, sem arquivo
# ---------------------------------------------------------------------------

def test_content_length_acima_do_limite_e_413_antes_de_ler_o_corpo(docs, monkeypatch):
    from starlette.requests import Request

    cliente, r = docs
    _limite(monkeypatch, 1024)
    lido: list[bool] = []
    original = Request.form

    def espiao(self, *a, **k):
        lido.append(True)
        return original(self, *a, **k)

    monkeypatch.setattr(Request, "form", espiao)

    resposta = _upload(cliente, b"%PDF-1.7\n" + b"x" * 200_000)

    assert resposta.status_code == 413, resposta.text
    assert lido == [], "o multipart foi lido antes de olhar o Content-Length"
    assert r.eventos == []


def test_arquivo_acima_do_limite_dentro_da_folga_e_413_na_leitura(docs, monkeypatch):
    cliente, r = docs
    _limite(monkeypatch, 1024)

    resposta = _upload(cliente, b"%PDF-1.7\n" + b"x" * 2000)

    assert resposta.status_code == 413, resposta.text
    assert r.eventos == [], "gastou cota ou gravou arquivo de um upload recusado"


def test_leitura_em_blocos_para_no_limite_mais_um():
    """Nem um byte a mais do que o necessario para saber que estourou."""
    import asyncio

    from fastapi import HTTPException

    from app.api.routes.documents import _ler_ate_o_limite

    class Arquivo:
        def __init__(self, total):
            self.restante, self.pedidos = total, []

        async def read(self, n):
            self.pedidos.append(n)
            bloco = b"x" * min(n, self.restante)
            self.restante -= len(bloco)
            return bloco

    exato = Arquivo(100)
    assert asyncio.run(_ler_ate_o_limite(exato, 100)) == b"x" * 100

    grande = Arquivo(10_000_000)
    with pytest.raises(HTTPException) as erro:
        asyncio.run(_ler_ate_o_limite(grande, 100))
    assert erro.value.status_code == 413
    assert sum(grande.pedidos) == 101


# ---------------------------------------------------------------------------
# A cota vem antes do arquivo
# ---------------------------------------------------------------------------

def test_upload_consome_a_cota_antes_de_gravar_o_arquivo(docs):
    cliente, r = docs

    resposta = _upload(cliente, pdf_com_texto(["Relatorio."]), effective_date="2025-03-10")

    assert resposta.status_code == 200, resposta.text
    assert r.eventos == ["consumir:ingest", "save_file", "insert", "enfileirar"]
    assert r.linha["title"] == "Manual" and r.linha["mime"] == "application/pdf"
    assert str(r.linha["effective_date"]) == "2025-03-10"


@pytest.mark.parametrize("rota", ["upload", "crawl"])
def test_cota_estourada_nao_deixa_arquivo_orfao(docs, rota):
    from agent_ops import metering

    cliente, r = docs
    r.teto = metering.TetoAtingido("acabou")

    if rota == "upload":
        resposta = _upload(cliente, pdf_com_texto(["Relatorio."]))
    else:
        resposta = cliente.post("/crawl", json={"url": "https://exemplo.com/pagina"})

    assert resposta.status_code == 429
    assert "Retry-After" in resposta.headers
    assert r.eventos == ["consumir:ingest"], "gravou arquivo sem cota: fica orfao no volume"


def test_falha_ao_registrar_devolve_a_cota_e_apaga_o_arquivo(docs):
    cliente, r = docs
    r.erro_ao_inserir = RuntimeError("banco fora do ar")

    resposta = _upload(cliente, pdf_com_texto(["Relatorio."]))

    assert resposta.status_code == 500
    assert r.eventos == ["consumir:ingest", "save_file", "insert",
                         "devolver:ingest", f"apagar_arquivo:{CAMINHO}"]


# ---------------------------------------------------------------------------
# As tres rotas enfileiram com o tenant, e a recusa da fila desfaz tudo
# ---------------------------------------------------------------------------

def _chamar(cliente, rota: str):
    if rota == "upload":
        return _upload(cliente, pdf_com_texto(["Relatorio."])), CAMINHO
    if rota == "crawl":
        return (cliente.post("/crawl", json={"url": "https://exemplo.com/pagina"}),
                f"{USUARIO}/docs/pagina.txt")
    return (cliente.post("/ingest", json={"storage_path": CAMINHO, "title": "Manual",
                                          "mime": "application/pdf"}), CAMINHO)


@pytest.mark.parametrize("rota", ["upload", "crawl", "ingest"])
def test_a_rota_enfileira_com_o_tenant_e_devolve_o_job_id(docs, monkeypatch, rota):
    """Sem o tenant, dois usuarios com o mesmo arquivo dividiriam job e
    progresso; sem o `job_id` na resposta, o cliente nao acompanha nada. E a
    ingestao nao roda no processo web."""
    from app.jobs import ingestao

    cliente, r = docs
    rodou_aqui: list[bool] = []
    monkeypatch.setattr(ingestao, "process_ingestion", lambda *a, **k: rodou_aqui.append(True))

    resposta, caminho = _chamar(cliente, rota)

    assert resposta.status_code == 200, resposta.text
    ((funcao, args, kw),) = r.enfileirados
    assert funcao == "ingerir"
    assert args == ("doc-1", USUARIO, caminho)
    assert kw["tenant"] == USUARIO
    assert kw["digest"] == digerir(f"doc-1:{caminho}")
    assert resposta.json()["job_id"] == job_id_de(kw["digest"], tenant=USUARIO)
    assert resposta.json()["status"] == "pending"
    assert rodou_aqui == []


@pytest.mark.parametrize("rota", ["upload", "crawl", "ingest"])
@pytest.mark.parametrize("erro,status", [
    (FilaCheia("cheia", retry_after=30), 429),
    (FilaIndisponivel("fora do ar"), 503),
])
def test_recusa_da_fila_desfaz_cota_linha_e_arquivo(docs, rota, erro, status):
    """Fila cheia tem prazo para voltar (429 + Retry-After); Redis ilegivel nao
    tem (503). Nos dois casos a cota volta, e nem linha `pending` nem arquivo
    ficam para tras."""
    cliente, r = docs
    r.erro_da_fila = erro

    resposta, caminho = _chamar(cliente, rota)

    assert resposta.status_code == status
    if status == 429:
        assert resposta.headers["Retry-After"] == "30"
    assert r.eventos[-3:] == ["devolver:ingest", "apagar_linha:doc-1", f"apagar_arquivo:{caminho}"]

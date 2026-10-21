"""Sessao de convidado: le o acervo de demonstracao, nao escreve, nao gasta a
cota de quem tem conta e nao ve a conversa de outro convidado.

O que se prende aqui:
- `POST /auth/guest` so abre sessao com o modo ligado e a conta do acervo
  existente; o token vence junto com a sessao; tres sessoes por IP por dia;
- convidado vencido (ou com o modo desligado) e 401 em qualquer rota;
- toda rota de escrita e as duas de voz respondem 403 `guest_read_only`, antes
  de consumir cota ou tocar no banco;
- a leitura de documentos e trechos usa a conta do acervo; threads, mensagens e
  trilhas continuam no id do proprio convidado;
- o chat do convidado consome `chat_convidado` (por sessao e no total do dia),
  nunca o `chat`, e gera com teto proprio de tokens;
- a faxina apaga so o convidado vencido, com as conversas dele.

Os unitarios trocam a leitura da conta e o metering por dubles; os marcados
`integration` rodam contra Postgres de verdade (fixture `banco_limpo`).
"""

from __future__ import annotations

import asyncio
import inspect
from collections import Counter
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

CONVIDADO = "00000000-0000-0000-0000-0000000000c1"
OUTRO_CONVIDADO = "00000000-0000-0000-0000-0000000000c2"
ACERVO = "00000000-0000-0000-0000-0000000000d0"
COMUM = "00000000-0000-0000-0000-00000000000a"
UM_UUID = "00000000-0000-0000-0000-0000000000f1"


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _token(user_id: str, minutos: int = 30) -> dict:
    from app.config.settings import get_settings

    s = get_settings()
    token = jwt.encode(
        {"sub": user_id, "exp": _agora() + timedelta(minutes=minutos)},
        s.jwt_secret, algorithm=s.jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}"}


class MeteringFalso:
    """Contadores em memoria com a mesma assinatura do `agent_ops.metering`."""

    def __init__(self) -> None:
        self.contadores: Counter = Counter()
        self.consumidos: list[tuple[str, str | None]] = []
        self.devolvidos: list[tuple[str, str | None]] = []

    async def consumir(self, tipo, limite, unidades=1, escopo=None):
        from agent_ops import metering

        chave = (tipo, escopo)
        if self.contadores[chave] + unidades > limite:
            raise metering.TetoAtingido("This demo reached today's usage cap.")
        self.contadores[chave] += unidades
        self.consumidos.append(chave)
        return limite - self.contadores[chave]

    async def devolver(self, tipo, unidades=1, escopo=None):
        self.contadores[(tipo, escopo)] -= unidades
        self.devolvidos.append((tipo, escopo))


@pytest.fixture
def cotas(monkeypatch) -> MeteringFalso:
    from agent_ops import metering

    falso = MeteringFalso()
    monkeypatch.setattr(metering, "consumir", falso.consumir)
    monkeypatch.setattr(metering, "devolver", falso.devolver)
    return falso


@pytest.fixture
def modo_convidado(monkeypatch):
    """Modo convidado ligado, com os padroes do settings."""
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "guest_enabled", True)
    return get_settings()


def _acervo(monkeypatch, consulta) -> None:
    """Troca a busca da conta do acervo onde ela e importada por nome."""
    from app.api import dependencies
    from app.api.routes import auth

    monkeypatch.setattr(dependencies, "id_do_acervo_demo", consulta)
    monkeypatch.setattr(auth, "id_do_acervo_demo", consulta)


@pytest.fixture
def contas(monkeypatch) -> dict:
    """`user_id -> (is_active, is_guest, expires_at)`: o que a consulta da conta devolve."""
    from app.api import dependencies

    tabela: dict = {}
    monkeypatch.setattr(dependencies, "_linha_da_conta", lambda user_id: tabela.get(user_id))
    _acervo(monkeypatch, lambda: ACERVO)
    return tabela


@pytest.fixture
def app_sem_limiter(monkeypatch):
    from app.api.rate_limit import limiter
    from app.main import create_app

    monkeypatch.setattr(limiter, "enabled", False)
    app = create_app()
    app.state.fila = None
    return app


# ---------------------------------------------------------------------------
# POST /auth/guest
# ---------------------------------------------------------------------------

def test_guest_desligado_responde_503(app_sem_limiter, cotas, monkeypatch):
    from app.api.routes import auth

    monkeypatch.setattr(auth, "_criar_convidado", lambda *_a: pytest.fail("criou convidado desligado"))

    r = TestClient(app_sem_limiter).post("/auth/guest")

    assert r.status_code == 503
    assert r.json() == {"detail": "guest_disabled"}
    assert cotas.consumidos == []


def test_guest_sem_acervo_responde_503(app_sem_limiter, cotas, modo_convidado, monkeypatch):
    from app.api.routes import auth

    _acervo(monkeypatch, lambda: None)
    monkeypatch.setattr(auth, "_criar_convidado", lambda *_a: pytest.fail("criou convidado sem acervo"))

    r = TestClient(app_sem_limiter).post("/auth/guest")

    assert r.status_code == 503
    assert r.json() == {"detail": "guest_disabled"}
    assert cotas.consumidos == []


@pytest.fixture
def criados(monkeypatch, contas) -> list:
    """Troca a criacao do convidado no banco por uma lista."""
    from app.api.routes import auth

    lista: list = []

    def criar(expira_em):
        user_id = f"00000000-0000-0000-0000-{len(lista) + 1:012d}"
        lista.append((user_id, expira_em))
        return user_id, f"convidado-{user_id}@convidado.invalid"

    monkeypatch.setattr(auth, "_criar_convidado", criar)
    return lista


def test_guest_devolve_token_de_convidado(app_sem_limiter, cotas, modo_convidado, criados):
    antes = _agora()
    r = TestClient(app_sem_limiter).post("/auth/guest")

    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["is_guest"] is True
    assert corpo["user"]["is_guest"] is True
    expira = datetime.fromisoformat(corpo["expires_at"])
    assert abs((expira - (antes + timedelta(minutes=60))).total_seconds()) < 5
    assert corpo["user"]["expires_at"] == corpo["expires_at"]

    claims = jwt.decode(corpo["access_token"], modo_convidado.jwt_secret,
                        algorithms=[modo_convidado.jwt_algorithm])
    assert claims["sub"] == corpo["user"]["id"] == criados[0][0]
    assert claims["exp"] <= expira.timestamp()
    assert criados[0][1] == expira
    assert corpo["questions_left"] == corpo["user"]["questions_left"] == 15
    assert cotas.consumidos == [("convidado_ip", "testclient")]


def test_quarta_sessao_do_mesmo_ip_no_dia_e_429(app_sem_limiter, cotas, modo_convidado, criados):
    cliente = TestClient(app_sem_limiter)

    codigos = [cliente.post("/auth/guest").status_code for _ in range(4)]

    assert codigos == [200, 200, 200, 429]
    assert len(criados) == 3


def test_sessoes_de_convidado_tem_rate_limit_por_hora(cotas, modo_convidado, criados, limiter_em_memoria,
                                                      monkeypatch):
    from app.main import create_app

    # Teto do dia folgado: quem barra a quarta e o rate limit por hora.
    monkeypatch.setattr(modo_convidado, "guest_sessions_per_ip_daily", 100)
    cliente = TestClient(create_app())

    codigos = [cliente.post("/auth/guest").status_code for _ in range(4)]

    assert codigos == [200, 200, 200, 429]


def test_falha_ao_criar_devolve_a_cota_do_ip(app_sem_limiter, cotas, modo_convidado, contas, monkeypatch):
    from app.api.routes import auth

    def quebra(_expira_em):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(auth, "_criar_convidado", quebra)

    r = TestClient(app_sem_limiter).post("/auth/guest")

    assert r.status_code == 500
    assert cotas.contadores[("convidado_ip", "testclient")] == 0


# ---------------------------------------------------------------------------
# Sessao vencida
# ---------------------------------------------------------------------------

def test_convidado_expirado_e_401(app_sem_limiter, modo_convidado, contas):
    contas[CONVIDADO] = (True, True, _agora() - timedelta(minutes=1))

    r = TestClient(app_sem_limiter).get("/auth/me", headers=_token(CONVIDADO))

    assert r.status_code == 401


def test_desligar_o_modo_derruba_as_sessoes_abertas(app_sem_limiter, contas):
    contas[CONVIDADO] = (True, True, _agora() + timedelta(minutes=30))

    r = TestClient(app_sem_limiter).get("/auth/me", headers=_token(CONVIDADO))

    assert r.status_code == 401


def test_me_diz_que_e_convidado_e_quando_vence(app_sem_limiter, modo_convidado, contas, monkeypatch):
    from app.api.routes import auth

    expira = _agora() + timedelta(minutes=30)
    contas[CONVIDADO] = (True, True, expira)
    monkeypatch.setattr(auth, "_ler_usuario", lambda _uid: (
        CONVIDADO, "convidado-x@convidado.invalid", "Guest", True, _agora(), True, expira))

    r = TestClient(app_sem_limiter).get("/auth/me", headers=_token(CONVIDADO))

    assert r.status_code == 200, r.text
    assert r.json()["is_guest"] is True
    assert r.json()["expires_at"] == expira.isoformat()


# ---------------------------------------------------------------------------
# Convidado so le
# ---------------------------------------------------------------------------

ROTAS_DE_ESCRITA = [
    ("POST", "/upload", {"files": {"file": ("a.pdf", b"%PDF-1.7 x", "application/pdf")},
                         "data": {"title": "x"}}),
    ("POST", "/crawl", {"json": {"url": "https://exemplo.com.br/p"}}),
    ("POST", "/ingest", {"json": {"storage_path": f"{CONVIDADO}/docs/a.pdf", "title": "x",
                                  "mime": "application/pdf"}}),
    ("DELETE", f"/documents/{UM_UUID}", {}),
    ("DELETE", f"/threads/{UM_UUID}", {}),
    ("POST", "/realtime/session", {"json": {}}),
    ("POST", "/realtime/tool/buscar", {"json": {"pergunta": "qual o prazo?"}}),
]


@pytest.mark.parametrize("metodo,caminho,kw", ROTAS_DE_ESCRITA, ids=[f"{m} {c}" for m, c, _ in ROTAS_DE_ESCRITA])
def test_convidado_nao_escreve(app_sem_limiter, cotas, modo_convidado, contas, monkeypatch, metodo, caminho, kw):
    from app.api.routes import chat, documents, realtime

    contas[CONVIDADO] = (True, True, _agora() + timedelta(minutes=30))

    def nao_pode(*_a, **_k):
        raise AssertionError("rota de escrita chegou ao banco, ao disco ou a rede")

    for modulo, nome in [(documents, "save_file"), (documents, "_inserir_documento"),
                         (documents, "_apagar_documento"), (chat, "_apagar_thread"),
                         (realtime, "_criar_thread_de_voz"), (realtime, "retrieve_documents")]:
        monkeypatch.setattr(modulo, nome, nao_pode)

    r = TestClient(app_sem_limiter).request(metodo, caminho, headers=_token(CONVIDADO), **kw)

    assert r.status_code == 403, r.text
    assert r.json() == {"detail": "guest_read_only"}
    assert cotas.consumidos == []


def test_conta_normal_inalterada(app_sem_limiter, cotas, contas, monkeypatch):
    from app.api import dependencies

    _acervo(monkeypatch, lambda: pytest.fail("conta normal consultou o acervo"))
    contas[COMUM] = (True, False, None)

    assert dependencies.corpus_owner(COMUM, convidado=False) == COMUM

    # Arquivo vazio: passa pela guarda de escrita e para na validacao, sem cota.
    r = TestClient(app_sem_limiter).post(
        "/upload", headers=_token(COMUM),
        files={"file": ("a.pdf", b"", "application/pdf")}, data={"title": "x"},
    )
    assert r.status_code == 400, r.text
    assert cotas.consumidos == []


def test_convidado_lista_e_abre_documentos_do_acervo(app_sem_limiter, cotas, modo_convidado, contas, monkeypatch):
    from app.api.routes import documents

    contas[CONVIDADO] = (True, True, _agora() + timedelta(minutes=30))
    lidos: list[str] = []

    def listar(user_id, _ids):
        lidos.append(user_id)
        return []

    def caminho(_doc_id, user_id):
        lidos.append(user_id)
        return None

    monkeypatch.setattr(documents, "_listar_documentos", listar)
    monkeypatch.setattr(documents, "_caminho_do_documento", caminho)
    cliente = TestClient(app_sem_limiter)

    assert cliente.get("/documents", headers=_token(CONVIDADO)).status_code == 200
    assert cliente.get(f"/document/{UM_UUID}/preview", headers=_token(CONVIDADO)).status_code == 404
    assert lidos == [ACERVO, ACERVO]


def test_busca_semantica_do_convidado_usa_o_acervo_e_a_cota_de_convidado(
        app_sem_limiter, cotas, modo_convidado, contas, monkeypatch):
    from app.api.routes import documents
    from app.services import embedding_cache

    contas[CONVIDADO] = (True, True, _agora() + timedelta(minutes=30))
    buscados: list[str] = []
    monkeypatch.setattr(embedding_cache, "get_query_embedding", lambda _q: [0.0] * 4)
    monkeypatch.setattr(documents, "_documentos_parecidos", lambda _v, uid: buscados.append(uid) or [])

    r = TestClient(app_sem_limiter).get("/documents", params={"semantic_query": "frete"},
                                        headers=_token(CONVIDADO))

    assert r.status_code == 200, r.text
    assert buscados == [ACERVO]
    assert ("chat", None) not in cotas.consumidos
    assert cotas.consumidos == [("chat_convidado", CONVIDADO), ("chat_convidado", None)]


# ---------------------------------------------------------------------------
# Chat do convidado: acervo, cota e teto de tokens proprios
# ---------------------------------------------------------------------------

@pytest.fixture
def chat_de_convidado(monkeypatch, cotas, modo_convidado):
    """A rota /chat com os dubles do pipeline e um convidado autenticado."""
    from app.api.rate_limit import limiter
    from app.api.routes import chat as chat_route
    from app.main import create_app

    from tests.dubles_chat import Cenario, instalar, trecho

    cenario = Cenario()
    instalar(monkeypatch, cenario)
    # Os dubles do pipeline trocam o metering; o convidado precisa do `escopo`.
    from agent_ops import metering

    monkeypatch.setattr(metering, "consumir", cotas.consumir)
    monkeypatch.setattr(metering, "devolver", cotas.devolver)
    _acervo(monkeypatch, lambda: ACERVO)

    quem = {"id": CONVIDADO, "convidado": True}

    async def usuario(request):
        request.state.is_guest = quem["convidado"]
        return quem["id"]

    monkeypatch.setattr(chat_route, "require_user", usuario)
    monkeypatch.setattr(limiter, "enabled", False)
    cenario.acervo = {
        "frete gratis?": [trecho("t1", "doc-1", "Politica comercial v2", texto="frete gratis acima de 150 reais")],
    }
    cenario.resposta_multi_query = ""
    return TestClient(create_app()), cenario, quem


def test_convidado_nao_consome_cota_global(chat_de_convidado, cotas):
    cliente, cenario, _ = chat_de_convidado

    r = cliente.post("/chat", json={"message": "frete gratis?"})

    assert r.status_code == 200, r.text
    assert cotas.contadores[("chat", None)] == 0
    assert cotas.contadores[("chat_convidado", None)] == 1
    assert cotas.contadores[("chat_convidado", CONVIDADO)] == 1


def test_convidado_busca_no_acervo_e_grava_a_trilha_no_proprio_id(chat_de_convidado):
    from tests.dubles_chat import do_tipo, eventos

    cliente, cenario, _ = chat_de_convidado

    r = cliente.post("/chat", json={"message": "frete gratis?"})

    assert {b["user_id"] for b in cenario.buscas} == {ACERVO}
    fontes = do_tipo(eventos(r.text), "sources")
    assert fontes and fontes[0][0]["document_id"] == "doc-1"
    assert [d["user_id"] for d in cenario.decisoes] == [CONVIDADO]


def test_o_done_diz_quantas_perguntas_restam_ao_convidado(chat_de_convidado):
    from tests.dubles_chat import do_tipo, eventos

    cliente, _, quem = chat_de_convidado

    primeiro = do_tipo(eventos(cliente.post("/chat", json={"message": "frete gratis?"}).text), "done")
    segundo = do_tipo(eventos(cliente.post("/chat", json={"message": "frete gratis?"}).text), "done")
    quem.update(id=COMUM, convidado=False)
    comum = do_tipo(eventos(cliente.post("/chat", json={"message": "frete gratis?"}).text), "done")

    assert [primeiro[0]["questions_left"], segundo[0]["questions_left"]] == [14, 13]
    assert "questions_left" not in comum[0]


def test_cota_por_convidado(chat_de_convidado, cotas, modo_convidado, monkeypatch):
    cliente, cenario, _ = chat_de_convidado
    monkeypatch.setattr(modo_convidado, "guest_chat_limit", 2)

    codigos = [cliente.post("/chat", json={"message": "frete gratis?"}).status_code for _ in range(3)]

    assert codigos == [200, 200, 429]
    assert cotas.contadores[("chat_convidado", None)] == 2


def test_teto_do_dia_dos_convidados_devolve_a_cota_da_sessao(chat_de_convidado, cotas, modo_convidado,
                                                              monkeypatch):
    cliente, _, _ = chat_de_convidado
    monkeypatch.setattr(modo_convidado, "daily_guest_chat_limit", 0)

    r = cliente.post("/chat", json={"message": "frete gratis?"})

    assert r.status_code == 429
    assert cotas.contadores[("chat_convidado", CONVIDADO)] == 0


def test_erro_antes_de_gerar_devolve_as_cotas_do_convidado(chat_de_convidado, cotas):
    cliente, cenario, _ = chat_de_convidado
    cenario.erro_embedding = RuntimeError("voyage fora")

    cliente.post("/chat", json={"message": "frete gratis?"})

    assert cotas.contadores[("chat_convidado", CONVIDADO)] == 0
    assert cotas.contadores[("chat_convidado", None)] == 0
    assert cotas.contadores[("chat", None)] == 0


def test_generator_usa_teto_do_convidado(chat_de_convidado):
    cliente, cenario, quem = chat_de_convidado

    cliente.post("/chat", json={"message": "frete gratis?"})
    quem.update(id=COMUM, convidado=False)
    cliente.post("/chat", json={"message": "frete gratis?"})

    assert [g["max_tokens"] for g in cenario.geracoes] == [800, 2000]


def test_demo_limits_mostra_o_uso_de_convidado_separado(app_sem_limiter, monkeypatch):
    from agent_ops import metering

    async def panorama(limites):
        return {"limits": limites}

    monkeypatch.setattr(metering, "panorama", panorama)

    limites = TestClient(app_sem_limiter).get("/demo-limits").json()["limits"]

    assert limites["chat"] == 300
    assert limites["chat_convidado"] == 150


# ---------------------------------------------------------------------------
# Faxina
# ---------------------------------------------------------------------------

def test_a_faxina_roda_a_cada_15_minutos():
    from app.jobs import worker

    (job,) = [c for c in worker.WorkerSettings.cron_jobs if c.coroutine is worker.limpar_convidados]
    assert job.minute == {0, 15, 30, 45}
    assert inspect.iscoroutinefunction(worker.limpar_convidados)


# ---------------------------------------------------------------------------
# Integracao: Postgres de verdade
# ---------------------------------------------------------------------------

@pytest.fixture
def com_acervo(banco_limpo, monkeypatch, cotas, modo_convidado):
    """Banco migrado, conta do acervo com um documento e o pipeline sem rede."""
    from sqlalchemy import text as sqltext

    from app.api.rate_limit import limiter
    from app.core.rag import conflict, generator, retriever, transformer
    from app.db.engine import engine
    from app.main import create_app

    from tests.test_integracao_busca import _documento, _eixo, _trecho

    with engine.begin() as conn:
        acervo = str(conn.execute(
            sqltext("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"),
            {"e": modo_convidado.demo_corpus_email},
        ).scalar_one())
    doc = _documento(acervo, "[acervo-demo] Politica Comercial da Aurora, versao 2")
    _trecho(acervo, doc, "A partir de quanto o frete nacional e gratis? Resposta: 150 reais.", eixo=1)
    _trecho(acervo, _documento(acervo, "[acervo-demo] Ata 001"), "indicadores do mes", eixo=2)

    buscas: list[str] = []
    hibrida = retriever.hybrid_search

    def busca(**kw):
        buscas.append(kw["user_id"])
        return hibrida(**kw)

    monkeypatch.setattr(modo_convidado, "cohere_api_key", None)
    monkeypatch.setattr(modo_convidado, "enable_conflict_detection", False)
    monkeypatch.setattr(modo_convidado, "tavily_api_key", None)
    monkeypatch.setattr(retriever, "chat_complete", lambda **_kw: "")
    monkeypatch.setattr(retriever, "get_query_embeddings", lambda consultas: [_eixo(1) for _ in consultas])
    monkeypatch.setattr(retriever, "hybrid_search", busca)
    monkeypatch.setattr(transformer, "chat_complete", lambda **_kw: "")  # reescrita = a propria pergunta
    monkeypatch.setattr(conflict, "chat_complete", lambda **_kw: '{"conflict": false}')
    monkeypatch.setattr(generator, "chat_stream", lambda **_kw: iter(["O frete e gratis ", "acima de 150 reais."]))
    monkeypatch.setattr(limiter, "enabled", False)

    return TestClient(create_app()), acervo, doc, buscas


def _entrar_como_convidado(cliente) -> tuple[str, dict]:
    r = cliente.post("/auth/guest")
    assert r.status_code == 200, r.text
    return r.json()["user"]["id"], {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.integration
def test_convidado_le_o_acervo_demo(com_acervo):
    from tests.dubles_chat import do_tipo, eventos

    cliente, acervo, doc, buscas = com_acervo
    convidado, cab = _entrar_como_convidado(cliente)

    r = cliente.post("/chat", json={"message": "A partir de quanto o frete e gratis?"}, headers=cab)

    assert r.status_code == 200, r.text
    assert buscas and set(buscas) == {acervo}
    fontes = do_tipo(eventos(r.text), "sources")
    assert doc in {f["document_id"] for f in fontes[0]}
    assert [i["id"] for i in cliente.get("/documents", headers=cab).json()["items"]]
    assert cliente.get("/auth/me", headers=cab).json()["is_guest"] is True
    assert convidado != acervo


@pytest.mark.integration
def test_dois_convidados_nao_veem_threads_um_do_outro(com_acervo):
    from tests.dubles_chat import do_tipo, eventos

    cliente, *_ = com_acervo
    _, cab_a = _entrar_como_convidado(cliente)
    _, cab_b = _entrar_como_convidado(cliente)

    r = cliente.post("/chat", json={"message": "A partir de quanto o frete e gratis?"}, headers=cab_a)
    (fim,) = do_tipo(eventos(r.text), "done")
    thread_a = fim["thread_id"]

    assert [t["id"] for t in cliente.get("/threads", headers=cab_a).json()["threads"]] == [thread_a]
    assert cliente.get("/threads", headers=cab_b).json() == {"threads": []}
    assert cliente.get(f"/threads/{thread_a}/messages", headers=cab_b).status_code == 404
    assert cliente.get("/decisions", headers=cab_b).json() == {"decisions": []}
    seguir = cliente.post("/chat", json={"message": "e antes?", "thread_id": thread_a}, headers=cab_b)
    assert seguir.status_code == 403


@pytest.mark.integration
def test_convidado_de_verdade_nao_escreve_e_vence(com_acervo, cotas, modo_convidado):
    """Com o token que `/auth/guest` emitiu e o banco real: as rotas de escrita e
    de voz dao 403 sem gravar nada nem gastar cota, e a sessao vencida (ou com o
    modo desligado) da 401 em leitura tambem."""
    from sqlalchemy import text as sqltext

    from app.db.engine import engine

    cliente, acervo, doc, _ = com_acervo
    convidado, cab = _entrar_como_convidado(cliente)

    def contagem() -> tuple:
        with engine.begin() as conn:
            return tuple(conn.execute(sqltext(q)).scalar_one() for q in (
                "SELECT count(*) FROM documents", "SELECT count(*) FROM chunks",
                "SELECT count(*) FROM threads", "SELECT count(*) FROM messages",
                "SELECT count(*) FROM decisions",
            ))

    antes, consumidos_antes = contagem(), list(cotas.consumidos)
    for metodo, caminho, kw in ROTAS_DE_ESCRITA + [("DELETE", f"/documents/{doc}", {})]:
        r = cliente.request(metodo, caminho.replace(CONVIDADO, convidado), headers=cab, **kw)
        assert (r.status_code, r.json()) == (403, {"detail": "guest_read_only"}), f"{metodo} {caminho}"
    assert contagem() == antes
    assert cotas.consumidos == consumidos_antes

    assert cliente.get("/documents", headers=cab).status_code == 200
    modo_convidado.guest_enabled = False
    assert cliente.get("/documents", headers=cab).status_code == 401
    modo_convidado.guest_enabled = True
    with engine.begin() as conn:
        conn.execute(sqltext("UPDATE users SET expires_at = now() - interval '1 second' WHERE id = :i"),
                     {"i": convidado})
    assert cliente.get("/documents", headers=cab).status_code == 401
    assert cliente.get("/auth/me", headers=cab).status_code == 401


@pytest.mark.integration
def test_login_nao_abre_conta_de_convidado(com_acervo):
    from sqlalchemy import text as sqltext

    from app.db.engine import engine

    cliente, *_ = com_acervo
    convidado, _ = _entrar_como_convidado(cliente)
    with engine.begin() as conn:
        hash_ = conn.execute(sqltext("SELECT password_hash FROM users WHERE id = :i"), {"i": convidado}).scalar_one()

    assert not hash_.startswith("$2")  # nao e um hash bcrypt: nenhuma senha confere


@pytest.mark.integration
def test_limpar_convidados_apaga_so_expirados(com_acervo):
    from sqlalchemy import text as sqltext

    from app.api.routes.chat import save_decision, save_message
    from app.db.engine import engine
    from app.jobs import worker

    cliente, acervo, *_ = com_acervo
    vencido, _ = _entrar_como_convidado(cliente)
    valido, _ = _entrar_como_convidado(cliente)
    with engine.begin() as conn:
        comum = str(conn.execute(
            sqltext("INSERT INTO users (email, password_hash) VALUES ('c@exemplo.com.br', 'x') RETURNING id")
        ).scalar_one())
        conn.execute(sqltext("UPDATE users SET expires_at = now() - interval '1 minute' WHERE id = :i"),
                     {"i": vencido})

    def conversa(user_id: str) -> str:
        with engine.begin() as conn:
            thread = str(conn.execute(
                sqltext("INSERT INTO threads (user_id) VALUES (:u) RETURNING id"), {"u": user_id}
            ).scalar_one())
        mensagem = save_message(thread, "assistant", "resposta")
        save_decision(user_id=user_id, thread_id=thread, message_id=mensagem, question="q",
                      retrieved=[], graded=[], web_used=False, low_confidence=False,
                      answered=True, latency_ms=1)
        return thread

    threads = {u: conversa(u) for u in (vencido, valido, comum)}

    asyncio.run(worker.limpar_convidados({}))

    with engine.begin() as conn:
        usuarios = {str(r[0]) for r in conn.execute(sqltext("SELECT id FROM users"))}
        restantes = {str(r[0]) for r in conn.execute(sqltext("SELECT id FROM threads"))}
        donos_de_trilha = {str(r[0]) for r in conn.execute(sqltext("SELECT user_id FROM decisions"))}
        mensagens = conn.execute(sqltext("SELECT count(*) FROM messages")).scalar_one()

    assert vencido not in usuarios and {valido, comum, acervo} <= usuarios
    assert restantes == {threads[valido], threads[comum]}
    assert donos_de_trilha == {valido, comum}
    assert mensagens == 2

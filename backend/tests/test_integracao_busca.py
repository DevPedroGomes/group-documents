"""A busca hibrida rodando SQL de verdade, num banco criado so pelas migrations.

Embeddings falsos e deterministicos: cada trecho recebe um vetor unitario num
EIXO proprio. Dois eixos diferentes tem cosseno 0, abaixo do piso de 0,1 da
perna semantica; entao consultar com um eixo que nenhum trecho usa isola a
perna de palavra-chave, e consultar com o eixo do trecho garante a semantica.
"""
import re
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import text as sqltext

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
DIM = 1024
EIXO_SEM_TRECHO = DIM - 1


def _eixo(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


def _usuario() -> str:
    import uuid

    from app.db.engine import engine

    with engine.begin() as conn:
        return str(conn.execute(
            sqltext("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"),
            {"e": f"{uuid.uuid4().hex}@exemplo.com.br"},
        ).scalar_one())


def _documento(user_id: str, titulo: str, effective_date: date | None = None,
               uploaded_at: datetime | None = None) -> str:
    from app.db.engine import engine

    with engine.begin() as conn:
        return str(conn.execute(
            sqltext(
                "INSERT INTO documents (user_id, title, storage_path, status, effective_date, uploaded_at) "
                "VALUES (:u, :t, 'x/docs/x.pdf', 'ready', :e, COALESCE(:up, now())) RETURNING id"
            ),
            {"u": user_id, "t": titulo, "e": effective_date, "up": uploaded_at},
        ).scalar_one())


def _trecho(user_id: str, doc_id: str, texto: str, eixo: int, enriquecido: str | None = None) -> None:
    from app.services.vector_store import add_chunks

    add_chunks(
        texts=[texto], enriched_texts=[enriquecido or texto], embeddings=[_eixo(eixo)],
        user_id=user_id, document_id=doc_id, pages=[1], chunk_indices=[0],
    )


def _buscar(user_id: str, pergunta: str, eixo: int = EIXO_SEM_TRECHO, **kw) -> list[dict]:
    from app.services.vector_store import hybrid_search

    return hybrid_search(_eixo(eixo), pergunta, user_id=user_id, top_k=5, **kw)


def _docs(resultados: list[dict]) -> list[str]:
    return [r["document_id"] for r in resultados]


# ---------------------------------------------------------------------------
# Palavra-chave
# ---------------------------------------------------------------------------

def test_pergunta_completa_em_portugues_acha_o_trecho_pela_palavra_chave(banco_limpo):
    """Com 'english' e AND, "qual", "o", "de" viravam termos obrigatorios e a
    pergunta nao casava nada. Com OR, se alguma metade deixasse "de" virar
    termo, a ata (que so tem "de" em comum) voltaria junto. O eixo da consulta
    nao e de trecho nenhum: quem acha e a perna textual, e sem acento."""
    u = _usuario()
    politica = _documento(u, "Politica comercial")
    _trecho(u, politica, "Resposta: o frete nacional é grátis acima de 150 reais.", eixo=1)
    ata = _documento(u, "Ata")
    _trecho(u, ata, "Revisão dos indicadores do mês anterior no setor de Logística.", eixo=2)

    resultados = _buscar(u, "qual o valor minimo de frete gratis?")

    assert _docs(resultados) == [politica]


def test_trigger_e_consulta_usam_as_mesmas_configs(banco_limpo):
    """Indexar com uma config e consultar com outra devolve vazio, sem erro.
    Cruza o que o trigger da migration grava com as configs da consulta."""
    from app.db.engine import engine
    from app.services.vector_store import TEXT_SEARCH_CONFIGS

    u = _usuario()
    texto = "Pedido retido na alfândega. International shipping takes 7 to 12 days."
    _trecho(u, _documento(u, "Manual"), texto, eixo=1)

    esperado = " || ".join(f"to_tsvector('{cfg}', :t)" for cfg in TEXT_SEARCH_CONFIGS)
    with engine.begin() as conn:
        igual = conn.execute(
            sqltext(f"SELECT search_vector = ({esperado}) FROM chunks"), {"t": texto}
        ).scalar_one()

    assert igual, "o trigger indexa com configs diferentes das de TEXT_SEARCH_CONFIGS"
    assert _buscar(u, "algum pedido parado na alfandega?")
    assert _buscar(u, "how long does international shipping take?")


FUNCIONAIS = (
    "qual o de para da em um que não é com os as do no na uma por mais até você "
    "what is the of for to in a and on it with was this that are"
)


def test_nenhuma_metade_emite_palavra_funcional_de_nenhuma_lingua(banco_limpo):
    """Com OR e `ts_rank` sem IDF, um trecho que casa so "de" pontua perto de um
    que casa o assunto. Cada metade descarta as stopwords das duas linguas, e a
    consulta do app nao leva nenhuma delas."""
    from app.db.engine import engine
    from app.services.vector_store import TEXT_SEARCH_CONFIGS, TSQUERY_SQL

    with engine.begin() as conn:
        for cfg in TEXT_SEARCH_CONFIGS:
            vetor = conn.execute(sqltext(f"SELECT to_tsvector('{cfg}', :t)::text"), {"t": FUNCIONAIS}).scalar()
            assert vetor == "", f"{cfg} emite palavra funcional: {vetor}"
        consulta = conn.execute(
            sqltext(f"SELECT {TSQUERY_SQL}::text"),
            {"query_text": "Qual é o valor mínimo de frete grátis? What is the minimum for free shipping?"},
        ).scalar()
    termos = set(re.findall(r"'([^']+)'", consulta))
    assert termos and not termos & {"qual", "o", "de", "e", "é", "what", "is", "the", "for"}, termos


def test_sem_unaccent_a_008_degrada_e_a_busca_segue(banco_limpo, caplog):
    """Producao e um Postgres externo: se o papel da app nao puder criar
    `unaccent`, a 008 nao pode derrubar o boot (migrate.py e fatal). Simula
    exatamente isso: reaplica a 008 com um papel sem CREATE no banco, depois de
    tirar a extensao. O papel e temporario e sai no fim, com o que for dele."""
    import logging
    import uuid

    from app.db.engine import engine

    papel = f"gd_test_{uuid.uuid4().hex[:12]}"
    sql_008 = (BACKEND / "migrations" / "008_busca_textual.sql").read_text()
    with engine.begin() as conn:
        conn.execute(sqltext("DROP TEXT SEARCH CONFIGURATION busca_portugues"))
        conn.execute(sqltext("DROP TEXT SEARCH CONFIGURATION busca_ingles"))
        conn.execute(sqltext("DROP EXTENSION unaccent"))
        conn.execute(sqltext(f'CREATE ROLE "{papel}" NOLOGIN'))
        conn.execute(sqltext(f'GRANT CREATE ON SCHEMA public TO "{papel}"'))
        conn.execute(sqltext(f'GRANT SELECT, UPDATE ON chunks TO "{papel}"'))
        conn.execute(sqltext(f'ALTER FUNCTION update_search_vector() OWNER TO "{papel}"'))
    try:
        # O dialeto psycopg2 do SQLAlchemy consome os avisos do servidor e os
        # manda para este logger em nivel INFO.
        caplog.set_level(logging.INFO, logger="sqlalchemy.dialects.postgresql")
        with engine.begin() as conn:
            conn.execute(sqltext(f'SET LOCAL ROLE "{papel}"'))
            conn.execute(sqltext(sql_008))
        with engine.begin() as conn:
            tem_unaccent = conn.execute(sqltext(
                "SELECT count(*) FROM pg_extension WHERE extname = 'unaccent'")).scalar()
            acentuado = conn.execute(sqltext("SELECT to_tsvector('busca_portugues', 'grátis')::text")).scalar()

        assert tem_unaccent == 0
        assert "unaccent indisponivel" in caplog.text, caplog.text
        assert acentuado == "'grát':1", "sem a extensao, o acento fica"

        u = _usuario()
        politica = _documento(u, "Politica")
        _trecho(u, politica, "Resposta: o frete nacional é grátis acima de 150 reais.", eixo=1)
        _trecho(u, _documento(u, "Ata"), "Revisão dos indicadores do mês anterior no setor de Logística.", eixo=2)
        assert _docs(_buscar(u, "qual o valor minimo de frete?")) == [politica]
    finally:
        with engine.begin() as conn:
            conn.execute(sqltext(f'REASSIGN OWNED BY "{papel}" TO CURRENT_USER'))
            conn.execute(sqltext(f'DROP OWNED BY "{papel}"'))
            conn.execute(sqltext(f'DROP ROLE "{papel}"'))


def test_a_008_recalcula_o_vetor_das_linhas_que_ja_existiam(banco_limpo):
    """Linha gravada pelo trigger antigo ('english') tem de ser reindexada pela
    migration; reaplicar a 008 tambem prova que ela e idempotente."""
    from app.db.engine import engine

    u = _usuario()
    _trecho(u, _documento(u, "Chamado"), "Pedido retido na alfândega.", eixo=1)
    with engine.begin() as conn:
        # So `search_vector` no SET: o trigger (UPDATE OF content) nao dispara.
        conn.execute(sqltext("UPDATE chunks SET search_vector = to_tsvector('english', content)"))
    assert _buscar(u, "problema de alfandega") == []

    with engine.begin() as conn:
        conn.execute(sqltext((BACKEND / "migrations" / "008_busca_textual.sql").read_text()))

    assert len(_buscar(u, "problema de alfandega")) == 1


def test_conjunto_ouro_nao_regride(banco_limpo):
    """A config textual so muda medindo. Roda a perna de palavra-chave do app
    (trigger + TSQUERY_SQL) no acervo e nas perguntas-ouro do script de
    avaliacao, com empate contado contra. Pisos um pouco abaixo do medido na
    adocao (pt originais: recall@45 0,94 e MRR 0,98; sem acento MRR 1,00;
    ingles MRR 0,90) e ruido zero: nenhum top-5 casado so por palavra funcional.
    Sem piso para `regra-vocab-diferente`: la a perna textual nao acha nada, e
    o script diz isso; quem responde e a semantica."""
    from scripts.avaliar_busca_textual import GRUPOS, _agregar, _app_como_candidato, medir, perguntas_ouro, semear
    from scripts.gerar_acervo_demo import gerar

    trechos = semear(gerar(500))
    linhas = medir([_app_como_candidato()], perguntas_ouro(), trechos)["candidatos"]["APP"]["perguntas"]
    originais = {g for g, origem in GRUPOS.items() if origem == "original"}
    pt = _agregar([x for x in linhas if x["idioma"] == "pt" and x["grupo"] in originais])
    sem_acento = _agregar([x for x in linhas if x["grupo"] == "sem-acento"])
    en = _agregar([x for x in linhas if x["idioma"] == "en"])

    assert pt["recall@45"] >= 0.9 and pt["mrr"] >= 0.95, pt
    assert sem_acento["mrr"] >= 0.9, sem_acento
    assert en["mrr"] >= 0.85, en
    for grupo in GRUPOS:
        a = _agregar([x for x in linhas if x["grupo"] == grupo])
        assert a["ruido@5"] in (None, 0), (grupo, a)


# ---------------------------------------------------------------------------
# Isolamento e filtros
# ---------------------------------------------------------------------------

def test_trecho_de_outro_usuario_nunca_volta_em_nenhuma_perna(banco_limpo):
    a, b = _usuario(), _usuario()
    _trecho(a, _documento(a, "Ata"), "Revisão dos indicadores.", eixo=2)
    doc_b = _documento(b, "Politica de B")
    _trecho(b, doc_b, "O frete nacional é grátis acima de 150 reais.", eixo=1)

    # cada perna isolada: so semantica (texto sem termo em comum) e so textual
    so_semantica = dict(pergunta="xyz", eixo=1)
    so_textual = dict(pergunta="frete gratis", eixo=EIXO_SEM_TRECHO)
    for consulta in (so_semantica, so_textual):
        assert doc_b in _docs(_buscar(b, **consulta)), f"sanidade: B acha o proprio trecho ({consulta})"
        assert doc_b not in _docs(_buscar(a, **consulta)), f"vazou para outro usuario ({consulta})"


def test_document_ids_restringe_aos_documentos_pedidos(banco_limpo):
    u = _usuario()
    d1, d2 = _documento(u, "Um"), _documento(u, "Dois")
    _trecho(u, d1, "O frete nacional é grátis acima de 120 reais.", eixo=1)
    _trecho(u, d2, "O frete nacional é grátis acima de 150 reais.", eixo=1)

    assert set(_docs(_buscar(u, "frete gratis", eixo=1))) == {d1, d2}
    assert _docs(_buscar(u, "frete gratis", eixo=1, document_ids=[d2])) == [d2]


def test_snippet_e_o_trecho_cru_inteiro(banco_limpo):
    """O gerador recebe o chunk inteiro (chunk de 500 tokens ~ 2000 caracteres),
    e o texto do documento, nao o contexto escrito pelo LLM no enriquecimento."""
    u = _usuario()
    texto = "O frete nacional é grátis acima de 150 reais. " * 60
    assert len(texto) > 2000
    _trecho(u, _documento(u, "Longo"), texto, eixo=1, enriquecido="CONTEXTO GERADO. " + texto)

    [resultado] = _buscar(u, "frete", eixo=1)

    assert resultado["snippet"] == texto


# ---------------------------------------------------------------------------
# Recorte no tempo pela data do documento
# ---------------------------------------------------------------------------

SO_SEMANTICA = dict(pergunta="xyz", eixo=1)
SO_TEXTUAL = dict(pergunta="frete gratis", eixo=EIXO_SEM_TRECHO)


def test_as_of_corta_pela_data_do_documento_nas_duas_pernas(banco_limpo):
    """Os trechos sao gravados hoje e os documentos valem em 2025: cortar por
    `uploaded_at` (ou pelo chunk) devolveria vazio. O proprio dia entra."""
    u = _usuario()
    vigente = _documento(u, "v1", effective_date=date(2025, 3, 10))
    posterior = _documento(u, "v2", effective_date=date(2025, 3, 11))
    _trecho(u, vigente, "O frete é grátis acima de 120 reais.", eixo=1)
    _trecho(u, posterior, "O frete é grátis acima de 150 reais.", eixo=1)

    for consulta in (SO_SEMANTICA, SO_TEXTUAL):
        resultados = _buscar(u, **consulta, as_of="2025-03-10")
        assert _docs(resultados) == [vigente], consulta
        assert resultados[0]["document_date"] == "2025-03-10"

    # Data e hora completas: vale a DATA, entao o dia 10 continua inteiro.
    assert _docs(_buscar(u, **SO_TEXTUAL, as_of="2025-03-10T00:00:00+00:00")) == [vigente]
    assert set(_docs(_buscar(u, **SO_TEXTUAL, as_of="2025-03-11"))) == {vigente, posterior}


def test_sem_data_efetiva_vale_o_dia_do_upload_em_utc(banco_limpo):
    u = _usuario()
    # 23h30 em Brasilia ja e dia 6 em UTC.
    doc = _documento(u, "sem data", uploaded_at=datetime.fromisoformat("2025-01-05T23:30:00-03:00"))
    _trecho(u, doc, "O frete é grátis acima de 120 reais.", eixo=1)

    assert _buscar(u, **SO_TEXTUAL, as_of="2025-01-05") == []
    [resultado] = _buscar(u, **SO_TEXTUAL, as_of="2025-01-06")
    assert resultado["document_date"] == "2025-01-06"
    [sem_corte] = _buscar(u, **SO_TEXTUAL)
    assert sem_corte["document_date"] == "2025-01-06"


# ---------------------------------------------------------------------------
# `effective_date` na entrada (upload e crawl)
# ---------------------------------------------------------------------------

@pytest.fixture
def cliente_docs(banco_limpo, monkeypatch):
    """App real sobre o banco temporario. Cota, fila, disco e rede viram dubles:
    o que se testa e a validacao da data e o que chega na linha do documento."""
    from fastapi.testclient import TestClient

    from app.api.rate_limit import limiter
    from app.api.routes import documents as rotas
    from app.core.ingestion import url_crawler
    from app.main import create_app

    cota: list[str] = []

    async def consumir(tipo, *_a, **_k):
        cota.append(tipo)

    async def enfileirar(*_a, **_k):
        return None

    monkeypatch.setattr(rotas.metering, "consumir", consumir)
    monkeypatch.setattr(rotas, "enfileirar", enfileirar)
    monkeypatch.setattr(rotas, "save_file", lambda uid, mime, dados: f"{uid}/docs/arquivo.pdf")
    monkeypatch.setattr(url_crawler, "is_safe_url", lambda url: (True, None))
    monkeypatch.setattr(url_crawler, "fetch_and_extract", lambda url: ("Texto da pagina.", "Pagina"))

    limiter.enabled = False
    try:
        app = create_app()
        app.state.fila = None
        cliente = TestClient(app)
        r = cliente.post("/auth/register", json={"email": "d@exemplo.com.br", "password": "uma-senha-longa-123"})
        assert r.status_code == 200, r.text
        cliente.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        cliente.cota = cota
        yield cliente
    finally:
        limiter.enabled = True


def _datas_gravadas() -> list:
    from app.db.engine import engine

    with engine.begin() as conn:
        return [r[0] for r in conn.execute(sqltext("SELECT effective_date FROM documents ORDER BY uploaded_at"))]


def _upload(cliente, **campos):
    pdf = (BACKEND.parent / "frontend" / "public" / "samples" / "aurora-coffee-handbook.pdf").read_bytes()
    return cliente.post("/upload", files={"file": ("manual.pdf", pdf, "application/pdf")},
                        data={"title": "Manual", **campos})


def test_upload_grava_a_data_efetiva(cliente_docs):
    assert _upload(cliente_docs, effective_date="2025-03-10").status_code == 200
    assert _upload(cliente_docs).status_code == 200
    assert _datas_gravadas() == [date(2025, 3, 10), None]


@pytest.mark.parametrize("invalida", ["2025-02-30", "10/03/2025", "2025-3-1", "ontem"])
def test_upload_recusa_data_invalida_sem_gastar_cota(cliente_docs, invalida):
    r = _upload(cliente_docs, effective_date=invalida)
    assert r.status_code == 422, r.text
    assert cliente_docs.cota == [] and _datas_gravadas() == []


def test_crawl_grava_a_data_efetiva(cliente_docs):
    r = cliente_docs.post("/crawl", json={"url": "https://exemplo.com.br/p", "effective_date": "2024-09-01"})
    assert r.status_code == 200, r.text
    assert _datas_gravadas() == [date(2024, 9, 1)]


@pytest.mark.parametrize("invalida", ["2024-13-01", "2024-09-01T10:00:00", 20240901])
def test_crawl_recusa_data_invalida(cliente_docs, invalida):
    r = cliente_docs.post("/crawl", json={"url": "https://exemplo.com.br/p", "effective_date": invalida})
    assert r.status_code == 422, r.text
    assert _datas_gravadas() == []


# ---------------------------------------------------------------------------
# HNSW: candidatos suficientes para o LIMIT e os filtros
# ---------------------------------------------------------------------------

def test_consulta_semantica_roda_com_ef_search_derivado_do_limit(banco_limpo):
    """Le o valor EM VIGOR no momento em que a consulta semantica executa, na
    mesma conexao, e confere que ele nao vaza para a proxima transacao."""
    from sqlalchemy import event

    from app.db.engine import engine

    vistos: list[str] = []

    def espiar(_conn, cursor, statement, *_a):
        if "ORDER BY c.embedding <=>" in statement:
            with cursor.connection.cursor() as cur:
                cur.execute("SELECT current_setting('hnsw.ef_search', true)")
                vistos.append(cur.fetchone()[0])

    u = _usuario()
    _trecho(u, _documento(u, "Doc"), "O frete é grátis acima de 150 reais.", eixo=1)
    event.listen(engine, "before_cursor_execute", espiar)
    try:
        _buscar(u, "frete", eixo=1)  # top_k 5 -> LIMIT 15 -> piso de 100
        from app.services.vector_store import hybrid_search

        hybrid_search(_eixo(1), "frete", user_id=u, top_k=100)  # LIMIT 300 -> 600
    finally:
        event.remove(engine, "before_cursor_execute", espiar)

    assert vistos == ["100", "600"]
    with engine.connect() as conn:
        depois = conn.execute(sqltext("SELECT current_setting('hnsw.ef_search', true)")).scalar()
    assert depois not in ("100", "600"), "o ajuste vazou da transacao da busca"

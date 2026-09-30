"""A ingestao gravando num banco de verdade: o que acontece com `meta`.

A falha sobrescrevia `meta` inteiro com `{"error": ...}` e apagava o
`source_url` do documento vindo de URL; e o sucesso depois de uma falha tem de
apagar SO o erro. Providers e disco sao dubles; o SQL e o real.
"""

import asyncio

import pytest
from sqlalchemy import text as sqltext

pytestmark = pytest.mark.integration

ORIGEM = "https://exemplo.com.br/politica"


def _documento_da_web() -> tuple[str, str]:
    from app.db.engine import engine

    with engine.begin() as conn:
        uid = str(conn.execute(
            sqltext("INSERT INTO users (email, password_hash) VALUES ('w@exemplo.com.br', 'x') RETURNING id")
        ).scalar_one())
        doc = str(conn.execute(
            sqltext(
                "INSERT INTO documents (user_id, title, mime, storage_path, status, meta) "
                "VALUES (:u, 'Politica', 'text/plain', 'u/docs/p.txt', 'pending', "
                "jsonb_build_object('source_url', CAST(:url AS text))) RETURNING id"
            ),
            {"u": uid, "url": ORIGEM},
        ).scalar_one())
    return uid, doc


def _linha(doc: str) -> tuple[str, dict, int]:
    from app.db.engine import engine

    with engine.begin() as conn:
        status, meta = conn.execute(
            sqltext("SELECT status, meta FROM documents WHERE id = :id"), {"id": doc}
        ).one()
        chunks = conn.execute(
            sqltext("SELECT count(*) FROM chunks WHERE document_id = :id"), {"id": doc}
        ).scalar_one()
    return status, meta, chunks


def test_falha_preserva_a_origem_e_o_sucesso_apaga_so_o_erro(banco_limpo, monkeypatch):
    from app.core import llm_client
    from app.jobs import ingestao

    uid, doc = _documento_da_web()

    def sumiu(_caminho):
        raise FileNotFoundError("u/docs/p.txt")

    monkeypatch.setattr(ingestao, "get_file", sumiu)
    with pytest.raises(FileNotFoundError):
        asyncio.run(ingestao.process_ingestion(doc, uid, "u/docs/p.txt"))

    assert _linha(doc) == ("failed", {"source_url": ORIGEM, "error": "The file could not be read."}, 0)

    monkeypatch.setattr(ingestao, "get_file", lambda _c: b"O prazo de entrega e de quinze dias uteis.")
    monkeypatch.setattr(ingestao, "chat_complete", lambda **_kw: "resumo")
    monkeypatch.setattr(llm_client, "chat_complete", lambda **_kw: "contexto")
    monkeypatch.setattr(ingestao, "embed_sequences",
                        lambda sequencias, _t="document": [[1.0] + [0.0] * 1023 for _ in sequencias])
    asyncio.run(ingestao.process_ingestion(doc, uid, "u/docs/p.txt"))

    assert _linha(doc) == ("completed", {"source_url": ORIGEM}, 1)


def test_retentativa_agendada_nao_deixa_o_erro_da_tentativa_que_falhou(banco_limpo, monkeypatch):
    """Depois de uma falha transitoria o documento volta a `processing` com uma
    retentativa marcada; o `erro` daquela tentativa nao pode seguir na API."""
    import httpx
    from arq.worker import Retry

    from app.jobs import ingestao, worker

    uid, doc = _documento_da_web()

    def fora_do_ar(_caminho):
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(ingestao, "get_file", fora_do_ar)
    with pytest.raises(Retry):
        asyncio.run(worker.ingerir({"job_id": "j", "job_try": 1}, doc, uid, "u/docs/p.txt"))

    assert _linha(doc) == ("processing", {"source_url": ORIGEM}, 0)


def test_nova_tentativa_comeca_sem_o_erro_da_anterior(banco_limpo, monkeypatch):
    from app.db.engine import engine
    from app.jobs import ingestao

    uid, doc = _documento_da_web()
    with engine.begin() as conn:
        conn.execute(
            sqltext("UPDATE documents SET status = 'failed', "
                    "meta = meta || jsonb_build_object('error', 'The file could not be read.') WHERE id = :id"),
            {"id": doc},
        )
    visto: list = []

    def ler(_caminho):
        visto.append(_linha(doc)[:2])  # como a API ve o documento durante a tentativa
        raise FileNotFoundError("u/docs/p.txt")

    monkeypatch.setattr(ingestao, "get_file", ler)
    with pytest.raises(FileNotFoundError):
        asyncio.run(ingestao.process_ingestion(doc, uid, "u/docs/p.txt"))

    assert visto == [("processing", {"source_url": ORIGEM})]

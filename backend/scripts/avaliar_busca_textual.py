"""Mede a perna de PALAVRA-CHAVE da busca hibrida, sem API paga.

POR QUE
-------
A perna textual (tsvector + GIN) foi "medida" uma vez com 8 palavras soltas.
Isso nao e recall: o usuario digita uma PERGUNTA, e `plainto_tsquery` faz AND
de todos os termos. Com `english` num acervo portugues, "qual", "o", "de",
"para" viram termos obrigatorios e a pergunta inteira nao casa com nada. Este
script mede pergunta de verdade contra um acervo de verdade.

COMO
----
1. Gera o acervo da demo (`scripts.gerar_acervo_demo.gerar`, deterministico),
   passa cada documento pelo MESMO caminho de texto da ingestao (PDF escrito
   pelo gerador -> `extract_pages_from_pdf` -> `chunk_document_pages`), SEM o
   enriquecimento por LLM, e grava com `add_chunks` (o trigger do schema monta
   `search_vector`).
2. Cria um banco TEMPORARIO (`gd_eval_<uuid>`) no servidor de `--database-url`,
   aplica as migrations e o derruba no fim. Nunca escreve em banco existente.
3. Para cada candidato, monta o tsvector numa coluna propria e roda a consulta
   de palavra-chave de cada pergunta-ouro, com o mesmo LIMIT da busca (45).

DOIS TENANTS
------------
- `pt`: so o acervo gerado. A relevancia das perguntas em portugues sai
  inteiramente do gerador (regras abaixo).
- `en`: o acervo gerado + o manual em ingles de `frontend/public/samples`. As
  perguntas em ingles disputam com os ~500 trechos portugueses, e so o trecho
  do manual que traz a resposta conta como relevante.

REGRA DE RELEVANCIA (explicita, deterministica)
-----------------------------------------------
Relevante = trecho de um documento que contem a resposta. Todo documento do
gerador vira UM trecho (conferido na execucao), entao, para o tenant `pt`:
- pergunta de uma Regra: documento cujo `regras_citadas` contem a chave da
  regra (as duas versoes da politica e todo chamado daquela regra; o gerador
  escreve a pergunta e o valor em cada um);
- demais perguntas: predicado declarado na propria pergunta, sobre a categoria
  e o titulo/texto do documento gerado (ex.: ficha de origem da regiao X).
O grupo `sem-acento` (pergunta digitada sem acento cujo termo decisivo so
existe acentuado no acervo) foi acrescentado depois da primeira rodada, para
testar se tirar o acento importa; por isso a tabela tambem sai por grupo.
Para o tenant `en`: trecho do manual cujo texto contem o marcador da resposta.

METRICAS (por candidato e por idioma)
-------------------------------------
- recall@k = |relevantes no top-k| / min(|relevantes|, k), media por pergunta
  (com teto: vale 1,0 quando o top-k esta cheio de relevantes);
- MRR = media de 1/posicao do primeiro relevante no top-45 (0 se nao ha);
- zero = % de perguntas cuja consulta devolveu nenhuma linha.
Empates de `ts_rank` sao desfeitos por (documento, indice do trecho), com ids
de documento deterministicos: a tabela e reproduzivel.

USO
    cd backend
    .venv/bin/python -m scripts.avaliar_busca_textual
    .venv/bin/python -m scripts.avaliar_busca_textual --json /tmp/aval.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scripts.gerar_acervo_demo import REGRAS, Documento, _escrever_pdf, gerar

BACKEND = Path(__file__).resolve().parents[1]
MANUAL_EN = BACKEND.parent / "frontend" / "public" / "samples" / "aurora-coffee-handbook.pdf"

# O LIMIT da perna textual em producao: retriever pede top_k=15 (5 * 3) e
# hybrid_search multiplica de novo por `search_candidates_multiplier` (3).
LIMITE = 45
KS = (5, 15, 45)

# Namespace fixo: mesmos ids a cada execucao, para o desempate ser estavel.
_NS = uuid.UUID("5b6f7c1e-2f1a-4c55-9d1e-6a8f0e7b3c21")

_TEXTO = "COALESCE(content, '') || ' ' || COALESCE(enriched_content, '')"


def _ou(tsquery_sql: str) -> str:
    """Troca o AND de `plainto_tsquery` por OR, mantendo a normalizacao da config."""
    return f"CAST(replace(CAST({tsquery_sql} AS text), ' & ', ' | ') AS tsquery)"


@dataclass(frozen=True)
class Candidato:
    nome: str
    descricao: str
    vetor: str
    """Expressao SQL do tsvector sobre as colunas de `chunks`."""
    consulta: str
    """Expressao SQL da tsquery; a pergunta chega em `:q`."""


# Duas configs sem acento. A "ingenua" e a receita da doc do Postgres (unaccent
# antes do stemmer): como a lista de stopwords e acentuada, "até"/"não" viram
# "ate"/"nao" ANTES da checagem e deixam de ser descartados. A outra descarta as
# stopwords primeiro e so entao tira o acento.
CONFIG_SA_INGENUA = "aval_pt_unaccent_ingenua"
CONFIG_SEM_ACENTO = "portugues_sem_acento"

_PT_EN = "to_tsvector('{cfg}', {t}) || to_tsvector('english', {t})"
_Q_PT_EN = "(plainto_tsquery('{cfg}', :q) || plainto_tsquery('english', :q))"

CANDIDATOS: tuple[Candidato, ...] = (
    Candidato("A", "english, AND (antes da 008)",
              f"to_tsvector('english', {_TEXTO})",
              "plainto_tsquery('english', :q)"),
    Candidato("B", "portuguese, AND",
              f"to_tsvector('portuguese', {_TEXTO})",
              "plainto_tsquery('portuguese', :q)"),
    Candidato("C", "vetor pt||en, (AND pt) OR (AND en)",
              _PT_EN.format(cfg="portuguese", t=_TEXTO),
              _Q_PT_EN.format(cfg="portuguese")),
    Candidato("D", "portuguese, termos em OR",
              f"to_tsvector('portuguese', {_TEXTO})",
              _ou("plainto_tsquery('portuguese', :q)")),
    Candidato("E", "vetor pt||en, termos em OR",
              _PT_EN.format(cfg="portuguese", t=_TEXTO),
              _ou(_Q_PT_EN.format(cfg="portuguese"))),
    # Extras: o usuario brasileiro digita sem acento ("frete gratis"), e o stemmer
    # portugues sozinho separa 'grát' de 'grat'.
    Candidato("F", "pt sem acento (ingenua), OR",
              f"to_tsvector('{CONFIG_SA_INGENUA}', {_TEXTO})",
              _ou(f"plainto_tsquery('{CONFIG_SA_INGENUA}', :q)")),
    Candidato("G", "vetor pt-sem-acento(ingenua)||en, OR",
              _PT_EN.format(cfg=CONFIG_SA_INGENUA, t=_TEXTO),
              _ou(_Q_PT_EN.format(cfg=CONFIG_SA_INGENUA))),
    Candidato("H", "pt sem acento (stopword antes), OR",
              f"to_tsvector('{CONFIG_SEM_ACENTO}', {_TEXTO})",
              _ou(f"plainto_tsquery('{CONFIG_SEM_ACENTO}', :q)")),
    Candidato("I", "vetor pt-sem-acento||en, OR",
              _PT_EN.format(cfg=CONFIG_SEM_ACENTO, t=_TEXTO),
              _ou(_Q_PT_EN.format(cfg=CONFIG_SEM_ACENTO))),
)


# ---------------------------------------------------------------------------
# Perguntas-ouro
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pergunta:
    texto: str
    idioma: str  # "pt" | "en"
    grupo: str   # "regra-literal" | "regra-parafrase" | "outros" | "sem-acento" | "manual"
    relevante: Callable[[str, "Documento | None", str], bool]
    """(chave do documento, Documento gerado ou None, texto do trecho) -> relevante?"""


def _da_regra(chave: str):
    return lambda _k, d, _t: d is not None and chave in d.regras_citadas


def _categoria(cat: str, *no_texto: str, no_titulo: str = ""):
    """Documento gerado da categoria, com `no_titulo` no titulo e cada item de
    `no_texto` no texto (comparacao exata, com acento, como o gerador escreve)."""
    def pred(_k, d, _t):
        return (
            d is not None
            and d.categoria == cat
            and no_titulo in d.titulo
            and all(s in d.texto for s in no_texto)
        )
    return pred


def _manual(marcador: str):
    return lambda k, _d, t: k == "manual" and marcador in t


PARAFRASES: dict[str, tuple[str, ...]] = {
    "frete_gratis_minimo": (
        "Qual o valor mínimo da compra para ganhar frete grátis?",
        "qual o valor minimo pra ter frete gratis?",
        "Acima de quantos reais o frete fica de graça?",
        "Com quanto de pedido eu não pago o frete para entrega no Brasil?",
    ),
    "devolucao_lacrado_dias": (
        "Qual é o prazo para devolver um pacote de café ainda lacrado?",
        "qual o prazo de devolucao de pacote fechado?",
        "Até quantos dias depois da entrega o cliente pode devolver o café sem abrir?",
        "tenho quantos dias pra devolver um pacote lacrado",
    ),
    "desconto_assinante": (
        "Quanto de desconto o assinante recebe?",
        "qual o desconto pra assinante",
        "Quem tem assinatura ganha quantos por cento de desconto?",
        "Qual a porcentagem de desconto da assinatura do café?",
    ),
    "corte_torra_no_dia": (
        "Qual é o horário limite para o pedido ser torrado no mesmo dia?",
        "ate que horas tenho que pedir pra torrarem no mesmo dia?",
        "Qual o horário de corte da torra do dia?",
        "Se eu fizer o pedido de tarde ele ainda é torrado hoje?",
    ),
    "atacado_minimo_kg": (
        "Qual a quantidade mínima por mês para ter preço de atacado?",
        "quantos kg por mes preciso comprar pra ter preco de atacado?",
        "Qual o volume mínimo mensal de compra no atacado?",
        "Quantos quilos uma cafeteria precisa pedir por mês para pagar preço de atacadista?",
    ),
}


def perguntas_ouro() -> list[Pergunta]:
    ps: list[Pergunta] = []
    for r in REGRAS:
        ps.append(Pergunta(r.pergunta, "pt", "regra-literal", _da_regra(r.chave)))
        for p in PARAFRASES[r.chave]:
            ps.append(Pergunta(p, "pt", "regra-parafrase", _da_regra(r.chave)))

    outros = [
        # fichas de origem: notas e processo sao fixos por regiao no gerador
        ("Quais são as notas sensoriais do café da Chapada Diamantina?",
         _categoria("origem", no_titulo="Chapada Diamantina")),
        ("qual o processo do cafe do cerrado mineiro",
         _categoria("origem", no_titulo="Cerrado Mineiro")),
        ("Tem algum café com notas de pêssego e floral?",
         _categoria("origem", "pêssego, floral leve")),
        ("Qual é a pontuação SCA e a altitude da ficha de origem 012?",
         _categoria("origem", "Ficha de origem 012")),
        ("Qual a recomendação de torra para espresso?",
         _categoria("origem", "média escura para espresso")),
        # chamados: o assunto esta no texto, o numero no cabecalho
        ("Teve algum chamado de cobrança duplicada no cartão?",
         _categoria("chamado", "cobrança duplicada no cartão")),
        ("o que aconteceu no chamado 2042?",
         _categoria("chamado", "Chamado 2042")),
        ("Quais chamados falam de pedido retido na alfândega?",
         _categoria("chamado", "retido na alfândega")),
        ("cliente reclamou que a valvula do pacote veio danificada",
         _categoria("chamado", "válvula danificada")),
        # atas: setor no titulo, item aprovado no texto
        ("Em qual reunião do Financeiro foi aprovada a compra de insumo?",
         _categoria("ata", "Compra de insumo aprovado", no_titulo="Financeiro")),
        ("O que foi decidido na ata de reunião 017?",
         _categoria("ata", "Ata de reunião 017")),
        ("Quando foi aprovado treinamento de equipe na Logística?",
         _categoria("ata", "Treinamento de equipe aprovado", no_titulo="Logística")),
        # procedimentos: passos fixos, setor no titulo
        ("Até que horas a fila do dia precisa ser conferida?",
         _categoria("procedimento", "Conferir a fila do dia antes das 9h")),
        ("Qual o procedimento operacional da logística?",
         _categoria("procedimento", no_titulo="Logística")),
        ("o que fazer quando um desvio nao e resolvido no mesmo turno",
         _categoria("procedimento", "não for resolvido no mesmo turno")),
    ]
    ps += [Pergunta(t, "pt", "outros", pred) for t, pred in outros]

    # Digitadas sem acento, e o termo que separa a resposta so existe COM acento
    # no acervo. Grupo acrescentado depois da primeira rodada, para testar
    # especificamente se tirar o acento importa; reportado a parte.
    sem_acento = [
        ("algum cliente teve problema com a alfandega?",
         _categoria("chamado", "retido na alfândega")),
        ("tem cafe com notas de pessego?",
         _categoria("origem", "pêssego, floral leve")),
        ("quais reunioes aprovaram treinamento na logistica?",
         _categoria("ata", "Treinamento de equipe aprovado", no_titulo="Logística")),
        ("qual o procedimento da area de logistica?",
         _categoria("procedimento", no_titulo="Logística")),
        ("cliente foi cobrado duas vezes no cartao",
         _categoria("chamado", "cobrança duplicada no cartão")),
        ("qual a politica de devolucao?", _da_regra("devolucao_lacrado_dias")),
    ]
    ps += [Pergunta(t, "pt", "sem-acento", pred) for t, pred in sem_acento]

    manual = [
        ("What is the minimum order value for free shipping?", "above 120 reais"),
        ("How long does domestic delivery take?", "Domestic delivery takes 2 to 4"),
        ("Can I return an opened bag of coffee?", "Opened bags can be returned"),
        ("How long does a refund take to show up on my credit card?", "further 2 billing cycles"),
        ("What time is the cutoff for same-day roasting?", "roasted the same day"),
        ("Do international orders get free shipping?", "no free shipping tier for international"),
        ("When does my subscription renew?", "renews every 30 days"),
        ("What discount do subscribers get?", "15 percent off"),
        ("How many kilos per month do I need to order to get wholesale pricing?", "more than 10 kilos"),
        ("Can wholesale customers use the subscriber discount?", "not eligible for the subscriber discount"),
    ]
    ps += [Pergunta(t, "en", "manual", _manual(m)) for t, m in manual]
    return ps


# ---------------------------------------------------------------------------
# Banco temporario
# ---------------------------------------------------------------------------

def _preparar_env(url_temp: str) -> None:
    """O app le a config do ambiente no import; so a URL do banco importa aqui.
    As chaves sao ficticias porque nada neste script chama provider."""
    os.environ["DATABASE_URL"] = url_temp
    os.environ.setdefault("JWT_SECRET", "avaliacao-local-sem-uso-real-0000000000")
    os.environ.setdefault("VOYAGE_API_KEY", "avaliacao-sem-chamada")


def _garantir_configs_sem_acento(conn) -> None:
    """Cria as configs sem acento que ainda nao existirem no banco temporario."""
    from sqlalchemy import text as sqltext

    conn.execute(sqltext("CREATE EXTENSION IF NOT EXISTS unaccent"))
    conn.execute(sqltext(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = '{CONFIG_SA_INGENUA}') THEN
                CREATE TEXT SEARCH CONFIGURATION {CONFIG_SA_INGENUA} (COPY = portuguese);
                ALTER TEXT SEARCH CONFIGURATION {CONFIG_SA_INGENUA}
                    ALTER MAPPING FOR hword, hword_part, word WITH unaccent, portuguese_stem;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_ts_dict WHERE dictname = 'portugues_stopwords') THEN
                CREATE TEXT SEARCH DICTIONARY portugues_stopwords
                    (TEMPLATE = simple, STOPWORDS = portuguese, ACCEPT = false);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = '{CONFIG_SEM_ACENTO}') THEN
                CREATE TEXT SEARCH CONFIGURATION {CONFIG_SEM_ACENTO} (COPY = portuguese);
                ALTER TEXT SEARCH CONFIGURATION {CONFIG_SEM_ACENTO}
                    ALTER MAPPING FOR hword, hword_part, word
                    WITH portugues_stopwords, unaccent, portuguese_stem;
            END IF;
        END $$;
    """))


@dataclass
class Trecho:
    id: str
    tenant: str
    chave: str             # nome do arquivo gerado, ou "manual"
    doc: Documento | None
    texto: str


def _textos_pelo_pdf(docs: list[Documento]) -> dict[str, list[str]]:
    """Paginas de cada documento pelo caminho real: PDF do gerador -> extracao."""
    from app.core.ingestion.pdf_processor import extract_pages_from_pdf

    paginas: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "doc.pdf"
        for d in docs:
            _escrever_pdf(d.texto, destino)
            paginas[d.nome_arquivo] = extract_pages_from_pdf(destino.read_bytes())
    return paginas


def semear(docs: list[Documento]) -> list[Trecho]:
    from sqlalchemy import insert, text as sqltext

    from app.core.ingestion.chunker import chunk_document_pages
    from app.core.ingestion.pdf_processor import extract_pages_from_pdf
    from app.db.engine import engine
    from app.db.models import documents, users
    from app.services.vector_store import add_chunks

    paginas = _textos_pelo_pdf(docs)
    paginas_manual = extract_pages_from_pdf(MANUAL_EN.read_bytes())

    trechos: list[Trecho] = []
    for tenant in ("pt", "en"):
        user_id = str(uuid.uuid5(_NS, f"user:{tenant}"))
        with engine.begin() as conn:
            conn.execute(insert(users).values(
                id=user_id, email=f"aval-{tenant}@exemplo.com.br", password_hash="x",
            ))

        fontes: list[tuple[str, Documento | None, list[str]]] = [
            (d.nome_arquivo, d, paginas[d.nome_arquivo]) for d in docs
        ]
        if tenant == "en":
            fontes.append(("manual", None, paginas_manual))

        for chave, doc, pags in fontes:
            doc_id = str(uuid.uuid5(_NS, f"doc:{tenant}:{chave}"))
            with engine.begin() as conn:
                conn.execute(insert(documents).values(
                    id=doc_id, user_id=user_id, title=chave, mime="application/pdf",
                    storage_path=f"{user_id}/docs/{chave}", status="ready",
                ))
            pedacos = chunk_document_pages(pags)
            if doc is not None and len(pedacos) != 1:
                # A regra de relevancia por documento assume um trecho por
                # documento gerado; se o gerador crescer, isto avisa.
                raise SystemExit(f"{chave} virou {len(pedacos)} trechos; revise a regra de relevancia")
            textos = [t for t, _ in pedacos]
            add_chunks(
                texts=textos, enriched_texts=textos, embeddings=[None] * len(textos),
                user_id=user_id, document_id=doc_id,
                pages=[m["page"] for _, m in pedacos],
                chunk_indices=[m["chunk_index"] for _, m in pedacos],
            )
        with engine.begin() as conn:
            linhas = conn.execute(sqltext(
                "SELECT c.id, d.title, c.content FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE c.user_id = :u"
            ), {"u": user_id}).all()
        por_chave = {d.nome_arquivo: d for d in docs}
        for cid, chave, texto in linhas:
            trechos.append(Trecho(str(cid), tenant, chave, por_chave.get(chave), texto))
    return trechos


# ---------------------------------------------------------------------------
# Medicao
# ---------------------------------------------------------------------------

def _app_como_candidato() -> Candidato:
    """A perna textual como o app a executa HOJE: coluna do trigger + tsquery do
    vector_store. Confere que migration e codigo entregam o numero medido."""
    from app.services import vector_store

    return Candidato("APP", "vector_store + trigger em vigor", "search_vector",
                     vector_store.TSQUERY_SQL.replace(":query_text", ":q"))


def medir(candidatos: list[Candidato], perguntas: list[Pergunta], trechos: list[Trecho]) -> dict:
    from sqlalchemy import text as sqltext

    from app.db.engine import engine

    with engine.begin() as conn:
        _garantir_configs_sem_acento(conn)
        for c in candidatos:
            if c.vetor == "search_vector":
                continue
            col = f"sv_{c.nome.lower()}"
            conn.execute(sqltext(f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS {col} tsvector"))
            conn.execute(sqltext(f"UPDATE chunks SET {col} = {c.vetor}"))

    user_de = {t: str(uuid.uuid5(_NS, f"user:{t}")) for t in ("pt", "en")}
    relevantes: list[set[str]] = []
    for p in perguntas:
        rel = {t.id for t in trechos if t.tenant == p.idioma and p.relevante(t.chave, t.doc, t.texto)}
        if not rel:
            raise SystemExit(f"pergunta sem nenhum trecho relevante (regra quebrada): {p.texto!r}")
        relevantes.append(rel)

    saida: dict = {"candidatos": {}}
    for c in candidatos:
        col = "search_vector" if c.vetor == "search_vector" else f"sv_{c.nome.lower()}"
        sql = sqltext(f"""
            SELECT c.id
              FROM chunks c
             WHERE c.{col} @@ {c.consulta}
               AND c.user_id = CAST(:u AS uuid)
             ORDER BY ts_rank(c.{col}, {c.consulta}) DESC, c.document_id, c.chunk_index
             LIMIT {LIMITE}
        """)
        por_pergunta = []
        with engine.begin() as conn:
            for p, rel in zip(perguntas, relevantes):
                ids = [str(r[0]) for r in conn.execute(sql, {"q": p.texto, "u": user_de[p.idioma]})]
                pos = next((i + 1 for i, x in enumerate(ids) if x in rel), None)
                por_pergunta.append({
                    "pergunta": p.texto, "idioma": p.idioma, "grupo": p.grupo,
                    "relevantes": len(rel), "devolvidos": len(ids),
                    "primeiro_relevante": pos,
                    **{f"recall@{k}": len(rel & set(ids[:k])) / min(len(rel), k) for k in KS},
                })
        saida["candidatos"][c.nome] = {"descricao": c.descricao, "perguntas": por_pergunta}
    return saida


def _agregar(linhas: list[dict]) -> dict:
    n = len(linhas)
    return {
        "n": n,
        **{f"recall@{k}": sum(l[f"recall@{k}"] for l in linhas) / n for k in KS},
        "mrr": sum(1 / l["primeiro_relevante"] for l in linhas if l["primeiro_relevante"]) / n,
        "zero": 100 * sum(1 for l in linhas if l["devolvidos"] == 0) / n,
    }


def tabela(resultado: dict, fatia: Callable[[dict], str], titulo: str) -> str:
    cab = "| cand | descricao | " + titulo + " | n | recall@5 | recall@15 | recall@45 | MRR | zero % |"
    sep = "|---|---|---|---:|---:|---:|---:|---:|---:|"
    linhas = [cab, sep]
    for nome, c in resultado["candidatos"].items():
        grupos: dict[str, list[dict]] = {}
        for l in c["perguntas"]:
            grupos.setdefault(fatia(l), []).append(l)
        for g in sorted(grupos, key=lambda g: (g != "pt", g)):
            a = _agregar(grupos[g])
            c["agregado"] = c.get("agregado", {}) | {g: a}
            linhas.append(
                f"| {nome} | {c['descricao']} | {g} | {a['n']} | {a['recall@5']:.3f} | "
                f"{a['recall@15']:.3f} | {a['recall@45']:.3f} | {a['mrr']:.3f} | {a['zero']:.1f} |"
            )
    return "\n".join(linhas)


def main() -> None:
    p = argparse.ArgumentParser(description="Avalia a perna de palavra-chave da busca.")
    p.add_argument("--database-url", default="postgresql://localhost/postgres",
                   help="servidor onde o banco temporario e criado (precisa de CREATE DATABASE)")
    p.add_argument("--quantidade", type=int, default=500)
    p.add_argument("--semente", type=int, default=7)
    p.add_argument("--json", type=Path, help="grava o resultado por pergunta neste arquivo")
    args = p.parse_args()

    from sqlalchemy import create_engine, text as sqltext
    from sqlalchemy.engine import make_url

    nome = f"gd_eval_{uuid.uuid4().hex}"
    admin = create_engine(args.database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sqltext(f'CREATE DATABASE "{nome}"'))
    url_temp = make_url(args.database_url).set(database=nome).render_as_string(hide_password=False)
    _preparar_env(url_temp)

    try:
        from app.db.engine import engine
        from app.db.migrate import run_migrations

        run_migrations()
        docs = gerar(args.quantidade, args.semente)
        trechos = semear(docs)
        perguntas = perguntas_ouro()
        candidatos = list(CANDIDATOS) + [_app_como_candidato()]
        resultado = medir(candidatos, perguntas, trechos)
        engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(
                sqltext("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d"),
                {"d": nome},
            )
            conn.execute(sqltext(f'DROP DATABASE IF EXISTS "{nome}"'))
        admin.dispose()

    n_pt = sum(1 for x in perguntas if x.idioma == "pt")
    n_en = len(perguntas) - n_pt
    print(f"acervo: {len(docs)} documentos (semente {args.semente}); "
          f"perguntas: {n_pt} pt, {n_en} en; LIMIT {LIMITE}\n")
    print("## Por idioma\n")
    print(tabela(resultado, lambda l: l["idioma"], "idioma"))
    print("\n## Portugues por grupo\n")
    resultado_pt = {"candidatos": {
        k: {"descricao": v["descricao"], "perguntas": [l for l in v["perguntas"] if l["idioma"] == "pt"]}
        for k, v in resultado["candidatos"].items()
    }}
    print(tabela(resultado_pt, lambda l: l["grupo"], "grupo"))

    if args.json:
        args.json.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\njson: {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()

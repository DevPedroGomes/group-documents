"""Mede a perna de PALAVRA-CHAVE da busca hibrida, sem API paga.

POR QUE
-------
A perna textual (tsvector + GIN) foi "medida" uma vez com 8 palavras soltas.
Isso nao e recall: o usuario digita uma PERGUNTA, e `plainto_tsquery` faz AND
de todos os termos. Com `english` num acervo portugues, "qual", "o", "de",
"para" viram termos obrigatorios e a pergunta inteira nao casa com nada.

COMO
----
1. Gera o acervo da demo (`scripts.gerar_acervo_demo.gerar`, deterministico),
   passa cada documento pelo MESMO caminho de texto da ingestao (PDF escrito
   pelo gerador -> `extract_pages_from_pdf` -> `chunk_document_pages`), SEM o
   enriquecimento por LLM, e grava com `add_chunks` (o trigger monta
   `search_vector`).
2. Cria um banco TEMPORARIO (`gd_eval_<uuid>`) no servidor de `--database-url`,
   aplica as migrations e o derruba no fim. As configs de texto vem SO das
   migrations: avaliacao e app nao podem divergir.
3. Para cada candidato, monta o tsvector numa coluna propria e roda a consulta
   de palavra-chave de cada pergunta-ouro.

O QUE ESTA AVALIACAO NAO MOSTRA
-------------------------------
- O acervo e SINTETICO e feito de moldes: todo documento de uma regra repete a
  mesma frase-pergunta, e muita pergunta tem um termo que so existe nos
  documentos relevantes (coluna "termo exclusivo"). Ai o OR vira busca de um
  termo raro. O grupo `regra-vocab-diferente` existe para medir o caso oposto.
- Nao mede a busca hibrida (so a perna textual), nem o reranker, nem trechos
  com o contexto do enriquecimento por LLM, que em producao tambem e indexado.
- O conjunto em ingles tem UM trecho relevante por pergunta (o manual tem 2
  trechos longos); os distratores em ingles sao moldes curtos.
- "zero %" (pergunta sem nenhuma linha) e diagnostico, nao qualidade: com OR,
  qualquer termo comum a todos os documentos zera a coluna sozinho.

GRUPOS
------
- original: `regra-literal` (as 5 `Regra.pergunta`), `regra-parafrase-molde`
  (reescritas que reusam o vocabulario do molde), `outros-tipos` (fichas,
  chamados, atas, procedimentos), `en-manual` (ingles).
- pos-hoc: `sem-acento` (rodada 1: digitada sem acento, termo decisivo so
  existe acentuado) e `regra-vocab-diferente` (revisao 1: pergunta natural SEM
  nenhum lexema do molde da regra; o script confere e aborta se houver).

REGRA DE RELEVANCIA (explicita, deterministica)
-----------------------------------------------
Relevante = trecho de um documento que contem a resposta. Todo documento do
gerador vira UM trecho (conferido na execucao), entao, para o tenant `pt`:
- pergunta de uma Regra: documento cujo `regras_citadas` contem a chave (as
  duas versoes da politica e todo chamado daquela regra);
- demais: predicado declarado na propria pergunta, sobre categoria e
  titulo/texto do documento gerado.
Tenant `en` (acervo gerado + manual + distratores em ingles): so o trecho do
manual cujo texto contem o marcador da resposta.

METRICAS
--------
- recall@k = |relevantes no top-k| / min(|relevantes|, k), media por pergunta;
- MRR = media de 1/posicao do primeiro relevante no top-45 (0 se nao ha);
- P@5 = relevantes no top-5 / 5;
- ruido@5 = fracao dos resultados do top-5 cujas palavras da pergunta que
  casaram sao TODAS funcionais (stopword pt ou en, ou forma sem acento de uma
  stopword pt, ou "é"/"pra"/"pro");
- zero % = diagnostico (ver acima).
Empate de `ts_rank` e desfeito de forma PESSIMISTA: nao relevante primeiro.
Linhas de base: R0 ordena todos os trechos do tenant por um hash fixo
(aleatorio); R1 pega o conjunto que casa na config do app e ordena por hash
(casar qualquer termo, sem ranking).

USO
    cd backend
    .venv/bin/python -m scripts.avaliar_busca_textual
    .venv/bin/python -m scripts.avaliar_busca_textual --json /tmp/aval.json \\
        --database-url postgresql://usuario:senha@localhost:5432/postgres
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import random
import re
import statistics
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

# Namespace fixo: mesmos ids a cada execucao.
_NS = uuid.UUID("5b6f7c1e-2f1a-4c55-9d1e-6a8f0e7b3c21")

_TEXTO = "COALESCE(content, '') || ' ' || COALESCE(enriched_content, '')"


def _ou(tsquery_sql: str) -> str:
    """Troca o AND de `plainto_tsquery` por OR, mantendo a normalizacao da config."""
    return f"CAST(replace(CAST({tsquery_sql} AS text), ' & ', ' | ') AS tsquery)"


@dataclass(frozen=True)
class Candidato:
    nome: str
    descricao: str
    vetor: str | None
    """tsvector sobre as colunas de `chunks`; "search_vector" = coluna do
    trigger; None = sem filtro (linha de base aleatoria)."""
    consulta: str | None
    """tsquery; a pergunta chega em `:q`."""
    aleatorio: bool = False
    requer_unaccent: bool = False


def _duplo(cfg_a: str, cfg_b: str, t: str = _TEXTO) -> str:
    return f"to_tsvector('{cfg_a}', {t}) || to_tsvector('{cfg_b}', {t})"


def _q_duplo(cfg_a: str, cfg_b: str) -> str:
    return f"(plainto_tsquery('{cfg_a}', :q) || plainto_tsquery('{cfg_b}', :q))"


CANDIDATOS: tuple[Candidato, ...] = (
    Candidato("A", "english, AND (antes da 008)",
              f"to_tsvector('english', {_TEXTO})", "plainto_tsquery('english', :q)"),
    Candidato("B", "portuguese, AND",
              f"to_tsvector('portuguese', {_TEXTO})", "plainto_tsquery('portuguese', :q)"),
    Candidato("C", "portuguese||english, (AND) OR (AND)",
              _duplo("portuguese", "english"), _q_duplo("portuguese", "english")),
    Candidato("D", "portuguese, OR",
              f"to_tsvector('portuguese', {_TEXTO})", _ou("plainto_tsquery('portuguese', :q)")),
    Candidato("E", "portuguese||english, OR",
              _duplo("portuguese", "english"), _ou(_q_duplo("portuguese", "english"))),
    # unaccent() antes do parser: a receita ingenua, sem config propria. A
    # stopword acentuada ("até") vira "ate" antes da checagem e sobrevive.
    Candidato("F", "portuguese sobre unaccent(), OR",
              f"to_tsvector('portuguese', unaccent({_TEXTO}))",
              _ou("plainto_tsquery('portuguese', unaccent(:q))"), requer_unaccent=True),
    Candidato("G", "F || english, OR",
              f"to_tsvector('portuguese', unaccent({_TEXTO})) || to_tsvector('english', {_TEXTO})",
              _ou("(plainto_tsquery('portuguese', unaccent(:q)) || plainto_tsquery('english', :q))"),
              requer_unaccent=True),
    # Configs da 008: cada metade descarta stopwords das duas linguas.
    Candidato("K", "busca_portugues, OR",
              f"to_tsvector('busca_portugues', {_TEXTO})", _ou("plainto_tsquery('busca_portugues', :q)")),
    Candidato("J", "busca_portugues||busca_ingles, OR",
              _duplo("busca_portugues", "busca_ingles"), _ou(_q_duplo("busca_portugues", "busca_ingles"))),
)


# ---------------------------------------------------------------------------
# Perguntas-ouro
# ---------------------------------------------------------------------------

GRUPOS: dict[str, str] = {
    "regra-literal": "original",
    "regra-parafrase-molde": "original",
    "outros-tipos": "original",
    "en-manual": "original",
    "sem-acento": "pos-hoc, rodada 1",
    "regra-vocab-diferente": "pos-hoc, revisao 1",
}


@dataclass(frozen=True)
class Pergunta:
    texto: str
    idioma: str  # "pt" | "en"
    grupo: str
    relevante: Callable[[str, "Documento | None", str], bool]
    """(chave do documento, Documento gerado ou None, texto do trecho) -> relevante?"""
    regra: str | None = None


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


# Reescritas que reusam o vocabulario do molde (frete, desconto, atacado...).
PARAFRASES_MOLDE: dict[str, tuple[str, ...]] = {
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

# Como alguem perguntaria SEM ter lido o documento: nenhum lexema do molde da
# regra (pergunta, valores e unidade), conferido por `_validar_vocab_diferente`.
# Palavra de OUTRA regra pode aparecer ("preço", "mês"): e confusao realista.
VOCAB_DIFERENTE: dict[str, tuple[str, ...]] = {
    "frete_gratis_minimo": (
        "Qual o valor mínimo de compra para a entrega sair sem custo?",
        "Existe um valor de compra que me isenta da taxa de envio?",
        "Comprando bastante, eu deixo de pagar o transporte?",
        "Pedindo acima de certo total, a remessa fica por conta da loja?",
    ),
    "devolucao_lacrado_dias": (
        "Qual o prazo para pedir reembolso de um produto que nem foi aberto?",
        "Até quando consigo trocar uma embalagem fechada por dinheiro de volta?",
        "Comprei e me arrependi: qual o limite de tempo para mandar a encomenda de volta sem abrir?",
        "Qual a janela de retorno para mercadoria intacta?",
    ),
    "desconto_assinante": (
        "Quem tem plano mensal paga mais barato?",
        "Clientes do clube de café ganham abatimento no valor?",
        "Vale a pena virar membro recorrente, sai mais em conta?",
        "Qual a vantagem no preço para quem recebe café todo mês?",
    ),
    "corte_torra_no_dia": (
        "Qual o horário limite para meu café sair fresquinho hoje?",
        "Comprando à tarde, a torrefação ainda processa no mesmo expediente?",
        "Qual o horário de fechamento da produção diária?",
        "Se eu comprar no fim da tarde, quando o café fica pronto?",
    ),
    "atacado_minimo_kg": (
        "Qual o volume mínimo para uma cafeteria comprar como revendedora?",
        "Tenho um restaurante: qual a quantidade mensal para ter condição de empresa?",
        "Comprando em grande quantidade, com que peso o valor cai?",
        "Qual o pedido mínimo para revenda?",
    ),
}


def perguntas_ouro() -> list[Pergunta]:
    ps: list[Pergunta] = []
    for r in REGRAS:
        ps.append(Pergunta(r.pergunta, "pt", "regra-literal", _da_regra(r.chave), r.chave))
        for p in PARAFRASES_MOLDE[r.chave]:
            ps.append(Pergunta(p, "pt", "regra-parafrase-molde", _da_regra(r.chave), r.chave))

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
    ps += [Pergunta(t, "pt", "outros-tipos", pred) for t, pred in outros]

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

    for r in REGRAS:
        for p in VOCAB_DIFERENTE[r.chave]:
            ps.append(Pergunta(p, "pt", "regra-vocab-diferente", _da_regra(r.chave), r.chave))

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
    ps += [Pergunta(t, "en", "en-manual", _manual(m)) for t, m in manual]
    return ps


def distratores_en(quantidade: int = 60, semente: int = 7) -> list[tuple[str, str]]:
    """Chamados em ingles sobre os MESMOS assuntos das perguntas do manual, sem
    nenhum valor de politica: disputam o ranking sem conter a resposta."""
    assuntos = [
        "free shipping on a domestic order", "an international delivery held at customs",
        "a refund for an opened bag", "a credit card refund that has not appeared yet",
        "renewing a subscription early", "the subscriber discount on a seasonal promotion",
        "wholesale pricing for a cafe", "same-day roasting of a late order",
        "the return window for a gift", "delivery times for an office address",
    ]
    acoes = [
        "The agent explained the policy and closed the case.",
        "The team replied by email and the customer agreed.",
        "The case was escalated to the commercial team and resolved.",
    ]
    rng = random.Random(semente)
    saida = []
    for n in range(quantidade):
        texto = (
            f"Support ticket {3000 + n}. Subject: {rng.choice(assuntos)}. "
            f"The customer contacted support about their order. {rng.choice(acoes)}"
        )
        saida.append((f"en-ticket-{3000 + n}", texto))
    return saida


# ---------------------------------------------------------------------------
# Banco temporario
# ---------------------------------------------------------------------------

def _preparar_env(url_temp: str) -> None:
    """O app le a config do ambiente no import; so a URL do banco importa aqui.
    As chaves sao ficticias porque nada neste script chama provider."""
    os.environ["DATABASE_URL"] = url_temp
    os.environ.setdefault("JWT_SECRET", "avaliacao-local-sem-uso-real-0000000000")
    os.environ.setdefault("VOYAGE_API_KEY", "avaliacao-sem-chamada")


@dataclass
class Trecho:
    id: str
    tenant: str
    chave: str             # nome do arquivo gerado, "manual" ou "en-ticket-N"
    doc: Documento | None
    texto: str
    indice: int            # chunk_index: com `chave`, identifica o trecho entre execucoes


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


def _user_id(tenant: str) -> str:
    return str(uuid.uuid5(_NS, f"user:{tenant}"))


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
        user_id = _user_id(tenant)
        with engine.begin() as conn:
            conn.execute(insert(users).values(
                id=user_id, email=f"aval-{tenant}@exemplo.com.br", password_hash="x",
            ))

        fontes: list[tuple[str, Documento | None, list[str]]] = [
            (d.nome_arquivo, d, paginas[d.nome_arquivo]) for d in docs
        ]
        if tenant == "en":
            fontes.append(("manual", None, paginas_manual))
            fontes += [(chave, None, [texto]) for chave, texto in distratores_en()]

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
                "SELECT c.id, d.title, c.content, c.chunk_index FROM chunks c "
                "JOIN documents d ON d.id = c.document_id WHERE c.user_id = :u"
            ), {"u": user_id}).all()
        por_chave = {d.nome_arquivo: d for d in docs}
        for cid, chave, texto, indice in linhas:
            trechos.append(Trecho(str(cid), tenant, chave, por_chave.get(chave), texto, indice))
    return trechos


# ---------------------------------------------------------------------------
# Medicao
# ---------------------------------------------------------------------------

# Formas sem acento das stopwords acentuadas do portugues (quem digita sem
# acento escreve "nao", "ate"), mais funcionais que a lista do Postgres nao tem.
SUPLEMENTO_FUNCIONAIS = frozenset({
    "ate", "eramos", "esta", "estao", "estavamos", "estiveramos", "estivessemos",
    "foramos", "fossemos", "ha", "hao", "houvera", "houveramos", "houverao",
    "houveriamos", "houvessemos", "ja", "nao", "nos", "sao", "sera", "serao",
    "seriamos", "so", "tambem", "tera", "terao", "teriamos", "tinhamos",
    "tiveramos", "tivessemos", "voce", "voces", "é", "pra", "pro", "pras", "pros",
})


def _palavras(texto: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\w+", texto.lower())))


def _app_como_candidato() -> Candidato:
    """A perna textual como o app a executa HOJE: coluna do trigger + tsquery do
    vector_store. Confere que migration e codigo entregam o numero medido."""
    from app.services import vector_store

    return Candidato("APP", "vector_store + trigger em vigor", "search_vector",
                     vector_store.TSQUERY_SQL.replace(":query_text", ":q"))


def linhas_de_base() -> list[Candidato]:
    app = _app_como_candidato()
    return [
        Candidato("R0", "aleatorio: todos os trechos, ordem por hash", None, None, aleatorio=True),
        dataclasses.replace(app, nome="R1", descricao="casa qualquer termo (app), ordem por hash",
                            aleatorio=True),
    ]


def _hash(trecho_estavel: str, pergunta: str) -> int:
    # O id do chunk e sorteado pelo banco; o hash usa (documento, indice).
    return int(hashlib.md5(f"{trecho_estavel}:{pergunta}".encode()).hexdigest()[:12], 16)


def _validar_vocab_diferente(conn, perguntas: list[Pergunta]) -> None:
    """Pergunta de `regra-vocab-diferente` nao pode ter lexema do molde da regra
    (na normalizacao do app); se tiver, nao mede o que diz medir."""
    from sqlalchemy import text as sqltext

    from app.services.vector_store import TEXT_SEARCH_CONFIGS

    vetor = " || ".join(f"to_tsvector('{cfg}', CAST(:t AS text))" for cfg in TEXT_SEARCH_CONFIGS)

    def lexemas(texto: str) -> set[str]:
        return set(conn.execute(sqltext(f"SELECT tsvector_to_array({vetor})"), {"t": texto}).scalar_one())

    regras = {r.chave: r for r in REGRAS}
    for p in perguntas:
        if p.grupo != "regra-vocab-diferente":
            continue
        r = regras[p.regra]
        molde = lexemas(f"{r.pergunta} {r.v1_valor} {r.v2_valor} {r.unidade}")
        comuns = lexemas(p.texto) & molde
        if comuns:
            raise SystemExit(f"{p.texto!r} usa o molde da regra {r.chave}: {sorted(comuns)}")


def medir(candidatos: list[Candidato], perguntas: list[Pergunta], trechos: list[Trecho]) -> dict:
    from sqlalchemy import text as sqltext

    from app.db.engine import engine
    from app.services.vector_store import TSQUERY_SQL

    with engine.begin() as conn:
        tem_unaccent = bool(conn.execute(sqltext(
            "SELECT count(*) FROM pg_extension WHERE extname = 'unaccent'")).scalar())
        candidatos = [c for c in candidatos if tem_unaccent or not c.requer_unaccent]
        for c in candidatos:
            if c.vetor in (None, "search_vector"):
                continue
            col = f"sv_{c.nome.lower()}"
            conn.execute(sqltext(f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS {col} tsvector"))
            conn.execute(sqltext(f"UPDATE chunks SET {col} = {c.vetor}"))
        _validar_vocab_diferente(conn, perguntas)

        todas = sorted({w for p in perguntas for w in _palavras(p.texto)})
        funcionais = {
            w for w, f in conn.execute(sqltext(
                "SELECT w, ts_lexize('portuguese_stem', w) = '{}' OR ts_lexize('english_stem', w) = '{}' "
                "FROM unnest(CAST(:ws AS text[])) AS w"), {"ws": todas})
            if f
        } | (SUPLEMENTO_FUNCIONAIS & set(todas))

    estavel = {t.id: f"{t.tenant}:{t.chave}#{t.indice}" for t in trechos}
    relevantes: list[set[str]] = []
    for p in perguntas:
        rel = {t.id for t in trechos if t.tenant == p.idioma and p.relevante(t.chave, t.doc, t.texto)}
        if not rel:
            raise SystemExit(f"pergunta sem nenhum trecho relevante (regra quebrada): {p.texto!r}")
        relevantes.append(rel)

    # Diagnostico independente de candidato: a pergunta tem alguma palavra de
    # conteudo que, na normalizacao do app, so aparece em trecho relevante?
    exclusivo_sql = sqltext(f"""
        SELECT array_agg(c.id) FROM chunks c
         WHERE c.search_vector @@ {TSQUERY_SQL} AND c.user_id = CAST(:u AS uuid)
    """)
    diagnostico = []
    with engine.begin() as conn:
        for p, rel in zip(perguntas, relevantes):
            exclusivas = []
            for w in _palavras(p.texto):
                if w in funcionais:
                    continue
                ids = conn.execute(exclusivo_sql, {"query_text": w, "u": _user_id(p.idioma)}).scalar()
                if ids and {str(i) for i in ids} <= rel:
                    exclusivas.append(w)
            diagnostico.append({"pergunta": p.texto, "grupo": p.grupo, "relevantes": len(rel),
                                "termos_exclusivos": exclusivas})

    saida: dict = {"funcionais": sorted(funcionais), "tem_unaccent": tem_unaccent,
                   "diagnostico": diagnostico, "candidatos": {}}
    for c in candidatos:
        col = "search_vector" if c.vetor == "search_vector" else f"sv_{c.nome.lower()}"
        if c.vetor is None:
            sql = sqltext("SELECT c.id, 0.0 FROM chunks c WHERE c.user_id = CAST(:u AS uuid)")
        else:
            sql = sqltext(f"""
                SELECT c.id, ts_rank(c.{col}, {c.consulta}) FROM chunks c
                 WHERE c.{col} @@ {c.consulta} AND c.user_id = CAST(:u AS uuid)
            """)
        casou_sql = None
        if c.consulta:
            casou_sql = sqltext(f"""
                SELECT c.id, array_agg(p.w) FILTER (WHERE c.{col} @@ {c.consulta.replace(':q', 'p.w')})
                  FROM chunks c CROSS JOIN unnest(CAST(:ws AS text[])) AS p(w)
                 WHERE c.id = ANY(CAST(:ids AS uuid[]))
                 GROUP BY c.id
            """)
        por_pergunta = []
        with engine.begin() as conn:
            for p, rel in zip(perguntas, relevantes):
                linhas = [(str(i), float(s)) for i, s in conn.execute(sql, {"q": p.texto, "u": _user_id(p.idioma)})]
                if c.aleatorio:
                    linhas = [(i, float(_hash(estavel[i], p.texto))) for i, _ in linhas]
                # Pessimista: no empate, o nao relevante vem antes.
                linhas.sort(key=lambda x: (-x[1], x[0] in rel))
                ids = [i for i, _ in linhas[:LIMITE]]
                top5 = ids[:5]
                ruidosos = 0
                if casou_sql is not None and top5:
                    casou = {
                        str(i): ws or []
                        for i, ws in conn.execute(casou_sql, {"ws": _palavras(p.texto), "ids": top5})
                    }
                    ruidosos = sum(
                        1 for i in top5
                        if casou.get(i) and all(w in funcionais for w in casou[i])
                    )
                pos = next((k + 1 for k, x in enumerate(ids) if x in rel), None)
                por_pergunta.append({
                    "pergunta": p.texto, "idioma": p.idioma, "grupo": p.grupo,
                    "relevantes": len(rel), "devolvidos": len(linhas),
                    "primeiro_relevante": pos,
                    **{f"recall@{k}": len(rel & set(ids[:k])) / min(len(rel), k) for k in KS},
                    "p@5": len(rel & set(top5)) / 5,
                    "top5": len(top5) if casou_sql is not None else 0,
                    "ruidosos_top5": ruidosos,
                })
        saida["candidatos"][c.nome] = {"descricao": c.descricao, "perguntas": por_pergunta}
    return saida


def _agregar(linhas: list[dict]) -> dict:
    n = len(linhas)
    top5 = sum(x["top5"] for x in linhas)
    return {
        "n": n,
        **{f"recall@{k}": sum(x[f"recall@{k}"] for x in linhas) / n for k in KS},
        "mrr": sum(1 / x["primeiro_relevante"] for x in linhas if x["primeiro_relevante"]) / n,
        "p@5": sum(x["p@5"] for x in linhas) / n,
        "ruido@5": (sum(x["ruidosos_top5"] for x in linhas) / top5) if top5 else None,
        "zero": 100 * sum(1 for x in linhas if x["devolvidos"] == 0) / n,
    }


def _fatias(resultado: dict) -> list[tuple[str, Callable[[dict], bool]]]:
    grupos = [(g, (lambda g: lambda x: x["grupo"] == g)(g)) for g in GRUPOS]
    originais = {g for g, o in GRUPOS.items() if o == "original"}
    grupos.insert(3, ("pt-original (todas as pt originais)",
                      lambda x: x["idioma"] == "pt" and x["grupo"] in originais))
    return grupos


def relatorio(resultado: dict) -> str:
    out = []
    diag = resultado["diagnostico"]
    out.append("## Diagnostico por grupo (independe de candidato)\n")
    out.append("| grupo | origem | n | relevantes min / mediana / max | com termo exclusivo |")
    out.append("|---|---|---:|---|---:|")
    for g, origem in GRUPOS.items():
        ds = [d for d in diag if d["grupo"] == g]
        tam = [d["relevantes"] for d in ds]
        excl = sum(1 for d in ds if d["termos_exclusivos"])
        out.append(f"| {g} | {origem} | {len(ds)} | {min(tam)} / {statistics.median(tam):g} / {max(tam)} | "
                   f"{excl}/{len(ds)} |")

    out.append("\n## Resultados por grupo (empate pessimista; zero % e diagnostico)\n")
    for nome_fatia, filtro in _fatias(resultado):
        out.append(f"### {nome_fatia}\n")
        out.append("| cand | descricao | recall@5 | recall@15 | recall@45 | MRR | P@5 | ruido@5 | zero % |")
        out.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for nome, c in resultado["candidatos"].items():
            a = _agregar([x for x in c["perguntas"] if filtro(x)])
            ruido = "-" if a["ruido@5"] is None else f"{a['ruido@5']:.3f}"
            out.append(
                f"| {nome} | {c['descricao']} | {a['recall@5']:.3f} | {a['recall@15']:.3f} | "
                f"{a['recall@45']:.3f} | {a['mrr']:.3f} | {a['p@5']:.3f} | {ruido} | {a['zero']:.1f} |"
            )
        out.append("")
    return "\n".join(out)


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
        candidatos = list(CANDIDATOS) + [_app_como_candidato()] + linhas_de_base()
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

    n_en = len(distratores_en())
    print(f"acervo: {len(docs)} documentos (semente {args.semente}); tenant en: + manual (2 trechos) "
          f"+ {n_en} distratores em ingles; LIMIT {LIMITE}; unaccent: "
          f"{'presente' if resultado['tem_unaccent'] else 'AUSENTE (F e G fora)'}\n")
    print(relatorio(resultado))

    if args.json:
        args.json.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json: {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()

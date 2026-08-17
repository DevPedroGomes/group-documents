"""Filtragem dos chunks recuperados, ciente da ESCALA do score.

O bug que este arquivo existia para causar:

`relevance_score` chega aqui vindo de duas origens completamente diferentes.
Quando o reranker da Cohere roda, e uma relevancia calibrada entre 0 e 1.
Quando ele nao roda — chave ausente ou ENABLE_RERANKING=false — o campo
continua sendo o score de Reciprocal Rank Fusion do `hybrid_search`, cujo
maximo teorico e `2 / (rrf_k + 1)`: com rrf_k=60, **0,0328**.

O limiar era um numero absoluto (0.7) comparado contra os dois. Com a Cohere
desligada isso descartava 100% dos documentos em TODA pergunta, sempre. A rede
de seguranca entao devolvia 2 chunks em vez de 5, e `needs_web_search` ficava
permanentemente ligado, disparando um rewrite pago de query a cada pergunta.

A licao nao e "religar a Cohere". E que um limiar absoluto nao pode ser
comparado contra uma escala que o codigo nao controla: a Cohere serve hoje
`rerank-v3.5` e a geracao `rerank-v4.0-*`, com distribuicoes de score
diferentes entre si. Qualquer numero fixo aqui volta a estar errado no dia em
que o modelo configurado muda.

Entao: o limiar absoluto so se aplica quando o score e sabidamente calibrado.
Sem isso, nao ha filtragem honesta a fazer — os candidatos que chegam aqui JA
sao o top-k do retriever — e o passo apenas reporta a qualidade do lote.
"""

import logging

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Abaixo disto o lote e pobre o suficiente para valer procurar fora dos
# documentos. Contagem, nao score: e a unica medida que independe de escala.
_MIN_DOCS_SAUDAVEL = 2


def grade_documents(
    documents: list[dict],
) -> tuple[list[dict], bool]:
    """
    Filtra os documentos recuperados pela relevancia.

    Returns:
        (filtered_docs, needs_web_search)

    `needs_web_search` e um sinal sobre a QUALIDADE do lote. Quem decide se vale
    pagar por um rewrite de query e uma busca externa e o chamador, que sabe se
    a busca web esta sequer configurada.
    """
    if not documents:
        return [], True

    settings = get_settings()

    # A escala vem etiquetada na origem: `hybrid_search` marca "rrf", o reranker
    # sobrescreve para "cohere" quando de fato reordenou. Ausente = nao
    # calibrado, que e o padrao seguro.
    escala = documents[0].get("score_scale", "rrf")

    if escala == "cohere":
        limiar = settings.relevance_threshold
        filtrados = [
            d for d in documents if d.get("relevance_score", 0) >= limiar
        ]
        if not filtrados:
            # Nada passou: devolve os melhores em vez de deixar o gerador sem
            # material nenhum, e sinaliza que o lote foi ruim.
            filtrados = sorted(
                documents,
                key=lambda d: d.get("relevance_score", 0),
                reverse=True,
            )[:_MIN_DOCS_SAUDAVEL]
            return filtrados, True
        return filtrados, len(filtrados) < len(documents) / 2

    # Escala nao calibrada (RRF). Estes documentos ja sao o top-k do retriever;
    # aplicar um corte por score aqui seria inventar precisao que o numero nao
    # tem. Passa adiante e julga o lote pelo tamanho.
    if escala != "rrf":
        logger.warning("grade_documents: escala de score desconhecida %r", escala)

    return list(documents), len(documents) < _MIN_DOCS_SAUDAVEL

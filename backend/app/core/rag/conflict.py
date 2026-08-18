"""Deteccao de divergencia entre as fontes recuperadas.

O PROBLEMA QUE ISTO RESOLVE: citar a fonte virou padrao de mercado e nao diz
nada sobre um acervo real. Numa pasta de empresa convivem o contrato de 2023 e
o aditivo de 2025, a politica antiga e a revisada, a tabela de preco do ano
passado e a deste ano. O RAG recupera os dois, o gerador escolhe um (em geral o
mais bem pontuado, que nao e necessariamente o mais recente) e responde com
confianca. Quem lê nao tem como saber que existia outra versao.

COMO A DECISAO E TOMADA, e onde o modelo entra:

- O PORTAO e deterministico: so vale checar quando sobraram trechos de DOIS ou
  mais documentos distintos. Um documento so nao diverge de si mesmo no escopo
  de uma resposta, e rodar a checagem ali seria pagar por nada.
- A CHECAGEM e semantica, entao e do modelo: nao existe heuristica honesta que
  perceba que "prazo de 30 dias" contradiz "prazo de 15 dias uteis".
- O RESULTADO e AVISO, nunca decisao. Nada e filtrado, nenhuma fonte e
  descartada e a resposta nao muda. A divergencia aparece ao lado dela para a
  pessoa decidir. Isso mantem o invariante do projeto: o modelo nao decide,
  redige.

CUSTO: usa o modelo barato, com teto de tokens, e passa pelo mesmo orcamento
diario do chat. Sem isso seria uma chamada paga fora do medidor, que e
exatamente o defeito que a auditoria de agosto encontrou em outro projeto.
"""

from __future__ import annotations

import json
import logging

from app.config.settings import get_settings
from app.core.llm_client import chat_complete

logger = logging.getLogger(__name__)

# Acima disso a checagem fica cara e o sinal nao melhora: divergencia relevante
# aparece entre os primeiros trechos, que sao os que o gerador de fato usou.
_MAX_TRECHOS = 6
_MAX_CHARS_POR_TRECHO = 700

_SYSTEM = """You compare excerpts retrieved from a company's own documents and report whether they DISAGREE with each other on a factual point: a deadline, a price, a rule, a limit, a date, a responsibility.

Disagreement means the same question would be answered differently depending on which excerpt you read. Different topics are NOT disagreement. Extra detail in one excerpt is NOT disagreement. A newer document restating an older one in other words is NOT disagreement.

Answer with a JSON object and nothing else:
{"conflict": true|false, "summary": "one sentence, in the language of the excerpts", "sources": ["<document_title>", "<document_title>"]}

If there is no disagreement, answer {"conflict": false, "summary": "", "sources": []}."""


def _documentos_distintos(trechos: list[dict]) -> int:
    return len({t.get("document_id") for t in trechos if t.get("document_id")})


def detectar_conflito(trechos: list[dict]) -> dict | None:
    """Devolve o aviso de divergencia, ou None quando nao ha o que avisar.

    Nunca levanta: a resposta do visitante nao pode cair porque a checagem de
    divergencia falhou. Em caso de erro, o resultado e "sem aviso".
    """
    settings = get_settings()
    if not getattr(settings, "enable_conflict_detection", True):
        return None

    # Portao deterministico: sem duas fontes, nao ha divergencia possivel.
    if _documentos_distintos(trechos) < 2:
        return None

    partes = []
    for t in trechos[:_MAX_TRECHOS]:
        titulo = t.get("document_title") or "documento"
        pagina = t.get("page")
        texto = (t.get("snippet") or "")[:_MAX_CHARS_POR_TRECHO]
        if not texto.strip():
            continue
        cabecalho = f"[{titulo}" + (f", p. {pagina}]" if pagina else "]")
        partes.append(f"{cabecalho}\n{texto}")

    if len(partes) < 2:
        return None

    try:
        bruto = chat_complete(
            model=settings.fast_model,
            max_tokens=220,
            system=_SYSTEM,
            messages=[{"role": "user", "content": "\n\n---\n\n".join(partes)}],
            temperature=0.0,
        )
        dados = json.loads(_so_json(bruto))
    except Exception:
        logger.warning("checagem de divergencia falhou", exc_info=True)
        return None

    if not isinstance(dados, dict) or not dados.get("conflict"):
        return None

    resumo = str(dados.get("summary") or "").strip()
    if not resumo:
        # Sem explicacao, o aviso viraria um alarme sem conteudo.
        return None

    fontes = [str(f) for f in (dados.get("sources") or []) if str(f).strip()][:4]
    return {"summary": resumo[:400], "sources": fontes}


def _so_json(texto: str) -> str:
    """Extrai o objeto JSON quando o modelo embrulha a resposta em texto ou cerca."""
    t = texto.strip()
    if t.startswith("```"):
        t = t.split("```")[1] if "```" in t[3:] else t.strip("`")
        t = t.removeprefix("json").strip()
    inicio, fim = t.find("{"), t.rfind("}")
    return t[inicio : fim + 1] if inicio != -1 and fim > inicio else t

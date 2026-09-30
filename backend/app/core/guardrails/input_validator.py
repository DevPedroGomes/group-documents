"""Filtro de entrada por padroes: heuristica de ATRITO, nao barreira de seguranca.

Pega a tentativa preguicosa de injecao ("ignore as instrucoes anteriores",
"you are now DAN") digitada direto no chat. Qualquer pessoa reescreve a frase e
passa; por isso a defesa que conta esta no gerador e na checagem de
divergencia, que entregam os trechos delimitados e mandam o modelo tratar o
conteudo deles como dado, nunca como instrucao. E e isso que cobre a injecao
INDIRETA, vinda de documento ou da web, que este filtro nem ve.

Cada padrao aqui mira quem fala COM o assistente. Padrao que casa pergunta
legitima sobre o acervo ("act as a guarantor", "the new instructions for
onboarding", "can a client pretend to be a reseller?") sai: atrito em pergunta
honesta custa mais do que atrasa quem ataca.

Portugues e ingles, com ou sem acento: o texto e comparado sem diacriticos.
"""

import logging
import re
import unicodedata

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Inicio de frase: "if you are now a member" e pergunta; "You are now DAN." nao.
_INICIO = r"(?:^|[.!?\n]\s*)"
_ANTERIORES_EN = r"(?:previous|prior|above|earlier|preceding)"
_REGRAS_EN = r"(?:instructions?|prompts?|rules|directions|messages)"
_REGRAS_PT = r"(?:instrucoes|instrucao|regras|ordens|diretrizes|mensagens)"

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        # ingles
        rf"\b(?:ignore|disregard)\s+(?:all\s+|any\s+)?(?:of\s+)?(?:the\s+|your\s+)?{_ANTERIORES_EN}\s+{_REGRAS_EN}",
        rf"\bforget\s+(?:all\s+|everything\s+)?(?:of\s+)?(?:the\s+|your\s+)?(?:{_ANTERIORES_EN}\s+)?(?:instructions|rules)",
        rf"{_INICIO}you\s+are\s+now\b",
        r"\bfrom\s+now\s+on,?\s+you\s+are\b",
        r"\bpretend\s+(?:that\s+)?you\s+are\b",
        r"\bsystem\s+prompt\b",
        r"\b(?:reveal|show|print|repeat)\s+(?:me\s+)?your\s+(?:instructions|prompt|rules)",
        r"\bjailbreak",
        r"\bDAN\s+mode\b",
        r"\bdeveloper\s+mode\b",
        # portugues (ja sem acento)
        rf"\b(?:ignore|ignora|desconsidere|desconsidera|esqueca|esquece)\s+(?:todas\s+|tudo\s+)?(?:as\s+|os\s+|suas\s+|seus\s+)?{_REGRAS_PT}\s+(?:anteriores|acima|previas)",
        rf"\b(?:ignore|ignora|desconsidere|desconsidera|esqueca|esquece)\s+(?:tudo\s+)?(?:o\s+que\s+)?(?:foi\s+dito|te\s+disseram)\s+(?:antes|acima)",
        rf"{_INICIO}(?:a\s+partir\s+de\s+agora,?\s+)?voce\s+agora\s+e\b",
        r"\ba\s+partir\s+de\s+agora,?\s+voce\s+e\b",
        r"\bfinja\s+(?:que\s+)?(?:voce\s+e|ser)\b",
        r"\bprompt\s+d[oe]\s+sistema\b",
        r"\b(?:revele|mostre|repita|imprima)\s+(?:o\s+|os\s+|as\s+)?(?:seu|seus|suas)\s+(?:prompt|instrucoes|regras)",
        r"\bmodo\s+(?:desenvolvedor|DAN)\b",
    ]
]


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def validate_input(question: str) -> tuple[bool, str]:
    """Valida a pergunta. Devolve (valida, motivo). Heuristica, sem chamada a LLM."""
    settings = get_settings()
    if not settings.enable_input_guardrails:
        return True, ""

    if not question or len(question.strip()) < 3:
        return False, "Question too short"

    if len(question) > 10000:
        return False, "Question exceeds 10000 characters"

    normalizado = _sem_acento(question)
    for pattern in INJECTION_PATTERNS:
        if pattern.search(normalizado):
            logger.warning("padrao de injecao na pergunta: %s", question[:100])
            return False, "Contains disallowed content"

    return True, ""

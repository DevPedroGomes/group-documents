"""Heuristic-based input validation for prompt injection detection."""

import re
import logging

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"ignore\s+(all\s+)?above",
        r"you\s+are\s+now",
        r"new\s+instructions",
        r"system\s+prompt",
        r"reveal\s+your\s+(instructions|prompt|system)",
        r"forget\s+(everything|all|previous)",
        r"act\s+as\s+(a|an)\s+",
        r"pretend\s+(to\s+be|you\s+are)",
        r"jailbreak",
        r"DAN\s+mode",
    ]
]


def validate_input(question: str) -> tuple[bool, str]:
    """
    Validate user input. Returns (is_valid, reason).
    Heuristic-based — no LLM calls.
    """
    settings = get_settings()
    if not settings.enable_input_guardrails:
        return True, ""

    if not question or len(question.strip()) < 3:
        return False, "Question too short"

    if len(question) > 10000:
        return False, "Question exceeds 10000 characters"

    for pattern in INJECTION_PATTERNS:
        if pattern.search(question):
            logger.warning(f"Injection pattern detected in query: {question[:100]}")
            return False, "Contains disallowed content"

    return True, ""

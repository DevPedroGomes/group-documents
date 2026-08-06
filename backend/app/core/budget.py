"""Teto diario de chamadas pagas, para uma demo aberta na internet.

Por que existe separado do rate limit: o rate limit do slowapi limita o que UM
chamador faz por minuto. Nenhum rate limit limita o que TODOS fazem juntos, nem
alguem criando contas novas — e neste app o cadastro e aberto e gratuito, entao
"exigir login" nao e teto de gasto nenhum. Ate esta auditoria o BrainHub era o
unico dos oito projetos do portfolio com provider pago e ZERO teto global.

Decisoes, todas iguais as dos outros apps do portfolio:

- A cota e consumida ANTES da chamada ao provider, nunca depois. Contar depois
  deixa uma rajada de requisicoes concorrentes passar toda junta, porque
  nenhuma delas foi contada ainda.
- O dia vira em UTC, para o reset nao andar com o fuso do servidor.
- INCR primeiro e checa depois: duas requisicoes concorrentes enxergam cada uma
  o proprio total pos-incremento, entao nenhuma escapa do teto. Quem perde
  desfaz o proprio incremento.
- Se o Redis nao responde, RECUSA. Um teto ilegivel nao e um teto ausente.
- O kill switch e variavel de ambiente, para estancar sem rebuild nem redeploy.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None

# 48h: sobrevive a virada de dia UTC sem acumular chave velha para sempre.
_TTL_SEGUNDOS = 172_800


def _hoje_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _kill_switch_ligado() -> bool:
    return os.getenv("DEMO_KILL_SWITCH", "") in ("1", "true", "True")


async def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_settings().redis_url, decode_responses=True
        )
    return _client


def segundos_ate_meia_noite_utc() -> int:
    agora = datetime.now(timezone.utc)
    inicio_do_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int(inicio_do_dia.timestamp() + 86400 - agora.timestamp()))


class TetoAtingido(Exception):
    """Recusa segura de mostrar ao visitante."""

    def __init__(self, mensagem: str):
        self.mensagem = mensagem
        super().__init__(mensagem)


async def consumir(tipo: str, limite: int, unidades: int = 1) -> int:
    """Gasta `unidades` da cota de hoje para `tipo`. Devolve o quanto sobrou.

    Levanta `TetoAtingido` sem ter consumido nada — uma chamada recusada nao
    custa cota ao proximo visitante.
    """
    if _kill_switch_ligado():
        logger.warning("budget.kill_switch_ativo tipo=%s", tipo)
        raise TetoAtingido("This demo is paused right now. Please try again later.")

    chave = f"budget:{_hoje_utc()}:{tipo}"
    try:
        r = await _redis()
        usado = await r.incrby(chave, unidades)
        if usado == unidades:
            await r.expire(chave, _TTL_SEGUNDOS)
    except TetoAtingido:
        raise
    except Exception as exc:
        logger.error("budget.estado_ilegivel tipo=%s erro=%s", tipo, exc)
        raise TetoAtingido(
            "This demo is temporarily unavailable."
        ) from exc

    if usado > limite:
        # Perdeu a corrida: devolve o proprio incremento e recusa.
        try:
            await r.incrby(chave, -unidades)
        except Exception:
            logger.exception("budget.rollback_falhou tipo=%s", tipo)
        logger.warning("budget.esgotado tipo=%s usado=%d limite=%d", tipo, usado, limite)
        raise TetoAtingido(
            "This demo reached today's usage cap. It resets at midnight UTC."
        )

    return limite - usado


async def devolver(tipo: str, unidades: int = 1) -> None:
    """Devolve cota quando a chamada paga nao chegou a acontecer.

    Melhor esforco: uma devolucao perdida custa um pouco de folga, um gasto nao
    registrado custa dinheiro.
    """
    try:
        r = await _redis()
        await r.incrby(f"budget:{_hoje_utc()}:{tipo}", -unidades)
    except Exception as exc:
        logger.error("budget.devolucao_falhou tipo=%s erro=%s", tipo, exc)


async def panorama() -> dict:
    """Uso de hoje. Serve ao health check e a um eventual selo na UI."""
    settings = get_settings()
    limites = {
        "chat": settings.daily_chat_limit,
        "ingest": settings.daily_ingest_limit,
    }
    try:
        r = await _redis()
        usados = {
            tipo: int(await r.get(f"budget:{_hoje_utc()}:{tipo}") or 0)
            for tipo in limites
        }
    except Exception:
        return {"date": _hoje_utc(), "degraded": True, "kill_switch": _kill_switch_ligado()}

    return {
        "date": _hoje_utc(),
        "kill_switch": _kill_switch_ligado(),
        "used": usados,
        "limits": limites,
        "remaining": {t: max(0, limites[t] - usados[t]) for t in limites},
    }

"""Engine SQLAlchemy de mentira: anota o SQL executado e nao fala com banco.

Existe para os testes do envelope do job afirmarem coisas sobre o que foi
GRAVADO — o documento virou `failed`? os chunks antigos foram apagados antes do
reprocessamento? — sem Postgres, sem Redis e sem rede. A suite roda no CI sem
nenhum servico de pe, e um teste que precisasse de banco nao rodaria la.

Nao e um duble generico de SQLAlchemy: implementa so o que a app usa (
`engine.begin()` como context manager, `conn.execute(...)`, `.scalar()`).
"""

from __future__ import annotations

import re


class _Resultado:
    def __init__(self, escalar):
        self._escalar = escalar

    def scalar(self):
        return self._escalar


class _Conexao:
    def __init__(self, motor: "MotorFalso", transacao: int):
        self._motor = motor
        self._transacao = transacao

    def execute(self, statement, params=None):
        # O numero da transacao vai junto: e o que permite afirmar que a limpeza
        # dos chunks acontece na MESMA transacao que marca `processing`, e nao
        # numa transacao propria que poderia commitar sozinha.
        self._motor.executados.append(
            (self._transacao, " ".join(str(statement).split()), dict(params or {}))
        )
        return _Resultado(self._motor.escalar)


class _Transacao:
    def __init__(self, motor: "MotorFalso", numero: int):
        self._motor = motor
        self._numero = numero

    def __enter__(self) -> _Conexao:
        return _Conexao(self._motor, self._numero)

    def __exit__(self, *_excecao) -> bool:
        # Nunca engole excecao: o codigo sob teste e que decide o que fazer com
        # ela, e um duble que engolisse mascararia exatamente o bug C1.
        return False


class MotorFalso:
    """`escalar` e o que todo `SELECT ... .scalar()` devolve (o mime, na pratica)."""

    def __init__(self, escalar: str = "application/pdf"):
        self.escalar = escalar
        self.executados: list[tuple[int, str, dict]] = []
        self._transacoes = 0

    def begin(self) -> _Transacao:
        self._transacoes += 1
        return _Transacao(self, self._transacoes)

    def sql(self) -> list[str]:
        return [s for _, s, _ in self.executados]

    def gravou(self, trecho: str) -> bool:
        return any(trecho in s for s in self.sql())


def status_do_documento(motor: MotorFalso) -> list[str]:
    """Os status gravados em `documents`, na ordem em que foram gravados.

    Le os dois formatos que a app usa: literal no SQL (`SET status = 'failed'`,
    em `jobs/ingestao.py`) e parametro ligado (`SET status = :status`, em
    `jobs/worker.py`).
    """
    saida: list[str] = []
    for _, sql, params in motor.executados:
        if "UPDATE documents SET status" not in sql:
            continue
        if "status" in params:
            saida.append(str(params["status"]))
            continue
        achado = re.search(r"status = '(\w+)'", sql)
        if achado:
            saida.append(achado.group(1))
    return saida

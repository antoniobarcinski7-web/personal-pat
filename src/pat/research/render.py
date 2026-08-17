"""O unico lugar do sistema onde um `Decimal` vira texto de apresentacao.

Ser um lugar so nao e organizacao: e o que torna a regra "o modelo nunca
emite digito" verificavel. Se a formatacao estivesse espalhada, nao haveria
como afirmar de onde cada numero da resposta saiu.

Formatacao nao muda valor. O `ComputationResult` continua com a precisao
cheia; o que sai daqui e uma *representacao*, sempre acompanhada do
`result_id` que a produziu.

Arredondamento e sempre `ROUND_HALF_EVEN` explicito - o modo default do
contexto decimal e estado de processo, e um numero de relatorio nao pode
depender disso.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from pat.contracts.research import (
    ComputationResult,
    DerivationOp,
    NumericClaim,
    ResultKind,
)
from pat.contracts.semantics import Dimension

_BILLION = Decimal(1_000_000_000)
_MILLION = Decimal(1_000_000)

_SYMBOLS = {"BRL": "R$", "USD": "US$", "EUR": "€"}


def value_token(step_id: str) -> str:
    return f"{{{{s:{step_id}}}}}"


def period_token(label: str) -> str:
    return f"{{{{p:{label}}}}}"


def _q(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_EVEN)


def format_money(value: Decimal, currency: str | None) -> str:
    symbol = _SYMBOLS.get(currency or "", currency or "")
    magnitude = abs(value)
    if magnitude >= _BILLION:
        return f"{symbol} {_q(value / _BILLION, '0.01')} bn".strip()
    if magnitude >= _MILLION:
        return f"{symbol} {_q(value / _MILLION, '0.1')} MM".strip()
    return f"{symbol} {_q(value, '0.01')}".strip()


def format_ratio(value: Decimal, *, places: str = "0.01", signed: bool = False) -> str:
    pct = _q(value * 100, places)
    if signed and pct > 0:
        return f"+{pct}%"
    return f"{pct}%"


def format_value(result: ComputationResult) -> str:
    """Representacao de um resultado, pela sua dimensao."""
    if result.dimension is Dimension.MONEY:
        return format_money(result.value, result.currency)
    if result.dimension is Dimension.RATIO:
        derivada = result.derived
        if derivada is not None and derivada.op in (DerivationOp.DELTA_PCT, DerivationOp.CAGR):
            return format_ratio(result.value, places="0.1", signed=True)
        if derivada is not None and derivada.op is DerivationOp.DELTA:
            return format_ratio(result.value, places="0.01", signed=True)
        return format_ratio(result.value)
    return f"{_q(result.value, '1')}"


def describe(result: ComputationResult) -> str:
    """O que o numero e, em prosa, SEM o valor.

    E isto que o escritor recebe no Milestone 2 - rotulo e token, nunca o
    numero formatado. Nao da para copiar um valor que nunca foi mostrado.
    """
    if result.kind is ResultKind.METRIC:
        metric = result.metric_result
        escopo = "consolidado" if metric.scope == "consolidated" else "individual"
        return (
            f"{metric.metric}@{metric.metric_version}, {escopo}, exercicio findo em "
            f"{metric.period_end}, conhecido em {metric.knowledge_date}, AS OF {metric.as_of}"
        )
    derivada = result.derived
    periodos = " -> ".join(derivada.period_labels) if derivada.period_labels else "sem periodo"
    return f"{derivada.op} sobre {periodos}"


def render_results(results: tuple[ComputationResult, ...]) -> tuple[NumericClaim, ...]:
    """Um `NumericClaim` por resultado, com token, valor e o que ele significa."""
    return tuple(
        NumericClaim(
            token=value_token(result.step_id),
            result_id=result.result_id,
            rendered_value=format_value(result),
            unit=result.currency if result.dimension is Dimension.MONEY else None,
            means=describe(result),
        )
        for result in results
    )


def period_tokens(results: tuple[ComputationResult, ...]) -> dict[str, str]:
    """Rotulos de periodo tambem sao token.

    Sem isso, "FY2024" na prosa obrigaria uma lista de excecoes na regra do
    digito - e lista de excecao envelhece mal. Com todo texto com digito
    tokenizado, a regra vira um regex sem excecao nenhuma.
    """
    out: dict[str, str] = {}
    for result in results:
        if result.metric_result is None:
            continue
        end = result.metric_result.period_end
        out[period_token(f"fy{end.year}")] = f"FY{end.year}"
        out[period_token(f"end_{end.year}")] = end.isoformat()
    return out

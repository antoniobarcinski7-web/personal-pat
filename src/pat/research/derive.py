"""As sete operacoes fechadas sobre resultados ja calculados.

Este modulo e o que substitui a geracao de codigo. Enquanto o espaco de
computacao couber nestas sete, um plano pode ser conferido inteiro antes de
rodar - garantia que nenhum sandbox oferece para codigo arbitrario.

Regras que valem antes de qualquer operacao:

- insumo que falhou nao vira zero nem some: a derivacao inteira falha com
  `INPUT_FAILED` carregando a falha de origem;
- moedas diferentes param a conta, como no motor da Fase 2. Nao ha conversao
  cambial implicita em lugar nenhum do sistema;
- fidelidade da saida e a mais fraca da entrada, pelo `weakest()` que ja
  existe;
- `knowledge_date` da saida e o maximo: uma conta so e conhecida quando o
  ultimo ingrediente ficou publico;
- toda divisao roda em contexto de precisao fixa. O default do modulo
  `decimal` e global e mutavel, e um resultado que depende de estado de
  processo nao e reproduzivel (I3).

E a pos-condicao que nao se negocia: nenhuma operacao devolve NaN ou
infinito. Valor nao finito escapando daqui e a classe de corrupcao silenciosa
que o projeto inteiro existe para evitar.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, localcontext

from pat.contracts.research import (
    ComputationFailure,
    ComputationResult,
    DerivationFailureReason,
    DerivationOp,
    DerivationParams,
    DerivedValue,
)
from pat.contracts.semantics import Dimension, weakest

PRECISION = 28
"""Igual a `margem_ebitda@v1`, e pela mesma razao."""

_RATIO_OPS = frozenset({DerivationOp.DELTA_PCT, DerivationOp.RATIO, DerivationOp.CAGR})
_BINARY = frozenset({DerivationOp.DELTA, DerivationOp.DELTA_PCT, DerivationOp.RATIO, DerivationOp.CAGR})


def _fail(
    step_id: str, reason: DerivationFailureReason, message: str, *, remedy: str | None = None
) -> ComputationFailure:
    return ComputationFailure(step_id=step_id, reason=reason, message=message, remedy=remedy)


def derive(
    step_id: str,
    op: DerivationOp,
    inputs: Sequence[ComputationResult | ComputationFailure],
    params: DerivationParams,
) -> DerivedValue | ComputationFailure:
    """Executa uma operacao. Nunca levanta por dado; recusa com motivo nomeado."""

    # -- insumo que falhou nao produz resultado parcial ---------------------
    for item in inputs:
        if isinstance(item, ComputationFailure):
            return ComputationFailure(
                step_id=step_id,
                reason=DerivationFailureReason.INPUT_FAILED,
                message=f"depende de {item.step_id}, que falhou: {item.message}",
                remedy=item.remedy,
                unavailable=item.unavailable,
            )

    values: list[ComputationResult] = list(inputs)

    arity = len(values)
    if op in _BINARY and arity != 2:
        return _fail(step_id, DerivationFailureReason.ARITY, f"{op} exige 2 insumos, recebeu {arity}")
    if op not in _BINARY and arity < 2:
        return _fail(
            step_id, DerivationFailureReason.ARITY, f"{op} exige ao menos 2 insumos, recebeu {arity}"
        )

    dimensions = {item.dimension for item in values}
    if len(dimensions) > 1:
        return _fail(
            step_id,
            DerivationFailureReason.DIMENSION_MISMATCH,
            f"{op} sobre grandezas de dimensoes diferentes: {sorted(d.value for d in dimensions)}",
        )

    currencies = {item.currency for item in values if item.currency}
    if len(currencies) > 1:
        return _fail(
            step_id,
            DerivationFailureReason.CURRENCY_MISMATCH,
            f"insumos em moedas diferentes: {sorted(currencies)}. Conversao cambial "
            "nao acontece aqui, e nao deve acontecer implicitamente em lugar nenhum.",
        )

    input_dimension = dimensions.pop()
    computed = _apply(step_id, op, values, params)
    if isinstance(computed, ComputationFailure):
        return computed

    if op in _RATIO_OPS:
        out_dimension, out_currency = Dimension.RATIO, None
    else:
        out_dimension = input_dimension
        out_currency = currencies.pop() if currencies else None

    return DerivedValue(
        op=op,
        value=computed,
        dimension=out_dimension,
        currency=out_currency,
        derived_from=tuple(item.result_id for item in values),
        fidelity=weakest(tuple(item.fidelity for item in values)),
        knowledge_date=max(item.knowledge_date for item in values),
        period_labels=tuple(_period_label(item) for item in values),
    )


def _period_label(result: ComputationResult) -> str:
    if result.metric_result is not None:
        return result.metric_result.period_end.isoformat()
    return ",".join(result.derived.period_labels) if result.derived.period_labels else ""


def _apply(
    step_id: str,
    op: DerivationOp,
    values: list[ComputationResult],
    params: DerivationParams,
) -> Decimal | ComputationFailure:
    numbers = [item.value for item in values]

    with localcontext() as ctx:
        ctx.prec = PRECISION

        if op is DerivationOp.DELTA:
            # Ordem de declaracao, nao ordem de data: um plano que subtrai ao
            # contrario devolve delta negativo, e nao um reordenamento calado.
            result = numbers[1] - numbers[0]

        elif op is DerivationOp.DELTA_PCT:
            base = numbers[0]
            if base == 0:
                return _fail(
                    step_id,
                    DerivationFailureReason.ZERO_DENOMINATOR,
                    "variacao percentual sobre base zero nao existe (e nao e zero)",
                )
            result = (numbers[1] - base) / abs(base)

        elif op is DerivationOp.RATIO:
            if numbers[1] == 0:
                return _fail(
                    step_id,
                    DerivationFailureReason.ZERO_DENOMINATOR,
                    "divisao por zero: a razao nao existe (e nao e zero)",
                )
            result = numbers[0] / numbers[1]

        elif op is DerivationOp.CAGR:
            base, final = numbers[0], numbers[1]
            years = params.years
            if years is None or years <= 0:
                return _fail(
                    step_id,
                    DerivationFailureReason.ARITY,
                    f"CAGR precisa de `years` positivo, recebeu {years}",
                )
            if base <= 0 or final <= 0:
                return _fail(
                    step_id,
                    DerivationFailureReason.NON_POSITIVE_CAGR_BASE,
                    f"CAGR entre {base} e {final}: taxa composta sobre valor nao "
                    "positivo nao e um numero pequeno, e um erro de categoria",
                )
            # Decimal nao tem `**` fracionario; ln/exp no mesmo contexto de
            # precisao mantem o resultado deterministico.
            result = ((final / base).ln() / Decimal(years)).exp() - Decimal(1)

        elif op is DerivationOp.MIN:
            result = min(numbers)
        elif op is DerivationOp.MAX:
            result = max(numbers)
        elif op is DerivationOp.MEAN:
            result = sum(numbers) / Decimal(len(numbers))
        else:  # pragma: no cover - enum fechado
            raise AssertionError(f"operacao sem implementacao: {op}")

    if not result.is_finite():  # pragma: no cover - defesa
        raise AssertionError(
            f"{op} produziu valor nao finito ({result}); isso e erro de programacao, "
            "nao dado faltante"
        )
    return result

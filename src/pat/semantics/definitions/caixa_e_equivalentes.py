"""caixa_e_equivalentes@v1 - leitura direta do conceito `cash_and_equivalents`.

Saldo pontual, nao fluxo: o resolver espera `PeriodType.INSTANT`. E a primeira
metrica de balanco do sistema, e ela existe sobretudo para ser insumo de
`divida_liquida@v1` - sozinha, ela e um numero que o leitor ja tinha.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import (
    ConsistencyCheck,
    Dimension,
    MetricDefinition,
    MetricKind,
    PeriodKind,
)
from pat.semantics import concepts
from pat.semantics.registry import CheckEval, Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="caixa_e_equivalentes",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.STOCK,
    requires_concepts=(concepts.CASH_AND_EQUIVALENTS,),
    definition="Caixa e equivalentes de caixa na data-base, como reportado.",
    rationale=(
        "Nao soma aplicacoes financeiras. Somar produziria uma 'posicao de "
        "liquidez' que a companhia nao publicou sob esse nome, e a diferenca "
        "entre as duas leituras muda a divida liquida - que e onde o numero "
        "acaba sendo usado."
    ),
    checks=(
        ConsistencyCheck(
            check_id="nao_negativa",
            description=(
                "Saldo de caixa negativo nao existe: conta garantida a descoberto "
                "e passivo, e um valor negativo aqui indica sinal invertido no "
                "mapeamento."
            ),
            tolerance_abs=Decimal(0),
        ),
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.CASH_AND_EQUIVALENTS)


def _check_nao_negativa(inputs: Inputs, value: Decimal, spec: ConsistencyCheck) -> CheckEval:
    return CheckEval(passed=value >= 0, observed=value)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(
        Metric(
            definition=DEFINITION,
            compute=_compute,
            check_fns={"nao_negativa": _check_nao_negativa},
        )
    )

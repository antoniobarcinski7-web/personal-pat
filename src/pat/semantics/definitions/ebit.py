"""ebit@v1 - resultado operacional COMO REPORTADO.

Decisao A1, tomada explicitamente: inclui equivalencia patrimonial.

Nao e detalhe. No GPA, FY2023 consolidado: EBIT reportado de R$ 677 MM contem
R$ 768 MM de equivalencia patrimonial. Sem ela, o resultado operacional seria
NEGATIVO em R$ 91 MM. As duas leituras sao defensaveis; o que nao e defensavel
e subtrair 3.04.06 caladinho e continuar chamando de `ebit@v1`. Quem quiser a
outra, `ebit_ex_equivalencia@v1` e uma metrica nova, e `ebitda@v2` em cima dela.
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
    name="ebit",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_concepts=(concepts.EBIT_REPORTED,),
    optional_concepts=(
        concepts.GROSS_PROFIT,
        concepts.OPERATING_EXPENSES_NET,
        concepts.EQUITY_METHOD_RESULT,
    ),
    definition=(
        "Resultado operacional antes do resultado financeiro e dos tributos, como "
        "reportado - incluindo equivalencia patrimonial."
    ),
    rationale=(
        "Decisao A1. 'Como reportado' e a unica leitura que nao exige julgamento por "
        "companhia, e portanto a unica que pode ser default. Excluir equivalencia e "
        "uma escolha analitica legitima, mas e OUTRA metrica: no GPA FY2023 ela vira "
        "o sinal do resultado operacional."
    ),
    checks=(
        ConsistencyCheck(
            check_id="fecha_com_a_dre",
            description=(
                "EBIT reportado deve bater com lucro bruto menos despesas operacionais "
                "liquidas. Falhar aqui significa que os tres conceitos nao vieram da "
                "mesma cascata."
            ),
            tolerance_abs=Decimal(1000),
        ),
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.EBIT_REPORTED)


def _check_fecha_com_a_dre(
    inputs: Inputs, value: Decimal, spec: ConsistencyCheck
) -> CheckEval | None:
    bruto = inputs.optional(concepts.GROSS_PROFIT)
    despesas = inputs.optional(concepts.OPERATING_EXPENSES_NET)
    if bruto is None or despesas is None:
        return None
    expected = bruto.value - despesas.value
    return CheckEval(
        passed=abs(value - expected) <= spec.tolerance_abs, observed=value, expected=expected
    )


def register(registry: MetricRegistry) -> Metric:
    return registry.register(
        Metric(
            definition=DEFINITION,
            compute=_compute,
            check_fns={"fecha_com_a_dre": _check_fecha_com_a_dre},
        )
    )

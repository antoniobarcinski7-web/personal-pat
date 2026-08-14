"""receita_liquida@v1 - leitura direta do conceito `revenue_net`.

Nao ha uma unica mencao a plano de contas neste arquivo, e isso e o teste
mais simples de que a separacao conceito/endereco pegou: para valer nos EUA,
esta metrica nao muda nada.
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
    name="receita_liquida",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_concepts=(concepts.REVENUE_NET,),
    definition="Receita liquida de vendas do periodo, como reportada.",
    rationale=(
        "Leitura direta, sem aritmetica: a receita liquida e uma linha publicada, "
        "nao um calculo. Se um dia precisar ser reconstruida a partir de receita "
        "bruta menos deducoes, isso e outro regime - e resolve-se no mapeamento, "
        "com fidelity, sem tocar nesta definicao."
    ),
    checks=(
        ConsistencyCheck(
            check_id="nao_negativa",
            description=(
                "Receita liquida negativa e possivel em teoria (devolucoes acima das "
                "vendas) e quase sempre e, na pratica, sinal de sinal invertido no "
                "mapeamento."
            ),
            tolerance_abs=Decimal(0),
        ),
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.REVENUE_NET)


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

"""patrimonio_liquido@v1 - leitura direta do conceito `equity_controlling`.

Atribuivel a controladora, pela decisao A3 - a mesma que separa
`lucro_liquido@v1` do resultado total. O par importa mais aqui do que na
DRE: quem calcula retorno sobre o capital DO ACIONISTA precisa que
numerador e denominador falem do mesmo acionista, e um resultado
atribuivel sobre um patrimonio total produz um ROE que nao e de ninguem.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics import concepts
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="patrimonio_liquido",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.STOCK,
    requires_concepts=(concepts.EQUITY_CONTROLLING,),
    definition="Patrimonio liquido atribuivel aos socios da controladora na data-base.",
    rationale=(
        "Leitura direta. Onde o regime publica so o total, o mapeamento soma as linhas publicadas - total menos nao controladores - em vez de esta definicao estimar a diferenca."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.EQUITY_CONTROLLING)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

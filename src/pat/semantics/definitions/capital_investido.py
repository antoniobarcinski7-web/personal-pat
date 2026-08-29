"""capital_investido@v1 = patrimonio_liquido@v1 + divida_liquida@v1.

O capital que a operacao emprega, pela otica do FINANCIAMENTO: o que os
acionistas puseram mais o que os credores puseram, liquido do caixa que sobrou
sem ser empregado.

A otica alternativa - somar ativo operacional e subtrair passivo operacional -
daria um numero proximo e exigiria decidir, conta a conta, o que e operacional.
Cada decisao dessas e um julgamento por companhia, e este projeto nao os toma
em silencio. A otica do financiamento usa duas grandezas que ja existem, cada
uma com procedencia propria.

Divida liquida NEGATIVA - caixa acima da divida - reduz o capital investido, e
isso e correto: caixa parado nao e capital empregado na operacao. Em companhia
muito capitalizada o resultado pode ficar proximo de zero ou negativo, e ai o
retorno sobre ele deixa de ter leitura - `roic@v1` recusa, e nao inventa.

Arrendamento fica de FORA, porque `divida_liquida@v1` o exclui (decisao A4).
Um capital investido que o incluisse seria outra metrica, e o nome diria.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics.definitions import divida_liquida, patrimonio_liquido
from pat.semantics.registry import Inputs, Metric, MetricRegistry

PATRIMONIO = patrimonio_liquido.DEFINITION.ref
DIVIDA_LIQUIDA = divida_liquida.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="capital_investido",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.STOCK,
    requires_metrics=(PATRIMONIO, DIVIDA_LIQUIDA),
    definition=(
        "Patrimonio liquido atribuivel a controladora mais divida liquida na "
        "data-base."
    ),
    rationale=(
        "Otica do financiamento, e nao do ativo: a segunda exigiria decidir "
        "conta a conta o que e operacional, e cada decisao dessas e um "
        "julgamento por companhia. Esta soma duas grandezas que ja existem, "
        "cada uma com procedencia propria."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.metric(PATRIMONIO).value + inputs.metric(DIVIDA_LIQUIDA).value


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

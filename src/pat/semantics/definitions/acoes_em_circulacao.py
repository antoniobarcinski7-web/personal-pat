"""acoes_em_circulacao@v1 - leitura direta do conceito `shares_outstanding`.

EM CIRCULACAO na data, e nao a media ponderada do periodo. A media e o
denominador do lucro por acao e suaviza recompra e emissao ao longo do
exercicio; usa-la para valor de mercado daria o valor de uma companhia que nao
existe em data nenhuma.

Primeira metrica do catalogo cuja dimensao nao e dinheiro nem razao. `COUNT`
existia no contrato desde a Fase 2 sem nenhum uso - era o contrato prevendo que
nem toda grandeza economica e monetaria.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics import concepts
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="acoes_em_circulacao",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.COUNT,
    period_kind=PeriodKind.STOCK,
    requires_concepts=(concepts.SHARES_OUTSTANDING,),
    definition="Quantidade de acoes em circulacao na data-base, como reportada.",
    rationale=(
        "Leitura direta do que a companhia declara. Reconstruir a partir do "
        "capital social e do valor nominal daria um numero proximo e diferente, "
        "e a diferenca seria atribuida ao sistema em vez de a companhia."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.SHARES_OUTSTANDING)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

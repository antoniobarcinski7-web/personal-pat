"""dividendos_pagos@v1 - leitura direta do conceito `dividends_paid`.

Caixa que saiu, e nao dividendo declarado: e o que se compara com o FCF
que o financiou. As duas grandezas circulam com o mesmo nome e pertencem
a exercicios diferentes.

Companhia que nao paga dividendo nao tem esta linha, e a metrica recusa
nomeando o conceito. A ausencia e FATO sobre a companhia, e a recusa e o
unico jeito de nao apresenta-la como zero calculado.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics import concepts
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="dividendos_pagos",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_concepts=(concepts.DIVIDENDS_PAID,),
    definition="Caixa desembolsado com dividendos e juros sobre capital proprio no periodo.",
    rationale=(
        "Leitura direta do desembolso publicado. Reconstruir a partir do dividendo declarado e da variacao do passivo daria um numero proximo e diferente, e a diferenca seria atribuida ao sistema em vez de a companhia."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.DIVIDENDS_PAID)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

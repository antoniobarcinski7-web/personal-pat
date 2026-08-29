"""recompras@v1 - leitura direta do conceito `share_repurchases`.

Fluxo de CAIXA da recompra, e nao a variacao do saldo de acoes em
tesouraria: a segunda muda com cancelamento e com reemissao, e produziria
uma 'recompra' que nao e caixa nenhum - o mesmo erro de ler capex pela
variacao do imobilizado.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics import concepts
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="recompras",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_concepts=(concepts.SHARE_REPURCHASES,),
    definition="Caixa desembolsado na recompra de acoes proprias no periodo.",
    rationale=(
        "Leitura direta do desembolso publicado nas atividades de financiamento, que e o que se compara com o caixa gerado no mesmo periodo."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.SHARE_REPURCHASES)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

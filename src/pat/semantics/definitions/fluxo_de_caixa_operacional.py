"""fluxo_de_caixa_operacional@v1 - leitura direta de `cash_flow_operating`.

Sem check de nao-negatividade, e a ausencia e deliberada: caixa operacional
negativo e um fato economico corriqueiro e importante - companhia em
crescimento acelerado, ou em dificuldade -, e marcar como suspeito o que o
sistema deveria justamente conseguir mostrar treinaria o leitor a ignorar o
aviso.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics import concepts
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="fluxo_de_caixa_operacional",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_concepts=(concepts.CASH_FLOW_OPERATING,),
    definition=(
        "Caixa liquido gerado pelas atividades operacionais no periodo, como "
        "reportado na demonstracao dos fluxos de caixa."
    ),
    rationale=(
        "Leitura direta. Reconstruir o caixa operacional a partir do lucro e das "
        "variacoes de capital de giro daria um numero proximo e diferente, e a "
        "diferenca seria atribuida ao sistema em vez de a companhia."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.CASH_FLOW_OPERATING)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

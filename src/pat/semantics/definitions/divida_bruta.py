"""divida_bruta@v1 = debt_current + debt_noncurrent. SEM arrendamento.

A decisao A4 dita o desenho: arrendamento entra explicitamente ou nao entra, e
nunca por normalizacao silenciosa. Aqui ele NAO entra, e o nome nao promete que
entre. Quem quer o outro tratamento usa
`divida_bruta_com_arrendamento@v1` - metrica separada, que diz no nome o que
faz.

Duas metricas em vez de um parametro
------------------------------------
Um `incluir_arrendamento=True` produziria dois numeros diferentes sob o mesmo
`ref`, e nenhuma analise arquivada diria qual dos dois foi usado. Metricas
separadas fazem a escolha aparecer na citacao.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics import concepts
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="divida_bruta",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.STOCK,
    requires_concepts=(concepts.DEBT_CURRENT, concepts.DEBT_NONCURRENT),
    definition=(
        "Divida onerosa total na data-base: circulante mais nao circulante, "
        "EXCLUINDO passivo de arrendamento."
    ),
    rationale=(
        "Decisao A4. Incluir arrendamento muda a faixa de alavancagem de "
        "companhias de varejo e de midia inteira, e as duas leituras circulam no "
        "mercado com o mesmo nome. Separar em duas metricas e o que impede uma "
        "analise antiga de ficar ambigua sobre qual delas usou."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.DEBT_CURRENT) + inputs.value(concepts.DEBT_NONCURRENT)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

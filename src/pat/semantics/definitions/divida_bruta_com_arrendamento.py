"""divida_bruta_com_arrendamento@v1 = divida_bruta@v1 + passivo de arrendamento.

A outra metade da decisao A4. O nome carrega o tratamento porque e ele que
muda o numero, e uma citacao que diga so "divida bruta" nao permitiria ao
leitor saber qual das duas leituras produziu a alavancagem que ele esta vendo.

Depende de `divida_bruta@v1` pinada, e nao dos conceitos de divida direto: as
duas metricas tem que concordar por construcao, e recalcular a soma aqui
abriria a porta para elas divergirem numa correcao futura.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics import concepts
from pat.semantics.definitions import divida_bruta
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DIVIDA_BRUTA = divida_bruta.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="divida_bruta_com_arrendamento",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.STOCK,
    requires_metrics=(DIVIDA_BRUTA,),
    requires_concepts=(
        concepts.LEASE_LIABILITY_CURRENT,
        concepts.LEASE_LIABILITY_NONCURRENT,
    ),
    definition=(
        "Divida onerosa total na data-base somada ao passivo de arrendamento, "
        "circulante e nao circulante."
    ),
    rationale=(
        "Existe para que a leitura que inclui arrendamento seja possivel SEM que "
        "ela se disfarce de `divida_bruta@v1`. Onde o regime nao publica o "
        "arrendamento em linha propria, esta metrica recusa - o que e melhor que "
        "devolver a divida sem arrendamento sob um nome que promete o contrario."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return (
        inputs.metric(DIVIDA_BRUTA).value
        + inputs.value(concepts.LEASE_LIABILITY_CURRENT)
        + inputs.value(concepts.LEASE_LIABILITY_NONCURRENT)
    )


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

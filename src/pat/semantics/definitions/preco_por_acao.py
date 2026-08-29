"""preco_por_acao@v1 - leitura direta do conceito `share_price`.

Fechamento AJUSTADO por desdobramento e proventos. O bruto e uma armadilha numa
serie historica: a Netflix desdobrou 10 para 1 em novembro de 2025, e uma serie
de fechamento bruto mostra queda de 90% num dia em que ninguem perdeu nada.

E a metrica de procedencia mais fraca do catalogo - `PUBLIC_WEB`, e nao
`PRIMARY_OFFICIAL` -, porque nenhum regulador publica preco. A marca viaja no
`Retrieval` e chega ao resultado, e e o que permite auditar depois quanto de
uma conclusao dependeu de fonte fraca.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics import concepts
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="preco_por_acao",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.STOCK,
    requires_concepts=(concepts.SHARE_PRICE,),
    definition="Preco de fechamento ajustado da acao na data-base.",
    rationale=(
        "Ajustado, porque a serie precisa ser comparavel consigo mesma ao longo "
        "do tempo. O custo e que ele muda retroativamente, e por isso o "
        "`knowledge_date` do fato e a data da busca e nao a do pregao."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.SHARE_PRICE)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

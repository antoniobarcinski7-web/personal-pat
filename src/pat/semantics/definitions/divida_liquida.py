"""divida_liquida@v1 = divida_bruta@v1 - caixa_e_equivalentes@v1.

Abate SO caixa e equivalentes. Nao abate aplicacoes financeiras, e essa e a
escolha que precisa estar escrita: as duas leituras circulam no mercado com o
mesmo nome, e a diferenca e material - na Netflix de FY2024, aplicacoes de
1.779 MM contra 21 MM em FY2023 mudariam a serie inteira de sentido.

Quem quiser a leitura que abate aplicacoes escreve `divida_liquida_de_
aplicacoes@v1`, com `short_term_investments` entre os insumos. Nao existe
parametro para escolher: parametro produziria dois numeros sob o mesmo `ref`.

Divida liquida NEGATIVA e resultado valido - caixa acima da divida - e
atravessa o sistema sem tratamento especial.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics.definitions import caixa_e_equivalentes, divida_bruta
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DIVIDA_BRUTA = divida_bruta.DEFINITION.ref
CAIXA = caixa_e_equivalentes.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="divida_liquida",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.STOCK,
    requires_metrics=(DIVIDA_BRUTA, CAIXA),
    definition=(
        "Divida bruta (sem arrendamento) menos caixa e equivalentes na data-base."
    ),
    rationale=(
        "A definicao mais estreita e a unica que nao exige julgamento sobre o que "
        "conta como liquidez. Aplicacoes financeiras ficam de fora porque a "
        "conversibilidade delas depende de prazo e de mercado, e um caixa "
        "'ajustado' que ninguem publicou entraria na alavancagem sem aparecer."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.metric(DIVIDA_BRUTA).value - inputs.metric(CAIXA).value


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

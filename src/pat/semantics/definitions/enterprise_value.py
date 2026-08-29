"""enterprise_value@v1 = valor_de_mercado@v1 + divida_liquida@v1.

O valor da OPERACAO, independente de como ela foi financiada. E o numerador que
se compara com EBITDA - que tambem e antes de juros -, e comparar valor de
mercado com EBITDA misturaria uma grandeza do acionista com uma da firma.

Divida liquida NEGATIVA reduz o EV, e isso e correto: quem compra a companhia
recebe o caixa junto. Em companhia muito capitalizada o EV pode ficar abaixo do
valor de mercado e ate negativo - e ai a razao contra EBITDA perde leitura, o
que `ev_ebitda@v1` trata recusando.

Arrendamento fica de FORA, porque `divida_liquida@v1` o exclui (decisao A4). Um
EV que o incluisse seria outra metrica, e o nome diria.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics.definitions import divida_liquida, valor_de_mercado
from pat.semantics.registry import Inputs, Metric, MetricRegistry

VALOR_DE_MERCADO = valor_de_mercado.DEFINITION.ref
DIVIDA_LIQUIDA = divida_liquida.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="enterprise_value",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.STOCK,
    requires_metrics=(VALOR_DE_MERCADO, DIVIDA_LIQUIDA),
    definition=(
        "Valor de mercado do capital proprio somado a divida liquida na data-base."
    ),
    rationale=(
        "O valor da operacao, independente de financiamento - e por isso o que se "
        "compara com EBITDA, que tambem e antes de juros. Comparar valor de "
        "mercado com EBITDA misturaria uma grandeza do acionista com uma da firma."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.metric(VALOR_DE_MERCADO).value + inputs.metric(DIVIDA_LIQUIDA).value


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

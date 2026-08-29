"""preco_lucro@v1 = valor_de_mercado@v1 / lucro_liquido@v1.

O multiplo mais conhecido e o mais facil de ler errado. Ele existe aqui pela
mesma razao que `ebitda@v1` existe: e o que um analista pede, e a alternativa a
te-lo com as ressalvas escritas e alguem calcula-lo por fora, sem elas.

Calculado sobre os TOTAIS, e nao por acao. Preco por acao dividido por lucro
por acao da o mesmo numero quando as duas contagens de acoes coincidem, e um
numero diferente quando nao coincidem - e elas nao coincidem, porque o lucro
por acao usa media ponderada e o valor de mercado usa acoes em circulacao. Uma
divisao de totais nao tem essa armadilha.

Recusa quando o lucro nao e positivo, pela mesma razao do EV/EBITDA: um P/L
negativo nao quer dizer barato.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import (
    Dimension,
    MetricDefinition,
    MetricKind,
    PeriodKind,
    UnavailableReason,
)
from pat.semantics.definitions import lucro_liquido, valor_de_mercado
from pat.semantics.registry import (
    ComputationUnavailable,
    Inputs,
    Metric,
    MetricRegistry,
)

VALOR_DE_MERCADO = valor_de_mercado.DEFINITION.ref
LUCRO = lucro_liquido.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="preco_lucro",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.RATIO,
    period_kind=PeriodKind.FLOW,
    mixes_period_kinds=True,
    requires_metrics=(VALOR_DE_MERCADO, LUCRO),
    definition=(
        "Valor de mercado na data-base dividido pelo resultado atribuivel a "
        "controladora no exercicio findo nela."
    ),
    rationale=(
        "Sobre os totais, e nao por acao: o lucro por acao usa media ponderada e o "
        "valor de mercado usa acoes em circulacao, e as duas contagens nao "
        "coincidem. Dividir totais nao tem essa armadilha."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    resultado = inputs.metric(LUCRO).value
    if resultado <= 0:
        raise ComputationUnavailable(
            UnavailableReason.DIVISION_BY_ZERO,
            f"lucro liquido {resultado} nao e positivo: o multiplo nao tem "
            "leitura. Um P/L negativo nao quer dizer barato.",
        )
    return inputs.metric(VALOR_DE_MERCADO).value / resultado


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

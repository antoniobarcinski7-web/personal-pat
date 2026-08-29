"""ev_ebitda@v1 = enterprise_value@v1 / ebitda@v1.

O multiplo que responde "esta caro?" sem depender de estrutura de capital nem
de aliquota. Numerador e denominador falam da FIRMA: o EV e o valor da
operacao, o EBITDA e o resultado dela antes de juros.

Mistura declarada de fluxo com saldo
------------------------------------
EV e saldo na data-base; EBITDA e fluxo do exercicio. Somar os dois seria uma
grandeza que nao existe; dividir e o que um multiplo E. A excecao e declarada
por metrica, como em `roe@v1`, e o resultado herda o periodo do fluxo.

Recusa quando o EBITDA nao e positivo. Um EV positivo sobre EBITDA negativo sai
NEGATIVO, e "EV/EBITDA de -8x" nao quer dizer barato: quer dizer que a companhia
nao gerou resultado operacional, e ai o multiplo nao e o instrumento.
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
from pat.semantics.definitions import ebitda, enterprise_value
from pat.semantics.registry import (
    ComputationUnavailable,
    Inputs,
    Metric,
    MetricRegistry,
)

EV = enterprise_value.DEFINITION.ref
EBITDA = ebitda.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="ev_ebitda",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.RATIO,
    period_kind=PeriodKind.FLOW,
    mixes_period_kinds=True,
    requires_metrics=(EV, EBITDA),
    definition=(
        "Enterprise value na data-base dividido pelo EBITDA do exercicio findo nela."
    ),
    rationale=(
        "Numerador e denominador falam da FIRMA, e por isso o multiplo nao depende "
        "de estrutura de capital nem de aliquota. Um multiplo sobre lucro liquido "
        "dependeria das duas, e duas companhias identicas na operacao sairiam com "
        "multiplos diferentes."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    resultado = inputs.metric(EBITDA).value
    if resultado <= 0:
        raise ComputationUnavailable(
            UnavailableReason.DIVISION_BY_ZERO,
            f"EBITDA {resultado} nao e positivo: o multiplo nao tem leitura. Um EV "
            "positivo sobre EBITDA negativo sai NEGATIVO, e isso nao quer dizer "
            "barato - quer dizer que nao houve resultado operacional.",
        )
    return inputs.metric(EV).value / resultado


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

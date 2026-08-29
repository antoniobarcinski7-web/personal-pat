"""roic@v1 = ebit@v1 * (1 - aliquota_efetiva@v1) / capital_investido@v1.

O retorno sobre o capital empregado, depois do imposto que ele de fato paga.

Tres escolhas que precisam estar escritas, porque cada uma tem uma alternativa
defensavel que daria outro numero:

1. **NOPAT com aliquota EFETIVA, nao nominal.** A nominal e uma constante do
   pais e nao diz nada sobre a companhia; usa-la produziria um retorno que
   ignora prejuizo fiscal acumulado e incentivo - exatamente o que separa duas
   companhias com o mesmo EBIT.

2. **`ebit@v1` como reportado**, decisao A1: inclui equivalencia patrimonial
   quando a companhia a poe acima da linha. Um ROIC ex-equivalencia e outra
   metrica, e no GPA a diferenca vira o sinal do resultado operacional.

3. **Capital do FIM do periodo**, pela mesma razao que `roe@v1` usa patrimonio
   final: a media exige o saldo de abertura, e recusaria no primeiro exercicio
   coberto.

Recusa quando o capital investido nao e positivo. Acontece em companhia muito
capitalizada, cujo caixa excede a divida em mais que o patrimonio - e ali a
razao inverte de sinal e "ROIC de 200%" diria o oposto do que aconteceu.
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
from pat.semantics.definitions import aliquota_efetiva, capital_investido, ebit
from pat.semantics.registry import (
    ComputationUnavailable,
    Inputs,
    Metric,
    MetricRegistry,
)

EBIT = ebit.DEFINITION.ref
ALIQUOTA = aliquota_efetiva.DEFINITION.ref
CAPITAL = capital_investido.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="roic",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.RATIO,
    period_kind=PeriodKind.FLOW,
    # Fluxo sobre saldo, de proposito: e o que um retorno sobre capital e.
    mixes_period_kinds=True,
    requires_metrics=(EBIT, ALIQUOTA, CAPITAL),
    definition=(
        "Resultado operacional apos o imposto efetivo, dividido pelo capital "
        "investido no fim do periodo."
    ),
    rationale=(
        "Aliquota efetiva e nao nominal, porque e ela que a companhia paga; "
        "EBIT como reportado, pela decisao A1; e capital do fim do periodo, pela "
        "mesma razao que `roe@v1` usa patrimonio final. As tres escolhas tem "
        "alternativa defensavel, e cada alternativa e outra metrica - nunca um "
        "parametro desta."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    capital = inputs.metric(CAPITAL).value
    if capital <= 0:
        raise ComputationUnavailable(
            UnavailableReason.DIVISION_BY_ZERO,
            f"capital investido {capital} nao e positivo: o retorno sobre ele nao "
            "tem leitura. Acontece em companhia cujo caixa excede a divida em "
            "mais que o patrimonio, e ali a razao inverte de sinal.",
        )
    nopat = inputs.metric(EBIT).value * (Decimal(1) - inputs.metric(ALIQUOTA).value)
    return nopat / capital


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

"""fcf@v1 = fluxo_de_caixa_operacional@v1 - capex@v1.

O free cash flow SIMPLES, e o nome curto e o unico que este projeto pode usar
sem prometer demais. Existem pelo menos tres FCF em circulacao - o da firma
(FCFF, depois de imposto sobre EBIT e antes de juros), o do acionista (FCFE,
depois de juros e amortizacao de divida) e este, caixa operacional menos capex
-, e nenhum e "o" FCF.

Este e o unico dos tres reconstruivel sem julgamento a partir da demonstracao
publicada: os outros dois exigem separar juros pagos, imposto sobre juros e
movimentacao de divida, e cada separacao dessas e uma escolha. Quando FCFF ou
FCFE forem implementados, serao `fcff@v1` e `fcfe@v1` - metricas novas, com o
julgamento declarado, e nunca este arquivo reescrito.

A subtracao usa a convencao dos conceitos: caixa operacional e positivo quando
ENTRA, capex e positivo quando SAI. Por isso a formula subtrai em vez de somar,
e por isso os dois `sign_convention` estao escritos no catalogo.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics.definitions import capex, fluxo_de_caixa_operacional
from pat.semantics.registry import Inputs, Metric, MetricRegistry

FCO = fluxo_de_caixa_operacional.DEFINITION.ref
CAPEX = capex.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="fcf",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_metrics=(FCO, CAPEX),
    definition=(
        "Caixa operacional do periodo menos o desembolso com imobilizado e "
        "intangivel."
    ),
    rationale=(
        "A unica leitura de free cash flow reconstruivel sem julgamento a partir "
        "da demonstracao publicada. FCFF e FCFE exigem separar juros e "
        "movimentacao de divida, e cada separacao e uma escolha - logo, outras "
        "metricas, com outro nome."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.metric(FCO).value - inputs.metric(CAPEX).value


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

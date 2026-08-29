"""aliquota_efetiva@v1 = income_tax_expense / pretax_income.

Existe para ser insumo de `roic@v1`, e nao para ser lida sozinha - embora ela
seja informativa sozinha: uma aliquota efetiva muito abaixo da nominal costuma
significar prejuizo fiscal acumulado ou incentivo, e as duas coisas tem prazo
de validade que a serie mostra.

Aliquota NEGATIVA e resultado valido: acontece quando ha beneficio fiscal
reconhecido, e atravessa o sistema sem tratamento especial.

O que a metrica recusa e o denominador nao positivo. Aliquota sobre prejuizo
antes de imposto nao tem leitura - o sinal se inverte e uma companhia que
pagou imposto apareceria com aliquota negativa por motivo diferente do
beneficio fiscal, misturando dois fenomenos no mesmo numero.
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
from pat.semantics import concepts
from pat.semantics.registry import (
    ComputationUnavailable,
    Inputs,
    Metric,
    MetricRegistry,
)

DEFINITION = MetricDefinition(
    name="aliquota_efetiva",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.RATIO,
    period_kind=PeriodKind.FLOW,
    requires_concepts=(concepts.INCOME_TAX_EXPENSE, concepts.PRETAX_INCOME),
    definition=(
        "Despesa de imposto de renda e contribuicao social do periodo dividida "
        "pelo resultado antes dos tributos."
    ),
    rationale=(
        "Efetiva, e nao nominal: a nominal e uma constante do pais e nao diz "
        "nada sobre a companhia. A efetiva e o que entra no NOPAT, e usar a "
        "nominal ali produziria um retorno sobre capital que ignora prejuizo "
        "fiscal acumulado e incentivo - exatamente o que separa duas companhias "
        "com o mesmo EBIT."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    antes = inputs.value(concepts.PRETAX_INCOME)
    if antes <= 0:
        # `ComputationUnavailable`, e nao `ValueError`: e o mecanismo que o
        # motor converte em `MetricUnavailable` com motivo nomeado. Um
        # `ValueError` atravessaria como excecao e derrubaria a corrida inteira
        # por causa de uma companhia que teve prejuizo.
        raise ComputationUnavailable(
            UnavailableReason.DIVISION_BY_ZERO,
            f"resultado antes dos tributos {antes} nao e positivo: a aliquota "
            "efetiva nao tem leitura. Uma companhia com prejuizo e outra com "
            "beneficio fiscal produziriam o mesmo sinal por razoes opostas.",
        )
    return inputs.value(concepts.INCOME_TAX_EXPENSE) / antes


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

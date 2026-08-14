"""d_and_a@v1 - D&A reconhecida no resultado (`d_and_a_pnl`).

Conceitualmente sempre a D&A que passou pelo resultado, porque e essa a que
se soma de volta ao EBIT. De onde ela vem e problema do mapeamento, e a
qualidade dessa origem viaja no `fidelity`:

    familia default (CVM)  -> DVA 7.04.01, fidelity=approximate
    mapeamento do GPA      -> DFC 6.01.01.04, fidelity=exact

Os dois caminhos produzem `d_and_a@v1`. O resultado nao finge que sao a mesma
coisa: quem usa a aproximacao carrega `approximate` no MetricResult, e o
`ebitda@v1` montado em cima herda isso.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import (
    ConsistencyCheck,
    Dimension,
    MetricDefinition,
    MetricKind,
    PeriodKind,
)
from pat.semantics import concepts
from pat.semantics.registry import CheckEval, Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="d_and_a",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_concepts=(concepts.D_AND_A_PNL,),
    optional_concepts=(concepts.D_AND_A_RETAINED,),
    definition=(
        "Depreciacao, amortizacao e exaustao reconhecidas no resultado do periodo."
    ),
    rationale=(
        "E a parcela que reduziu o lucro, portanto a unica que faz sentido somar de "
        "volta ao EBIT. A D&A total retida do valor adicionado e grandeza diferente "
        "(`d_and_a_retained`) e nao e sinonimo - onde ela e usada como substituta, o "
        "binding marca fidelity=approximate e o resultado propaga isso."
    ),
    checks=(
        ConsistencyCheck(
            check_id="nao_negativa",
            description="D&A negativa indica sinal invertido no mapeamento.",
            tolerance_abs=Decimal(0),
        ),
        ConsistencyCheck(
            check_id="proxima_da_retida",
            description=(
                "Compara a D&A do resultado com a D&A retida do valor adicionado. "
                "Divergencia grande e real e informativa (capitalizacao em estoque, "
                "operacoes descontinuadas), nao necessariamente erro. Quando as duas "
                "vem do mesmo binding - caso da familia default - o check passa "
                "trivialmente, e e o `fidelity` que carrega a ressalva."
            ),
            tolerance_abs=Decimal(1000),
            tolerance_rel=Decimal("0.05"),
        ),
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.D_AND_A_PNL)


def _check_nao_negativa(inputs: Inputs, value: Decimal, spec: ConsistencyCheck) -> CheckEval:
    return CheckEval(passed=value >= 0, observed=value)


def _check_proxima_da_retida(
    inputs: Inputs, value: Decimal, spec: ConsistencyCheck
) -> CheckEval | None:
    retida = inputs.optional(concepts.D_AND_A_RETAINED)
    if retida is None:
        return None
    limite = spec.tolerance_abs
    if spec.tolerance_rel is not None:
        limite = max(limite, abs(retida.value) * spec.tolerance_rel)
    return CheckEval(
        passed=abs(value - retida.value) <= limite, observed=value, expected=retida.value
    )


def register(registry: MetricRegistry) -> Metric:
    return registry.register(
        Metric(
            definition=DEFINITION,
            compute=_compute,
            check_fns={
                "nao_negativa": _check_nao_negativa,
                "proxima_da_retida": _check_proxima_da_retida,
            },
        )
    )

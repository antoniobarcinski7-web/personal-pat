"""ebitda@v1 = ebit@v1 + d_and_a@v1.

Uma definicao, escrita uma vez, valida em qualquer regime.

O que ela NAO e, e nao pode virar por conveniencia:

- EBITDA ajustado divulgado pela companhia. Vem do RI, tier ISSUER, nao e
  derivavel da DFP e nunca serve de fallback. Se for implementado, sera
  `ebitda_ajustado_divulgado@v1` - outro conceito, outra fonte.
- EBITDA normalizado por itens nao recorrentes. Exige julgamento, logo seria
  kind=ESTIMATED com `assumptions` declaradas.

Dependencias pinadas por versao: publicar `ebit@v2` deixa esta metrica bit a
bit identica.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics.definitions import d_and_a, ebit
from pat.semantics.registry import Inputs, Metric, MetricRegistry

EBIT = ebit.DEFINITION.ref
D_AND_A = d_and_a.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="ebitda",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_metrics=(EBIT, D_AND_A),
    definition="EBIT como reportado, somado a depreciacao e amortizacao do resultado.",
    rationale=(
        "A definicao classica e a unica reconstruivel a partir de demonstracao "
        "publicada, sem julgamento. Como `ebit@v1` inclui equivalencia patrimonial "
        "(decisao A1), este EBITDA tambem inclui - e o `rationale` de `ebit@v1` "
        "explica por que."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.metric(EBIT).value + inputs.metric(D_AND_A).value


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

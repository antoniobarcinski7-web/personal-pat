"""margem_ebitda@v1 = ebitda@v1 / receita_liquida@v1.

Primeira metrica adimensional do sistema, e por isso a que exercita duas
regras do contrato: dimension=RATIO nao carrega moeda, e a checagem de moeda
dos insumos continua valendo mesmo assim - dividir EBITDA em BRL por receita
em USD daria um numero limpo e sem sentido.

Divisor zero nao vira `None` nem infinito: vira indisponibilidade nomeada.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from pat.contracts.semantics import (
    Dimension,
    MetricDefinition,
    MetricKind,
    PeriodKind,
    UnavailableReason,
)
from pat.semantics.definitions import ebitda, receita_liquida
from pat.semantics.registry import ComputationUnavailable, Inputs, Metric, MetricRegistry

EBITDA = ebitda.DEFINITION.ref
RECEITA = receita_liquida.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="margem_ebitda",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.RATIO,
    period_kind=PeriodKind.FLOW,
    requires_metrics=(EBITDA, RECEITA),
    definition="EBITDA dividido pela receita liquida do mesmo periodo.",
    rationale=(
        "Fracao, nao percentual: a conversao para percentual e apresentacao, e "
        "guardar 0,0942 em vez de 9,42 evita a classe de erro em que alguem "
        "multiplica por 100 duas vezes."
    ),
)

_PRECISION = 28
"""Precisao fixada em contexto local: o default do modulo `decimal` e global e
mutavel, e uma divisao cujo resultado depende de estado de processo nao e
reproduzivel (I3)."""


def _compute(inputs: Inputs) -> Decimal:
    receita = inputs.metric(RECEITA).value
    if receita == 0:
        raise ComputationUnavailable(
            UnavailableReason.DIVISION_BY_ZERO,
            "receita liquida zero no periodo: margem nao existe (e nao e zero)",
        )
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return inputs.metric(EBITDA).value / receita


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

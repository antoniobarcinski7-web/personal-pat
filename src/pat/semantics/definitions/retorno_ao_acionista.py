"""retorno_ao_acionista@v1 = dividendos_pagos@v1 + recompras@v1.

O caixa que saiu da companhia para o acionista no periodo. E a metade que
faltava da alocacao de capital: sem ela, o relatorio mostrava o caixa gerado e
o capex, e o resto sumia.

As duas parcelas sao economicamente a mesma coisa - devolucao de capital - e
contabilmente linhas diferentes, e por isso a metrica soma DUAS metricas em vez
de ler um conceito so. A consequencia e que ela recusa quando qualquer das duas
falta, o que e o comportamento certo: uma companhia que recompra e nao paga
dividendo tem `dividendos_pagos@v1` indisponivel, e apresentar so a recompra
sob o nome "retorno ao acionista" seria dizer que o resto e zero quando ninguem
olhou.

Quem quiser a parcela isolada usa a metrica isolada. Elas existem.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics.definitions import dividendos_pagos, recompras
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DIVIDENDOS = dividendos_pagos.DEFINITION.ref
RECOMPRAS = recompras.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="retorno_ao_acionista",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_metrics=(DIVIDENDOS, RECOMPRAS),
    definition=(
        "Caixa devolvido ao acionista no periodo: dividendos pagos mais recompra "
        "de acoes proprias."
    ),
    rationale=(
        "As duas parcelas sao a mesma decisao de alocacao sob formas diferentes, "
        "e separa-las na leitura faria uma companhia que so recompra parecer nao "
        "devolver capital. Somar exige as duas: apresentar so uma sob este nome "
        "diria que a outra e zero quando ninguem olhou."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.metric(DIVIDENDOS).value + inputs.metric(RECOMPRAS).value


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

"""capex@v1 - leitura direta do conceito `capex`.

Positivo = saida de caixa, ao contrario de `fluxo_de_caixa_operacional@v1`. A
convencao esta no conceito e o `sign` do binding normaliza cada fonte ate ela;
uma companhia que publique o desembolso com sinal negativo entra com sign=-1.

O check de nao-negatividade existe justamente para pegar o mapeamento que
esqueceu essa inversao - e o erro mais provavel de quem escreve o primeiro
binding de fluxo de caixa de um regime novo.
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
    name="capex",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_concepts=(concepts.CAPEX,),
    definition=(
        "Caixa desembolsado na aquisicao de imobilizado e intangivel no periodo."
    ),
    rationale=(
        "Leitura direta do desembolso publicado, e nao a variacao do imobilizado "
        "no balanco: a segunda mistura depreciacao, baixa e conversao cambial, e "
        "produziria um 'capex' que nao e caixa nenhum."
    ),
    checks=(
        ConsistencyCheck(
            check_id="nao_negativo",
            description=(
                "Capex negativo indica sinal invertido no mapeamento. A convencao "
                "do conceito e 'positivo = saida de caixa', e um regime que "
                "publique negativo normaliza com sign=-1 no binding."
            ),
            tolerance_abs=Decimal(0),
        ),
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.CAPEX)


def _check_nao_negativo(inputs: Inputs, value: Decimal, spec: ConsistencyCheck) -> CheckEval:
    return CheckEval(passed=value >= 0, observed=value)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(
        Metric(
            definition=DEFINITION,
            compute=_compute,
            check_fns={"nao_negativo": _check_nao_negativo},
        )
    )

"""roe@v1 = lucro_liquido@v1 / patrimonio_liquido@v1.

Patrimonio FINAL, e nao medio. As duas leituras existem e dao numeros
diferentes; esta escolhe a que nao exige um segundo periodo, porque uma metrica
que precisasse do saldo de abertura recusaria no primeiro exercicio coberto -
justamente quando a serie e mais curta e a resposta mais util. Quem quiser a
media escreve `roe_medio@v1`, com o julgamento declarado.

O denominador e o patrimonio ATRIBUIVEL A CONTROLADORA, e o numerador o
resultado atribuivel a ela. O par nao e detalhe: misturar um resultado
atribuivel com um patrimonio total produz um retorno que nao e de nenhum
acionista.

Patrimonio negativo
-------------------
Acontece - recompra agressiva, prejuizo acumulado - e torna a razao sem
sentido: um prejuizo sobre patrimonio negativo sai POSITIVO, e um leitor
distraido veria "ROE de 40%" numa companhia que destruiu capital. Por isso a
metrica RECUSA quando o denominador nao e positivo, em vez de devolver um
numero com o sinal invertido. E o mesmo desenho da divisao por zero: nao ha
resposta, e inventar uma seria pior que nao ter.
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
from pat.semantics.definitions import lucro_liquido, patrimonio_liquido
from pat.semantics.registry import (
    ComputationUnavailable,
    Inputs,
    Metric,
    MetricRegistry,
)

LUCRO = lucro_liquido.DEFINITION.ref
PATRIMONIO = patrimonio_liquido.DEFINITION.ref

DEFINITION = MetricDefinition(
    name="roe",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.RATIO,
    period_kind=PeriodKind.FLOW,
    # Fluxo sobre saldo, de proposito: e o que um retorno sobre capital e.
    mixes_period_kinds=True,
    requires_metrics=(LUCRO, PATRIMONIO),
    definition=(
        "Resultado atribuivel a controladora no periodo dividido pelo patrimonio "
        "liquido atribuivel a ela no fim do periodo."
    ),
    rationale=(
        "Patrimonio final, e nao medio: a media exige o saldo de abertura, e uma "
        "metrica que o exigisse recusaria no primeiro exercicio coberto. "
        "Numerador e denominador sao os dois atribuiveis a controladora, porque "
        "misturar produziria um retorno que nao e de nenhum acionista."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    patrimonio = inputs.metric(PATRIMONIO).value
    if patrimonio <= 0:
        # Recusa, e nao um numero de sinal invertido. Prejuizo sobre patrimonio
        # negativo sai positivo, e "ROE de 40%" numa companhia que destruiu
        # capital e o pior tipo de numero: certo na aritmetica e errado em tudo.
        raise ComputationUnavailable(
            UnavailableReason.DIVISION_BY_ZERO,
            f"patrimonio liquido {patrimonio} nao e positivo: a razao nao tem "
            "leitura. Patrimonio negativo e informacao real - recompra agressiva "
            "ou prejuizo acumulado -, e o que ele torna sem sentido e o retorno "
            "sobre ele, nao o saldo.",
        )
    return inputs.metric(LUCRO).value / patrimonio


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

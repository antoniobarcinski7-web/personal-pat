"""lucro_liquido@v1 - resultado atribuivel aos socios da controladora.

Leitura direta do conceito `net_income_controlling`. Como em
`receita_liquida.py`, nao ha uma unica mencao a plano de contas aqui - e por
isso a metrica vale nos dois regimes sem alteracao.

Decisao A3, tomada explicitamente e nao reaberta
-------------------------------------------------
O alvo e o resultado ATRIBUIVEL A CONTROLADORA, e nao o consolidado total.
As duas jurisdicoes publicam a distincao, e ela nao e cosmetica:

    companhia brasileira, FY2024   total 37.009 MM   controladora 36.606 MM
    companhia americana,  FY2024   total -19.233 MM  controladora -18.756 MM

Quem calcula lucro por acao, retorno sobre o patrimonio do acionista ou
qualquer multiplo de mercado quer o atribuivel - o resultado dos nao
controladores pertence a outra gente. Somar os dois e contar o que nao e seu.

O resultado total, incluindo nao controladores, e METRICA SEPARADA quando
alguem precisar dela. Nao ha fallback automatico de uma para a outra: uma
companhia sem nao controladores tem os dois numeros iguais, e uma com tem os
dois diferentes - devolver o que estiver disponivel faria a resposta mudar de
significado conforme a estrutura societaria, em silencio.

Por que nao ha check de sinal
-----------------------------
`receita_liquida@v1` confere que nao e negativa, porque receita negativa quase
sempre denuncia sinal invertido no mapeamento. Aqui e o oposto: prejuizo e
comum e legitimo. Um check de nao-negatividade produziria alarme falso todo ano
ruim, e alarme que soa sem motivo e alarme que se aprende a ignorar.

Por que este arquivo nao nomeia nenhuma linha
---------------------------------------------
Nem codigo de conta, nem elemento de taxonomia, nem sequer o nome das
companhias usadas para conferir. Onde cada conceito mora e assunto do
MAPEAMENTO, e e la que os numeros acima estao atribuidos a suas fontes, com
`equivalence_basis`. Uma definicao que citasse a linha deixaria de valer no
regime seguinte - e o teste de layering falha se alguem tentar.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import (
    Dimension,
    MetricDefinition,
    MetricKind,
    PeriodKind,
)
from pat.semantics import concepts
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="lucro_liquido",
    version="v1",
    kind=MetricKind.REPORTED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.FLOW,
    requires_concepts=(concepts.NET_INCOME_CONTROLLING,),
    definition=(
        "Resultado do periodo atribuivel aos socios da controladora, como "
        "reportado - depois do resultado financeiro, dos tributos sobre o lucro e "
        "das operacoes descontinuadas."
    ),
    rationale=(
        "Leitura direta, sem aritmetica: e uma linha publicada em ambos os regimes, "
        "e nao um calculo. Decisao A3 do projeto - o alvo e o atribuivel a "
        "controladora, e o consolidado total (incluindo nao controladores) e metrica "
        "separada, sem fallback automatico entre as duas.\n\n"
        "Prejuizo atravessa sem tratamento especial: um sistema que suprimisse o "
        "sinal esconderia exatamente o ano que importa."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.NET_INCOME_CONTROLLING)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

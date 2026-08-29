"""valor_de_mercado@v1 = share_price x shares_outstanding.

A primeira metrica do sistema que combina um numero do REGULADOR com um numero
do MERCADO. Ela existe para tornar respondivel a pergunta que separa analise de
negocio de analise de investimento: nao "quanto a companhia gera", e sim
"quanto estao pedindo por ela".

O preco e a parte fraca da cadeia
---------------------------------
`share_price` vem de agregador - `PUBLIC_WEB` -, e todo o resto do catalogo vem
de arquivamento oficial. A cadeia vale o elo mais fraco, e e por isso que a
procedencia viaja no `Retrieval` e chega ao resultado: um valor de mercado nao
tem a mesma dureza que uma receita auditada, e o sistema nao pode deixar de
saber disso.

Acoes EM CIRCULACAO, e nao media ponderada
------------------------------------------
A media ponderada e o denominador do lucro por acao; ela suaviza recompra e
emissao ao longo do exercicio. Multiplicar preco de uma DATA por uma media de
um PERIODO daria o valor de uma companhia que nao existe em data nenhuma.

Desdobramento
-------------
Preco e contagem se ajustam ao mesmo evento, em direcoes opostas, e o produto
sobrevive - desde que os dois venham do mesmo momento. E o que a consulta
bitemporal garante: sob um `as_of` anterior ao desdobramento saem as duas
leituras antigas, e sob um posterior saem as duas novas. Misturar as duas
epocas erraria por uma ordem de grandeza, e e o erro que este arquivo mais
teme.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.semantics import Dimension, MetricDefinition, MetricKind, PeriodKind
from pat.semantics import concepts
from pat.semantics.registry import Inputs, Metric, MetricRegistry

DEFINITION = MetricDefinition(
    name="valor_de_mercado",
    version="v1",
    kind=MetricKind.CALCULATED,
    dimension=Dimension.MONEY,
    period_kind=PeriodKind.STOCK,
    requires_concepts=(concepts.SHARE_PRICE, concepts.SHARES_OUTSTANDING),
    definition=(
        "Preco de fechamento na data-base multiplicado pelas acoes em circulacao "
        "na mesma data."
    ),
    rationale=(
        "Acoes em circulacao, e nao media ponderada: multiplicar o preco de uma "
        "DATA por uma media de um PERIODO daria o valor de uma companhia que nao "
        "existe em data nenhuma. A media e o denominador do lucro por acao, e e "
        "outra grandeza."
    ),
)


def _compute(inputs: Inputs) -> Decimal:
    return inputs.value(concepts.SHARE_PRICE) * inputs.value(concepts.SHARES_OUTSTANDING)


def register(registry: MetricRegistry) -> Metric:
    return registry.register(Metric(definition=DEFINITION, compute=_compute))

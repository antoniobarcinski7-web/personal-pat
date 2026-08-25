"""Catalogo de decomposicoes declaradas. Universal, como `concepts.py`.

Uma decomposicao e uma IDENTIDADE CONTABIL afirmada entre conceitos:

    ebit_reported = revenue_net - cogs - operating_expenses_net

Nao e uma aproximacao nem uma regressao: e a cascata da demonstracao de
resultado, escrita uma vez e valida em qualquer regime que publique esses
conceitos. Este modulo nao menciona `cd_conta`, `cod_cvm` nem elemento XBRL -
`tests/semantics/test_layering.py` confere isso por AST, como faz com
`concepts.py` e com `definitions/`.

Por que identidade, e nao atribuicao estatistica
-------------------------------------------------
A tentacao obvia seria estimar contribuicoes - regredir a variacao do EBIT
contra a das partes, ou distribuir o residual proporcionalmente. Nenhuma das
duas entra aqui, e a razao e a mesma que proibe o LLM de calcular: um numero
estimado que se apresenta como medido e pior do que numero nenhum. A soma ou
fecha, ou o que sobra aparece como residual com nome proprio.

Versionamento
-------------
Mesma disciplina das metricas. Mudou o conjunto de termos ou o sinal de
algum? Decomposicao nova, versao nova, as duas registradas ao mesmo tempo. Uma
analise antiga continua reproduzindo com a versao com que foi feita.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pat.contracts.decomposition import BreakdownAxis
from pat.semantics import concepts

__all__ = [
    "CATALOG",
    "DecompositionDefinition",
    "Term",
    "all_definitions",
    "get",
]


@dataclass(frozen=True)
class Term:
    """Um termo da identidade, com o sinal com que entra no alvo.

    `sign` e do TERMO na identidade, e nao do valor. `cogs` tem convencao
    "positivo = despesa" e entra com sinal -1 em `ebit`: um custo maior reduz
    o resultado. Guardar as duas coisas separadas - convencao do conceito e
    papel na identidade - e o que impede um sinal trocado de virar uma
    contribuicao com a direcao invertida, que e um erro que *parece* uma
    descoberta.
    """

    concept_id: str
    sign: Literal[-1, 1]
    label: str


@dataclass(frozen=True)
class DecompositionDefinition:
    decomposition_id: str
    version: str
    axis: BreakdownAxis
    target_concept: str
    target_label: str
    terms: tuple[Term, ...]
    definition: str
    rationale: str
    tolerance_abs: Decimal
    """Quanto de residual ainda conta como 'fecha'.

    Nao e licenca para esconder: o residual aparece sempre, com valor. A
    tolerancia so decide se o resultado sai marcado como fechado. Um valor
    grande demais faria uma decomposicao furada se apresentar como completa;
    por isso ela e da DEFINICAO, revisavel, e nao um parametro de chamada."""

    member_axis: str = ""
    """Eixo dimensional, quando `axis` nao e COMPONENT.

    Vazio no eixo COMPONENT, cujos "membros" sao conceitos universais. Nos
    demais e o nome do eixo na fonte (`BusinessSegments`), e os membros vem
    declarados no mapeamento da empresa - nunca descobertos, porque a fonte
    publica roll-up e folha lado a lado sem marcar qual e qual."""

    @property
    def ref(self) -> str:
        return f"{self.decomposition_id}@{self.version}"

    @property
    def concept_ids(self) -> tuple[str, ...]:
        return tuple(term.concept_id for term in self.terms)


_CATALOG: tuple[DecompositionDefinition, ...] = (
    DecompositionDefinition(
        decomposition_id="gross_profit_by_line",
        version="v1",
        axis=BreakdownAxis.COMPONENT,
        target_concept=concepts.GROSS_PROFIT,
        target_label="Lucro bruto",
        terms=(
            Term(concepts.REVENUE_NET, 1, "Receita liquida"),
            Term(concepts.COGS, -1, "Custo dos bens e servicos"),
        ),
        definition="lucro bruto = receita liquida - custo dos bens e servicos vendidos",
        rationale=(
            "A identidade mais basica da cascata, e a que separa efeito de preco/volume "
            "(receita) de efeito de custo. Quando as duas caem juntas, a margem pode "
            "subir ou cair, e so a decomposicao diz qual."
        ),
        tolerance_abs=Decimal(1000),
    ),
    DecompositionDefinition(
        decomposition_id="ebit_by_line",
        version="v1",
        axis=BreakdownAxis.COMPONENT,
        target_concept=concepts.EBIT_REPORTED,
        target_label="Resultado operacional (EBIT reportado)",
        terms=(
            Term(concepts.REVENUE_NET, 1, "Receita liquida"),
            Term(concepts.COGS, -1, "Custo dos bens e servicos"),
            Term(concepts.OPERATING_EXPENSES_NET, -1, "Despesas operacionais liquidas"),
        ),
        definition=(
            "EBIT reportado = receita liquida - custo dos bens e servicos - despesas "
            "operacionais liquidas"
        ),
        rationale=(
            "A decomposicao que responde 'por que o resultado operacional mudou'. Ela e "
            "a composicao de `gross_profit_by_line` com `ebit_by_stage`, achatada em um "
            "nivel so - util porque as tres pontas competem entre si na explicacao: "
            "receita menor, custo maior e despesa maior sao tres historias diferentes.\n\n"
            "Segue a decisao A1: o alvo e o EBIT COMO REPORTADO, entao a equivalencia "
            "patrimonial que a companhia tenha posto acima da linha esta dentro de "
            "`operating_expenses_net`. Isola-la seria outra decomposicao, com outro "
            "conjunto de termos e outra versao."
        ),
        tolerance_abs=Decimal(1000),
    ),
    DecompositionDefinition(
        decomposition_id="ebit_by_stage",
        version="v1",
        axis=BreakdownAxis.COMPONENT,
        target_concept=concepts.EBIT_REPORTED,
        target_label="Resultado operacional (EBIT reportado)",
        terms=(
            Term(concepts.GROSS_PROFIT, 1, "Lucro bruto"),
            Term(concepts.OPERATING_EXPENSES_NET, -1, "Despesas operacionais liquidas"),
        ),
        definition="EBIT reportado = lucro bruto - despesas operacionais liquidas",
        rationale=(
            "O mesmo alvo de `ebit_by_line`, aberto num nivel acima. Existe separada, e "
            "nao como um modo da outra, porque as duas respondem perguntas diferentes: "
            "esta diz se o problema foi na operacao ou na estrutura; a outra abre a "
            "operacao em preco/volume e custo. Registrar as duas e mais honesto do que "
            "escolher uma e chamar de 'a' decomposicao do EBIT."
        ),
        tolerance_abs=Decimal(1000),
    ),
    DecompositionDefinition(
        decomposition_id="revenue_by_segment",
        version="v1",
        axis=BreakdownAxis.SEGMENT,
        target_concept=concepts.REVENUE_NET,
        target_label="Receita liquida",
        terms=(),
        definition=(
            "receita liquida = soma dos segmentos operacionais + eliminacao "
            "intersegmento"
        ),
        rationale=(
            "A decomposicao que responde 'qual negocio explica a variacao da "
            "receita'. Diferente das outras tres, os membros NAO sao conceitos "
            "universais: sao segmentos daquela companhia, declarados no mapeamento "
            "dela.\n\n"
            "A eliminacao intersegmento e um membro, e nao um ajuste escondido. Sem "
            "ela a soma dos segmentos excede a receita consolidada - na Intel, em "
            "17,2 bi de dolares em FY2024, porque a receita da Foundry e "
            "majoritariamente interna. Um sistema que a omitisse mostraria esse "
            "valor como 'residual nao explicado', o que seria falso: ele e uma "
            "eliminacao publicada.\n\n"
            "So resolve onde a fonte publica a dimensao de forma estruturada. No "
            "regime da CVM ela nao existe - a informacao por segmento vive em nota "
            "explicativa, em PDF - e a decomposicao recusa com NO_BREAKDOWN_SOURCE."
        ),
        tolerance_abs=Decimal(1000),
        member_axis="BusinessSegments",
    ),
)

CATALOG: dict[str, DecompositionDefinition] = {d.ref: d for d in _CATALOG}


def all_definitions() -> tuple[DecompositionDefinition, ...]:
    return tuple(sorted(_CATALOG, key=lambda d: d.ref))


def get(ref: str) -> DecompositionDefinition | None:
    """Aceita 'nome@versao'. Sem versao NAO resolve para a mais recente.

    Deliberado: 'a mais recente' faria uma analise antiga mudar de significado
    quando alguem publicasse uma v2. E a mesma razao pela qual `ebitda@v1`
    depende de `ebit@v1` pinado, e nao do 'ebit atual'.
    """
    return CATALOG.get(ref)

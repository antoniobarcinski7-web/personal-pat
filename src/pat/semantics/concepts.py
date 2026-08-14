"""Catalogo universal de conceitos. Sem jurisdicao, sem plano de contas.

Este arquivo e a resposta a pergunta "o que e receita liquida?" - nao a
"onde a receita liquida aparece no arquivo da CVM?". A segunda pergunta e
respondida em `mappings/`, uma por regime.

Duas regras estruturais
-----------------------
1. `concept_id` e imutavel. Significado refinado vira conceito novo. Nao
   existe `revenue_net@v2`.

2. Todo conceito declara `sign_convention`. Isso nao e preciosismo: a CVM
   publica custo como negativo, o XBRL americano publica positivo com rotulo
   negado, e fluxos de caixa trocam de sinal entre fontes. A convencao mora
   no conceito; o `sign` do binding e que normaliza cada fonte ate ela. Sem
   isso, o primeiro mapeamento americano produziria EBITDA subtraindo D&A.
"""

from __future__ import annotations

from pat.contracts.semantics import Concept, Dimension, PeriodKind

REVENUE_NET = "revenue_net"
COGS = "cogs"
GROSS_PROFIT = "gross_profit"
OPERATING_EXPENSES_NET = "operating_expenses_net"
EBIT_REPORTED = "ebit_reported"
EQUITY_METHOD_RESULT = "equity_method_result"
D_AND_A_PNL = "d_and_a_pnl"
D_AND_A_RETAINED = "d_and_a_retained"


_CATALOG: tuple[Concept, ...] = (
    Concept(
        concept_id=REVENUE_NET,
        label_en="Net revenue",
        definition=(
            "Receita de contratos com clientes reconhecida no periodo, liquida de "
            "impostos sobre venda, devolucoes, abatimentos e descontos comerciais."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = receita",
        boundary_notes=(
            "Nao e receita bruta: deducoes de venda ja estao subtraidas.",
            "Nao inclui receita financeira nem resultado de equivalencia patrimonial.",
            "Receita de operacoes descontinuadas fica FORA: pertence ao resultado "
            "descontinuado, e mistura-la quebra comparacao ano a ano.",
        ),
    ),
    Concept(
        concept_id=COGS,
        label_en="Cost of goods and services sold",
        definition="Custo dos bens e servicos vendidos, atribuivel a receita do periodo.",
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = despesa",
        boundary_notes=("Inclui a depreciacao alocada ao custo, quando a companhia a aloca.",),
    ),
    Concept(
        concept_id=GROSS_PROFIT,
        label_en="Gross profit",
        definition="Receita liquida menos custo dos bens e servicos vendidos.",
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = lucro",
    ),
    Concept(
        concept_id=OPERATING_EXPENSES_NET,
        label_en="Net operating expenses",
        definition=(
            "Despesas operacionais liquidas de outras receitas operacionais, entre o "
            "lucro bruto e o resultado operacional."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = despesa liquida",
        boundary_notes=(
            "Conforme o regime, pode conter o resultado de equivalencia patrimonial; "
            "por isso `equity_method_result` existe em separado, para poder ser "
            "isolado sem adivinhacao.",
        ),
    ),
    Concept(
        concept_id=EBIT_REPORTED,
        label_en="Operating result before finance and taxes, as reported",
        definition=(
            "Resultado operacional antes do resultado financeiro e dos tributos sobre "
            "o lucro, exatamente como a companhia o apresenta."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = lucro",
        boundary_notes=(
            "Como reportado: INCLUI equivalencia patrimonial e outros itens "
            "operacionais que a companhia tenha colocado acima desta linha.",
            "Um EBIT que exclua equivalencia e outro conceito, nao este.",
            "Nao e ajustado por itens nao recorrentes.",
        ),
    ),
    Concept(
        concept_id=EQUITY_METHOD_RESULT,
        label_en="Share of profit of associates and joint ventures",
        definition="Resultado de participacoes societarias avaliadas por equivalencia patrimonial.",
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = ganho",
    ),
    Concept(
        concept_id=D_AND_A_PNL,
        label_en="Depreciation and amortisation charged to profit or loss",
        definition=(
            "Depreciacao, amortizacao e exaustao reconhecidas no resultado do periodo - "
            "a parcela que efetivamente reduziu o lucro e que, portanto, e a que se "
            "soma de volta ao EBIT."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = despesa",
        boundary_notes=(
            "E a despesa que passou pelo resultado, nao a depreciacao total do periodo: "
            "parcela capitalizada em estoque so atinge o resultado quando o estoque e "
            "vendido. Ver `d_and_a_retained`, que e a outra grandeza.",
            "Inclui depreciacao de direito de uso (IFRS 16) quando a companhia a "
            "reconhece no resultado.",
        ),
    ),
    Concept(
        concept_id=D_AND_A_RETAINED,
        label_en="Total depreciation and amortisation retained in the period",
        definition=(
            "Depreciacao, amortizacao e exaustao totais do periodo, incluindo a parcela "
            "capitalizada em outros ativos e ainda nao reconhecida no resultado."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = despesa",
        boundary_notes=(
            "Grandeza economicamente distinta de `d_and_a_pnl`, nao um sinonimo dela. "
            "Nas duas ha anos em que coincidem e anos em que divergem muito.",
            "Nem todo regime publica esta grandeza.",
        ),
    ),
)


CATALOG: dict[str, Concept] = {c.concept_id: c for c in _CATALOG}


class UnknownConceptError(KeyError):
    pass


def get(concept_id: str) -> Concept:
    """Conceito pelo id. Levanta em vez de devolver None: referenciar conceito
    inexistente e erro de programacao, nao dado faltante."""
    try:
        return CATALOG[concept_id]
    except KeyError:
        raise UnknownConceptError(
            f"conceito desconhecido: {concept_id!r}. "
            f"Conhecidos: {', '.join(sorted(CATALOG))}"
        ) from None


def exists(concept_id: str) -> bool:
    return concept_id in CATALOG

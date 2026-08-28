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
NET_INCOME_CONTROLLING = "net_income_controlling"

# Balanco e caixa. Sao os conceitos que faltavam para sair da DRE - sem eles
# nenhuma pergunta sobre solvencia, geracao de caixa ou alocacao de capital
# tem como ser respondida, por mais dado que se ingira.
CASH_AND_EQUIVALENTS = "cash_and_equivalents"
SHORT_TERM_INVESTMENTS = "short_term_investments"
DEBT_CURRENT = "debt_current"
DEBT_NONCURRENT = "debt_noncurrent"
LEASE_LIABILITY_CURRENT = "lease_liability_current"
LEASE_LIABILITY_NONCURRENT = "lease_liability_noncurrent"
CASH_FLOW_OPERATING = "cash_flow_operating"
CAPEX = "capex"


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
    Concept(
        concept_id=NET_INCOME_CONTROLLING,
        label_en="Net income attributable to owners of the parent",
        definition=(
            "Resultado do periodo atribuivel aos socios da controladora, depois do "
            "resultado financeiro, dos tributos sobre o lucro e das operacoes "
            "descontinuadas."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = lucro",
        boundary_notes=(
            "ATRIBUIVEL A CONTROLADORA, e nao o resultado consolidado total. A "
            "decisao A3 do projeto: o total incluindo nao controladores e conceito "
            "SEPARADO, e nao ha fallback automatico de um para o outro.",
            "A diferenca nao e cosmetica. Petrobras FY2024: consolidado 37.009 MM, "
            "atribuivel a controladora 36.606 MM, nao controladores 403 MM. Intel "
            "FY2024: ProfitLoss -19.233 MM, NetIncomeLoss -18.756 MM. Quem calcula "
            "lucro por acao ou retorno sobre o capital do acionista quer o "
            "atribuivel; quem soma os dois numeros esta contando o que nao lhe "
            "pertence.",
            "Inclui operacoes descontinuadas, porque e o resultado do periodo COMO "
            "REPORTADO. Um lucro ex-descontinuadas e outro conceito - mesma "
            "disciplina de `ebit_reported`, que inclui equivalencia patrimonial.",
            "Nao e EBIT nem EBITDA: aqueles param antes do resultado financeiro e "
            "dos tributos, e substituir um pelo outro muda a pergunta.",
        ),
    ),
    # -----------------------------------------------------------------------
    # Balanco: saldos pontuais (PeriodKind.STOCK)
    # -----------------------------------------------------------------------
    Concept(
        concept_id=CASH_AND_EQUIVALENTS,
        label_en="Cash and cash equivalents",
        definition=(
            "Caixa, depositos a vista e aplicacoes de liquidez imediata com "
            "vencimento original de ate tres meses e risco insignificante de "
            "mudanca de valor."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.STOCK,
        sign_convention="positivo = saldo disponivel",
        boundary_notes=(
            "NAO inclui aplicacoes financeiras de prazo maior: aquilo e "
            "`short_term_investments`, conceito separado. Os dois juntos sao uma "
            "posicao de liquidez, e somar por conta propria seria escolher uma "
            "definicao de caixa que a companhia nao escolheu.",
            "O criterio de tres meses e do proprio regime contabil, e por isso a "
            "linha pode ser lida direto: quem decide o que e equivalente e o "
            "emissor, nao este catalogo.",
        ),
    ),
    Concept(
        concept_id=SHORT_TERM_INVESTMENTS,
        label_en="Short-term investments",
        definition=(
            "Aplicacoes financeiras classificadas no ativo circulante que NAO "
            "atendem ao criterio de equivalente de caixa."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.STOCK,
        sign_convention="positivo = saldo aplicado",
        boundary_notes=(
            "Separado de `cash_and_equivalents` de proposito. Uma divida liquida "
            "que abate aplicacoes e uma metrica DIFERENTE de uma que abate so "
            "caixa, e as duas circulam com o mesmo nome no mercado. Manter os "
            "conceitos separados e o que permite as duas existirem sem que uma "
            "se disfarce da outra.",
        ),
    ),
    Concept(
        concept_id=DEBT_CURRENT,
        label_en="Current debt",
        definition=(
            "Emprestimos, financiamentos e titulos de divida com vencimento em ate "
            "doze meses, ou classificados no passivo circulante."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.STOCK,
        sign_convention="positivo = obrigacao",
        boundary_notes=(
            "Divida ONEROSA: obrigacao contratual de pagar principal e juros. "
            "Fornecedores, obrigacoes fiscais e trabalhistas ficam fora - sao "
            "passivo operacional, e inclui-los tornaria toda companhia com bom "
            "capital de giro artificialmente alavancada.",
            "Arrendamento fica FORA, por decisao A4. Ele e `lease_liability_current`, "
            "e a metrica que o inclui diz no nome que o inclui.",
        ),
    ),
    Concept(
        concept_id=DEBT_NONCURRENT,
        label_en="Non-current debt",
        definition=(
            "Emprestimos, financiamentos e titulos de divida com vencimento acima "
            "de doze meses, ou classificados no passivo nao circulante."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.STOCK,
        sign_convention="positivo = obrigacao",
        boundary_notes=(
            "Mesma fronteira de `debt_current`: onerosa, e sem arrendamento.",
        ),
    ),
    Concept(
        concept_id=LEASE_LIABILITY_CURRENT,
        label_en="Current lease liability",
        definition=(
            "Passivo de arrendamento com vencimento em ate doze meses, reconhecido "
            "no balanco pelo regime de arrendamentos vigente."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.STOCK,
        sign_convention="positivo = obrigacao",
        boundary_notes=(
            "Existe como conceito PROPRIO por causa da decisao A4: o arrendamento "
            "entra na divida bruta explicitamente ou nao entra, e nunca por "
            "normalizacao silenciosa. Companhias de varejo e de midia mudam de "
            "faixa de alavancagem inteira dependendo dessa escolha.",
        ),
    ),
    Concept(
        concept_id=LEASE_LIABILITY_NONCURRENT,
        label_en="Non-current lease liability",
        definition=(
            "Passivo de arrendamento com vencimento acima de doze meses, "
            "reconhecido no balanco pelo regime de arrendamentos vigente."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.STOCK,
        sign_convention="positivo = obrigacao",
    ),
    # -----------------------------------------------------------------------
    # Fluxo de caixa
    # -----------------------------------------------------------------------
    Concept(
        concept_id=CASH_FLOW_OPERATING,
        label_en="Net cash provided by operating activities",
        definition=(
            "Caixa liquido gerado (ou consumido) pelas atividades operacionais no "
            "periodo, como apresentado na demonstracao dos fluxos de caixa."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = entrada de caixa",
        boundary_notes=(
            "A convencao de sinal aqui e o oposto da de despesa, e a inversao e a "
            "fonte de erro mais provavel num mapeamento novo: um consumo de caixa "
            "e NEGATIVO. Regime que publique com sinal invertido normaliza no "
            "`sign` do binding, nunca aqui.",
            "E o total DEPOIS de juros e impostos pagos, quando a companhia os "
            "classifica em operacional. A classificacao e escolha do emissor "
            "dentro do regime, e reclassificar seria refazer a demonstracao.",
        ),
    ),
    Concept(
        concept_id=CAPEX,
        label_en="Capital expenditure",
        definition=(
            "Caixa desembolsado na aquisicao de imobilizado e intangivel no "
            "periodo, como apresentado nas atividades de investimento."
        ),
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        sign_convention="positivo = saida de caixa",
        boundary_notes=(
            "POSITIVO = saida, ao contrario de `cash_flow_operating`. A escolha e "
            "deliberada: capex e uma grandeza que se soma e se compara em modulo, "
            "e uma convencao negativa faria toda formula que o usa carregar um "
            "sinal de menos que ninguem lembra de conferir. O regime que publica "
            "negativo normaliza com sign=-1 no binding.",
            "NAO inclui aquisicao de participacoes societarias nem investimento "
            "financeiro: aquilo e alocacao de capital de outra natureza, e "
            "misturar produziria um capex que nao se compara com depreciacao.",
            "Conteudo audiovisual capitalizado e caso de fronteira REAL e nao "
            "resolvido aqui: em alguns emissores ele e ativo intangivel e em "
            "outros e custo de receita. Onde a distincao importar, o mapeamento "
            "declara o que entrou, com `divergence_note`.",
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

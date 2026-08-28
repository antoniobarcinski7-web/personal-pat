"""O8 - valuation: quatro coisas que nunca se misturam.

    DATA           veio do motor. Tem procedencia, `as_of` e fidelidade.
    ASSUMPTION     alguem escolheu. Tem autor, base e justificativa.
    CALCULATION    aritmetica deterministica sobre as duas primeiras.
    INTERPRETATION prosa sobre o resultado. Nao entra em conta nenhuma.

A separacao nao e organizacional. Ela existe porque um DCF e a construcao mais
facil de mentir do mercado: o resultado depende quase inteiramente de tres ou
quatro premissas, e um relatorio que apresenta premissa e dado com a mesma
tipografia faz o leitor - inclusive o autor - tratar uma escolha como uma
observacao.

Aqui isso e impossivel por tipo. `Assumption` carrega `basis` e `author`
obrigatorios; `DataPoint` carrega `result_id`; e o `ValuationModel` recusa ser
construido se uma premissa nao tiver justificativa. O `ValuationResult`
carrega a lista das premissas que o produziram, na ordem em que pesam.

Por que o modelo nao "estima" nada
----------------------------------
Nao existe premissa default. Nem WACC de 10%, nem crescimento na perpetuidade
igual a inflacao, nem margem "conservadora". Um default aqui seria uma escolha
de investimento embutida numa biblioteca - e sairia no relatorio como se
alguem a tivesse feito, quando ninguem fez.

Isso torna o primeiro uso mais chato: e preciso declarar tudo. E o ponto.

Valuation reversa
-----------------
`implied_growth` responde a pergunta que uma tese honesta faz antes das
outras: "o que o preco de hoje ja esta assumindo?". Ela e a mesma aritmetica
resolvida para a outra incognita, e nao um modelo diferente - por isso mora
aqui e usa os mesmos contratos.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen
from pat.contracts.opportunity.base import Actor, Slug

__all__ = [
    "Assumption",
    "AssumptionBasis",
    "AssumptionChanged",
    "AssumptionSet",
    "CashFlowLine",
    "DataPoint",
    "SensitivityCell",
    "SensitivityGrid",
    "ValuationAssumptionSet",
    "ValuationDeclared",
    "ValuationInterpretation",
    "ValuationInterpreted",
    "ValuationModel",
    "ValuationResult",
    "ValuationUnavailable",
    "ValuationUnavailableReason",
]


class AssumptionBasis(StrEnum):
    """De onde veio a premissa. Obrigatorio em toda uma.

    O analogo exato de `DateBasis` no corpus e de `Fidelity` na metrica: uma
    escolha que se apresenta como leitura e o mesmo erro que uma data
    adivinhada que se apresenta como lida.
    """

    HISTORICAL = "historical"
    """Extrapolada de serie do motor. A serie esta citada em `derived_from`."""

    ISSUER_GUIDANCE = "issuer_guidance"
    """A companhia disse. E citacao, e por isso `derived_from` aponta para a
    unidade - o numero do emissor nunca vira insumo, mas a premissa DE QUE ele
    se realiza e uma escolha legitima, desde que assinada."""

    PEER = "peer"
    """De comparaveis. Quais, e por que sao comparaveis, vao em `rationale`."""

    JUDGMENT = "judgment"
    """Juizo do analista, sem ancora externa. E um valor legitimo - o que nao
    e legitimo e uma premissa de juizo que se apresenta como derivada."""

    MARKET = "market"
    """Observavel de mercado: taxa livre de risco, preco, divida liquida."""


class Assumption(Frozen):
    """Uma escolha, assinada.

    `rationale` e `basis` sao obrigatorios. Uma premissa sem justificativa e
    um numero que aparece no meio da conta e ninguem sabe defender - e num
    DCF ela costuma ser a que decide o resultado.
    """

    slug: Slug
    label: str = Field(min_length=1)
    value: Decimal
    unit: str = Field(
        min_length=1, description="'ratio' para taxas, ou o codigo da moeda"
    )
    basis: AssumptionBasis
    rationale: str = Field(min_length=1)
    author: Actor
    derived_from: tuple[str, ...] = Field(
        default=(), description="`result_id` ou `unit_id` que embasam, quando ha"
    )
    at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> "Assumption":
        precisa_ancora = (AssumptionBasis.HISTORICAL, AssumptionBasis.ISSUER_GUIDANCE)
        if self.basis in precisa_ancora and not self.derived_from:
            raise ValueError(
                f"premissa {self.slug} declara base {self.basis.value!r} e nao aponta "
                "para nada. Uma premissa que se diz derivada precisa dizer de que - "
                "senao ela e JUDGMENT com nome melhor."
            )
        if self.author is Actor.ENGINE:
            raise ValueError(
                f"premissa {self.slug} atribuida ao motor. O motor produz numero, "
                "nunca escolha; premissa e do analista ou do agente."
            )
        return self


class DataPoint(Frozen):
    """Um numero que veio do motor, com o endereco de onde veio.

    Distinto de `Assumption` no tipo, e nao por convencao de nome: e o que
    impede que um valor escolhido entre na conta pela porta dos dados.
    """

    slug: Slug
    label: str = Field(min_length=1)
    value: Decimal
    unit: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    period_end: date | None = None


class AssumptionSet(Frozen):
    """As premissas de um modelo, sem repeticao de slug."""

    assumptions: tuple[Assumption, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "AssumptionSet":
        slugs = [a.slug for a in self.assumptions]
        if len(set(slugs)) != len(slugs):
            raise ValueError(f"premissa repetida: {slugs}")
        return self

    def get(self, slug: str) -> Assumption | None:
        return next((a for a in self.assumptions if a.slug == slug), None)

    def value(self, slug: str) -> Decimal:
        premissa = self.get(slug)
        if premissa is None:
            raise KeyError(
                f"premissa {slug!r} nao foi declarada. Nao existe default nesta "
                "camada: um default seria uma escolha de investimento embutida numa "
                "biblioteca, e sairia no relatorio como se alguem a tivesse feito."
            )
        return premissa.value

    @property
    def judgment_only(self) -> tuple[Assumption, ...]:
        """As premissas sem ancora externa. A tese precisa dize-las em voz
        alta - sao elas que decidem o numero."""
        return tuple(a for a in self.assumptions if a.basis is AssumptionBasis.JUDGMENT)


# Alias com o nome que o milestone usa. Existe so para leitura; o tipo e um so.
ValuationAssumptionSet = AssumptionSet


class CashFlowLine(Frozen):
    """Um ano projetado. Cada campo e resultado de conta, nunca de escolha
    direta - as escolhas estao nas premissas que o geraram."""

    year: int = Field(ge=1, description="1 = primeiro ano projetado")
    period_end: date | None = None
    revenue: Decimal
    ebit: Decimal
    nopat: Decimal
    reinvestment: Decimal
    free_cash_flow: Decimal
    discount_factor: Decimal
    present_value: Decimal


class SensitivityCell(Frozen):
    row: Decimal
    column: Decimal
    equity_value: Decimal
    per_share: Decimal | None = None


class SensitivityGrid(Frozen):
    """A grade de sensibilidade. Duas premissas por vez, nomeadas.

    Nomear os eixos importa: uma grade sem rotulo e um bloco de numeros que o
    leitor interpreta como quiser, e a interpretacao mais comum e a mais
    favoravel.
    """

    row_assumption: Slug
    column_assumption: Slug
    cells: tuple[SensitivityCell, ...] = ()


class ValuationUnavailableReason(StrEnum):
    MISSING_ASSUMPTION = "missing_assumption"
    MISSING_DATA = "missing_data"
    MIXED_CURRENCY = "mixed_currency"
    """Insumos em moedas diferentes param a conta. Converter em silencio
    produziria um valor que parece certo."""

    NON_CONVERGENT = "non_convergent"
    """Crescimento na perpetuidade >= custo de capital. A formula de Gordon
    devolve valor negativo ou infinito, e nenhum dos dois e um preco."""


class ValuationUnavailable(Frozen):
    """A valuation nao sai, e o motivo tem nome.

    Mesma disciplina de `MetricUnavailable`: nunca zero, nunca parcial, nunca
    um numero "aproximado" para nao deixar a celula vazia.
    """

    reason: ValuationUnavailableReason
    message: str = Field(min_length=1)
    remedy: str = Field(min_length=1)
    missing: tuple[str, ...] = ()


class ValuationModel(Frozen):
    """O modelo declarado: dados, premissas e horizonte.

    Nao contem resultado. Separar o modelo do resultado e o que permite
    reexecutar o mesmo modelo com `as_of` diferente e comparar - e o que
    impede alguem de editar o resultado sem editar o que o produziu.
    """

    model_version: Literal["dcf/v1"] = "dcf/v1"
    slug: Slug
    currency: str = Field(min_length=3, max_length=3)
    horizon_years: int = Field(ge=1, le=20)
    data: tuple[DataPoint, ...] = ()
    assumptions: AssumptionSet = AssumptionSet()
    created_by: Actor
    at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> "ValuationModel":
        moedas = {d.unit for d in self.data if len(d.unit) == 3}
        if moedas - {self.currency}:
            raise ValueError(
                f"modelo em {self.currency} com dado em {sorted(moedas)}. Moeda nunca "
                "e convertida implicitamente."
            )
        return self

    def data_point(self, slug: str) -> DataPoint | None:
        return next((d for d in self.data if d.slug == slug), None)


class ValuationResult(Frozen):
    """O resultado, com tudo que o produziu junto.

    `assumptions_used` nao e conveniencia: e o que faz o numero ser lido com a
    incerteza certa. Um valor por acao apresentado sozinho e uma precisao que
    o modelo nao tem.
    """

    model_slug: Slug
    currency: str
    enterprise_value: Decimal
    equity_value: Decimal
    per_share: Decimal | None = None
    terminal_value: Decimal
    terminal_share: Decimal = Field(
        description=(
            "Fracao do EV que veio da perpetuidade. Acima de ~0,75 o modelo esta "
            "dizendo mais sobre a premissa terminal do que sobre a companhia"
        )
    )
    lines: tuple[CashFlowLine, ...] = ()
    assumptions_used: tuple[Slug, ...] = ()
    data_used: tuple[str, ...] = Field(
        default=(), description="`result_id` de todo dado que entrou"
    )
    computed_at: AwareDatetime

    @property
    def terminal_dominates(self) -> bool:
        return self.terminal_share > Decimal("0.75")


class ValuationInterpretation(Frozen):
    """Prosa sobre o resultado. Nao entra em conta nenhuma.

    Tipo proprio para que ninguem possa alimentar uma interpretacao de volta
    no modelo. E a mesma razao pela qual `QuoteClaim` nao tem `value`.
    """

    model_slug: Slug
    text: str = Field(min_length=1)
    author: Actor
    at: AwareDatetime


# -- eventos ----------------------------------------------------------------
#
# O que entra no diario e o MODELO, nunca o resultado. O resultado e derivado:
# `run_dcf` sobre o mesmo modelo devolve o mesmo bit, sempre. Gravar os dois
# criaria uma segunda fonte de verdade, e no dia em que divergissem - alguem
# edita uma premissa e esquece de recalcular - ninguem saberia qual esta certa.
#
# E a mesma razao pela qual o catalogo do PAT e derivado do bronze.


class ValuationDeclared(Frozen):
    kind: Literal["valuation_declared"] = "valuation_declared"
    model: ValuationModel


class AssumptionChanged(Frozen):
    """O analista troca uma premissa. O valor antigo continua no diario.

    Trocar premissa e a operacao mais comum de uma valuation, e e tambem onde
    uma tese se auto-ajusta ate dar o numero que o autor queria. O historico
    no diario e o que torna esse movimento visivel depois - nao proibido, mas
    visivel.
    """

    kind: Literal["assumption_changed"] = "assumption_changed"
    model: Slug
    assumption: Assumption


class ValuationInterpreted(Frozen):
    kind: Literal["valuation_interpreted"] = "valuation_interpreted"
    model: Slug
    text: str = Field(min_length=1)

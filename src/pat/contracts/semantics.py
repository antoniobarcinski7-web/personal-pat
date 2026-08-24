"""Contratos da camada semantica (L2). Universais e sem jurisdicao.

Nada neste modulo pode mencionar um regulador, um plano de contas ou um
codigo de conta especifico. Essa e a fronteira que permite que `ebitda@v1`
seja o *mesmo objeto* no Brasil e nos EUA: o que muda entre regimes e o
endereco de onde o numero vem, nunca a definicao do que ele significa.

Os tres eixos que a Fase 2 mantem separados
--------------------------------------------
CONCEITO   `revenue_net` - uma ideia economica. Nao tem pais nem taxonomia.
ENDERECO   `LineAddress` - onde essa ideia aparece numa taxonomia especifica.
           Opaco aqui dentro; so o adapter daquela taxonomia sabe le-lo.
REGIME     framework / jurisdicao / fonte - metadado do *mapeamento*, nunca
           da metrica.

A ponte entre conceito e endereco e o `ConceptBinding`, e ela e sempre uma
afirmacao humana explicita. O sistema nunca deduz que "Net Revenue",
"Revenue" e "Sales" sao a mesma coisa: alguem precisa dizer que sao, e dizer
por que. Dai `equivalence_basis` ser obrigatorio.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from pat.contracts.common import Frozen, PeriodType, Sha256


class Dimension(StrEnum):
    """O que a grandeza e, independente de moeda ou unidade local."""

    MONEY = "money"
    RATIO = "ratio"
    COUNT = "count"


class PeriodKind(StrEnum):
    """Fluxo (acumulado num intervalo) vs. saldo (pontual).

    Deliberadamente mais grosso que `PeriodType`: um conceito diz que e
    FLOW; o fato resolvido e que sera YEAR, QUARTER ou o que a fonte
    publicar. Assim o catalogo de conceitos nao precisa saber que o DFP e
    anual e o 10-Q e trimestral.
    """

    FLOW = "flow"
    STOCK = "stock"


class Fidelity(StrEnum):
    """Quao bem a linha da fonte representa o conceito.

    Existe porque a alternativa e pior: sem isso, um EBITDA montado sobre
    uma aproximacao sai indistinguivel de um exato, e a aproximacao vira
    invisivel tres camadas adiante. Fidelity propaga ate o `MetricResult` e
    e a coisa mais fraca de toda a cadeia que manda.
    """

    EXACT = "exact"
    APPROXIMATE = "approximate"
    PARTIAL = "partial"

    @property
    def rank(self) -> int:
        return {"exact": 0, "approximate": 1, "partial": 2}[self.value]


def weakest(fidelities: tuple[Fidelity, ...]) -> Fidelity:
    """A cadeia vale o seu elo mais fraco."""
    return max(fidelities, key=lambda f: f.rank) if fidelities else Fidelity.EXACT


class MetricKind(StrEnum):
    REPORTED = "reported"
    """Lido de um unico conceito, sem aritmetica. O numero existe na peca."""

    CALCULATED = "calculated"
    """Formula deterministica sobre fatos reportados."""

    INFERRED = "inferred"
    """Obtido por rota indireta ou identidade contabil. Mesmo tipo, epistemologia
    mais fraca. Nao implementado na Fase 2 - o membro existe para que ninguem
    o dobre dentro de CALCULATED depois."""

    ESTIMATED = "estimated"
    """Depende de premissa que nao esta na demonstracao. Exige `assumptions`."""


class ReportingScope(StrEnum):
    """Escopo da peca. Generaliza o individual/consolidado da CVM.

    A maioria dos regimes so publica consolidado; PARENT_ONLY existir aqui
    nao obriga nenhuma fonte a te-lo. Um resolver que nao suporta o escopo
    pedido responde SCOPE_NOT_AVAILABLE, em vez de devolver o outro escopo
    calado - que seria um numero errado com cara de certo.
    """

    CONSOLIDATED = "consolidated"
    PARENT_ONLY = "parent_only"


class StatementKind(StrEnum):
    """Familias de demonstracao, como superconjunto entre regimes.

    VALUE_ADDED (a DVA brasileira) nao tem equivalente americano, e tudo
    bem: o conjunto e uniao, nao intersecao.
    """

    INCOME = "income"
    BALANCE = "balance"
    CASH_FLOW = "cash_flow"
    EQUITY_CHANGES = "equity_changes"
    COMPREHENSIVE_INCOME = "comprehensive_income"
    VALUE_ADDED = "value_added"


class TaxonomyId(StrEnum):
    """Taxonomia de relatorio que um endereco enderaca.

    Distinta de framework contabil e de fonte: a CVM publica IFRS/CPC no
    plano padronizado dela; a mesma empresa poderia publicar IFRS num ESEF
    com outra taxonomia inteiramente.
    """

    CVM_PLANO_PADRONIZADO = "cvm.plano_padronizado"
    US_GAAP_XBRL = "us-gaap.xbrl"
    """Reservado. Nada implementa esta taxonomia na Fase 2."""


class AccountingFramework(StrEnum):
    IFRS_CPC_BR = "ifrs_cpc_br"
    US_GAAP = "us_gaap"
    IFRS_IASB = "ifrs_iasb"


class UnavailableReason(StrEnum):
    NO_MAPPING = "no_mapping"
    MISSING_CONCEPT = "missing_concept"
    """O mapeamento nao liga este conceito a nenhuma linha."""

    MISSING_FACT_AS_OF = "missing_fact_as_of"
    """A linha existe no mapeamento, mas nada era conhecido naquela data.
    Resposta legitima, e diferente de buraco de dado."""

    WRONG_PERIOD_TYPE = "wrong_period_type"
    SCOPE_NOT_AVAILABLE = "scope_not_available"
    MIXED_CURRENCY = "mixed_currency"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    DIVISION_BY_ZERO = "division_by_zero"
    UNKNOWN_ENTITY = "unknown_entity"


# ---------------------------------------------------------------------------
# Conceito: a ideia economica, sem regime
# ---------------------------------------------------------------------------


class Concept(Frozen):
    """Um slot semantico universal.

    `concept_id` e imutavel para sempre. Um significado refinado vira um
    conceito *novo*, nunca `revenue_net@v2`: isso mantem um unico eixo de
    versao no sistema (o das metricas) e garante que um mapeamento escrito
    hoje continue querendo dizer o que dizia.
    """

    concept_id: str
    label_en: str = Field(description="Rotulo neutro. NUNCA usado para busca.")
    definition: str = Field(description="O que e, em prosa, sem citar plano de contas")
    dimension: Dimension
    period_kind: PeriodKind
    sign_convention: str = Field(
        description="Ex. 'positivo = despesa', 'positivo = saida de caixa'. "
        "Fixa aqui porque a CVM publica custo negativo e o XBRL publica "
        "positivo com rotulo negado; sem convencao canonica, um mapeamento "
        "americano produziria EBITDA com sinal trocado."
    )
    boundary_notes: tuple[str, ...] = Field(
        default=(), description="O que esta dentro e o que esta fora do conceito"
    )


# ---------------------------------------------------------------------------
# Endereco: onde o conceito aparece numa taxonomia
# ---------------------------------------------------------------------------


class LineAddress(Frozen):
    """Endereco de uma linha dentro de uma taxonomia.

    `address` e opaco para esta camada: pares chave/valor ordenados, cujas
    chaves so o adapter registrado para `taxonomy` sabe interpretar. Uma
    taxonomia enderaca por codigo de conta hierarquico, outra por elemento de
    um dicionario XBRL; nenhuma das duas formas aparece aqui, e por isso
    plugar um regime novo nao toca contrato, conceito nem metrica.

    A ordenacao por chave e o que da identidade estavel ao endereco.
    """

    taxonomy: TaxonomyId
    address: tuple[tuple[str, str], ...]
    statement_kind: StatementKind
    label_as_reported: str | None = Field(
        default=None,
        description="Rotulo como publicado. Conferido no `mapping-check` para "
        "pegar renomeacao na origem; nunca e chave de busca.",
    )

    @model_validator(mode="after")
    def _check_address(self) -> "LineAddress":
        if not self.address:
            raise ValueError("endereco vazio")
        keys = [k for k, _ in self.address]
        if len(keys) != len(set(keys)):
            raise ValueError(f"chaves repetidas no endereco: {keys}")
        if list(self.address) != sorted(self.address):
            raise ValueError("endereco deve estar ordenado por chave (identidade estavel)")
        return self

    def as_str(self) -> str:
        inner = ",".join(f"{k}={v}" for k, v in self.address)
        return f"{self.taxonomy}[{inner}]"


class BoundLine(Frozen):
    address: LineAddress
    sign: int = Field(description="+1 ou -1: normaliza a fonte para a convencao do conceito")

    @model_validator(mode="after")
    def _check_sign(self) -> "BoundLine":
        if self.sign not in (1, -1):
            raise ValueError(f"sign deve ser +1 ou -1, recebido {self.sign}")
        return self


class ConceptBinding(Frozen):
    """A afirmacao de que estas linhas *sao* este conceito.

    Esta e a unica declaracao de equivalencia semantica no sistema. Nao ha
    casamento lexico em lugar nenhum: 'Receita', 'Net Revenue' e 'Sales' so
    sao o mesmo conceito se um humano escrever aqui que sao, e dizer por que.
    """

    concept_id: str
    lines: tuple[BoundLine, ...]
    fidelity: Fidelity
    equivalence_basis: str = Field(
        min_length=1, description="Por que esta linha e este conceito. Obrigatorio."
    )
    divergence_note: str | None = Field(
        default=None, description="Como diverge do conceito. Obrigatorio se fidelity != exact."
    )

    @model_validator(mode="after")
    def _check(self) -> "ConceptBinding":
        if not self.lines:
            raise ValueError(f"binding de {self.concept_id} sem nenhuma linha")
        if self.fidelity is not Fidelity.EXACT and not (self.divergence_note or "").strip():
            raise ValueError(
                f"binding de {self.concept_id} tem fidelity={self.fidelity} e precisa "
                "de divergence_note dizendo como diverge"
            )
        taxonomies = {line.address.taxonomy for line in self.lines}
        if len(taxonomies) > 1:
            raise ValueError(f"binding de {self.concept_id} mistura taxonomias: {taxonomies}")
        return self


class Mapping(Frozen):
    """Um conjunto de bindings, com o regime a que pertence como metadado.

    Framework, taxonomia, jurisdicao e fonte vivem *aqui*, nunca na metrica.
    """

    mapping_id: str
    mapping_version: str
    framework: AccountingFramework
    taxonomy: TaxonomyId
    jurisdiction: str = Field(description="ISO 3166-1 alpha-2")
    source: str = Field(description="dataset_id da fonte, ex. 'cvm.dfp'")
    source_sha256: Sha256 = Field(description="Hash do arquivo. Entra no resultado (I3).")

    parent: str | None = Field(default=None, description="mapping_id herdado")
    entity_id: str | None = Field(
        default=None, description="Preenchido em mapeamento de empresa; nulo em familia"
    )
    is_default_for_source: bool = Field(
        default=False, description="Familia usada quando a empresa nao tem mapeamento proprio"
    )

    verified_by: str | None = None
    verified_against: str | None = Field(
        default=None, description="Documento contra o qual um humano conferiu"
    )

    bindings: tuple[ConceptBinding, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "Mapping":
        ids = [b.concept_id for b in self.bindings]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"{self.mapping_id}: conceito ligado duas vezes: {dupes}")
        if self.entity_id and self.is_default_for_source:
            raise ValueError(
                f"{self.mapping_id}: mapeamento de empresa nao pode ser default de fonte"
            )
        return self

    def binding_for(self, concept_id: str) -> ConceptBinding | None:
        for binding in self.bindings:
            if binding.concept_id == concept_id:
                return binding
        return None


# ---------------------------------------------------------------------------
# Metrica
# ---------------------------------------------------------------------------


class MetricRef(Frozen):
    """Referencia pinada: `ebitda@v1`.

    Dependencia sempre pinada por versao. Publicar `ebit@v2` deixa
    `ebitda@v1` bit a bit identico - e assim que analise antiga continua
    reproduzindo.
    """

    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"

    @staticmethod
    def parse(raw: str) -> "MetricRef":
        name, _, version = raw.partition("@")
        if not name or not version:
            raise ValueError(f"referencia de metrica invalida: {raw!r} (esperado 'nome@versao')")
        return MetricRef(name=name, version=version)


class ConsistencyCheck(Frozen):
    """Conferencia declarada de uma definicao.

    Falhar nao suprime o valor: expoe. Um numero que nao fecha com a propria
    demonstracao e informacao, nao motivo para calar a resposta.
    """

    check_id: str
    description: str
    tolerance_abs: Decimal = Field(
        description="Tolerancia absoluta, na moeda do fato. Explicita por check - "
        "nao existe epsilon global."
    )
    tolerance_rel: Decimal | None = Field(
        default=None,
        description="Tolerancia relativa (fracao, ex. 0.05). Necessaria quando o "
        "check compara duas grandezas cuja diferenca escala com o tamanho da "
        "empresa; tolerancia absoluta sozinha nao se transporta entre companhias.",
    )


class MetricDefinition(Frozen):
    """Os dados de uma metrica. A funcao de calculo vive no registro, nao aqui:
    `contracts/` permanece so contrato, sem comportamento."""

    name: str
    version: str
    kind: MetricKind
    dimension: Dimension
    period_kind: PeriodKind

    requires_concepts: tuple[str, ...] = ()
    requires_metrics: tuple[MetricRef, ...] = ()
    optional_concepts: tuple[str, ...] = Field(
        default=(), description="Usados so por checks; ausencia nao torna a metrica indisponivel"
    )

    definition: str = Field(min_length=1, description="A definicao em uma frase")
    rationale: str = Field(min_length=1, description="Por que esta escolha e nao a alternativa")
    checks: tuple[ConsistencyCheck, ...] = ()
    assumptions: tuple[str, ...] = ()

    @property
    def ref(self) -> MetricRef:
        return MetricRef(name=self.name, version=self.version)

    @model_validator(mode="after")
    def _check(self) -> "MetricDefinition":
        if self.kind is MetricKind.ESTIMATED and not self.assumptions:
            raise ValueError(f"{self.name}@{self.version}: metrica ESTIMATED exige `assumptions`")
        if not self.requires_concepts and not self.requires_metrics:
            raise ValueError(f"{self.name}@{self.version}: metrica sem nenhum insumo")
        if self.kind is MetricKind.REPORTED and (
            len(self.requires_concepts) != 1 or self.requires_metrics
        ):
            raise ValueError(
                f"{self.name}@{self.version}: REPORTED e leitura de exatamente um "
                "conceito, sem aritmetica"
            )
        return self


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


class InputRef(Frozen):
    """Um insumo usado, com o caminho de volta ate o byte de origem."""

    role: str = Field(description="concept_id, ou 'nome@versao' se for metrica")
    is_metric: bool
    value: Decimal
    fidelity: Fidelity

    # Preenchidos quando o insumo e um fato:
    fact_id: str | None = None
    address: str | None = None
    sign_applied: int | None = None
    currency: str | None = None
    knowledge_date: date | None = None
    locator: str | None = None


class CheckOutcome(Frozen):
    check_id: str
    status: str = Field(description="pass | fail | skipped_missing")
    observed: Decimal | None = None
    expected: Decimal | None = None
    tolerance_abs: Decimal | None = None


class MetricResult(Frozen):
    """Um numero, e tudo que e preciso para nao ter que confiar nele."""

    metric: str
    metric_version: str
    kind: MetricKind

    entity_id: str
    local_ids: tuple[tuple[str, str], ...] = Field(
        default=(),
        description="Identificadores da companhia no regime de origem, para exibicao. "
        "Pares (nome, valor); quais nomes existem e assunto do resolver.",
    )
    display_name: str | None = None

    scope: ReportingScope
    period_type: PeriodType
    period_start: date | None
    period_end: date
    as_of: date
    knowledge_date: date = Field(description="max(knowledge_date) dos insumos")

    value: Decimal
    dimension: Dimension
    currency: str | None = Field(default=None, description="Nulo quando dimension != money")

    fidelity: Fidelity = Field(
        description="A mais fraca de toda a cadeia de insumos. Um EBITDA montado "
        "sobre binding aproximado diz isso, em todo relatorio, para sempre."
    )
    inputs: tuple[InputRef, ...]
    checks: tuple[CheckOutcome, ...] = ()

    mapping_id: str
    mapping_version: str
    mapping_sha256: Sha256
    mapping_confirmed: bool = Field(
        description="False quando caiu na familia default, sem mapeamento conferido "
        "para esta empresa"
    )

    framework: AccountingFramework
    jurisdiction: str
    pat_version: str
    git_sha: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "MetricResult":
        if self.dimension is Dimension.MONEY and not self.currency:
            raise ValueError(f"{self.metric}: metrica monetaria sem moeda")
        if self.dimension is not Dimension.MONEY and self.currency:
            raise ValueError(f"{self.metric}: dimension={self.dimension} nao carrega moeda")
        if self.knowledge_date < self.period_end:
            raise ValueError("knowledge_date anterior ao fim do periodo")
        if self.knowledge_date > self.as_of:
            raise ValueError("resultado usa insumo desconhecido na data consultada")
        return self


class MetricUnavailable(Frozen):
    """A resposta honesta quando nao da para calcular.

    Nunca zero, nunca parcial, nunca None que se le como zero. Carrega o
    suficiente para consertar o mapeamento.
    """

    metric: str
    metric_version: str
    reason: UnavailableReason
    message: str
    entity_id: str
    period_end: date
    as_of: date
    scope: ReportingScope

    concept_id: str | None = None
    tried: tuple[str, ...] = Field(default=(), description="Enderecos tentados, legiveis")
    remedy: str | None = None

    @property
    def value(self) -> Decimal:
        raise MetricNotAvailableError(self)


class ConceptUnavailable(Frozen):
    """O analogo de `MetricUnavailable` um nivel abaixo.

    Existe porque a decomposicao da Fase 5 le CONCEITOS, e nao metricas: os
    termos de uma identidade contabil (`gross_profit`, `cogs`) nunca foram
    metricas registradas, e promove-los a metrica so para poder le-los criaria
    definicoes cujo unico proposito seria contornar a falta deste tipo.

    Mesma disciplina de sempre: motivo nomeado, conceito que faltou, enderecos
    tentados e o que fazer. Nunca zero, nunca parcial.
    """

    reason: UnavailableReason
    message: str
    concept_id: str
    tried: tuple[str, ...] = Field(default=(), description="Enderecos tentados, legiveis")
    remedy: str | None = None


class MetricNotAvailableError(RuntimeError):
    def __init__(self, unavailable: MetricUnavailable) -> None:
        self.unavailable = unavailable
        super().__init__(
            f"{unavailable.metric}@{unavailable.metric_version} indisponivel "
            f"({unavailable.reason}): {unavailable.message}"
        )

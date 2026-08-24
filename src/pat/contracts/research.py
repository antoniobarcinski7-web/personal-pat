"""Contratos da camada de pesquisa (L3). Universais e sem LLM.

Nada neste modulo importa `pat.semantics.*`, `pat.query`, `pat.store` ou
`pat.research.*`. Ele so conhece `contracts.common` e `contracts.semantics` -
e e isso que permite que a gramatica de plano seja verificavel sem banco,
sem rede e sem modelo.

A propriedade que este arquivo existe para garantir
---------------------------------------------------
Nao ha nenhum no da gramatica onde caiba um numero literal. `MetricStep`
aponta para uma metrica registrada; `DerivationStep` aponta para passos
anteriores. Um plano nao consegue *expressar* "receita = 19 bilhoes", entao
um planejador - humano ou modelo - nao consegue injetar um valor por essa
porta. Nao e uma regra de prompt: e a forma do tipo.

Consequencia pratica: quem quiser afrouxar isso vai ter que adicionar um
campo aqui, num arquivo que so tem contrato, e o diff vai aparecer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen, Sha256, SourceTier
from pat.contracts.semantics import (
    Dimension,
    Fidelity,
    MetricRef,
    MetricResult,
    MetricUnavailable,
    PeriodKind,
    ReportingScope,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OutputKind(StrEnum):
    """O formato que a pergunta pede. Nao muda o calculo - muda a montagem."""

    NUMBER = "number"
    SERIES = "series"
    COMPARISON = "comparison"
    NARRATIVE = "narrative"


class DerivationOp(StrEnum):
    """Conjunto fechado de operacoes sobre resultados ja calculados.

    Fechado de proposito: e este conjunto que substitui a geracao de codigo.
    Enquanto o espaco de computacao couber aqui, um plano pode ser validado
    exaustivamente antes de rodar - coisa que nenhum sandbox faz por codigo
    arbitrario.
    """

    DELTA = "delta"
    DELTA_PCT = "delta_pct"
    RATIO = "ratio"
    CAGR = "cagr"
    MIN = "min"
    MAX = "max"
    MEAN = "mean"


class UnresolvedKind(StrEnum):
    """Por que o planejador nao conseguiu fechar o plano.

    Existe para que ambiguidade vire recusa nomeada, e nunca um chute.
    """

    AMBIGUOUS_ENTITY = "ambiguous_entity"
    AMBIGUOUS_PERIOD = "ambiguous_period"
    AMBIGUOUS_SCOPE = "ambiguous_scope"
    UNSUPPORTED_METRIC = "unsupported_metric"
    UNSUPPORTED_QUESTION = "unsupported_question"


class ViolationCode(StrEnum):
    """Falhas estruturais de um plano. Detectadas sem banco e sem modelo."""

    EMPTY_PLAN = "empty_plan"
    DUPLICATE_STEP_ID = "duplicate_step_id"
    BAD_STEP_ID = "bad_step_id"
    UNKNOWN_METRIC = "unknown_metric"
    BAD_METRIC_REF = "bad_metric_ref"
    DANGLING_INPUT = "dangling_input"
    FORWARD_REFERENCE = "forward_reference"
    PLAN_CYCLE = "plan_cycle"
    NO_OUTPUT = "no_output"
    DANGLING_OUTPUT = "dangling_output"
    ARITY = "arity"
    MISSING_PARAM = "missing_param"
    BAD_PARAM = "bad_param"
    DIMENSION_MISMATCH = "dimension_mismatch"
    PERIOD_AFTER_AS_OF = "period_after_as_of"
    AS_OF_MISMATCH = "as_of_mismatch"
    QUESTION_ID_MISMATCH = "question_id_mismatch"
    PIN_CONTRADICTED_ENTITY = "pin_contradicted_entity"
    PIN_CONTRADICTED_PERIOD = "pin_contradicted_period"
    PIN_CONTRADICTED_SCOPE = "pin_contradicted_scope"
    PLAN_NOT_EXECUTABLE = "plan_not_executable"
    PLAN_TOO_LARGE = "plan_too_large"


class ResolutionCode(StrEnum):
    """Falhas que so o warehouse sabe responder."""

    UNKNOWN_ENTITY = "unknown_entity"
    PERIOD_NOT_COVERED = "period_not_covered"
    SCOPE_NOT_COVERED = "scope_not_covered"
    NO_MAPPING = "no_mapping"
    UNCONFIRMED_MAPPING = "unconfirmed_mapping"


class DerivationFailureReason(StrEnum):
    """Recusas da camada de derivacao.

    Enum proprio, e nao membros novos em `UnavailableReason`: o motivo de uma
    metrica nao existir e assunto da camada semantica; o motivo de uma conta
    entre metricas nao existir e assunto daqui.
    """

    ZERO_DENOMINATOR = "zero_denominator"
    NON_POSITIVE_CAGR_BASE = "non_positive_cagr_base"
    DIMENSION_MISMATCH = "dimension_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    INPUT_FAILED = "input_failed"
    ARITY = "arity"


class ResultKind(StrEnum):
    METRIC = "metric"
    DERIVED = "derived"


class WarningKind(StrEnum):
    """Ressalvas que acompanham um numero correto.

    Nenhuma delas suprime valor. Todas viajam ate a resposta.
    """

    APPROXIMATE_FIDELITY = "approximate_fidelity"
    UNCONFIRMED_MAPPING = "unconfirmed_mapping"
    CHECK_FAILED = "check_failed"
    PERIOD_MISSING = "period_missing"
    RESTATED_SINCE = "restated_since"


# ---------------------------------------------------------------------------
# Pergunta
# ---------------------------------------------------------------------------


class ResearchConstraints(Frozen):
    """O que o usuario aceita como resposta.

    Nao existe - e nao deve passar a existir - flag de conversao cambial nem
    equivalente de `allow_missing`. As quatro que existem escolhem entre
    *recusar* e *avisar*; nenhuma escolhe entre responder e inventar.
    """

    min_source_tier: SourceTier = SourceTier.PRIMARY_OFFICIAL
    max_fidelity: Fidelity = Fidelity.APPROXIMATE
    """Fidelidade mais fraca aceitavel. `EXACT` recusaria toda companhia que
    depende da familia default, que hoje sao quase todas."""

    allow_unconfirmed_mapping: bool = False
    """Default False: sem mapeamento conferido para a empresa, recusa. Explorar
    sobre a familia default e razoavel; publicar sem saber que caiu nela, nao."""

    allow_failed_checks: bool = True
    """True nao esconde nada - o check falho vira warning de qualquer jeito.
    Decide apenas se ele *bloqueia* a resposta."""


class ResearchQuestion(Frozen):
    """O que o usuario perguntou, com o que ele fixou.

    Os `pinned_*` sao soberanos: o planejador pode completar o que falta,
    nunca contradizer o que foi fixado. Um plano que contradiz um pin e
    rejeitado, e nao negociado.
    """

    question_version: Literal["v1"] = "v1"
    text: str = Field(min_length=1)
    as_of: date = Field(
        description="Obrigatorio, sem default. A mesma regra do `Engine.compute`: "
        "nao existe consulta que devolva 'o valor' sem dizer segundo quando."
    )
    asked_at: AwareDatetime

    pinned_entities: tuple[str, ...] = ()
    pinned_periods: tuple[date, ...] = ()
    pinned_scope: ReportingScope | None = None

    requested_output: OutputKind
    constraints: ResearchConstraints = ResearchConstraints()

    @model_validator(mode="after")
    def _check(self) -> "ResearchQuestion":
        if not self.text.strip():
            raise ValueError("pergunta vazia")
        for field_name in ("pinned_entities", "pinned_periods"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} tem repeticoes: {values}")
            if list(values) != sorted(values):
                raise ValueError(f"{field_name} deve estar ordenado (identidade estavel)")
        for period in self.pinned_periods:
            if period > self.as_of:
                raise ValueError(
                    f"periodo fixado {period} e posterior ao as_of {self.as_of}: "
                    "nao havia como saber dele naquela data"
                )
        return self


# ---------------------------------------------------------------------------
# Capability: o que o sistema sabe fazer
# ---------------------------------------------------------------------------


class ConceptCard(Frozen):
    concept_id: str
    label_en: str
    definition: str
    dimension: Dimension
    period_kind: PeriodKind


class MetricCard(Frozen):
    ref: str = Field(description="'nome@versao'")
    kind: str
    dimension: Dimension
    period_kind: PeriodKind
    definition: str
    requires_concepts: tuple[str, ...] = ()
    requires_metrics: tuple[str, ...] = ()


class MappingCard(Frozen):
    """O que o mapeamento cobre - nunca *onde*.

    Carrega `concept_id`s e fidelidade, jamais `LineAddress`. O planejador nao
    precisa saber em que codigo de conta a receita mora, e conta-lo seria o
    primeiro passo de volta para casamento por rotulo.
    """

    mapping_id: str
    entity_id: str | None = None
    framework: str
    jurisdiction: str
    source: str
    is_default_for_source: bool = False
    bound_concepts: tuple[str, ...] = ()
    weakest_fidelity: Fidelity


class EntityCard(Frozen):
    """Quais periodos e escopos existem - nunca que valores existem."""

    entity_id: str
    cod_cvm: int | None = None
    denom_cia: str | None = None
    period_ends: tuple[date, ...] = ()
    scopes: tuple[ReportingScope, ...] = ()
    has_own_mapping: bool = False


class DerivationCard(Frozen):
    op: DerivationOp
    arity: str = Field(description="'2' ou '>=2'")
    output_dimension: str
    refusals: tuple[str, ...] = ()


class SnapshotLimits(Frozen):
    max_steps: int = 32
    max_periods_per_entity: int = 12
    max_entities: int = 4
    max_serialized_bytes: int = 65_536


class CapabilitySnapshot(Frozen):
    """Retrato deterministico e hashavel do que o sistema pode executar.

    `built_at` fica de fora do hash: um sistema inalterado tem que produzir o
    mesmo `capability_sha256` em toda invocacao, senao o campo no manifesto
    nao significa nada.
    """

    snapshot_version: Literal["v1"] = "v1"
    built_at: AwareDatetime
    concepts: tuple[ConceptCard, ...] = ()
    metrics: tuple[MetricCard, ...] = ()
    mappings: tuple[MappingCard, ...] = ()
    entities: tuple[EntityCard, ...] = ()
    scopes: tuple[ReportingScope, ...] = ()
    derivations: tuple[DerivationCard, ...] = ()
    limits: SnapshotLimits = SnapshotLimits()


# ---------------------------------------------------------------------------
# Plano
# ---------------------------------------------------------------------------

STEP_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class DerivationParams(Frozen):
    """Parametros de derivacao. Fechado - so o que alguma operacao usa."""

    years: int | None = None


class MetricStep(Frozen):
    """Le uma metrica registrada. Nao carrega `scope` nem `as_of`: eles vivem
    no plano e descem inalterados, exatamente como no motor da Fase 2."""

    step_kind: Literal["metric"] = "metric"
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    metric: MetricRef
    entity_id: str = Field(min_length=1)
    period_end: date


class DerivationStep(Frozen):
    """Conta sobre passos anteriores. `inputs` sao `step_id`s, nunca valores."""

    step_kind: Literal["derivation"] = "derivation"
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    op: DerivationOp
    inputs: tuple[str, ...] = Field(min_length=1)
    params: DerivationParams = DerivationParams()


PlanStep = Annotated[MetricStep | DerivationStep, Field(discriminator="step_kind")]


class UnresolvedItem(Frozen):
    kind: UnresolvedKind
    detail: str = Field(min_length=1)
    candidates: tuple[str, ...] = ()


class ResearchPlan(Frozen):
    """O programa. Declarativo, fechado e conferivel inteiro antes de rodar.

    Um plano ou CALCULA alguma coisa ou DEVOLVE uma duvida - e a gramatica
    precisa saber expressar as duas.

    Ate a M4.1 ela nao sabia: `steps` e `outputs` exigiam pelo menos um item, e
    portanto recusar era impossivel de escrever. O planejador que quisesse dizer
    "esta pergunta e ambigua" era obrigado a inventar um passo qualquer so para
    o plano ser aceito, e quem fizesse a coisa certa - devolver `unresolved` com
    zero passos - tinha a recusa rejeitada como violacao de contrato. O sintoma
    chegava ao usuario como "o modelo nao produziu uma saida valida", que culpa
    o modelo exatamente quando ele acertou, e joga fora os candidatos que ele
    ofereceu para desambiguar.

    A regra agora e condicional, e continua fechada nos dois lados: sem
    `unresolved`, o plano tem que ter passo e saida (nao existe plano executavel
    vazio); com `unresolved`, pode nao ter nenhum dos dois (uma duvida nao
    calcula nada). O que nao pode e o meio-termo silencioso.
    """

    plan_version: Literal["v1"] = "v1"
    question_id: Sha256
    objective: str = Field(min_length=1)
    as_of: date
    scope: ReportingScope = Field(
        description="Explicito, sem default. Um numero no escopo errado parece certo."
    )
    steps: tuple[PlanStep, ...] = ()
    outputs: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    unresolved: tuple[UnresolvedItem, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "ResearchPlan":
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"step_id repetido: {dupes}")

        # A condicao que substitui os dois `min_length=1`. Note que ela nao
        # afrouxa nada para o plano que pretende executar: sem pendencia
        # declarada, plano vazio continua sendo erro na fronteira de entrada, e
        # nao tres camadas adiante como uma resposta sem numero.
        if not self.unresolved:
            if not self.steps:
                raise ValueError(
                    "plano sem passos e sem pendencia declarada: um plano ou calcula "
                    "alguma coisa ou diz por que nao consegue. Para recusar, preencha "
                    "`unresolved`."
                )
            if not self.outputs:
                raise ValueError(
                    "plano com passos e sem saidas: nada seria apresentado. Declare "
                    "quais passos sao resposta, ou recuse por `unresolved`."
                )
        return self

    @property
    def is_refusal(self) -> bool:
        """Plano que devolve duvida em vez de calculo.

        Existe para que quem le nao precise deduzir a intencao de um `steps`
        vazio - deducao que, em dois lugares diferentes, viraria duas regras
        diferentes.
        """
        return bool(self.unresolved) and not self.steps

    def step(self, step_id: str) -> PlanStep | None:
        for candidate in self.steps:
            if candidate.step_id == step_id:
                return candidate
        return None


class PlanEnvelope(Frozen):
    """O que um arquivo de plano contem.

    Pergunta e plano juntos, e nao o plano sozinho: `question_id` so pode ser
    conferido contra a pergunta que o gerou. Um plano orfao obrigaria a
    acreditar no hash que ele mesmo declara.
    """

    envelope_version: Literal["v1"] = "v1"
    question: "ResearchQuestion"
    plan: ResearchPlan


class PlanViolation(Frozen):
    """Uma falha estrutural, com onde e o que fazer."""

    code: ViolationCode
    message: str = Field(min_length=1)
    step_id: str | None = None
    remedy: str | None = None


class ResolutionIssue(Frozen):
    """Uma falha dependente do warehouse."""

    code: ResolutionCode
    message: str = Field(min_length=1)
    step_id: str | None = None
    entity_id: str | None = None
    remedy: str | None = None


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


class DerivedValue(Frozen):
    """Um numero produzido por conta entre resultados, com o caminho de volta.

    `derived_from` e obrigatorio e nao vazio: um valor derivado que nao aponta
    para os pais e indistinguivel de um valor inventado.
    """

    op: DerivationOp
    value: Decimal
    dimension: Dimension
    currency: str | None = None
    derived_from: tuple[Sha256, ...] = Field(min_length=1)
    fidelity: Fidelity
    knowledge_date: date
    period_labels: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "DerivedValue":
        if self.dimension is Dimension.MONEY and not self.currency:
            raise ValueError(f"derivacao {self.op} monetaria sem moeda")
        if self.dimension is not Dimension.MONEY and self.currency:
            raise ValueError(f"derivacao {self.op} com dimension={self.dimension} nao leva moeda")
        if not self.value.is_finite():
            raise ValueError(
                f"derivacao {self.op} produziu valor nao finito ({self.value}). "
                "NaN e infinito nunca podem sair desta camada."
            )
        return self


class ComputationResult(Frozen):
    """Um numero do plano: ou uma metrica da Fase 2, ou uma conta sobre elas.

    Exatamente um dos dois campos e preenchido. Nao existe terceira forma, e
    portanto nao existe forma que um numero fabricado possa assumir.
    """

    result_id: Sha256
    step_id: str
    kind: ResultKind
    metric_result: MetricResult | None = None
    derived: DerivedValue | None = None

    @model_validator(mode="after")
    def _check(self) -> "ComputationResult":
        has_metric = self.metric_result is not None
        has_derived = self.derived is not None
        if has_metric == has_derived:
            raise ValueError(
                f"{self.step_id}: um ComputationResult carrega exatamente um de "
                "`metric_result` ou `derived`"
            )
        if (self.kind is ResultKind.METRIC) != has_metric:
            raise ValueError(f"{self.step_id}: kind={self.kind} nao bate com o campo preenchido")
        return self

    @property
    def value(self) -> Decimal:
        return self.metric_result.value if self.metric_result else self.derived.value

    @property
    def dimension(self) -> Dimension:
        return self.metric_result.dimension if self.metric_result else self.derived.dimension

    @property
    def currency(self) -> str | None:
        return self.metric_result.currency if self.metric_result else self.derived.currency

    @property
    def fidelity(self) -> Fidelity:
        return self.metric_result.fidelity if self.metric_result else self.derived.fidelity

    @property
    def knowledge_date(self) -> date:
        return (
            self.metric_result.knowledge_date
            if self.metric_result
            else self.derived.knowledge_date
        )


class ComputationFailure(Frozen):
    """Por que um passo nao virou numero.

    Quando o passo era metrica, o `MetricUnavailable` da Fase 2 viaja inteiro
    aqui dentro: motivo, conceito que faltou, enderecos tentados e remedio
    chegam a linha de comando sem reescrita.
    """

    step_id: str
    reason: DerivationFailureReason
    message: str = Field(min_length=1)
    remedy: str | None = None
    unavailable: MetricUnavailable | None = None


# ---------------------------------------------------------------------------
# Afirmacoes
# ---------------------------------------------------------------------------


class NumericClaim(Frozen):
    """Um numero, com token de substituicao e o resultado que o produziu.

    `rendered_value` so pode vir de `research/render.py`. Esta e a unica
    classe do sistema com lugar para guardar um numero apresentavel.
    """

    claim_kind: Literal["numeric"] = "numeric"
    token: str = Field(min_length=1)
    result_id: Sha256
    rendered_value: str = Field(min_length=1)
    unit: str | None = None
    means: str = Field(min_length=1, description="O que o numero e, em prosa, sem o valor")


class InterpretiveClaim(Frozen):
    """Uma leitura. Nao tem campo de valor, e nao deve ganhar um.

    E o que impede 'a margem caiu por alavancagem operacional' de virar
    indistinguivel de 'a margem foi 3,89%'.
    """

    claim_kind: Literal["interpretive"] = "interpretive"
    text: str = Field(min_length=1)
    supports: tuple[Sha256, ...] = Field(min_length=1)


Claim = Annotated[NumericClaim | InterpretiveClaim, Field(discriminator="claim_kind")]


class ResearchWarning(Frozen):
    kind: WarningKind
    message: str = Field(min_length=1)
    result_id: Sha256 | None = None


class ResearchAnswer(Frozen):
    question_id: Sha256
    plan_id: Sha256
    prose: str
    claims: tuple[Claim, ...] = ()
    warnings: tuple[ResearchWarning, ...] = ()
    manifest_id: Sha256


# ---------------------------------------------------------------------------
# Procedencia da execucao
# ---------------------------------------------------------------------------


class PlanProvenance(Frozen):
    """Quem escreveu o plano (ou o texto).

    Separado de `ResearchPlan` de proposito: nada daqui entra no `plan_id`.
    Dois modelos que produzam o mesmo plano produzem o mesmo hash.
    """

    model_id: str
    temperature: Decimal | None = None
    """`None` quando o PAT nao pediu override de amostragem - que e o caso
    normal. Nao e "zero implicito": e a afirmacao de que valeu a configuracao
    do adapter, identificada por `client_fingerprint`. O campo e opcional
    porque temperatura nao e conceito desta arquitetura - nenhum ramo do
    sistema le o valor, e a propria nota de `DEFAULT_TEMPERATURE` diz que a
    reprodutibilidade nao se apoia nele. Um valor nao-nulo aqui significa que
    foi pedido *e* aplicado: adapter que nao consegue honrar um override tem
    que falhar, nunca descartar em silencio."""
    max_tokens: int
    system_prompt_sha256: Sha256
    prompt_sha256: Sha256
    response_sha256: Sha256
    capability_sha256: Sha256
    called_at: AwareDatetime
    cached: bool = False
    client_fingerprint: str = Field(
        min_length=1,
        description="Quem respondeu e sob que configuracao de geracao, no "
        "formato <provider>/<adapter_version>/<config_sha8>",
    )
    """Opaco de proposito. Registrar `thinking_enabled` ou `budget_tokens` aqui
    poria vocabulario de um fornecedor no contrato universal - o mesmo erro que
    citar `cd_conta` em `concepts.py` seria na Fase 2 - e nao generalizaria:
    outro provider chama a mesma ideia de outro nome, ou nao a tem.

    Continua auditavel porque resolve: o `adapter_version` embutido cobre o
    perfil de geracao, que e codigo versionado sob a mesma disciplina de
    `extractor_version` e `metric_version`. Auditar uma chamada antiga e ler o
    fingerprint e o commit correspondente."""


class ResearchRunManifest(Frozen):
    """O suficiente para auditar - e, quando o plano esta salvo, reproduzir.

    `planner` e `writer` sao nulos no caminho deterministico: um plano lido de
    arquivo nao teve autor de modelo, e dizer isso e mais honesto do que
    inventar uma procedencia vazia.
    """

    manifest_id: Sha256
    question_id: Sha256
    plan_id: Sha256
    capability_sha256: Sha256

    planner: PlanProvenance | None = None
    writer: PlanProvenance | None = None

    as_of: date
    executed_at: AwareDatetime
    outputs_available: bool

    result_ids: tuple[Sha256, ...] = ()
    metric_versions: tuple[str, ...] = ()
    mapping_sha256s: tuple[Sha256, ...] = ()
    fact_ids: tuple[str, ...] = ()

    pat_version: str
    python_version: str
    git_sha: str | None = None

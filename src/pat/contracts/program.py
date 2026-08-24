"""Contratos do programa de pesquisa (Fase 5, M5.3).

Nada neste modulo importa implementacao. Ele conhece `contracts.common`,
`contracts.semantics`, `contracts.research`, `contracts.corpus` e
`contracts.decomposition` - e e isso que permite conferir a forma de um
programa sem banco, sem rede e sem modelo.

O que um programa e
-------------------
Um `ResearchPlan` responde "quanto foi". Um `ResearchProgram` responde "o que
precisa ser investigado":

    ResearchProgram
    ├── compute          metricas e derivacoes   (ResearchPlan v1, INTACTO)
    ├── decompositions   de onde a variacao veio (deterministico)
    └── evidence         o que a administracao disse (verbatim)

`compute` e um `ResearchPlan v1` sem uma virgula de alteracao. Isso e
deliberado e segue a disciplina do projeto: `ebitda@v1` nao muda quando sai
`ebit@v2`, e um plano salvo em disco na Fase 3 continua executando igual
depois da Fase 5. O que o programa faz e ENVOLVER o plano, nunca reescreve-lo.

O que continua inexprimivel
---------------------------
A propriedade central da Fase 3 sobrevive intacta: nenhum no desta gramatica
tem onde caber um numero literal. `DecompositionRequest` aponta para uma
identidade registrada; `EvidenceRequest` carrega termos de busca. Nenhum dos
dois tem campo `Decimal`, e `tests/research/test_contracts_program.py` confere
por AST.

Um programa tambem nao carrega RESPOSTA. Ele diz o que perguntar; o que foi
respondido vive em `ProgramResult`, que so o executor deterministico monta.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen, Sha256
from pat.contracts.corpus import (
    DocumentKind,
    EvidenceResult,
    EvidenceUnavailable,
)
from pat.contracts.decomposition import DecompositionResult, DecompositionUnavailable
from pat.contracts.research import (
    ComputationFailure,
    ComputationResult,
    PlanProvenance,
    ResearchPlan,
    ResearchQuestion,
    UnresolvedItem,
)
from pat.contracts.semantics import ReportingScope

REQUEST_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------


class DecompositionRequest(Frozen):
    """Abrir a variacao de um total entre dois periodos.

    Aponta para uma identidade REGISTRADA (`ebit_by_line@v1`), nunca para uma
    formula escrita aqui. Um programa nao consegue inventar uma decomposicao
    nova, do mesmo jeito que um plano nao consegue inventar uma metrica -
    quem define aritmetica e codigo versionado, nao texto de modelo.
    """

    request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    decomposition: str = Field(min_length=1, description="'nome@versao'")
    entity_id: str = Field(min_length=1)
    period_from: date
    period_to: date

    @model_validator(mode="after")
    def _check(self) -> "DecompositionRequest":
        if self.period_from >= self.period_to:
            raise ValueError(
                f"{self.request_id}: {self.period_from} nao e anterior a {self.period_to}; "
                "uma decomposicao abre uma VARIACAO"
            )
        if "@" not in self.decomposition:
            raise ValueError(
                f"{self.request_id}: {self.decomposition!r} sem versao. 'A mais recente' "
                "faria uma analise antiga mudar de significado."
            )
        return self


class EvidenceRequest(Frozen):
    """Buscar o que foi dito, no corpus, dentro do `as_of` do programa.

    Nao tem `as_of` proprio de proposito: ele vem do programa e desce
    inalterado. Um pedido com data propria permitiria que uma parte da
    resposta olhasse mais longe que a outra - o analogo textual de comparar
    dois `AS OF` diferentes na mesma frase.
    """

    request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    entity_id: str = Field(min_length=1)
    terms: tuple[str, ...] = Field(min_length=1)
    kinds: tuple[DocumentKind, ...] = ()
    published_from: date | None = None
    published_to: date | None = None
    limit: int = Field(default=5, ge=1, le=20)
    rationale: str = Field(
        min_length=1,
        description="Por que esta busca. Texto do planejador, conferivel por um humano.",
    )

    @model_validator(mode="after")
    def _check(self) -> "EvidenceRequest":
        if len(self.terms) != len(set(self.terms)):
            raise ValueError(f"{self.request_id}: termos repetidos")
        if not any(term.strip() for term in self.terms):
            raise ValueError(f"{self.request_id}: nenhum termo util")
        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_from > self.published_to
        ):
            raise ValueError(f"{self.request_id}: janela de publicacao invertida")
        return self


# ---------------------------------------------------------------------------
# Programa
# ---------------------------------------------------------------------------


class ResearchProgram(Frozen):
    """O que precisa ser investigado. Revisavel antes de qualquer execucao.

    Como o `ResearchPlan`, ele ou INVESTIGA alguma coisa ou DEVOLVE uma
    duvida. O meio-termo silencioso - programa vazio, sem pendencia declarada -
    e recusado na fronteira de entrada, e nao tres camadas adiante como uma
    resposta sem conteudo.
    """

    program_version: Literal["v1"] = "v1"
    question_id: Sha256
    objective: str = Field(min_length=1)
    as_of: date
    scope: ReportingScope = Field(
        description="Explicito, sem default. Um numero no escopo errado parece certo."
    )

    compute: ResearchPlan | None = None
    decompositions: tuple[DecompositionRequest, ...] = ()
    evidence: tuple[EvidenceRequest, ...] = ()

    questions_to_answer: tuple[str, ...] = ()
    """As perguntas que o programa pretende responder, em prosa.

    Sao do PLANEJADOR e servem ao humano que revisa: elas tornam auditavel a
    intencao, e nao so os passos. Nao entram em nenhum calculo e nao viram
    afirmacao - `ProgramResult` nao as copia para lugar nenhum."""

    unresolved: tuple[UnresolvedItem, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "ResearchProgram":
        for campo in ("decompositions", "evidence"):
            ids = [item.request_id for item in getattr(self, campo)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"request_id repetido em {campo}")

        faz_algo = bool(self.compute or self.decompositions or self.evidence)
        if not faz_algo and not self.unresolved:
            raise ValueError(
                "programa sem passo e sem pendencia declarada: ele ou investiga "
                "alguma coisa ou diz por que nao consegue. Para recusar, preencha "
                "`unresolved`."
            )

        if self.compute is not None:
            if self.compute.as_of != self.as_of:
                raise ValueError(
                    f"o plano de calculo esta em AS OF {self.compute.as_of} e o programa "
                    f"em {self.as_of}: duas visoes do mundo na mesma resposta"
                )
            if self.compute.scope != self.scope:
                raise ValueError(
                    f"o plano de calculo esta em escopo {self.compute.scope} e o programa "
                    f"em {self.scope}"
                )
            if self.compute.question_id != self.question_id:
                raise ValueError("o plano de calculo responde a outra pergunta")

        for pedido in self.evidence:
            for campo_data in ("published_from", "published_to"):
                valor = getattr(pedido, campo_data)
                if valor is not None and valor > self.as_of:
                    raise ValueError(
                        f"{pedido.request_id}: {campo_data}={valor} e posterior ao as_of "
                        f"{self.as_of}. Nao havia como saber daqueles documentos naquela data."
                    )
        return self

    @property
    def is_refusal(self) -> bool:
        return bool(self.unresolved) and not (
            self.compute or self.decompositions or self.evidence
        )


class ProgramEnvelope(Frozen):
    """O que um arquivo de programa contem: pergunta e programa juntos.

    Mesma razao do `PlanEnvelope`: `question_id` so pode ser conferido contra
    a pergunta que o gerou. Um programa orfao obrigaria a acreditar no hash
    que ele mesmo declara.
    """

    envelope_version: Literal["v1"] = "v1"
    question: ResearchQuestion
    program: ResearchProgram
    planner_compute: PlanProvenance | None = None
    planner_evidence: PlanProvenance | None = None
    """As DUAS procedencias do planejamento em dois estagios.

    Separadas, e nao somadas: sao duas chamadas, com prompts diferentes e
    entradas diferentes, e cada uma tem que ser auditavel por conta propria.
    Colapsa-las esconderia qual dos dois estagios produziu o que."""


# ---------------------------------------------------------------------------
# A forma de um resultado - o que o estagio 2 ve
# ---------------------------------------------------------------------------


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class Magnitude(StrEnum):
    """Faixa de magnitude, em degraus fixos definidos em codigo versionado.

    Faixa, e nao numero, porque este enum atravessa a fronteira do modelo. O
    estagio 2 do planejador precisa saber que a receita caiu MUITO para
    escolher o que procurar no corpus; ele nao precisa saber que caiu 4,13%, e
    se soubesse, um numero do motor teria chegado a um prompt.
    """

    NEGLIGIBLE = "negligible"
    SMALL = "small"
    MODERATE = "moderate"
    LARGE = "large"
    EXTREME = "extreme"


class ResultShape(Frozen):
    """O que um resultado PARECE, sem dizer quanto ele vale.

    Este e o tipo que atravessa a fronteira do modelo no estagio 2, e a razao
    de ele existir e a mesma de `ConversationContext` nao ter `Decimal`: se a
    forma pudesse carregar um valor, o numero de um estagio chegaria ao prompt
    do outro, e "todo numero vem do motor" deixaria de ser propriedade do tipo
    e viraria promessa de prompt.

    Nao ha campo `Decimal` aqui, e nao deve passar a haver. `top_contributors`
    carrega `member_id`s ORDENADOS - a ordem e a informacao, o valor nao entra.
    """

    request_id: str = Field(min_length=1)
    kind: Literal["metric", "derived", "decomposition", "evidence"]
    label: str = Field(min_length=1, description="O que o resultado e, em prosa, sem o valor")

    direction: Direction | None = None
    magnitude: Magnitude | None = None
    shape_version: str = Field(min_length=1)

    top_contributors: tuple[str, ...] = ()
    """Membros da decomposicao por magnitude de contribuicao, maior primeiro.

    So os identificadores. Saber que `operating_expenses_net` explicou mais do
    que `revenue_net` e o que permite ao estagio 2 procurar no corpus o que a
    administracao disse sobre despesa - e isso nao exige saber quanto."""

    residual_is_material: bool | None = None
    """A decomposicao NAO fechou. Vira ressalva na resposta, e e informacao
    que o estagio 2 deve poder usar para buscar explicacao."""

    hits: int | None = Field(default=None, ge=0, description="Quantos trechos, nunca quais")
    unavailable_reason: str | None = None


# ---------------------------------------------------------------------------
# Resultado do programa
# ---------------------------------------------------------------------------


class DecompositionOutcome(Frozen):
    """Um pedido de decomposicao, resolvido ou recusado. Nunca os dois."""

    request_id: str = Field(min_length=1)
    result: DecompositionResult | None = None
    unavailable: DecompositionUnavailable | None = None

    @model_validator(mode="after")
    def _check(self) -> "DecompositionOutcome":
        if (self.result is None) == (self.unavailable is None):
            raise ValueError(
                f"{self.request_id}: um resultado OU uma recusa nomeada, nunca ambos "
                "nem nenhum"
            )
        return self


class EvidenceOutcome(Frozen):
    """Um pedido de evidencia, resolvido ou recusado. Nunca os dois."""

    request_id: str = Field(min_length=1)
    result: EvidenceResult | None = None
    unavailable: EvidenceUnavailable | None = None

    @model_validator(mode="after")
    def _check(self) -> "EvidenceOutcome":
        if (self.result is None) == (self.unavailable is None):
            raise ValueError(
                f"{self.request_id}: um resultado OU uma recusa nomeada, nunca ambos "
                "nem nenhum"
            )
        return self


class ProgramResult(Frozen):
    """Tudo que a execucao do programa produziu - inclusive o que falhou.

    Uma execucao cujo calculo nao pode ser feito e registrada do mesmo jeito:
    a auditoria de por que NAO houve resposta vale tanto quanto a de por que
    houve. E a mesma decisao do `ResearchRunManifest` da Fase 3.
    """

    program_id: Sha256
    question_id: Sha256
    as_of: date
    executed_at: AwareDatetime

    computations: tuple[ComputationResult, ...] = ()
    computation_failures: tuple[ComputationFailure, ...] = ()
    decompositions: tuple[DecompositionOutcome, ...] = ()
    evidence: tuple[EvidenceOutcome, ...] = ()
    shapes: tuple[ResultShape, ...] = ()

    capability_sha256: Sha256
    index_version: str | None = None
    """Versao do indice lexico, quando houve busca de evidencia. `None` quando
    o programa nao pediu nenhuma - dizer isso e mais honesto do que gravar a
    versao de um indice que nao foi consultado."""

    @property
    def has_any_output(self) -> bool:
        return bool(
            self.computations
            or any(o.result is not None for o in self.decompositions)
            or any(o.result is not None for o in self.evidence)
        )

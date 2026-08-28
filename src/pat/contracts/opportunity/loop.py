"""O6 - o vocabulario do ciclo de pesquisa.

O ciclo e explicito e inspecionavel, e as oito fases tem nome porque cada uma
pode falhar de um jeito diferente:

    UNDERSTAND  o que esta tarefa quer estabelecer
    PLAN        que passos respondem isso, dentro do que o motor sabe fazer
    RESEARCH    executar - deterministico, sem modelo nenhum
    ANALYZE     interpretar os resultados, citando o que os sustenta
    TEST        as citacoes resolvem? o criterio de conclusao foi atendido?
    CRITICIZE   o que fica de pe sem ancora
    UPDATE      gravar no diario
    NEXT        proxima tarefa pronta

A fronteira que o modulo existe para manter
-------------------------------------------
Um `StepRequest` diz O QUE perguntar. Ele nao tem valor, nao tem unidade e nao
tem resultado. Um `StepOutcome` tem os IDs do que virou citavel e o motivo
quando nada virou - mas o numero em si continua morando no `MetricResult`, no
warehouse, com a procedencia inteira.

Um `FindingProposal` e a interpretacao, e ela cita por ID. A verificacao e
mecanica: todo ID citado tem que estar no que aquele passo de fato produziu.
E o ponto exato onde uma frase bem escrita e sem ancora e barrada - e ela
precisa ser barrada com registro, nunca descartada em silencio, porque uma
interpretacao rejeitada e informacao sobre o proprio raciocinio.

Por que `rationale` e obrigatorio no passo
------------------------------------------
Um plano de dez passos sem justificativa por passo e indistinguivel de dez
consultas por desencargo. `rationale` obriga a dizer por que AQUELE passo
responde AQUELA tarefa, e e o que permite, depois, ver que a tarefa foi
declarada concluida sem que nenhum passo tocasse no criterio dela.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from pat.contracts.common import Frozen
from pat.contracts.opportunity.agenda import TaskPriority, TaskStatus
from pat.contracts.opportunity.base import Slug

__all__ = [
    "FindingProposal",
    "HypothesisProposal",
    "Phase",
    "RejectedFinding",
    "RejectionReason",
    "StepKind",
    "StepOutcome",
    "StepRequest",
    "TaskProposal",
    "TaskReport",
    "TaskVerdict",
]


class Phase(StrEnum):
    UNDERSTAND = "understand"
    PLAN = "plan"
    RESEARCH = "research"
    ANALYZE = "analyze"
    TEST = "test"
    CRITICIZE = "criticize"
    UPDATE = "update"
    NEXT = "next"


class StepKind(StrEnum):
    """O que um passo pergunta. Gramatica fechada.

    Fechada de proposito: um passo "livre" seria uma consulta que ninguem
    consegue validar antes de rodar, e a validacao antes de rodar e o que
    impede o agente de inventar uma metrica.
    """

    METRIC = "metric"
    """Uma metrica em um ou mais periodos."""

    BREAKDOWN = "breakdown"
    """A variacao de um alvo entre dois periodos, aberta em componentes."""

    EVIDENCE = "evidence"
    """Trechos verbatim do corpus."""

    ACCOUNTS = "accounts"
    """O plano de contas efetivo. Para um humano escolher a linha - nunca para
    o agente casar conta por rotulo."""


class StepRequest(Frozen):
    """O QUE perguntar. Sem valor, sem unidade, sem resultado."""

    step_id: Slug
    kind: StepKind
    rationale: str = Field(
        min_length=1, description="Por que este passo responde a esta tarefa"
    )
    ref: str | None = Field(
        default=None, description="'nome@versao' da metrica ou da decomposicao"
    )
    period_ends: tuple[date, ...] = ()
    period_from: date | None = None
    period_to: date | None = None
    terms: tuple[str, ...] = ()
    statement: str | None = None
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def _check(self) -> "StepRequest":
        exige_ref = {StepKind.METRIC, StepKind.BREAKDOWN}
        if self.kind in exige_ref and not self.ref:
            raise ValueError(f"passo {self.step_id} do tipo {self.kind.value} exige `ref`")
        if self.kind is StepKind.METRIC and not self.period_ends:
            raise ValueError(f"passo {self.step_id}: metrica sem periodo nao pergunta nada")
        if self.kind is StepKind.BREAKDOWN and not (self.period_from and self.period_to):
            raise ValueError(
                f"passo {self.step_id}: decomposicao abre a variacao ENTRE dois "
                "periodos, e precisa dos dois"
            )
        if self.kind is StepKind.EVIDENCE and not self.terms:
            raise ValueError(f"passo {self.step_id}: busca sem termo nao pergunta nada")
        if self.kind is StepKind.ACCOUNTS and not (self.statement and self.period_ends):
            raise ValueError(
                f"passo {self.step_id}: plano de contas exige demonstracao e periodo"
            )
        return self


class StepOutcome(Frozen):
    """O que um passo produziu: enderecos citaveis, ou um motivo nomeado.

    `rendered` e a leitura humana do que saiu - com valor, porque o valor veio
    do motor. E o que o interpretador le. O numero nunca e reconstruido a
    partir daqui: quem precisa dele vai ao `result_id`.
    """

    step_id: Slug
    kind: StepKind
    ref: str | None = None
    result_ids: tuple[str, ...] = ()
    unit_ids: tuple[str, ...] = ()
    rendered: tuple[str, ...] = Field(
        default=(), description="Uma linha por resultado, para leitura"
    )
    shape_hint: str | None = Field(
        default=None,
        description=(
            "A forma da serie - direcao e faixa de magnitude - ja classificada. "
            "Vem do executor, e nunca de quem interpreta: os degraus sao codigo "
            "versionado, e um raciocinador que decidisse o que e 'grande' estaria "
            "produzindo a classificacao em vez de recebe-la"
        ),
    )
    unavailable: tuple[str, ...] = Field(
        default=(),
        description="Motivo nomeado por resultado que nao saiu. Nunca lista vazia calada",
    )

    @property
    def produced_anything(self) -> bool:
        return bool(self.result_ids or self.unit_ids)

    @property
    def citable_ids(self) -> frozenset[str]:
        return frozenset(self.result_ids) | frozenset(self.unit_ids)


class FindingProposal(Frozen):
    """Uma interpretacao, com o que a sustenta - por ID.

    `cites` pode ser vazio, e isso e legitimo: uma observacao de leitura
    ("os tres periodos vem da mesma demonstracao") nao cita numero. O que ela
    nao faz e sustentar conclusao sozinha, e o tipo preserva a diferenca em
    vez de apagar.
    """

    text: str = Field(min_length=1)
    cites: tuple[str, ...] = ()


class RejectionReason(StrEnum):
    UNKNOWN_CITATION = "unknown_citation"
    """Citou um ID que nenhum passo produziu. E a alucinacao de procedencia:
    a frase parece ancorada e nao esta."""

    EMPTY_AFTER_STRIP = "empty_after_strip"


class RejectedFinding(Frozen):
    """Interpretacao barrada, guardada.

    Descartar em silencio esconderia que o interpretador citou o que nao
    existe - que e informacao sobre a qualidade do proprio raciocinio, e que
    some justamente quando mais importa.
    """

    text: str = Field(min_length=1)
    reason: RejectionReason
    detail: str = Field(min_length=1)


class TaskProposal(Frozen):
    """Uma tarefa proposta pela decomposicao. Ainda nao esta na agenda."""

    slug: Slug
    objective: str = Field(min_length=1)
    completion_criteria: str = Field(min_length=1)
    priority: TaskPriority = TaskPriority.NORMAL
    depends_on: tuple[Slug, ...] = ()
    hypothesis: Slug | None = None


class HypothesisProposal(Frozen):
    """Uma hipotese proposta. Exige falsificador, como toda hipotese."""

    slug: Slug
    statement: str = Field(min_length=1)
    falsifiers: tuple[str, ...] = Field(min_length=1)


class TaskVerdict(Frozen):
    """O que fazer com a tarefa depois de pesquisar.

    `rationale` sem default: um veredito sem razao e um estado que muda sem
    ninguem poder discordar depois.
    """

    status: TaskStatus
    rationale: str = Field(min_length=1)
    blocker_reason: str | None = None
    blocker_remedy: str | None = None
    needs_human: bool = False

    @model_validator(mode="after")
    def _check(self) -> "TaskVerdict":
        bloqueado = self.status in (TaskStatus.BLOCKED, TaskStatus.NEEDS_HUMAN)
        if bloqueado and not (self.blocker_reason and self.blocker_remedy):
            raise ValueError(
                f"veredito {self.status.value} exige motivo E remedio. Bloqueio sem "
                "remedio nao diz a ninguem o que fazer na segunda-feira."
            )
        if not bloqueado and (self.blocker_reason or self.blocker_remedy):
            raise ValueError(
                f"veredito {self.status.value} traz bloqueio. Um bloqueio que nao "
                "bloqueia vira ressalva decorativa."
            )
        return self


class TaskReport(Frozen):
    """O que uma volta do ciclo fez numa tarefa. Auditavel inteiro.

    Carrega os passos planejados, o que cada um produziu, o que foi
    interpretado, o que foi REJEITADO e o veredito. Um relatorio que so
    mostrasse o que deu certo faria uma volta ruim se ler como uma volta boa.
    """

    task: Slug
    steps: tuple[StepRequest, ...] = ()
    outcomes: tuple[StepOutcome, ...] = ()
    findings: tuple[FindingProposal, ...] = ()
    rejected: tuple[RejectedFinding, ...] = ()
    verdict: TaskVerdict
    reasoner_id: str = Field(min_length=1, description="Quem raciocinou nesta volta")

    @property
    def produced_anything(self) -> bool:
        return any(o.produced_anything for o in self.outcomes)

    @property
    def grounded_findings(self) -> tuple[FindingProposal, ...]:
        return tuple(f for f in self.findings if f.cites)

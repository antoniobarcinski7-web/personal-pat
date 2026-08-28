"""O1 - o estado da pesquisa: agenda, tarefas, perguntas em aberto.

Nota de nome
------------
`ResearchProgram` e `ResearchQuestion` ja existem no projeto, e sao outra
coisa: o programa executavel de UM turno (Fase 5) e a pergunta que o origina.
Os dois vivem e morrem dentro de uma execucao.

O que vive aqui e mais longo que um turno: uma agenda que atravessa semanas,
sessoes e mudancas de ideia. Reusar os nomes faria duas coisas de tempos de
vida diferentes parecerem a mesma - e a confusao apareceria justamente quando
alguem tentasse persistir um `ResearchProgram`, que nao foi feito para isso.
Por isso a agenda se chama `ResearchAgenda`, e a pergunta pendente se chama
`OpenQuestion`.

O que a agenda impede
---------------------
1. Tarefa sem `completion_criteria`. Uma tarefa sem criterio e concluida por
   sensacao, e "achei que ja tinha investigado o bastante" e exatamente como
   um analista para de pesquisar logo antes do que refutaria a tese.
2. Ciclo de dependencia. Um ciclo trava a agenda para sempre e o agente
   reportaria "nada a fazer" com trabalho pendente.
3. BLOCKED sem motivo nomeado. Bloqueio sem `remedy` nao diz a ninguem o que
   fazer na segunda-feira.
4. BLOCKED e NEEDS_HUMAN contando como conclusao. So COMPLETE encerra; uma
   agenda que tratasse parada como fim produziria uma tese que se declara
   pronta tendo pulado o que nao conseguiu fazer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen
from pat.contracts.opportunity.base import Actor, Slug

__all__ = [
    "TERMINAL_STATUSES",
    "AgendaObjectiveSet",
    "Blocker",
    "BlockerCleared",
    "BlockerRecorded",
    "Finding",
    "FindingRecorded",
    "OpenQuestion",
    "QuestionAnswered",
    "QuestionOpened",
    "ResearchAgenda",
    "ResearchTask",
    "TaskCreated",
    "TaskPriority",
    "TaskPriorityChanged",
    "TaskStatus",
    "TaskStatusChanged",
    "priority_rank",
]


class TaskStatus(StrEnum):
    """Estados de uma tarefa de pesquisa. Conjunto fechado."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"

    BLOCKED = "blocked"
    """Depende de algo que o sistema pode resolver sozinho: dado que falta
    ingerir, documento que falta sincronizar, tarefa anterior nao concluida."""

    NEEDS_HUMAN = "needs_human"
    """Depende de uma decisao que nao e do agente: escolher a linha de um
    mapeamento, aceitar uma premissa de valuation, decidir o que a tese quer
    provar. `pat accounts` existe para um humano escolher a linha e nao deve
    virar inferencia - este estado e aquela regra no nivel da agenda."""


TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETE})
"""So COMPLETE encerra. BLOCKED e NEEDS_HUMAN sao paradas, nao fins."""


class TaskPriority(StrEnum):
    """Tres niveis, e nao um inteiro.

    Inteiro convida a ordenacao falsamente precisa - prioridade 7 contra 8 -,
    e o agente gastaria decisao em desempate que nao muda nada.
    """

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


_PRIORITY_ORDER = {TaskPriority.HIGH: 0, TaskPriority.NORMAL: 1, TaskPriority.LOW: 2}


def priority_rank(priority: TaskPriority) -> int:
    return _PRIORITY_ORDER[priority]


class Blocker(Frozen):
    """Por que uma tarefa parou, e o que a destrava.

    `remedy` e obrigatorio pela mesma razao que em `ReadinessGap`: "faltam
    dados" nao diz a ninguem o que fazer na segunda-feira.
    """

    reason: str = Field(min_length=1)
    remedy: str = Field(min_length=1)
    blocking_task: Slug | None = Field(
        default=None, description="Preenchido quando o bloqueio e outra tarefa"
    )
    needs_human: bool = Field(
        default=False,
        description="O remedio e uma decisao humana, nao uma acao que o agente possa tomar",
    )


class Finding(Frozen):
    """O que uma tarefa descobriu.

    Nao tem `value`, `unit` nem `currency`, e nao e esquecimento. Achado e
    prosa; o numero que o sustenta mora no resultado apontado por
    `result_ids`, e a citacao mora na unidade apontada por `unit_ids`. Um
    achado que carregasse o proprio numero seria numero produzido por esta
    camada - exatamente o que ela nao pode fazer.
    """

    text: str = Field(min_length=1)
    actor: Actor
    result_ids: tuple[str, ...] = Field(
        default=(), description="`ComputationResult.result_id` que sustentam o achado"
    )
    unit_ids: tuple[str, ...] = Field(
        default=(), description="`DocumentUnit.unit_id` citados pelo achado"
    )
    at: AwareDatetime

    @property
    def is_grounded(self) -> bool:
        """Ancorado em numero do motor ou em trecho verbatim.

        Achado sem ancora nao e ilegal - o agente pode registrar uma
        observacao de leitura -, mas nao sustenta conclusao sozinho, e a
        critica distingue os dois.
        """
        return bool(self.result_ids or self.unit_ids)


class OpenQuestion(Frozen):
    """Uma pergunta que ainda nao tem resposta.

    De primeira classe, e nao texto solto dentro de um achado, porque o que
    ainda nao se sabe e a parte da pesquisa que some primeiro. Uma tese que
    lista suas perguntas em aberto e defensavel; uma que as perdeu no caminho
    parece mais forte do que e.
    """

    slug: Slug
    text: str = Field(min_length=1)
    asked_by: Actor
    answered: bool = False
    answer: str | None = None
    at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> "OpenQuestion":
        if self.answered and not self.answer:
            raise ValueError(f"pergunta {self.slug} marcada respondida sem resposta")
        if self.answer and not self.answered:
            raise ValueError(f"pergunta {self.slug} tem resposta sem estar marcada respondida")
        return self


class ResearchTask(Frozen):
    """Uma linha de investigacao, ja dobrada."""

    slug: Slug
    objective: str = Field(min_length=1)
    completion_criteria: str = Field(min_length=1)
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    depends_on: tuple[Slug, ...] = ()
    findings: tuple[Finding, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    questions: tuple[OpenQuestion, ...] = ()
    hypothesis: Slug | None = Field(
        default=None, description="Hipotese que esta tarefa testa, quando ha uma"
    )
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> "ResearchTask":
        if self.slug in self.depends_on:
            raise ValueError(f"tarefa {self.slug} depende de si mesma")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError(f"tarefa {self.slug} repete dependencia: {self.depends_on}")
        if self.status is TaskStatus.BLOCKED and not self.blockers:
            raise ValueError(
                f"tarefa {self.slug} esta BLOCKED sem blocker. Bloqueio sem motivo "
                "nomeado nao diz a ninguem o que destrava."
            )
        slugs = [q.slug for q in self.questions]
        if len(set(slugs)) != len(slugs):
            raise ValueError(f"tarefa {self.slug} repete slug de pergunta: {slugs}")
        return self

    @property
    def is_done(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def open_questions(self) -> tuple[OpenQuestion, ...]:
        return tuple(q for q in self.questions if not q.answered)

    @property
    def grounded_findings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.is_grounded)


class ResearchAgenda(Frozen):
    """As tarefas de um workspace, e a ordem em que fazem sentido.

    Uma agenda por workspace: a investigacao e sobre uma empresa, e varias
    agendas so criariam a duvida de qual delas a tese resume.
    """

    agenda_version: Literal["v1"] = "v1"
    objective: str | None = Field(
        default=None, description="O que a investigacao inteira quer estabelecer"
    )
    tasks: tuple[ResearchTask, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "ResearchAgenda":
        por_slug = {t.slug: t for t in self.tasks}
        if len(por_slug) != len(self.tasks):
            raise ValueError("slug de tarefa repetido na agenda")
        for tarefa in self.tasks:
            for dep in tarefa.depends_on:
                if dep not in por_slug:
                    raise ValueError(f"tarefa {tarefa.slug} depende de {dep}, que nao existe")
        _reject_cycles(por_slug)
        return self

    def task(self, slug: str) -> ResearchTask | None:
        return next((t for t in self.tasks if t.slug == slug), None)

    def ready(self) -> tuple[ResearchTask, ...]:
        """Tarefas executaveis agora: PENDING com todas as dependencias
        concluidas.

        Ordenadas por prioridade e, dentro dela, pela ordem de criacao - que e
        a ordem em que alguem pensou nelas.
        """
        concluidas = {t.slug for t in self.tasks if t.is_done}
        prontas = [
            t
            for t in self.tasks
            if t.status is TaskStatus.PENDING and set(t.depends_on) <= concluidas
        ]
        return tuple(
            sorted(prontas, key=lambda t: (priority_rank(t.priority), t.created_at, t.slug))
        )

    def waiting_on_dependency(self) -> tuple[ResearchTask, ...]:
        """PENDING esperando outra tarefa.

        Separado de `ready` porque "nao ha o que fazer" e "ha, mas depende"
        pedem acoes diferentes de quem esta na frente do terminal.
        """
        concluidas = {t.slug for t in self.tasks if t.is_done}
        return tuple(
            t
            for t in self.tasks
            if t.status is TaskStatus.PENDING and not set(t.depends_on) <= concluidas
        )

    def of_status(self, status: TaskStatus) -> tuple[ResearchTask, ...]:
        return tuple(t for t in self.tasks if t.status is status)

    @property
    def all_open_questions(self) -> tuple[tuple[str, OpenQuestion], ...]:
        return tuple((t.slug, q) for t in self.tasks for q in t.open_questions)

    @property
    def is_exhausted(self) -> bool:
        """Nada mais a fazer sem intervencao.

        Nao e o mesmo que "tudo concluido": uma agenda com tarefa BLOCKED e
        exaurida e incompleta ao mesmo tempo, e confundir as duas coisas e
        como uma tese se declara pronta pulando o que travou.
        """
        return not self.ready()


def _reject_cycles(por_slug: dict[str, ResearchTask]) -> None:
    """Ciclo trava a agenda para sempre: nenhuma tarefa do ciclo fica pronta, e
    o agente reportaria "nada a fazer" com trabalho pendente. Recusar na
    construcao e o unico ponto em que da para dizer onde o ciclo esta."""
    VISITANDO, PRONTO = 1, 2
    marca: dict[str, int] = {}

    def visita(slug: str, caminho: tuple[str, ...]) -> None:
        estado = marca.get(slug)
        if estado == PRONTO:
            return
        if estado == VISITANDO:
            ciclo = " -> ".join((*caminho[caminho.index(slug) :], slug))
            raise ValueError(f"ciclo de dependencia entre tarefas: {ciclo}")
        marca[slug] = VISITANDO
        for dep in por_slug[slug].depends_on:
            visita(dep, (*caminho, slug))
        marca[slug] = PRONTO

    for slug in sorted(por_slug):
        visita(slug, ())


# -- eventos ----------------------------------------------------------------


class AgendaObjectiveSet(Frozen):
    kind: Literal["agenda_objective_set"] = "agenda_objective_set"
    objective: str = Field(min_length=1)


class TaskCreated(Frozen):
    kind: Literal["task_created"] = "task_created"
    slug: Slug
    objective: str = Field(min_length=1)
    completion_criteria: str = Field(min_length=1)
    priority: TaskPriority = TaskPriority.NORMAL
    depends_on: tuple[Slug, ...] = ()
    hypothesis: Slug | None = None


class TaskStatusChanged(Frozen):
    kind: Literal["task_status_changed"] = "task_status_changed"
    slug: Slug
    status: TaskStatus
    note: str | None = None


class TaskPriorityChanged(Frozen):
    kind: Literal["task_priority_changed"] = "task_priority_changed"
    slug: Slug
    priority: TaskPriority


class FindingRecorded(Frozen):
    kind: Literal["finding_recorded"] = "finding_recorded"
    slug: Slug
    text: str = Field(min_length=1)
    result_ids: tuple[str, ...] = ()
    unit_ids: tuple[str, ...] = ()


class BlockerRecorded(Frozen):
    """Registra o bloqueio E move a tarefa para BLOCKED ou NEEDS_HUMAN.

    Os dois num evento so de proposito: uma tarefa BLOCKED sem blocker e
    proibida pelo contrato, entao permitir gravar o estado sem o motivo
    criaria uma janela em que o diario nao pode ser dobrado.
    """

    kind: Literal["blocker_recorded"] = "blocker_recorded"
    slug: Slug
    blocker: Blocker


class BlockerCleared(Frozen):
    """Remove os bloqueios da tarefa e a devolve para PENDING.

    Nao apaga nada do diario: o bloqueio continua gravado, e o que muda e a
    dobra. Saber que uma tarefa esteve travada por falta de documento e parte
    da historia da tese.
    """

    kind: Literal["blocker_cleared"] = "blocker_cleared"
    slug: Slug
    note: str | None = None


class QuestionOpened(Frozen):
    kind: Literal["question_opened"] = "question_opened"
    task: Slug
    slug: Slug
    text: str = Field(min_length=1)


class QuestionAnswered(Frozen):
    kind: Literal["question_answered"] = "question_answered"
    task: Slug
    slug: Slug
    answer: str = Field(min_length=1)

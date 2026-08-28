"""A dobra: sequencia de eventos -> estado corrente da investigacao.

`fold` e uma funcao pura sobre uma tupla de eventos. Nao le disco, nao abre
banco e nao chama modelo. E o que torna todo o resto testavel sem I/O, e e
tambem o que garante que reabrir um workspace produz exatamente o mesmo estado
que estava em memoria antes de fechar - porque nos dois casos o estado saiu da
mesma funcao sobre os mesmos eventos.

Exaustividade
-------------
`_HANDLERS` mapeia tipo de corpo -> tratador, e `tests/opportunity/
test_fold.py::test_todo_corpo_de_evento_tem_tratador` compara as chaves com os
membros de `EventBody`. Um corpo novo sem dobra falha em teste, em vez de virar
evento que o replay ignora em silencio - que seria uma perda de estado que so
apareceria semanas depois, como uma hipotese que "sumiu".

O que a dobra recusa, e o que ela deixa passar
----------------------------------------------
A dobra recusa o que e estruturalmente impossivel: evento antes da criacao,
`as_of` para tras, tarefa que nao existe, slug repetido. Ela NAO julga a
qualidade da pesquisa - achado sem ancora, conclusao mal sustentada, hipotese
sem falsificador. Isso e trabalho do critico (O7), e a distincao importa: a
dobra tem que aceitar um estado ruim, porque uma investigacao passa por
estados ruins no caminho. O que ela nao pode aceitar e um estado impossivel.
"""

from __future__ import annotations

import typing
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from pat.contracts.common import Frozen
from pat.contracts.opportunity import (
    Actor,
    AgendaObjectiveSet,
    AlternativeConsidered,
    AsOfAdvanced,
    AssumptionChanged,
    AssumptionSet,
    Blocker,
    BlockerCleared,
    BlockerRecorded,
    Claim,
    ClaimAsserted,
    CompanyProfile,
    Conclusion,
    ConclusionDrawn,
    CounterEvidence,
    CounterEvidenceAdded,
    CoverageRefreshed,
    CoverageSnapshot,
    EventBody,
    EvidenceLink,
    EvidenceLinked,
    FalsificationAttempted,
    FalsifierAdded,
    Finding,
    FindingRecorded,
    Hypothesis,
    HypothesisOpened,
    HypothesisStatus,
    HypothesisStatusChanged,
    HypothesisStrength,
    JournalEvent,
    MandateSet,
    Note,
    NoteAdded,
    OpenQuestion,
    OpportunityWorkspace,
    QuestionAnswered,
    QuestionOpened,
    ResearchAgenda,
    ResearchTask,
    TaskCreated,
    TaskPriority,
    TaskPriorityChanged,
    TaskStatus,
    TaskStatusChanged,
    InvestmentThesis,
    ThesisDrafted,
    TitleSet,
    ValuationDeclared,
    ValuationInterpretation,
    ValuationInterpreted,
    ValuationModel,
    WorkspaceArchived,
    WorkspaceCreated,
    WorkspaceReopened,
    WorkspaceStatus,
)

__all__ = ["FoldError", "OpportunityState", "event_body_types", "fold"]


class FoldError(RuntimeError):
    """Sequencia de eventos que nao descreve uma investigacao possivel.

    Separado de `JournalCorrupt`: la o arquivo esta danificado, aqui o arquivo
    esta intacto e conta uma historia impossivel.
    """


class OpportunityState(Frozen):
    """Tudo que se sabe sobre uma investigacao, agora.

    Cresce a cada milestone (agenda, hipoteses, valuation, tese). O cabecalho
    fica separado em `workspace` porque listar workspaces nao pode exigir
    reconstruir a investigacao inteira de cada um.
    """

    state_version: Literal["v1"] = "v1"
    workspace: OpportunityWorkspace
    agenda: ResearchAgenda = ResearchAgenda()
    hypotheses: tuple[Hypothesis, ...] = ()
    claims: tuple[Claim, ...] = ()
    conclusions: tuple[Conclusion, ...] = ()
    valuations: tuple[ValuationModel, ...] = ()
    valuation_notes: tuple[ValuationInterpretation, ...] = ()
    theses: tuple[InvestmentThesis, ...] = ()
    notes: tuple[Note, ...] = ()

    def thesis(self, slug: str) -> InvestmentThesis | None:
        """A versao CORRENTE da tese. As anteriores continuam no diario.

        Uma tese que muda de LONG para NO_POSITION e a informacao mais
        valiosa que o diario guarda; sobrescrever a apagaria.
        """
        return next((t for t in self.theses if t.slug == slug), None)

    def valuation(self, slug: str) -> ValuationModel | None:
        return next((v for v in self.valuations if v.slug == slug), None)

    def hypothesis(self, slug: str) -> Hypothesis | None:
        return next((h for h in self.hypotheses if h.slug == slug), None)

    def claim(self, slug: str) -> Claim | None:
        return next((c for c in self.claims if c.slug == slug), None)

    def conclusion_for(self, hypothesis: str) -> Conclusion | None:
        return next((c for c in self.conclusions if c.hypothesis == hypothesis), None)

    def claims_for(self, hypothesis: str) -> tuple[Claim, ...]:
        alvo = self.hypothesis(hypothesis)
        if alvo is None:
            return ()
        return tuple(c for c in self.claims if c.slug in alvo.claims)

    @property
    def open_hypotheses(self) -> tuple[Hypothesis, ...]:
        return tuple(h for h in self.hypotheses if h.status is HypothesisStatus.OPEN)

    @property
    def workspace_id(self) -> str:
        return self.workspace.workspace_id

    @property
    def entity_id(self) -> str:
        return self.workspace.company.entity_id

    @property
    def as_of(self) -> date:
        return self.workspace.as_of


@dataclass
class _TaskBuilder:
    """Uma tarefa em construcao. Mutavel, nao escapa deste modulo."""

    slug: str
    objective: str
    completion_criteria: str
    created_at: datetime
    updated_at: datetime
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    depends_on: tuple[str, ...] = ()
    hypothesis: str | None = None
    findings: list[Finding] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    questions: list[OpenQuestion] = field(default_factory=list)

    def freeze(self) -> ResearchTask:
        return ResearchTask(
            slug=self.slug,
            objective=self.objective,
            completion_criteria=self.completion_criteria,
            status=self.status,
            priority=self.priority,
            depends_on=self.depends_on,
            findings=tuple(self.findings),
            blockers=tuple(self.blockers),
            questions=tuple(self.questions),
            hypothesis=self.hypothesis,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class _HypothesisBuilder:
    """Uma hipotese em construcao. Mutavel, nao escapa deste modulo."""

    slug: str
    statement: str
    falsifiers: list[str]
    opened_by: Actor
    created_at: datetime
    updated_at: datetime
    open_questions: tuple[str, ...] = ()
    status: HypothesisStatus = HypothesisStatus.OPEN
    strength: HypothesisStrength | None = None
    supporting: list[EvidenceLink] = field(default_factory=list)
    attempts: list[FalsificationAttempted] = field(default_factory=list)
    alternatives: list[AlternativeConsidered] = field(default_factory=list)
    counter: list[CounterEvidence] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)

    def freeze(self) -> Hypothesis:
        return Hypothesis(
            slug=self.slug,
            statement=self.statement,
            status=self.status,
            strength=self.strength,
            falsifiers=tuple(self.falsifiers),
            supporting=tuple(self.supporting),
            counter=tuple(self.counter),
            claims=tuple(self.claims),
            falsification_attempts=tuple(self.attempts),
            alternatives=tuple(self.alternatives),
            open_questions=self.open_questions,
            opened_by=self.opened_by,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class _Builder:
    """Acumulador mutavel da dobra. Nao escapa deste modulo.

    Mutavel de proposito: reconstruir um `Frozen` a cada evento seria
    quadratico no numero de eventos, e um diario de investigacao longa tem
    milhares. O congelamento acontece uma vez, em `freeze`.
    """

    workspace_id: str
    company: CompanyProfile | None = None
    as_of: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    status: WorkspaceStatus = WorkspaceStatus.OPEN
    title: str | None = None
    mandate: str | None = None
    coverage: CoverageSnapshot | None = None
    seq: int = 0
    notes: list[Note] = field(default_factory=list)
    agenda_objective: str | None = None
    # `dict` guarda a ordem de insercao, que e a ordem em que alguem pensou nas
    # tarefas. Ordenar por slug faria a agenda mudar de ordem quando alguem
    # renomeasse uma tarefa.
    tasks: dict[str, _TaskBuilder] = field(default_factory=dict)
    hypotheses: dict[str, _HypothesisBuilder] = field(default_factory=dict)
    claims: dict[str, Claim] = field(default_factory=dict)
    # Uma conclusao por hipotese: redesenhar uma conclusao substitui a
    # anterior no estado, e o diario guarda as duas. Acumular varias faria a
    # tese citar a que o leitor achasse primeiro.
    conclusions: dict[str, Conclusion] = field(default_factory=dict)
    valuations: dict[str, ValuationModel] = field(default_factory=dict)
    valuation_notes: list[ValuationInterpretation] = field(default_factory=list)
    theses: dict[str, InvestmentThesis] = field(default_factory=dict)

    def hypothesis(self, slug: str, ev: JournalEvent) -> _HypothesisBuilder:
        hipotese = self.hypotheses.get(slug)
        if hipotese is None:
            raise FoldError(
                f"workspace {self.workspace_id}: evento {ev.body.kind!r} no seq "
                f"{ev.seq} referencia a hipotese {slug!r}, que nao existe."
            )
        return hipotese

    def task(self, slug: str, ev: JournalEvent) -> _TaskBuilder:
        tarefa = self.tasks.get(slug)
        if tarefa is None:
            raise FoldError(
                f"workspace {self.workspace_id}: evento {ev.body.kind!r} no seq "
                f"{ev.seq} referencia a tarefa {slug!r}, que nao existe."
            )
        return tarefa

    def freeze(self) -> OpportunityState:
        if self.company is None or self.as_of is None or self.created_at is None:
            raise FoldError(
                f"workspace {self.workspace_id}: diario sem evento de criacao. "
                "O primeiro evento tem que ser `workspace_created`."
            )
        return OpportunityState(
            workspace=OpportunityWorkspace(
                workspace_id=self.workspace_id,
                company=self.company,
                as_of=self.as_of,
                status=self.status,
                title=self.title,
                mandate=self.mandate,
                coverage=self.coverage,
                created_at=self.created_at,
                updated_at=self.updated_at or self.created_at,
                seq=self.seq,
            ),
            agenda=ResearchAgenda(
                objective=self.agenda_objective,
                tasks=tuple(t.freeze() for t in self.tasks.values()),
            ),
            hypotheses=tuple(h.freeze() for h in self.hypotheses.values()),
            claims=tuple(self.claims.values()),
            conclusions=tuple(self.conclusions.values()),
            valuations=tuple(self.valuations.values()),
            valuation_notes=tuple(self.valuation_notes),
            theses=tuple(self.theses.values()),
            notes=tuple(self.notes),
        )


# -- tratadores: workspace (O0) ---------------------------------------------


def _on_created(b: _Builder, ev: JournalEvent, body: WorkspaceCreated) -> None:
    if b.company is not None:
        raise FoldError(
            f"workspace {b.workspace_id}: segundo `workspace_created` no seq {ev.seq}. "
            "Um diario descreve UMA investigacao."
        )
    b.company = body.company
    b.as_of = body.as_of
    b.title = body.title
    b.mandate = body.mandate
    b.created_at = ev.at


def _on_mandate(b: _Builder, ev: JournalEvent, body: MandateSet) -> None:
    b.mandate = body.mandate


def _on_title(b: _Builder, ev: JournalEvent, body: TitleSet) -> None:
    b.title = body.title


def _on_as_of(b: _Builder, ev: JournalEvent, body: AsOfAdvanced) -> None:
    # Retroceder invalidaria evidencia ja admitida: um trecho citado sob o
    # `as_of` antigo passaria a ser posterior ao novo, e a conclusao que ele
    # sustenta viraria vazamento retroativo sem ninguem ter mexido nela.
    if b.as_of is not None and body.as_of < b.as_of:
        raise FoldError(
            f"workspace {b.workspace_id}: as_of andando para tras no seq {ev.seq} "
            f"({b.as_of} -> {body.as_of}). Recuar exige workspace novo."
        )
    b.as_of = body.as_of


def _on_coverage(b: _Builder, ev: JournalEvent, body: CoverageRefreshed) -> None:
    b.coverage = body.coverage


def _on_note(b: _Builder, ev: JournalEvent, body: NoteAdded) -> None:
    b.notes.append(Note(seq=ev.seq, at=ev.at, actor=ev.actor, text=body.text))


def _on_archived(b: _Builder, ev: JournalEvent, body: WorkspaceArchived) -> None:
    b.status = WorkspaceStatus.ARCHIVED


def _on_reopened(b: _Builder, ev: JournalEvent, body: WorkspaceReopened) -> None:
    b.status = WorkspaceStatus.OPEN


# -- tratadores: agenda (O1) ------------------------------------------------


def _on_agenda_objective(b: _Builder, ev: JournalEvent, body: AgendaObjectiveSet) -> None:
    b.agenda_objective = body.objective


def _on_task_created(b: _Builder, ev: JournalEvent, body: TaskCreated) -> None:
    if body.slug in b.tasks:
        raise FoldError(
            f"workspace {b.workspace_id}: tarefa {body.slug!r} criada duas vezes "
            f"(seq {ev.seq}). Slug e identidade; reusar um trocaria o objetivo de "
            "uma tarefa que ja tem achados."
        )
    for dep in body.depends_on:
        if dep not in b.tasks:
            raise FoldError(
                f"workspace {b.workspace_id}: tarefa {body.slug!r} (seq {ev.seq}) "
                f"depende de {dep!r}, que ainda nao existe. Crie a dependencia antes."
            )
    b.tasks[body.slug] = _TaskBuilder(
        slug=body.slug,
        objective=body.objective,
        completion_criteria=body.completion_criteria,
        priority=body.priority,
        depends_on=body.depends_on,
        hypothesis=body.hypothesis,
        created_at=ev.at,
        updated_at=ev.at,
    )


def _on_task_status(b: _Builder, ev: JournalEvent, body: TaskStatusChanged) -> None:
    tarefa = b.task(body.slug, ev)
    if body.status is TaskStatus.BLOCKED and not tarefa.blockers:
        raise FoldError(
            f"workspace {b.workspace_id}: tarefa {body.slug!r} para BLOCKED sem "
            f"blocker (seq {ev.seq}). Registre o bloqueio - bloqueio sem motivo "
            "nomeado nao diz a ninguem o que destrava."
        )
    if body.status is TaskStatus.COMPLETE and tarefa.blockers:
        # Concluir com bloqueio pendente e como uma tese se declarar pronta
        # tendo pulado o que travou: o registro do bloqueio continua la, e a
        # tarefa passa a dizer que terminou. Limpar e explicito.
        raise FoldError(
            f"workspace {b.workspace_id}: tarefa {body.slug!r} para COMPLETE com "
            f"{len(tarefa.blockers)} bloqueio(s) pendente(s) (seq {ev.seq}). "
            "Limpe o bloqueio (`blocker_cleared`) antes de concluir."
        )
    tarefa.status = body.status
    tarefa.updated_at = ev.at


def _on_task_priority(b: _Builder, ev: JournalEvent, body: TaskPriorityChanged) -> None:
    tarefa = b.task(body.slug, ev)
    tarefa.priority = body.priority
    tarefa.updated_at = ev.at


def _on_finding(b: _Builder, ev: JournalEvent, body: FindingRecorded) -> None:
    tarefa = b.task(body.slug, ev)
    tarefa.findings.append(
        Finding(
            text=body.text,
            actor=ev.actor,
            result_ids=body.result_ids,
            unit_ids=body.unit_ids,
            at=ev.at,
        )
    )
    tarefa.updated_at = ev.at


def _on_blocker(b: _Builder, ev: JournalEvent, body: BlockerRecorded) -> None:
    tarefa = b.task(body.slug, ev)
    tarefa.blockers.append(body.blocker)
    # O estado acompanha o tipo do bloqueio: o que o agente pode destravar
    # sozinho e BLOCKED, o que depende de uma decisao humana e NEEDS_HUMAN.
    # Sao filas diferentes - a segunda espera uma pessoa, a primeira nao.
    tarefa.status = (
        TaskStatus.NEEDS_HUMAN if body.blocker.needs_human else TaskStatus.BLOCKED
    )
    tarefa.updated_at = ev.at


def _on_blocker_cleared(b: _Builder, ev: JournalEvent, body: BlockerCleared) -> None:
    tarefa = b.task(body.slug, ev)
    tarefa.blockers.clear()
    tarefa.status = TaskStatus.PENDING
    tarefa.updated_at = ev.at


def _on_question_opened(b: _Builder, ev: JournalEvent, body: QuestionOpened) -> None:
    tarefa = b.task(body.task, ev)
    if any(q.slug == body.slug for q in tarefa.questions):
        raise FoldError(
            f"workspace {b.workspace_id}: pergunta {body.slug!r} aberta duas vezes "
            f"na tarefa {body.task!r} (seq {ev.seq})."
        )
    tarefa.questions.append(
        OpenQuestion(slug=body.slug, text=body.text, asked_by=ev.actor, at=ev.at)
    )
    tarefa.updated_at = ev.at


def _on_question_answered(b: _Builder, ev: JournalEvent, body: QuestionAnswered) -> None:
    tarefa = b.task(body.task, ev)
    for i, pergunta in enumerate(tarefa.questions):
        if pergunta.slug == body.slug:
            tarefa.questions[i] = pergunta.model_copy(
                update={"answered": True, "answer": body.answer}
            )
            tarefa.updated_at = ev.at
            return
    raise FoldError(
        f"workspace {b.workspace_id}: pergunta {body.slug!r} respondida (seq "
        f"{ev.seq}) sem ter sido aberta na tarefa {body.task!r}."
    )


# -- tratadores: hipotese e evidencia (O2) ----------------------------------


def _on_hypothesis_opened(b: _Builder, ev: JournalEvent, body: HypothesisOpened) -> None:
    if body.slug in b.hypotheses:
        raise FoldError(
            f"workspace {b.workspace_id}: hipotese {body.slug!r} aberta duas vezes "
            f"(seq {ev.seq}). Reabrir com outro enunciado apagaria a evidencia que "
            "ja estava ligada a primeira."
        )
    b.hypotheses[body.slug] = _HypothesisBuilder(
        slug=body.slug,
        statement=body.statement,
        falsifiers=list(body.falsifiers),
        open_questions=body.open_questions,
        opened_by=ev.actor,
        created_at=ev.at,
        updated_at=ev.at,
    )


def _on_falsifier_added(b: _Builder, ev: JournalEvent, body: FalsifierAdded) -> None:
    hipotese = b.hypothesis(body.slug, ev)
    if body.falsifier not in hipotese.falsifiers:
        hipotese.falsifiers.append(body.falsifier)
    hipotese.updated_at = ev.at


def _on_evidence_linked(b: _Builder, ev: JournalEvent, body: EvidenceLinked) -> None:
    hipotese = b.hypothesis(body.hypothesis, ev)
    hipotese.supporting.append(body.link)
    hipotese.updated_at = ev.at


def _on_counter_evidence(b: _Builder, ev: JournalEvent, body: CounterEvidenceAdded) -> None:
    hipotese = b.hypothesis(body.hypothesis, ev)
    hipotese.counter.append(body.counter)
    hipotese.updated_at = ev.at


def _on_hypothesis_status(b: _Builder, ev: JournalEvent, body: HypothesisStatusChanged) -> None:
    hipotese = b.hypothesis(body.slug, ev)
    if body.status is HypothesisStatus.SUPPORTED and not hipotese.supporting:
        raise FoldError(
            f"workspace {b.workspace_id}: hipotese {body.slug!r} para SUPPORTED sem "
            f"evidencia a favor (seq {ev.seq}). Ligue a evidencia antes."
        )
    if body.status is HypothesisStatus.REJECTED and not hipotese.counter:
        raise FoldError(
            f"workspace {b.workspace_id}: hipotese {body.slug!r} para REJECTED sem "
            f"contra-evidencia (seq {ev.seq}). Rejeitar por mudanca de opiniao apaga "
            "a razao, que e o que se le depois."
        )
    if body.status is HypothesisStatus.OPEN and body.strength is not None:
        raise FoldError(
            f"workspace {b.workspace_id}: hipotese {body.slug!r} volta a OPEN com "
            f"forca declarada (seq {ev.seq}). Forca antes do teste e opiniao com "
            "cara de veredito."
        )
    hipotese.status = body.status
    # Voltar para OPEN limpa a forca: reabrir e dizer que o veredito anterior
    # nao vale mais, e manter a forca antiga faria a hipotese reaberta parecer
    # ja julgada.
    hipotese.strength = None if body.status is HypothesisStatus.OPEN else body.strength
    hipotese.updated_at = ev.at


def _on_falsification(b: _Builder, ev: JournalEvent, body: FalsificationAttempted) -> None:
    hipotese = b.hypothesis(body.hypothesis, ev)
    hipotese.attempts.append(body)
    hipotese.updated_at = ev.at


def _on_alternative(b: _Builder, ev: JournalEvent, body: AlternativeConsidered) -> None:
    hipotese = b.hypothesis(body.hypothesis, ev)
    hipotese.alternatives.append(body)
    hipotese.updated_at = ev.at


def _on_claim_asserted(b: _Builder, ev: JournalEvent, body: ClaimAsserted) -> None:
    if body.slug in b.claims:
        raise FoldError(
            f"workspace {b.workspace_id}: claim {body.slug!r} afirmado duas vezes "
            f"(seq {ev.seq})."
        )
    b.claims[body.slug] = Claim(
        slug=body.slug,
        text=body.text,
        evidence=body.evidence,
        asserted_by=ev.actor,
        at=ev.at,
    )
    if body.hypothesis is not None:
        hipotese = b.hypothesis(body.hypothesis, ev)
        hipotese.claims.append(body.slug)
        hipotese.updated_at = ev.at


def _on_conclusion(b: _Builder, ev: JournalEvent, body: ConclusionDrawn) -> None:
    hipotese = b.hypothesis(body.hypothesis, ev)
    # OPEN e o unico estado que nao conclui, e a razao e estreita: OPEN quer
    # dizer "ainda nao testada". WEAKENED e INCONCLUSIVE sao resultados de
    # teste, e concluir a partir deles e justamente o que um analista honesto
    # faz - "ha indicio, insuficiente" e "os dados nao resolvem isso" sao
    # conclusoes, e das mais uteis. Exigir SUPPORTED ou REJECTED para poder
    # concluir empurraria as duvidas para um dos extremos.
    if hipotese.status is HypothesisStatus.OPEN:
        raise FoldError(
            f"workspace {b.workspace_id}: conclusao sobre a hipotese "
            f"{body.hypothesis!r} ainda OPEN (seq {ev.seq}). Concluir de OPEN e "
            "declarar veredito sem ter testado. Mude o estado primeiro - "
            "INCONCLUSIVE tambem e um resultado."
        )
    b.conclusions[body.hypothesis] = Conclusion(
        hypothesis=body.hypothesis,
        text=body.text,
        strength=body.strength,
        residual_uncertainty=body.residual_uncertainty,
        drawn_by=ev.actor,
        at=ev.at,
    )


# -- tratadores: valuation (O8) ---------------------------------------------


def _on_valuation(b: _Builder, ev: JournalEvent, body: ValuationDeclared) -> None:
    if body.model.slug in b.valuations:
        raise FoldError(
            f"workspace {b.workspace_id}: valuation {body.model.slug!r} declarada duas "
            f"vezes (seq {ev.seq}). Trocar premissa e `assumption_changed`; redeclarar "
            "o modelo inteiro apagaria o historico das trocas."
        )
    b.valuations[body.model.slug] = body.model


def _on_assumption_changed(b: _Builder, ev: JournalEvent, body: AssumptionChanged) -> None:
    modelo = b.valuations.get(body.model)
    if modelo is None:
        raise FoldError(
            f"workspace {b.workspace_id}: premissa trocada (seq {ev.seq}) num modelo "
            f"{body.model!r} que nao foi declarado."
        )
    # O valor antigo continua no diario. Trocar premissa e onde uma tese se
    # auto-ajusta ate dar o numero que o autor queria; o historico nao proibe
    # isso - torna visivel.
    outras = tuple(
        a for a in modelo.assumptions.assumptions if a.slug != body.assumption.slug
    )
    b.valuations[body.model] = modelo.model_copy(
        update={"assumptions": AssumptionSet(assumptions=(*outras, body.assumption))}
    )


def _on_valuation_interpreted(
    b: _Builder, ev: JournalEvent, body: ValuationInterpreted
) -> None:
    if body.model not in b.valuations:
        raise FoldError(
            f"workspace {b.workspace_id}: interpretacao (seq {ev.seq}) sobre a "
            f"valuation {body.model!r}, que nao foi declarada."
        )
    b.valuation_notes.append(
        ValuationInterpretation(
            model_slug=body.model, text=body.text, author=ev.actor, at=ev.at
        )
    )


# -- tratadores: tese (O9) --------------------------------------------------


def _on_thesis(b: _Builder, ev: JournalEvent, body: ThesisDrafted) -> None:
    # Reescrever substitui o estado CORRENTE; o diario mantem as versoes
    # anteriores. Guardar todas no estado faria "a tese" ficar ambigua.
    b.theses[body.thesis.slug] = body.thesis


_HANDLERS: dict[type, Callable[[_Builder, JournalEvent, typing.Any], None]] = {
    # O0
    WorkspaceCreated: _on_created,
    MandateSet: _on_mandate,
    TitleSet: _on_title,
    AsOfAdvanced: _on_as_of,
    CoverageRefreshed: _on_coverage,
    NoteAdded: _on_note,
    WorkspaceArchived: _on_archived,
    WorkspaceReopened: _on_reopened,
    # O1
    AgendaObjectiveSet: _on_agenda_objective,
    TaskCreated: _on_task_created,
    TaskStatusChanged: _on_task_status,
    TaskPriorityChanged: _on_task_priority,
    FindingRecorded: _on_finding,
    BlockerRecorded: _on_blocker,
    BlockerCleared: _on_blocker_cleared,
    QuestionOpened: _on_question_opened,
    QuestionAnswered: _on_question_answered,
    # O2
    HypothesisOpened: _on_hypothesis_opened,
    FalsifierAdded: _on_falsifier_added,
    EvidenceLinked: _on_evidence_linked,
    CounterEvidenceAdded: _on_counter_evidence,
    HypothesisStatusChanged: _on_hypothesis_status,
    ClaimAsserted: _on_claim_asserted,
    ConclusionDrawn: _on_conclusion,
    # O7
    FalsificationAttempted: _on_falsification,
    AlternativeConsidered: _on_alternative,
    # O8
    ValuationDeclared: _on_valuation,
    AssumptionChanged: _on_assumption_changed,
    ValuationInterpreted: _on_valuation_interpreted,
    # O9
    ThesisDrafted: _on_thesis,
}


def event_body_types() -> frozenset[type]:
    """Membros de `EventBody`. Usado pelo teste de exaustividade."""
    return frozenset(typing.get_args(EventBody))


def fold(workspace_id: str, events: Sequence[JournalEvent]) -> OpportunityState:
    """Eventos -> estado. Pura, sem I/O."""
    builder = _Builder(workspace_id=workspace_id)
    for evento in events:
        handler = _HANDLERS.get(type(evento.body))
        if handler is None:
            raise FoldError(
                f"workspace {workspace_id}: evento {evento.body.kind!r} no seq "
                f"{evento.seq} nao tem tratador na dobra."
            )
        if builder.company is None and not isinstance(evento.body, WorkspaceCreated):
            raise FoldError(
                f"workspace {workspace_id}: evento {evento.body.kind!r} no seq "
                f"{evento.seq} antes da criacao do workspace."
            )
        handler(builder, evento, evento.body)
        builder.seq = evento.seq
        builder.updated_at = evento.at
    return builder.freeze()

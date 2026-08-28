"""O6 - o ciclo de pesquisa, explicito e inspecionavel.

    UNDERSTAND -> PLAN -> RESEARCH -> ANALYZE -> TEST -> CRITICIZE -> UPDATE -> NEXT

Sem framework de agente. O laco e um `for` sobre tarefas prontas, e cada fase
e uma funcao com nome - porque o valor deste desenho esta em poder abrir o
relatorio e ver exatamente o que foi perguntado, o que voltou, o que foi
interpretado e o que foi barrado. Um framework esconderia justamente isso.

As duas fronteiras que o laco mantem
------------------------------------
1. **RESEARCH e deterministico.** `_research` so fala com a mesa de
   ferramentas. Nenhum modelo participa, e nenhum numero e produzido aqui -
   os numeros vem do motor, com procedencia, e o que este modulo faz e
   colecionar os enderecos deles.

2. **TEST e mecanico.** Toda citacao de uma interpretacao tem que estar no
   que os passos daquela tarefa de fato produziram. Uma frase que cita um ID
   inexistente e barrada e REGISTRADA - `RejectedFinding` -, nunca descartada
   em silencio. Descartar caladamente esconderia que o interpretador cita o
   que nao existe, e essa e informacao sobre a qualidade do raciocinio que
   some justamente quando mais importa.

Por que o laco grava a cada tarefa, e nao no fim
-----------------------------------------------
Uma volta que morra na terceira tarefa deixa as duas primeiras gravadas. O
diario e append-only e a dobra e pura, entao um estado parcial e um estado
valido - e o operador reabre e continua. Acumular tudo em memoria para gravar
no fim trocaria isso por "ou tudo ou nada", que numa investigacao de horas e
sempre nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pat.contracts.opportunity import (
    Actor,
    AgendaObjectiveSet,
    Blocker,
    BlockerRecorded,
    FindingRecorded,
    HypothesisOpened,
    TaskCreated,
    TaskStatus,
    TaskStatusChanged,
)
from pat.contracts.opportunity.loop import (
    FindingProposal,
    Phase,
    RejectedFinding,
    RejectionReason,
    StepKind,
    StepOutcome,
    StepRequest,
    TaskReport,
    TaskVerdict,
)
from pat.contracts.semantics import MetricResult
from pat.opportunity.reason import Reasoner, ResearchContext, series_shape
from pat.opportunity.store import Workspace
from pat.opportunity.tools import PatTools

__all__ = [
    "AgendaRun",
    "build_context",
    "plan_agenda",
    "run_agenda",
    "run_task",
]


@dataclass(frozen=True)
class AgendaRun:
    """O que uma corrida do laco fez. Uma entrada por tarefa visitada."""

    reports: tuple[TaskReport, ...]
    stopped_because: str

    @property
    def completed(self) -> tuple[str, ...]:
        return tuple(
            r.task for r in self.reports if r.verdict.status is TaskStatus.COMPLETE
        )

    @property
    def blocked(self) -> tuple[str, ...]:
        return tuple(
            r.task
            for r in self.reports
            if r.verdict.status in (TaskStatus.BLOCKED, TaskStatus.NEEDS_HUMAN)
        )


# ---------------------------------------------------------------------------
# UNDERSTAND
# ---------------------------------------------------------------------------


def build_context(workspace: Workspace, tools: PatTools) -> ResearchContext:
    """O que da para saber antes de perguntar qualquer coisa.

    Le a cobertura do PAT e o estado do workspace. Nao carrega valor nenhum -
    ver `ResearchContext` para a razao.
    """
    cobertura = tools.coverage()
    estado = workspace.state
    return ResearchContext(
        company=tools.company,
        as_of=tools.context.as_of,
        periods=cobertura.period_ends,
        metrics_available=cobertura.metrics_available,
        decompositions_available=(),
        documents=cobertura.documents,
        missing_concepts=cobertura.missing_concepts,
        open_questions=tuple(q.text for _, q in estado.agenda.all_open_questions),
        mandate=estado.workspace.mandate,
    )


# ---------------------------------------------------------------------------
# PLAN (decomposicao do objetivo em agenda)
# ---------------------------------------------------------------------------


def plan_agenda(
    workspace: Workspace,
    tools: PatTools,
    reasoner: Reasoner,
    *,
    objective: str,
    actor: Actor = Actor.AGENT,
) -> tuple[str, ...]:
    """Objetivo -> tarefas e hipoteses na agenda. Devolve os slugs criados.

    Idempotente por slug: uma tarefa que ja existe nao e recriada, e a dobra
    recusaria de qualquer forma. Isso e o que permite repetir "investiga isso"
    sem duplicar a agenda.
    """
    contexto = build_context(workspace, tools)
    propostas, hipoteses = reasoner.decompose(objective=objective, context=contexto)

    workspace.apply(AgendaObjectiveSet(objective=objective), actor=actor)

    existentes = {t.slug for t in workspace.state.agenda.tasks}
    for hipotese in hipoteses:
        if workspace.state.hypothesis(hipotese.slug) is None:
            workspace.apply(
                HypothesisOpened(
                    slug=hipotese.slug,
                    statement=hipotese.statement,
                    falsifiers=hipotese.falsifiers,
                ),
                actor=actor,
            )

    criados: list[str] = []
    for proposta in propostas:
        if proposta.slug in existentes:
            continue
        # Dependencia que nao existe na agenda e descartada, e nao inventada:
        # criar a dependencia faltante aqui produziria uma tarefa que ninguem
        # pediu, com objetivo escrito por este arquivo.
        deps = tuple(d for d in proposta.depends_on if d in existentes)
        workspace.apply(
            TaskCreated(
                slug=proposta.slug,
                objective=proposta.objective,
                completion_criteria=proposta.completion_criteria,
                priority=proposta.priority,
                depends_on=deps,
                hypothesis=proposta.hypothesis
                if proposta.hypothesis and workspace.state.hypothesis(proposta.hypothesis)
                else None,
            ),
            actor=actor,
        )
        existentes.add(proposta.slug)
        criados.append(proposta.slug)
    return tuple(criados)


# ---------------------------------------------------------------------------
# RESEARCH - deterministico, sem modelo
# ---------------------------------------------------------------------------


def _research(tools: PatTools, steps: tuple[StepRequest, ...]) -> tuple[StepOutcome, ...]:
    """Executa os passos na mesa. E a unica fase que toca no warehouse."""
    return tuple(_run_step(tools, passo) for passo in steps)


def _run_step(tools: PatTools, step: StepRequest) -> StepOutcome:
    if step.kind is StepKind.METRIC:
        return _step_metric(tools, step)
    if step.kind is StepKind.BREAKDOWN:
        return _step_breakdown(tools, step)
    if step.kind is StepKind.EVIDENCE:
        return _step_evidence(tools, step)
    return _step_accounts(tools, step)


def _step_metric(tools: PatTools, step: StepRequest) -> StepOutcome:
    resultados = tools.series(step.ref, period_ends=step.period_ends)
    ids: list[str] = []
    linhas: list[str] = []
    recusas: list[str] = []
    pares: list[tuple[date, Decimal]] = []

    for periodo, resultado in zip(step.period_ends, resultados, strict=True):
        if isinstance(resultado, MetricResult):
            # O identificador citavel de um numero e o `fact_id` da cadeia
            # quando ha um, e o proprio par (metrica, periodo) quando o
            # resultado e derivado. Usamos uma chave estavel e reproduzivel.
            ident = _metric_ref_id(step.ref, resultado)
            ids.append(ident)
            linhas.append(
                f"{step.ref} {periodo.isoformat()}: {resultado.value} "
                f"{resultado.currency or resultado.dimension.value} "
                f"[fidelidade={resultado.fidelity.value}]"
            )
            pares.append((periodo, resultado.value))
        else:
            recusas.append(
                f"{step.ref} {periodo.isoformat()}: {resultado.reason.value} - "
                f"{resultado.message}"
            )

    forma = series_shape(pares)
    return StepOutcome(
        step_id=step.step_id,
        kind=step.kind,
        ref=step.ref,
        result_ids=tuple(ids),
        rendered=tuple(linhas),
        unavailable=tuple(recusas),
        shape_hint=_shape_em_prosa(forma),
    )


def _metric_ref_id(ref: str, resultado: MetricResult) -> str:
    """Identificador citavel e reproduzivel de um resultado de metrica.

    Nao inventa um sha: monta a chave a partir do que ja define o numero -
    metrica, entidade, periodo, escopo e `as_of`. Dois calculos iguais dao a
    mesma chave, que e o que permite verificar uma citacao antiga.
    """
    return (
        f"{ref}|{resultado.entity_id}|{resultado.period_end.isoformat()}"
        f"|{resultado.scope.value}|{resultado.as_of.isoformat()}"
    )


def _shape_em_prosa(forma) -> str | None:
    """A forma da serie, dita inteira - inclusive quando ela nao e uma linha.

    Uma serie que vai e volta descrita so pelas pontas produz uma frase certa
    na aritmetica e errada no que importa. O caso que motivou isto e o EBIT do
    GPA: -565 MM, +660 MM, -436 MM. Comparar as pontas dava "subiu", e o ano do
    meio - o unico positivo dos tres - sumia da frase.
    """
    if forma.direction is None:
        return None

    partes = [forma.direction.value]
    if forma.magnitude is not None:
        partes.append(f"/ {forma.magnitude.value}")
    partes.append(
        f"entre {forma.first_period.isoformat()} e {forma.last_period.isoformat()}"
    )
    if forma.reversals:
        partes.append(
            f"- NAO MONOTONICA, {forma.reversals} reversao(oes) no caminho: as "
            "pontas nao contam a serie"
        )
    if forma.crosses_zero:
        partes.append(
            "- a serie troca de sinal, entao nao ha variacao percentual que se "
            "possa ler"
        )
    return " ".join(partes)


def _step_breakdown(tools: PatTools, step: StepRequest) -> StepOutcome:
    resultado = tools.breakdown(
        step.ref, period_from=step.period_from, period_to=step.period_to
    )
    if hasattr(resultado, "reason"):
        return StepOutcome(
            step_id=step.step_id,
            kind=step.kind,
            ref=step.ref,
            unavailable=(f"{step.ref}: {resultado.reason.value} - {resultado.message}",),
        )
    ident = (
        f"{step.ref}|{tools.context.entity_id}|{step.period_from.isoformat()}"
        f":{step.period_to.isoformat()}|{tools.context.as_of.isoformat()}"
    )
    linhas = [f"{step.ref}: {len(getattr(resultado, 'terms', ()) or ())} termo(s)"]
    return StepOutcome(
        step_id=step.step_id,
        kind=step.kind,
        ref=step.ref,
        result_ids=(ident,),
        rendered=tuple(linhas),
    )


def _step_evidence(tools: PatTools, step: StepRequest) -> StepOutcome:
    resultado = tools.evidence(step.terms, limit=step.limit)
    if hasattr(resultado, "reason"):
        return StepOutcome(
            step_id=step.step_id,
            kind=step.kind,
            unavailable=(f"busca {list(step.terms)}: {resultado.reason.value} - {resultado.message}",),
        )
    return StepOutcome(
        step_id=step.step_id,
        kind=step.kind,
        unit_ids=tuple(h.quote.unit_id for h in resultado.hits),
        # O trecho e verbatim e vai inteiro para a leitura. Cortar aqui
        # produziria uma citacao que nao bate byte a byte com a fonte.
        rendered=tuple(
            f"[{h.quote.published_at.isoformat()}] {h.quote.title}: {h.quote.text}"
            for h in resultado.hits
        ),
    )


def _step_accounts(tools: PatTools, step: StepRequest) -> StepOutcome:
    linhas = tools.accounts(statement=step.statement, period_end=step.period_ends[0])
    if not linhas:
        return StepOutcome(
            step_id=step.step_id,
            kind=step.kind,
            unavailable=(
                f"nenhuma conta em {step.statement} para "
                f"{step.period_ends[0].isoformat()} sob as_of "
                f"{tools.context.as_of.isoformat()}",
            ),
        )
    return StepOutcome(
        step_id=step.step_id,
        kind=step.kind,
        result_ids=tuple(x.fact_id for x in linhas),
        rendered=tuple(f"{x.account_code} {x.label}: {x.value}" for x in linhas),
    )


# ---------------------------------------------------------------------------
# TEST - a verificacao mecanica das citacoes
# ---------------------------------------------------------------------------


def _verify(
    findings: tuple[FindingProposal, ...], outcomes: tuple[StepOutcome, ...]
) -> tuple[tuple[FindingProposal, ...], tuple[RejectedFinding, ...]]:
    """Toda citacao tem que estar no que os passos produziram.

    Uma frase que cita um ID inexistente parece ancorada e nao esta - e a
    alucinacao de procedencia, que e pior que a de conteudo justamente porque
    passa por verificada.
    """
    disponiveis: set[str] = set()
    for outcome in outcomes:
        disponiveis |= outcome.citable_ids

    aceitos: list[FindingProposal] = []
    rejeitados: list[RejectedFinding] = []
    for finding in findings:
        if not finding.text.strip():
            rejeitados.append(
                RejectedFinding(
                    text=finding.text or "(vazio)",
                    reason=RejectionReason.EMPTY_AFTER_STRIP,
                    detail="interpretacao sem texto",
                )
            )
            continue
        desconhecidas = [c for c in finding.cites if c not in disponiveis]
        if desconhecidas:
            rejeitados.append(
                RejectedFinding(
                    text=finding.text,
                    reason=RejectionReason.UNKNOWN_CITATION,
                    detail=(
                        f"cita {len(desconhecidas)} identificador(es) que nenhum passo "
                        f"produziu: {desconhecidas[:3]}"
                    ),
                )
            )
            continue
        aceitos.append(finding)
    return tuple(aceitos), tuple(rejeitados)


# ---------------------------------------------------------------------------
# O ciclo de uma tarefa
# ---------------------------------------------------------------------------


def run_task(
    workspace: Workspace,
    tools: PatTools,
    reasoner: Reasoner,
    *,
    slug: str,
    context: ResearchContext | None = None,
    actor: Actor = Actor.AGENT,
) -> TaskReport:
    """Uma volta completa do ciclo numa tarefa. Grava tudo no diario.

    A tarefa vai para RUNNING antes dos passos e sai com o veredito. Marcar
    RUNNING no diario e o que faz um processo morto no meio deixar rastro: ao
    reabrir, uma tarefa RUNNING que ninguem esta rodando e visivelmente uma
    corrida interrompida, e nao uma tarefa que nunca comecou.
    """
    tarefa = workspace.state.agenda.task(slug)
    if tarefa is None:
        raise LookupError(f"tarefa {slug!r} nao existe na agenda")
    ctx = context or build_context(workspace, tools)

    workspace.apply(
        TaskStatusChanged(slug=slug, status=TaskStatus.RUNNING, note="ciclo iniciado"),
        actor=actor,
    )

    passos = reasoner.plan(task=tarefa, context=ctx)
    resultados = _research(tools, passos)
    propostos = reasoner.interpret(task=tarefa, outcomes=resultados, context=ctx)
    aceitos, rejeitados = _verify(propostos, resultados)
    veredito = reasoner.judge(
        task=tarefa, outcomes=resultados, findings=aceitos, context=ctx
    )

    _record(workspace, slug=slug, findings=aceitos, rejected=rejeitados, actor=actor)
    _apply_verdict(workspace, slug=slug, verdict=veredito, actor=actor)

    return TaskReport(
        task=slug,
        steps=passos,
        outcomes=resultados,
        findings=aceitos,
        rejected=rejeitados,
        verdict=veredito,
        reasoner_id=reasoner.reasoner_id,
    )


def _record(
    workspace: Workspace,
    *,
    slug: str,
    findings: tuple[FindingProposal, ...],
    rejected: tuple[RejectedFinding, ...],
    actor: Actor,
) -> None:
    """UPDATE. Os aceitos viram achado; os barrados viram achado tambem.

    O achado de uma rejeicao e sobre o RACIOCINIO, e nao sobre a empresa - ele
    entra sem citacao, que e exatamente o que ele e: uma afirmacao sem ancora.
    Guardar isso e o que permite notar depois que um raciocinador cita o que
    nao existe com frequencia.
    """
    for finding in findings:
        workspace.apply(
            FindingRecorded(
                slug=slug,
                text=finding.text,
                result_ids=tuple(c for c in finding.cites if "|" in c),
                unit_ids=tuple(c for c in finding.cites if "|" not in c),
            ),
            actor=actor,
        )
    for rejeitado in rejected:
        workspace.apply(
            FindingRecorded(
                slug=slug,
                text=(
                    f"[interpretacao barrada: {rejeitado.reason.value}] "
                    f"{rejeitado.text} ({rejeitado.detail})"
                ),
            ),
            actor=actor,
        )


def _apply_verdict(
    workspace: Workspace, *, slug: str, verdict: TaskVerdict, actor: Actor
) -> None:
    if verdict.status in (TaskStatus.BLOCKED, TaskStatus.NEEDS_HUMAN):
        workspace.apply(
            BlockerRecorded(
                slug=slug,
                blocker=Blocker(
                    reason=verdict.blocker_reason,
                    remedy=verdict.blocker_remedy,
                    needs_human=verdict.needs_human,
                ),
            ),
            actor=actor,
        )
        return
    workspace.apply(
        TaskStatusChanged(slug=slug, status=verdict.status, note=verdict.rationale),
        actor=actor,
    )


# ---------------------------------------------------------------------------
# NEXT - o laco sobre a agenda
# ---------------------------------------------------------------------------

MAX_TASKS = 12
"""Teto de tarefas por corrida.

Nao e economia: e o que impede uma agenda que se realimenta de rodar para
sempre sem ninguem olhar. Quem quiser mais roda de novo, e a decisao de
continuar volta a ser de uma pessoa.
"""


def run_agenda(
    workspace: Workspace,
    tools: PatTools,
    reasoner: Reasoner,
    *,
    max_tasks: int = MAX_TASKS,
    actor: Actor = Actor.AGENT,
) -> AgendaRun:
    """Executa tarefas prontas, uma por uma, ate acabar ou bater o teto.

    "Pronta" e `agenda.ready()`: PENDING com dependencias concluidas. Uma
    tarefa que o ciclo bloqueia sai da fila e as dependentes dela ficam
    esperando - o que e o comportamento certo, e o que faz `is_exhausted`
    diferir de "tudo concluido".
    """
    relatorios: list[TaskReport] = []
    contexto = build_context(workspace, tools)

    while len(relatorios) < max_tasks:
        prontas = workspace.state.agenda.ready()
        if not prontas:
            esperando = workspace.state.agenda.waiting_on_dependency()
            motivo = (
                f"nenhuma tarefa pronta; {len(esperando)} esperando dependencia"
                if esperando
                else "agenda exaurida"
            )
            return AgendaRun(reports=tuple(relatorios), stopped_because=motivo)
        relatorios.append(
            run_task(
                workspace,
                tools,
                reasoner,
                slug=prontas[0].slug,
                context=contexto,
                actor=actor,
            )
        )
    return AgendaRun(
        reports=tuple(relatorios),
        stopped_because=f"teto de {max_tasks} tarefas por corrida",
    )


def phases() -> tuple[Phase, ...]:
    """As oito fases, na ordem. Existe para a documentacao e para o teste que
    confere que o laco passa por todas."""
    return tuple(Phase)

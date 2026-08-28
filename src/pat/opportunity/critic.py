"""O7 - o advogado do diabo, mecanico.

Duas funcoes, e as duas importam:

    `critique`               o que esta investigacao deixou de procurar
    `falsification_agenda`   as tarefas que consertariam isso

A segunda e o que separa uma etapa de falsificacao de uma lista de
reclamacoes. Um critico que so aponta produz um relatorio que alguem le,
concorda e arquiva; um que devolve tarefas coloca o trabalho na agenda, onde
ele fica visivel ate ser feito ou explicitamente descartado.

O que este modulo nao faz
-------------------------
Nao chama modelo, nao reescreve hipotese e nao muda estado. Ele le a dobra e
devolve objecoes. Mudar estado a partir de uma critica seria o laco
`critic -> autor -> critic`, que e um amostrador que uma hora aprova algo
errado - a mesma razao pela qual o critic da Fase 3 tambem nao corrige.

Sobre discordar do usuario
--------------------------
`USER_ASSERTION_CONTRADICTED` e um achado DURO, e e o que autoriza o agente a
dizer "eu nao aceitaria essa conclusao ainda". Ele so aparece quando ha
contra-evidencia registrada apontando para o contrario do que o humano
afirmou, e o achado carrega os enderecos dela. Discordar sem evidencia nao e
ceticismo - e outra opiniao, e o sistema nao tem por que ter opiniao.
"""

from __future__ import annotations

from pat.contracts.claims import Severity
from pat.contracts.opportunity import (
    Actor,
    EvidenceKind,
    Hypothesis,
    HypothesisStatus,
    TaskStatus,
)
from pat.contracts.opportunity.critic import (
    HARD_FINDINGS,
    Critique,
    CritiqueFinding,
    WorkspaceFinding,
)
from pat.contracts.opportunity.loop import TaskProposal
from pat.contracts.opportunity.agenda import TaskPriority

__all__ = ["critique", "falsification_agenda"]

_DIGITS = set("0123456789")


def _finding(code: WorkspaceFinding, **kw) -> CritiqueFinding:
    """Severidade vem da taxonomia, nunca de quem escreve o achado.

    Deixar cada ponto de chamada escolher produziria o mesmo codigo duro num
    lugar e leve no outro, e a pergunta "esta tese pode ser apresentada?"
    passaria a depender de qual caminho gerou o achado.
    """
    severidade = Severity.HARD if code in HARD_FINDINGS else Severity.SOFT
    return CritiqueFinding(code=code, severity=severidade, **kw)


def critique(state) -> Critique:
    """Le o estado do workspace e devolve as objecoes.

    Pura: nao toca disco, nao abre banco, nao chama modelo. E o que permite
    criticar um estado montado a mao num teste, e e o que garante que a
    critica de hoje sobre o diario de ontem da o mesmo resultado.
    """
    achados: list[CritiqueFinding] = []
    for hipotese in state.hypotheses:
        achados.extend(_critique_hypothesis(state, hipotese))
    for claim in state.claims:
        achados.extend(_critique_claim(claim))
    return Critique(
        findings=tuple(achados),
        hypotheses_checked=len(state.hypotheses),
        claims_checked=len(state.claims),
    )


def _critique_hypothesis(state, h: Hypothesis) -> list[CritiqueFinding]:
    achados: list[CritiqueFinding] = []
    sustentada = h.status is HypothesisStatus.SUPPORTED

    # -- duros ---------------------------------------------------------------

    if sustentada and h.is_falsifiable_now:
        decisivas = tuple(c.link.ref for c in h.counter if c.decisive)
        achados.append(
            _finding(
                WorkspaceFinding.DECISIVE_COUNTER_IGNORED,
                message=(
                    f"{h.slug} continua SUPPORTED com {len(decisivas)} contra-evidencia(s) "
                    "marcada(s) como decisiva(s): o falsificador ocorreu e nao foi honrado"
                ),
                remedy=(
                    f"Mude {h.slug} para REJECTED ou WEAKENED, ou explique por que a "
                    "contra-evidencia deixou de ser decisiva."
                ),
                hypothesis=h.slug,
                evidence_refs=decisivas,
            )
        )

    if sustentada and not h.was_falsification_attempted:
        achados.append(
            _finding(
                WorkspaceFinding.SUPPORTED_WITHOUT_FALSIFICATION,
                message=(
                    f"{h.slug} foi sustentada sem nenhum registro de busca por "
                    "contra-evidencia. Zero contra-evidencias e ambiguo entre "
                    "'procuramos e nao achamos' e 'nao procuramos'"
                ),
                remedy=(
                    f"Rode a busca por contra-evidencia de {h.slug} e registre COMO ela "
                    "foi feita (`falsification_attempted`)."
                ),
                hypothesis=h.slug,
            )
        )

    tarefas = tuple(t for t in state.agenda.tasks if t.hypothesis == h.slug)
    travadas = tuple(
        t for t in tarefas if t.status in (TaskStatus.BLOCKED, TaskStatus.NEEDS_HUMAN)
    )
    if sustentada and travadas:
        achados.append(
            _finding(
                WorkspaceFinding.SUPPORTED_WITH_BLOCKED_TASK,
                message=(
                    f"{h.slug} esta SUPPORTED enquanto {len(travadas)} tarefa(s) que a "
                    f"testariam continuam travadas: {[t.slug for t in travadas]}"
                ),
                remedy=(
                    "Destrave a tarefa e conclua o teste, ou baixe a hipotese para "
                    "WEAKENED dizendo que o teste nao foi feito."
                ),
                hypothesis=h.slug,
                task=travadas[0].slug,
            )
        )

    if h.opened_by is Actor.USER and h.is_falsifiable_now and h.status not in (
        HypothesisStatus.REJECTED,
        HypothesisStatus.WEAKENED,
    ):
        decisivas = tuple(c.link.ref for c in h.counter if c.decisive)
        achados.append(
            _finding(
                WorkspaceFinding.USER_ASSERTION_CONTRADICTED,
                message=(
                    f"a hipotese {h.slug}, afirmada pelo analista, tem contra-evidencia "
                    "decisiva registrada e continua de pe"
                ),
                remedy=(
                    "Apresente a contra-evidencia ao analista e peca uma decisao "
                    "explicita antes de seguir usando a hipotese."
                ),
                hypothesis=h.slug,
                evidence_refs=decisivas,
            )
        )

    # -- leves ---------------------------------------------------------------

    if sustentada and not h.alternatives:
        achados.append(
            _finding(
                WorkspaceFinding.NO_ALTERNATIVE_CONSIDERED,
                message=(
                    f"nenhuma explicacao alternativa foi registrada para {h.slug}. Uma "
                    "hipotese que explica os dados nao esta certa por isso"
                ),
                remedy=(
                    f"Registre ao menos uma alternativa para {h.slug} e diga em que base "
                    "ela foi descartada - ou deixe-a aberta."
                ),
                hypothesis=h.slug,
            )
        )

    periodos = _periods_supporting(h)
    if sustentada and len(periodos) == 1:
        achados.append(
            _finding(
                WorkspaceFinding.SINGLE_PERIOD_SUPPORT,
                message=(
                    f"{h.slug} se sustenta em numeros de um periodo so ({periodos.pop()}). "
                    "Um ano pode ser qualquer coisa"
                ),
                remedy="Estenda a serie: persistencia e o que separa caracteristica de acidente.",
                hypothesis=h.slug,
            )
        )

    if sustentada and h.supporting and not any(
        e.kind is EvidenceKind.METRIC for e in h.supporting
    ):
        achados.append(
            _finding(
                WorkspaceFinding.ISSUER_ONLY_SUPPORT,
                message=(
                    f"{h.slug} se sustenta so em citacao: repetir o que a companhia disse "
                    "e diferente de ter verificado"
                ),
                remedy="Ligue pelo menos um resultado do motor a hipotese.",
                hypothesis=h.slug,
            )
        )

    for falsificador in h.untested_falsifiers:
        achados.append(
            _finding(
                WorkspaceFinding.FALSIFIER_UNTESTED,
                message=(
                    f"o falsificador {falsificador!r} de {h.slug} nunca foi testado. "
                    "Declarar o que derrubaria e depois nao olhar e o mesmo que nao "
                    "ter declarado"
                ),
                remedy=(
                    f"Crie a tarefa que testa {falsificador!r} e registre a tentativa."
                ),
                hypothesis=h.slug,
            )
        )

    if h.status is HypothesisStatus.OPEN and not tarefas:
        achados.append(
            _finding(
                WorkspaceFinding.ORPHAN_HYPOTHESIS,
                message=f"{h.slug} esta aberta e nenhuma tarefa a testa",
                remedy=f"Crie uma tarefa apontando para {h.slug}, ou feche a hipotese.",
                hypothesis=h.slug,
            )
        )

    return achados


def _critique_claim(claim) -> list[CritiqueFinding]:
    """Premissa nao sustenta afirmacao sobre o que a empresa FEZ.

    A checagem e por digito: um claim que carrega numero e uma afirmacao sobre
    grandeza, e uma grandeza ancorada so numa premissa e uma projecao escrita
    no passado.
    """
    if not any(c in _DIGITS for c in claim.text):
        return []
    tipos = {e.kind for e in claim.evidence}
    if tipos <= {EvidenceKind.ASSUMPTION}:
        return [
            _finding(
                WorkspaceFinding.ASSUMPTION_AS_OBSERVATION,
                message=(
                    f"o claim {claim.slug} afirma grandeza e se ancora apenas em "
                    "premissa. Premissa sustenta projecao, nunca historico"
                ),
                remedy=(
                    f"Ligue {claim.slug} a um resultado do motor, ou reescreva-o como "
                    "projecao declarada."
                ),
                claim=claim.slug,
            )
        ]
    return []


def _periods_supporting(h: Hypothesis) -> set[str]:
    """Periodos distintos citados pelas evidencias metricas da hipotese.

    O identificador de um resultado de metrica e `ref|entidade|periodo|escopo|as_of`;
    o periodo e o terceiro campo. Ler por posicao e feio, e a alternativa -
    reabrir o warehouse aqui - transformaria uma funcao pura numa consulta.
    """
    periodos: set[str] = set()
    for evidencia in h.supporting:
        if evidencia.kind is not EvidenceKind.METRIC:
            continue
        partes = evidencia.ref.split("|")
        if len(partes) >= 3:
            periodos.add(partes[2])
    return periodos


# ---------------------------------------------------------------------------
# A agenda de falsificacao
# ---------------------------------------------------------------------------


def falsification_agenda(state) -> tuple[TaskProposal, ...]:
    """As tarefas que a critica pede. Uma por falsificador nao testado.

    Prioridade alta de proposito: testar o que derrubaria a tese e mais
    urgente que acrescentar mais uma evidencia a favor - a segunda so muda a
    confianca, a primeira muda a conclusao.

    A funcao propoe; quem coloca na agenda e `plan_agenda`. Manter a proposta
    separada da gravacao e o que permite mostrar ao analista o que o critico
    quer fazer antes de fazer.
    """
    propostas: list[TaskProposal] = []
    for h in state.hypotheses:
        if h.status in (HypothesisStatus.REJECTED, HypothesisStatus.INCONCLUSIVE):
            continue
        for i, falsificador in enumerate(h.untested_falsifiers):
            slug = f"falsificar-{h.slug}-{i}"[:63]
            propostas.append(
                TaskProposal(
                    slug=slug,
                    objective=f"testar se ocorreu: {falsificador}",
                    completion_criteria=(
                        "o falsificador foi procurado no motor ou no corpus, e a "
                        "tentativa esta registrada com o COMO"
                    ),
                    priority=TaskPriority.HIGH,
                    hypothesis=h.slug,
                )
            )
        if h.status is HypothesisStatus.SUPPORTED and not h.alternatives:
            propostas.append(
                TaskProposal(
                    slug=f"alternativa-{h.slug}"[:63],
                    objective=f"procurar explicacao alternativa para: {h.statement}",
                    completion_criteria=(
                        "ao menos uma alternativa registrada, descartada com base ou "
                        "deixada aberta"
                    ),
                    priority=TaskPriority.HIGH,
                    hypothesis=h.slug,
                )
            )
    return tuple(propostas)

"""A uniao dos corpos de evento, e o envelope que os carrega.

Este modulo e folha de proposito: ele importa todos os modulos de contrato do
Opportunity, e nenhum deles importa este. E o que permite que `agenda.py`,
`hypothesis.py`, `valuation.py` e `thesis.py` definam os seus proprios eventos
sem que nenhum precise conhecer os outros - a uniao se monta aqui, uma vez.

Sem esta separacao, `base.py` teria que conhecer a hipotese para poder
declarar o evento que muda o estado dela, e a hipotese teria que conhecer
`base.py` para ter `Frozen` e `Slug`. Import circular, e a saida usual seria
uma string com o nome do tipo - que e como se perde a checagem que a uniao
discriminada existe para dar.
"""

from __future__ import annotations

import typing
from typing import Literal

from pydantic import Field

from pat.contracts.common import AwareDatetime, Frozen
from pat.contracts.opportunity.agenda import (
    AgendaObjectiveSet,
    BlockerCleared,
    BlockerRecorded,
    FindingRecorded,
    QuestionAnswered,
    QuestionOpened,
    TaskCreated,
    TaskPriorityChanged,
    TaskStatusChanged,
)
from pat.contracts.opportunity.base import (
    Actor,
    AsOfAdvanced,
    CoverageRefreshed,
    MandateSet,
    NoteAdded,
    TitleSet,
    WorkspaceArchived,
    WorkspaceCreated,
    WorkspaceReopened,
)
from pat.contracts.opportunity.chat import ChatTurnRecorded
from pat.contracts.opportunity.critic import (
    AlternativeConsidered,
    FalsificationAttempted,
)
from pat.contracts.opportunity.valuation import (
    AssumptionChanged,
    ValuationDeclared,
    ValuationInterpreted,
)
from pat.contracts.opportunity.thesis import ThesisDrafted
from pat.contracts.opportunity.hypothesis import (
    ClaimAsserted,
    ConclusionDrawn,
    CounterEvidenceAdded,
    EvidenceLinked,
    FalsifierAdded,
    HypothesisOpened,
    HypothesisStatusChanged,
)

__all__ = ["EVENT_BODIES", "EventBody", "JournalEvent"]


# A uniao cresce a cada milestone. `opportunity/state.py` exige um tratador
# para cada membro, e o teste de exaustividade compara os dois conjuntos - o
# que faz um corpo novo sem dobra falhar em teste, em vez de virar evento que
# o replay ignora em silencio.
EventBody = (
    WorkspaceCreated
    | MandateSet
    | TitleSet
    | AsOfAdvanced
    | CoverageRefreshed
    | NoteAdded
    | WorkspaceArchived
    | WorkspaceReopened
    # O1
    | AgendaObjectiveSet
    | TaskCreated
    | TaskStatusChanged
    | TaskPriorityChanged
    | FindingRecorded
    | BlockerRecorded
    | BlockerCleared
    | QuestionOpened
    | QuestionAnswered
    # O2
    | HypothesisOpened
    | FalsifierAdded
    | EvidenceLinked
    | CounterEvidenceAdded
    | HypothesisStatusChanged
    | ClaimAsserted
    | ConclusionDrawn
    # O7
    | FalsificationAttempted
    | AlternativeConsidered
    # O8
    | ValuationDeclared
    | AssumptionChanged
    | ValuationInterpreted
    # O9
    | ThesisDrafted
    # O5
    | ChatTurnRecorded
)


class JournalEvent(Frozen):
    """Uma linha do diario.

    `seq` comeca em 1 e nao tem buraco. `at` e o relogio de quando o evento
    foi gravado, e nao tem nada a ver com `as_of`: um evento gravado hoje pode
    falar do mundo como ele era em 2024, e confundir os dois e exatamente o
    erro que a regra bitemporal existe para impedir.
    """

    event_version: Literal["v1"] = "v1"
    seq: int = Field(ge=1)
    at: AwareDatetime
    actor: Actor
    body: EventBody = Field(discriminator="kind")

    @property
    def kind(self) -> str:
        return self.body.kind


EVENT_BODIES: frozenset[type] = frozenset(typing.get_args(EventBody))
"""Membros da uniao, como conjunto de tipos. Derivado da uniao e nunca escrito
a mao: uma lista a mao envelhece calada, que e o modo de falha que a checagem
de exaustividade existe para impedir."""

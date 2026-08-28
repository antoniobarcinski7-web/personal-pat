"""Contratos da camada Opportunity.

Um pacote, e nao um modulo, porque a uniao de eventos precisa de uma folha:
`base.py` da os tipos comuns, cada milestone declara os seus eventos no seu
proprio modulo, e `events.py` - que ninguem importa de volta - monta a uniao.
Sem essa forma, `base.py` teria que conhecer a hipotese para declarar o evento
que a muda, e a hipotese teria que conhecer `base.py` para ter `Frozen`.

Quem consome importa daqui:

    from pat.contracts.opportunity import Actor, JournalEvent, TaskCreated
"""

from pat.contracts.opportunity.agenda import (
    TERMINAL_STATUSES,
    AgendaObjectiveSet,
    Blocker,
    BlockerCleared,
    BlockerRecorded,
    Finding,
    FindingRecorded,
    OpenQuestion,
    QuestionAnswered,
    QuestionOpened,
    ResearchAgenda,
    ResearchTask,
    TaskCreated,
    TaskPriority,
    TaskPriorityChanged,
    TaskStatus,
    TaskStatusChanged,
    priority_rank,
)
from pat.contracts.opportunity.base import (
    SLUG_PATTERN,
    WORKSPACE_ID_PATTERN,
    Actor,
    AsOfAdvanced,
    CompanyProfile,
    CoverageRefreshed,
    CoverageSnapshot,
    MandateSet,
    Note,
    NoteAdded,
    OpportunityWorkspace,
    Slug,
    TitleSet,
    WorkspaceArchived,
    WorkspaceCreated,
    WorkspaceId,
    WorkspaceReopened,
    WorkspaceStatus,
    check_slug,
    check_workspace_id,
)
from pat.contracts.opportunity.events import EVENT_BODIES, EventBody, JournalEvent

__all__ = [
    "EVENT_BODIES",
    "SLUG_PATTERN",
    "TERMINAL_STATUSES",
    "WORKSPACE_ID_PATTERN",
    "Actor",
    "AgendaObjectiveSet",
    "AsOfAdvanced",
    "Blocker",
    "BlockerCleared",
    "BlockerRecorded",
    "CompanyProfile",
    "CoverageRefreshed",
    "CoverageSnapshot",
    "EventBody",
    "Finding",
    "FindingRecorded",
    "JournalEvent",
    "MandateSet",
    "Note",
    "NoteAdded",
    "OpenQuestion",
    "OpportunityWorkspace",
    "QuestionAnswered",
    "QuestionOpened",
    "ResearchAgenda",
    "ResearchTask",
    "Slug",
    "TaskCreated",
    "TaskPriority",
    "TaskPriorityChanged",
    "TaskStatus",
    "TaskStatusChanged",
    "TitleSet",
    "WorkspaceArchived",
    "WorkspaceCreated",
    "WorkspaceId",
    "WorkspaceReopened",
    "WorkspaceStatus",
    "check_slug",
    "check_workspace_id",
    "priority_rank",
]

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
"""

from __future__ import annotations

import typing
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from pat.contracts.common import Frozen
from pat.contracts.opportunity import (
    AsOfAdvanced,
    CompanyProfile,
    CoverageRefreshed,
    CoverageSnapshot,
    EventBody,
    JournalEvent,
    MandateSet,
    Note,
    NoteAdded,
    OpportunityWorkspace,
    TitleSet,
    WorkspaceArchived,
    WorkspaceCreated,
    WorkspaceReopened,
    WorkspaceStatus,
)

__all__ = ["FoldError", "OpportunityState", "fold"]


class FoldError(RuntimeError):
    """Sequencia de eventos que nao descreve uma investigacao possivel.

    Ex.: evento antes da criacao do workspace, ou `as_of` andando para tras.
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
    notes: tuple[Note, ...] = ()

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
            notes=tuple(self.notes),
        )


# -- tratadores -------------------------------------------------------------


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


_HANDLERS: dict[type, Callable[[_Builder, JournalEvent, typing.Any], None]] = {
    WorkspaceCreated: _on_created,
    MandateSet: _on_mandate,
    TitleSet: _on_title,
    AsOfAdvanced: _on_as_of,
    CoverageRefreshed: _on_coverage,
    NoteAdded: _on_note,
    WorkspaceArchived: _on_archived,
    WorkspaceReopened: _on_reopened,
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

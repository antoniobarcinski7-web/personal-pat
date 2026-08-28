"""A dobra e pura, total e exaustiva.

Pura: nao toca disco. Total: todo corpo de evento tem tratador. Exaustiva: a
comparacao e feita contra os membros de `EventBody`, e nao contra uma lista
escrita a mao - lista a mao envelhece calada, que e exatamente o modo de falha
que este teste existe para impedir.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pat.contracts.opportunity import (
    Actor,
    JournalEvent,
    NoteAdded,
    WorkspaceCreated,
)
from pat.opportunity.state import _HANDLERS, FoldError, event_body_types, fold

AT = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)


def test_todo_corpo_de_evento_tem_tratador():
    faltando = event_body_types() - set(_HANDLERS)
    assert not faltando, (
        f"corpos sem tratador na dobra: {sorted(t.__name__ for t in faltando)}. "
        "Um evento sem dobra e gravado e ignorado no replay - perda de estado "
        "silenciosa."
    )
    sobrando = set(_HANDLERS) - event_body_types()
    assert not sobrando, f"tratador sem corpo correspondente: {sobrando}"


def _evento(seq: int, body, actor: Actor = Actor.USER) -> JournalEvent:
    return JournalEvent(seq=seq, at=AT, actor=actor, body=body)


def test_dobra_de_sequencia_vazia_e_erro(gpa_profile):
    with pytest.raises(FoldError, match="sem evento de criacao"):
        fold("a" * 16, ())


def test_dobra_e_deterministica(gpa_profile):
    eventos = (
        _evento(1, WorkspaceCreated(company=gpa_profile, as_of=date(2025, 6, 30))),
        _evento(2, NoteAdded(text="uma nota")),
    )
    assert fold("a" * 16, eventos) == fold("a" * 16, eventos)


def test_segunda_criacao_e_recusada(gpa_profile, intel_profile):
    """Um diario descreve uma investigacao. Aceitar uma segunda criacao
    trocaria a empresa do workspace no meio da tese."""
    eventos = (
        _evento(1, WorkspaceCreated(company=gpa_profile, as_of=date(2025, 6, 30))),
        _evento(2, WorkspaceCreated(company=intel_profile, as_of=date(2025, 6, 30))),
    )
    with pytest.raises(FoldError, match="segundo `workspace_created`"):
        fold("a" * 16, eventos)


def test_updated_at_segue_o_ultimo_evento(gpa_profile):
    depois = datetime(2025, 8, 1, 8, 0, tzinfo=UTC)
    eventos = (
        _evento(1, WorkspaceCreated(company=gpa_profile, as_of=date(2025, 6, 30))),
        JournalEvent(seq=2, at=depois, actor=Actor.AGENT, body=NoteAdded(text="x")),
    )
    estado = fold("a" * 16, eventos)
    assert estado.workspace.created_at == AT
    assert estado.workspace.updated_at == depois


def test_notas_guardam_a_ordem_do_diario_nao_a_do_relogio(gpa_profile):
    """Dois eventos no mesmo instante: `seq` desempata, e nao `at`."""
    eventos = (
        _evento(1, WorkspaceCreated(company=gpa_profile, as_of=date(2025, 6, 30))),
        _evento(2, NoteAdded(text="primeira")),
        _evento(3, NoteAdded(text="segunda")),
    )
    estado = fold("a" * 16, eventos)
    assert [n.text for n in estado.notes] == ["primeira", "segunda"]
    assert [n.seq for n in estado.notes] == [2, 3]

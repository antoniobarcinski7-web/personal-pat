"""L5 - Opportunity: o parceiro de pesquisa que fica em cima do PAT.

    PAT          motor de pesquisa deterministico. Produz numero.
    Opportunity  parceiro de pesquisa. Nao produz numero nenhum.

A divisao e literal e vale para o codigo: nada em `pat/opportunity/` calcula,
agrega ou converte. Quando um numero aparece numa tese escrita aqui, ele veio
de um `MetricResult` do motor ou de um `QuoteClaim` verbatim de documento - e
os dois carregam para dentro do Opportunity toda a procedencia que tinham.

O que esta camada acrescenta e o que o motor deliberadamente nao tem: memoria
de uma investigacao. O que foi perguntado, o que ficou aberto, o que foi
suposto, o que foi refutado, e por que alguem mudou de ideia.

Montagem tipica:

    from pat.opportunity import create_workspace, open_workspace, profile_for
    ws = create_workspace(paths.opportunity, company=profile_for(conn, eid),
                          as_of=date(2025, 6, 30))
    ws.apply(NoteAdded(text="..."), actor=Actor.USER)
"""

from __future__ import annotations

from pat.opportunity.company import UnknownEntity, profile_for
from pat.opportunity.critic import critique, falsification_agenda
from pat.opportunity.documents import (
    DocumentInventory,
    DocumentProvider,
    StoredDocument,
    inventory,
    stored_documents,
)
from pat.opportunity.journal import Journal, JournalCorrupt
from pat.opportunity.providers import (
    BrazilDocumentProvider,
    USDocumentProvider,
    provider_for,
)
from pat.opportunity.state import FoldError, OpportunityState, fold
from pat.opportunity.store import (
    Workspace,
    WorkspaceArchivedError,
    WorkspaceNotFound,
    create_workspace,
    list_workspaces,
    new_workspace_id,
    open_workspace,
)
from pat.opportunity.tools import AccountLine, PatTools, ToolContext

__all__ = [
    "AccountLine",
    "BrazilDocumentProvider",
    "DocumentInventory",
    "DocumentProvider",
    "FoldError",
    "Journal",
    "JournalCorrupt",
    "OpportunityState",
    "PatTools",
    "StoredDocument",
    "ToolContext",
    "USDocumentProvider",
    "UnknownEntity",
    "Workspace",
    "WorkspaceArchivedError",
    "WorkspaceNotFound",
    "create_workspace",
    "critique",
    "falsification_agenda",
    "fold",
    "inventory",
    "list_workspaces",
    "new_workspace_id",
    "open_workspace",
    "profile_for",
    "provider_for",
    "stored_documents",
]

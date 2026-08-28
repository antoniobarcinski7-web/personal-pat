"""Onde os workspaces moram, e como se abre um.

Layout:

    <home>/opportunity/<workspace_id>/journal.jsonl

Um diretorio por workspace, e nada alem do diario dentro dele. Nao existe
arquivo de "estado corrente" ao lado: ele seria uma segunda fonte de verdade,
e no dia em que divergisse do diario ninguem saberia qual das duas esta certa.
O estado corrente e sempre a dobra, sempre recalculada.

`workspace_id` e emitido aqui, nunca escolhido por quem chama - mesma regra do
`session_id` do chat. Vir de dentro e o que torna path traversal impossivel na
origem, em vez de depender de uma validacao que alguem pode remover depois por
parecer redundante.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from pat.contracts.opportunity import (
    Actor,
    CompanyProfile,
    EventBody,
    JournalEvent,
    WorkspaceCreated,
    WorkspaceReopened,
    WorkspaceStatus,
    check_workspace_id,
)
from pat.opportunity.journal import Journal
from pat.opportunity.state import OpportunityState, fold

__all__ = [
    "Workspace",
    "WorkspaceArchivedError",
    "WorkspaceNotFound",
    "create_workspace",
    "list_workspaces",
    "new_workspace_id",
    "open_workspace",
]

JOURNAL_NAME = "journal.jsonl"


class WorkspaceNotFound(LookupError):
    pass


class WorkspaceArchivedError(RuntimeError):
    """Escrita num workspace arquivado.

    Arquivar e uma declaracao de que a investigacao terminou; aceitar evento
    novo depois faria a data de encerramento mentir. Reabrir e explicito e
    fica no diario.
    """


def new_workspace_id(*, created_at: datetime | None = None, nonce: bytes | None = None) -> str:
    """Sha256 de (instante, nonce), truncado em 16 hex.

    Nao e derivado do `entity_id` de proposito: duas investigacoes sobre a
    mesma empresa - mandatos diferentes, momentos diferentes - sao workspaces
    diferentes, e um id derivado da empresa faria a segunda sobrescrever a
    primeira.
    """
    momento = (created_at or datetime.now(UTC)).isoformat()
    material = momento.encode("utf-8") + (nonce if nonce is not None else os.urandom(16))
    return hashlib.sha256(material).hexdigest()[:16]


class Workspace:
    """Um workspace aberto: o diario mais a dobra corrente.

    O objeto e um punho (handle), nao o estado. `state` e recalculado a cada
    `apply`, e nunca mutado no lugar - quem guardou uma referencia ao estado
    antigo continua vendo o estado antigo, que e o que se espera de um valor
    imutavel.
    """

    def __init__(self, workspace_id: str, journal: Journal) -> None:
        self._workspace_id = check_workspace_id(workspace_id)
        self._journal = journal
        self._state = fold(workspace_id, journal.read())

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    @property
    def state(self) -> OpportunityState:
        return self._state

    @property
    def journal(self) -> Journal:
        return self._journal

    def apply(
        self,
        body: EventBody,
        *,
        actor: Actor,
        at: datetime | None = None,
    ) -> OpportunityState:
        """Grava um evento e devolve o estado ja dobrado.

        A dobra roda ANTES de o evento ir para o disco, sobre a sequencia que
        existiria com ele: um evento que `fold` recusa - `as_of` para tras, por
        exemplo - nunca chega ao arquivo. O contrario deixaria no diario um
        evento que impede o proprio workspace de abrir.
        """
        if self._state.workspace.status is WorkspaceStatus.ARCHIVED and not isinstance(
            body, WorkspaceReopened
        ):
            raise WorkspaceArchivedError(
                f"workspace {self._workspace_id} esta arquivado; "
                "reabra antes de gravar (`pat opportunity reopen`)."
            )
        candidato = JournalEvent(
            seq=self._state.workspace.seq + 1,
            at=at or datetime.now(UTC),
            actor=actor,
            body=body,
        )
        novo = fold(self._workspace_id, (*self._journal.read(), candidato))
        self._journal.append(body, actor=actor, at=candidato.at)
        self._state = novo
        return novo

    def reload(self) -> OpportunityState:
        """Redobra a partir do disco. Existe para o teste de reabertura e para
        um processo que suspeite de escrita concorrente."""
        self._state = fold(self._workspace_id, self._journal.read())
        return self._state


def _dir(root: Path, workspace_id: str) -> Path:
    return Path(root) / check_workspace_id(workspace_id)


def create_workspace(
    root: Path,
    *,
    company: CompanyProfile,
    as_of: date,
    title: str | None = None,
    mandate: str | None = None,
    actor: Actor = Actor.USER,
    created_at: datetime | None = None,
    workspace_id: str | None = None,
) -> Workspace:
    """Abre uma investigacao nova.

    `workspace_id` e parametro so para o teste fixar o identificador. Em
    producao ninguem passa, e o valor vem de `new_workspace_id`.
    """
    momento = created_at or datetime.now(UTC)
    wid = workspace_id or new_workspace_id(created_at=momento)
    caminho = _dir(root, wid)
    if (caminho / JOURNAL_NAME).exists():
        raise FileExistsError(f"workspace {wid} ja existe em {caminho}")
    caminho.mkdir(parents=True, exist_ok=True)
    journal = Journal(caminho / JOURNAL_NAME)
    journal.append(
        WorkspaceCreated(company=company, as_of=as_of, title=title, mandate=mandate),
        actor=actor,
        at=momento,
    )
    return Workspace(wid, journal)


def open_workspace(root: Path, workspace_id: str) -> Workspace:
    caminho = _dir(root, workspace_id) / JOURNAL_NAME
    if not caminho.exists():
        raise WorkspaceNotFound(f"nenhum workspace {workspace_id} em {Path(root)}")
    return Workspace(workspace_id, Journal(caminho))


def list_workspaces(root: Path) -> Sequence[OpportunityState]:
    """Do mais recente para o mais antigo, por `created_at`.

    Dobra o diario inteiro de cada um. E aceitavel porque a lista e de uma
    pessoa so, com poucos workspaces; se um dia doer, o remedio e um indice
    derivado - reconstruivel a partir dos diarios -, nunca um arquivo de
    estado ao lado do diario.
    """
    raiz = Path(root)
    if not raiz.exists():
        return ()
    estados = []
    for filho in sorted(raiz.iterdir()):
        if not (filho / JOURNAL_NAME).exists():
            continue
        estados.append(Workspace(filho.name, Journal(filho / JOURNAL_NAME)).state)
    return tuple(
        sorted(estados, key=lambda e: (e.workspace.created_at, e.workspace_id), reverse=True)
    )

"""Localizacao dos artefatos em disco.

Um unico lugar decide onde as coisas moram, para que testes possam apontar
para um diretorio temporario sem que nenhuma outra camada saiba disso.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "PAT_HOME"
DEFAULT_HOME = Path("data")


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def bronze(self) -> Path:
        """Camada imutavel. Nunca editar, nunca sobrescrever."""
        return self.home / "bronze"

    @property
    def warehouse(self) -> Path:
        return self.home / "warehouse.duckdb"

    @property
    def runs(self) -> Path:
        return self.home / "runs"

    @property
    def llm(self) -> Path:
        """Respostas de modelo gravadas. Separado do bronze de proposito.

        As duas cadeias de procedencia sao paralelas e nunca se encontram: o
        bronze prova de onde veio um *numero*, isto aqui prova de onde veio uma
        *frase*. Guardar as duas no mesmo lugar apagaria a distincao no dia em
        que alguem escrevesse um `verify()` sobre o diretorio inteiro.
        """
        return self.home / "llm"

    @property
    def chat(self) -> Path:
        """Log das sessoes de conversa. Conveniencia de UI, nao auditoria.

        A auditoria de um turno ja esta em `research_run` e `llm_call`, ligados
        pelo `manifest_id`. O que mora aqui e so qual turno veio antes de qual -
        e por isso apagar `data/chat/` nao perde nada auditavel, enquanto apagar
        `research_run` perde. Guardar as duas coisas no mesmo lugar apagaria a
        distincao.
        """
        return self.home / "chat"

    @property
    def opportunity(self) -> Path:
        """Diarios dos workspaces do Opportunity. Um diretorio por workspace.

        Ao contrario de `chat`, isto NAO e conveniencia de UI: o diario e a
        unica copia do raciocinio - o que foi suposto, o que foi rejeitado e
        por que. O warehouse guarda o numero; nada nele guarda a razao de
        alguem ter mudado de ideia sobre a empresa. Apagar este diretorio
        perde a investigacao inteira, e por isso ele nao mora dentro de
        `chat/`.
        """
        return self.home / "opportunity"

    def ensure(self) -> "Paths":
        for path in (self.home, self.bronze, self.runs, self.llm, self.chat, self.opportunity):
            path.mkdir(parents=True, exist_ok=True)
        return self


def resolve_paths(home: str | os.PathLike[str] | None = None) -> Paths:
    if home is not None:
        return Paths(Path(home).expanduser().resolve())
    if env := os.environ.get(ENV_HOME):
        return Paths(Path(env).expanduser().resolve())
    return Paths(DEFAULT_HOME.resolve())

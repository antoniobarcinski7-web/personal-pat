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

    def ensure(self) -> "Paths":
        for path in (self.home, self.bronze, self.runs):
            path.mkdir(parents=True, exist_ok=True)
        return self


def resolve_paths(home: str | os.PathLike[str] | None = None) -> Paths:
    if home is not None:
        return Paths(Path(home).expanduser().resolve())
    if env := os.environ.get(ENV_HOME):
        return Paths(Path(env).expanduser().resolve())
    return Paths(DEFAULT_HOME.resolve())

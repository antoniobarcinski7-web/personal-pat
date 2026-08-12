"""Manifesto de execucao.

Toda escrita no bronze acontece dentro de um run identificado, e todo
documento aponta para o run que o trouxe. Meses depois isso responde "de
onde veio este arquivo e com qual versao do codigo ele foi buscado" sem
depender de memoria ou de log solto.
"""

from __future__ import annotations

import platform
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pat import __version__
from pat.contracts.common import RunStatus
from pat.contracts.lineage import Run


def make_run_id(now: datetime | None = None) -> str:
    """Ordenavel por tempo e globalmente unico."""
    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{uuid.uuid4().hex[:8]}"


def current_git_sha(cwd: Path | None = None) -> str | None:
    """SHA do commit, quando o projeto esta sob git. None e aceitavel."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def new_run(command: str, *, notes: str | None = None, cwd: Path | None = None) -> Run:
    return Run(
        run_id=make_run_id(),
        command=command,
        started_at=datetime.now(UTC),
        status=RunStatus.RUNNING,
        pat_version=__version__,
        python_version=platform.python_version(),
        git_sha=current_git_sha(cwd),
        notes=notes,
    )


def finish(run: Run, status: RunStatus) -> Run:
    return run.model_copy(update={"status": status, "finished_at": datetime.now(UTC)})

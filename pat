#!/usr/bin/env bash
# Atalho para rodar o PAT deste worktree, de qualquer diretorio.
#
# Existe porque o console script `pat` instalado pelo `uv` nao funciona no
# Python 3.14 desta maquina: o interpretador ignora arquivos `.pth` marcados
# com a flag UF_HIDDEN do macOS, e o pacote nunca entra no `sys.path`.
# `PYTHONPATH=src` contorna sem depender do `.pth`.
#
# Tambem resolve o `cd`: `$(dirname "$0")` ancora no worktree, entao
# `~/algum/outro/lugar $ /caminho/para/pat opportunity status` funciona.
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"
PYTHONPATH=src exec uv run --locked python -m pat.cli "$@"

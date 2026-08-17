"""Manifesto de uma execucao de pesquisa.

Segue a convencao de `contracts/lineage.py::Run` nos tres campos de ambiente
(`pat_version`, `python_version`, `git_sha`), mas e contrato proprio e tabela
propria: `Run` e o manifesto de ingestao, escrito pelo `Catalog`, e dobrar os
dois significados num tipo so seria trocar clareza por economia de linha.

No caminho deterministico, `planner` e `writer` sao nulos - um plano lido de
arquivo nao teve autor de modelo, e dizer isso e mais honesto do que inventar
uma procedencia vazia.

`executed_at` e o unico campo nao deterministico, e entra no `manifest_id` de
proposito: duas execucoes do mesmo plano sao execucoes diferentes, e o que
tem que bater entre elas sao os `result_id` - nao a identidade da corrida.
"""

from __future__ import annotations

import platform
from datetime import UTC, datetime

from pat.contracts.research import (
    PlanProvenance,
    ResearchPlan,
    ResearchRunManifest,
)
from pat.research.canonical import sha256_of
from pat.research.execute import ExecutionOutcome


def build_manifest(
    *,
    plan: ResearchPlan,
    plan_id: str,
    capability_sha256: str,
    outcome: ExecutionOutcome,
    pat_version: str,
    git_sha: str | None = None,
    planner: PlanProvenance | None = None,
    writer: PlanProvenance | None = None,
    executed_at: datetime | None = None,
) -> ResearchRunManifest:
    """Manifesto a partir do que realmente rodou."""
    when = executed_at or datetime.now(UTC)

    metric_versions: list[str] = []
    mapping_shas: list[str] = []
    for result in outcome.results:
        metric = result.metric_result
        if metric is None:
            continue
        ref = f"{metric.metric}@{metric.metric_version}"
        if ref not in metric_versions:
            metric_versions.append(ref)
        if metric.mapping_sha256 not in mapping_shas:
            mapping_shas.append(metric.mapping_sha256)

    body = {
        "question_id": plan.question_id,
        "plan_id": plan_id,
        "capability_sha256": capability_sha256,
        "as_of": plan.as_of,
        "executed_at": when,
        "planner": planner,
        "writer": writer,
        "git_sha": git_sha,
    }

    return ResearchRunManifest(
        manifest_id=sha256_of(body),
        question_id=plan.question_id,
        plan_id=plan_id,
        capability_sha256=capability_sha256,
        planner=planner,
        writer=writer,
        as_of=plan.as_of,
        executed_at=when,
        outputs_available=outcome.outputs_available,
        result_ids=tuple(result.result_id for result in outcome.results),
        metric_versions=tuple(sorted(metric_versions)),
        mapping_sha256s=tuple(sorted(mapping_shas)),
        fact_ids=outcome.leaf_facts(),
        pat_version=pat_version,
        python_version=platform.python_version(),
        git_sha=git_sha,
    )

"""L3 - camada de pesquisa: plano declarativo, validacao, execucao determinista.

Milestone 1 nao tem LLM. O caminho inteiro - plano -> validacao -> resolucao
-> execucao -> renderizacao -> resposta -> manifesto - roda offline, sem rede
e sem modelo:

    from pat.research import run_plan
    outcome = run_plan(conn, plan=plan, question=question)

O planejador e o escritor entram no Milestone 2, atras de um Protocol. Nada
aqui depende deles, e este arquivo e a raiz de composicao onde eles serao
ligados quando existirem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import duckdb

from pat.contracts.research import (
    PlanEnvelope,
    PlanViolation,
    ResearchAnswer,
    ResearchConstraints,
    ResearchPlan,
    ResearchQuestion,
    ResearchRunManifest,
    ResolutionIssue,
)

DEFAULT_SOURCE = "cvm.dfp"

__all__ = [
    "DEFAULT_SOURCE",
    "ResearchOutcome",
    "load_envelope",
    "review_plan",
    "run_plan",
]


@dataclass(frozen=True)
class ResearchOutcome:
    """Tudo que uma corrida produziu, executada ou nao."""

    plan: ResearchPlan
    plan_id: str
    capability_sha256: str
    violations: tuple[PlanViolation, ...]
    issues: tuple[ResolutionIssue, ...]
    execution: object | None = None
    answer: ResearchAnswer | None = None
    manifest: ResearchRunManifest | None = None

    @property
    def executable(self) -> bool:
        return not self.violations and not self.issues


def load_envelope(payload: bytes) -> PlanEnvelope:
    """JSON -> pergunta + plano. `extra="forbid"` rejeita campo desconhecido na
    fronteira, e nao tres camadas adiante ja como numero errado."""
    return PlanEnvelope.model_validate_json(payload)


def _pieces(conn: duckdb.DuckDBPyConnection, *, as_of: date | None, source: str):
    from pat.semantics.loader import load_dir
    from pat.semantics.registry import default_registry
    from pat.research.capability import build_snapshot, snapshot_sha256

    snapshot = build_snapshot(conn, as_of=as_of, source=source)
    return load_dir(), default_registry(), snapshot, snapshot_sha256(snapshot)


def review_plan(
    conn: duckdb.DuckDBPyConnection,
    *,
    plan: ResearchPlan,
    question: ResearchQuestion,
    constraints: ResearchConstraints | None = None,
    source: str = DEFAULT_SOURCE,
) -> ResearchOutcome:
    """Valida e resolve, sem executar. E o que `--dry-run` chama."""
    from pat.research.canonical import plan_id as compute_plan_id
    from pat.research.resolve import resolve_plan
    from pat.research.validate import validate_plan

    mappings, registry, _snapshot, cap_sha = _pieces(conn, as_of=plan.as_of, source=source)
    limits = constraints or question.constraints

    violations = validate_plan(plan, question, registry)
    issues = resolve_plan(plan, conn=conn, mappings=mappings, constraints=limits, source=source)

    return ResearchOutcome(
        plan=plan,
        plan_id=compute_plan_id(plan),
        capability_sha256=cap_sha,
        violations=violations,
        issues=issues,
    )


def run_plan(
    conn: duckdb.DuckDBPyConnection,
    *,
    plan: ResearchPlan,
    question: ResearchQuestion,
    constraints: ResearchConstraints | None = None,
    source: str = DEFAULT_SOURCE,
    git_sha: str | None = None,
    executed_at: datetime | None = None,
) -> ResearchOutcome:
    """Caminho deterministico completo. Nenhuma chamada de modelo, nenhuma rede.

    Um plano com violacao ou pendencia devolve `ResearchOutcome` sem execucao -
    a certificacao acontece antes, e o executor so aceita plano certificado.
    """
    from pat import __version__
    from pat.research.answer import build_answer
    from pat.research.execute import execute_plan
    from pat.research.manifest import build_manifest
    from pat.research.validate import certify
    from pat.semantics import build_engine

    review = review_plan(
        conn, plan=plan, question=question, constraints=constraints, source=source
    )
    if not review.executable:
        return review

    validated = certify(plan, question, violations=(), issues=())
    engine = build_engine(conn, source=source, git_sha=git_sha)
    outcome = execute_plan(validated, engine=engine)

    manifest = build_manifest(
        plan=plan,
        plan_id=review.plan_id,
        capability_sha256=review.capability_sha256,
        outcome=outcome,
        pat_version=__version__,
        git_sha=git_sha,
        executed_at=executed_at,
    )

    answer = None
    if outcome.outputs_available:
        answer = build_answer(
            plan=plan,
            plan_id=review.plan_id,
            results=outcome.output_results(plan),
            manifest_id=manifest.manifest_id,
        )

    return ResearchOutcome(
        plan=plan,
        plan_id=review.plan_id,
        capability_sha256=review.capability_sha256,
        violations=(),
        issues=(),
        execution=outcome,
        answer=answer,
        manifest=manifest,
    )

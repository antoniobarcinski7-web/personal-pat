"""L3 - camada de pesquisa: plano declarativo, validacao, execucao determinista.

Milestone 1 nao tem LLM. O caminho inteiro - plano -> validacao -> resolucao
-> execucao -> renderizacao -> resposta -> manifesto - roda offline, sem rede
e sem modelo:

    from pat.research import run_plan
    outcome = run_plan(conn, plan=plan, question=question)

O planejador entrou no Milestone 2 atras de um Protocol, e `plan_for_question`
abaixo e onde ele se liga ao snapshot. O escritor entrou no Milestone 3, pelo
parametro `llm` de `run_plan`: sem ele o caminho continua sendo exatamente o
de antes, prosa montada por codigo e `writer` nulo no manifesto.

A ordem entre executar, escrever, manifestar e responder mora aqui e so aqui,
porque so este arquivo tem as tres pontas - e porque ela nao e arbitraria: a
procedencia do escritor entra no `manifest_id`, e o `manifest_id` entra na
resposta. Inverter faria a resposta citar um manifesto que nao registra o
autor do seu proprio texto.

O cliente de LLM e *injetado*, nunca construido aqui. Nao e cerimonia: se este
arquivo importasse o adapter, o pacote inteiro passaria a ter saida de rede na
importacao, e o guard que exige que ela caiba num arquivo so
(`tests/research/test_layering_research.py`) deixaria de dizer o que diz. Quem
escolhe provider, cache e credencial e a linha de comando - do mesmo jeito que
e ela quem abre a conexao do warehouse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:  # so para tipo: importar em runtime traria a porta junto
    from pat.contracts.chat import ConversationContext
    from pat.research.llm import LLMClient
    from pat.research.planner import PlannerOutcome
    from pat.research.writer import WriterOutcome

DEFAULT_SOURCE = "cvm.dfp"

__all__ = [
    "DEFAULT_SOURCE",
    "ResearchOutcome",
    "load_envelope",
    "plan_for_question",
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
    writer: "WriterOutcome | None" = None
    """Nulo no caminho deterministico. Nao e redundante com
    `manifest.writer`: aquele e a procedencia que vai para o banco, este
    carrega tambem o texto cru que o modelo escreveu - com os tokens ainda por
    substituir -, que e o que se quer ler ao auditar uma resposta estranha."""

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
    llm: "LLMClient | None" = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: Decimal | None = None,
    timeout_s: int | None = None,
) -> ResearchOutcome:
    """Plano -> numeros -> resposta. Sem `llm`, nenhuma chamada de modelo.

    Um plano com violacao ou pendencia devolve `ResearchOutcome` sem execucao -
    a certificacao acontece antes, e o executor so aceita plano certificado.

    O escritor e OPCIONAL de proposito, e o default e nao ter. A camada
    deterministica precisa continuar rodando inteira sem credencial, sem rede e
    sem custo - e o par offline/modelo da Fase 3 e o mesmo de sempre: o
    caminho sem LLM prova que o numero esta certo, o com LLM prova que o texto
    em volta dele sobrevive ao mundo. Um escritor ligado por default apagaria
    o primeiro.

    O escritor nao muda nenhum numero, e isso e estrutural e nao uma promessa:
    ele so escolhe a prosa e as leituras. Os `NumericClaim`, os avisos e os
    valores continuam vindo de `build_answer` sobre os mesmos `results`.
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

    # O escritor so e chamado quando ha o que citar. Pedir prosa sobre uma
    # execucao incompleta seria gastar uma chamada para receber um texto sobre
    # numeros que nao existem - e a regra do digito o recusaria por falta de
    # citacao, o que e a resposta certa pelo caminho caro.
    written: WriterOutcome | None = None
    if llm is not None and outcome.outputs_available:
        written = _write(
            question=question,
            plan=plan,
            results=outcome.output_results(plan),
            capability_sha256=review.capability_sha256,
            llm=llm,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_s=timeout_s,
        )

    manifest = build_manifest(
        plan=plan,
        plan_id=review.plan_id,
        capability_sha256=review.capability_sha256,
        outcome=outcome,
        pat_version=__version__,
        git_sha=git_sha,
        writer=written.provenance if written else None,
        executed_at=executed_at,
    )

    answer = None
    if outcome.outputs_available:
        answer = build_answer(
            plan=plan,
            plan_id=review.plan_id,
            results=outcome.output_results(plan),
            manifest_id=manifest.manifest_id,
            prose=written.prose if written else None,
            extra_claims=written.interpretations if written else (),
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
        writer=written,
    )


def _write(
    *,
    question: ResearchQuestion,
    plan: ResearchPlan,
    results: tuple,
    capability_sha256: str,
    llm: "LLMClient",
    model: str | None,
    max_tokens: int | None,
    temperature: Decimal | None,
    timeout_s: int | None,
) -> "WriterOutcome":
    """Import tardio pela regra de sempre: `pat.research` importado no topo do
    programa nao pode arrastar a porta do modelo junto."""
    from pat.research.writer import DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT_S, write_prose

    if model is None:
        raise ValueError(
            "run_plan recebeu `llm` sem `model`. Escolher o modelo e decisao de "
            "produto e nao tem default nesta camada - um default aqui viraria "
            "configuracao global escondida numa biblioteca."
        )

    return write_prose(
        question,
        plan,
        results,
        llm=llm,
        model=model,
        capability_sha256=capability_sha256,
        max_tokens=DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens,
        temperature=temperature,
        timeout_s=DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s,
    )


def plan_for_question(
    conn: duckdb.DuckDBPyConnection,
    *,
    question: ResearchQuestion,
    llm: "LLMClient",
    model: str,
    source: str = DEFAULT_SOURCE,
    max_tokens: int | None = None,
    temperature: Decimal | None = None,
    timeout_s: int | None = None,
    context: "ConversationContext | None" = None,
) -> "PlannerOutcome":
    """Pergunta -> plano. Uma chamada de modelo, nenhuma execucao.

    Duas coisas juntas que nao pertencem uma a outra: o snapshot vem do
    warehouse, o plano vem do modelo, e o planejador nao pode conhecer o
    primeiro (ele nao importa `pat.query` nem `pat.store`, e ha teste de AST
    exigindo isso). Alguem tem que segurar as duas pontas, e o lugar certo e a
    raiz de composicao - nao o planejador, nem a linha de comando.

    O que esta funcao deliberadamente NAO faz: validar, resolver e executar.
    O plano sai daqui do jeito que o modelo o produziu, para ser recusado por
    `review_plan` se for o caso. Planejar e validar no mesmo passo esconderia
    de qual dos dois veio a recusa, que e justamente o que se quer saber
    quando um plano nao passa.
    """
    from pat.research.capability import build_snapshot
    from pat.research.planner import DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT_S, plan_question

    snapshot = build_snapshot(conn, as_of=question.as_of, source=source)
    return plan_question(
        question,
        snapshot,
        llm=llm,
        model=model,
        max_tokens=DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens,
        temperature=temperature,
        timeout_s=DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s,
        context=context,
    )

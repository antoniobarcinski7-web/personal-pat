"""Identidades enderecadas por conteudo da camada de pesquisa.

O serializador canonico generico mora em `pat.canonical` desde a Fase 5 - a
camada de corpus precisa dele e nao pode depender de `pat.research`. Este
modulo continua sendo o lugar das identidades *tipadas* de L3: o que entra em
cada hash, e principalmente o que fica de fora dele.

`normalize_text`, `decimal_str`, `canonical_bytes`, `sha256_of` e
`sha256_bytes` continuam importaveis daqui, e sao exatamente os mesmos
objetos: mover o serializador nao podia mudar um unico byte de identidade ja
gravada, e `tests/research/test_canonical.py` continua sendo o que prova isso.
"""

from __future__ import annotations

from typing import Any

from pat.canonical import (
    canonical_bytes,
    decimal_str,
    normalize_text,
    sha256_bytes,
    sha256_of,
)
from pat.contracts.research import (
    CapabilitySnapshot,
    ComputationResult,
    ResearchPlan,
    ResearchQuestion,
)

__all__ = [
    "canonical_bytes",
    "capability_sha256",
    "decimal_str",
    "normalize_text",
    "plan_id",
    "program_id",
    "question_id",
    "result_id",
    "result_id_of",
    "sha256_bytes",
    "sha256_of",
]

# ---------------------------------------------------------------------------
# Identidades
# ---------------------------------------------------------------------------


def question_id(question: ResearchQuestion) -> str:
    """`asked_at` fica de fora: senao a mesma pergunta nunca seria a mesma."""
    return sha256_of(
        {
            "question_version": question.question_version,
            "text": normalize_text(question.text),
            "as_of": question.as_of,
            "pinned_entities": question.pinned_entities,
            "pinned_periods": question.pinned_periods,
            "pinned_scope": question.pinned_scope,
            "requested_output": question.requested_output,
            "constraints": question.constraints,
        }
    )


def plan_id(plan: ResearchPlan) -> str:
    """Nada de `PlanProvenance` entra aqui.

    E este recorte que faz metadado de LLM - modelo, temperatura, hash de
    prompt, se veio do cache - nao conseguir mexer na identidade do plano.
    Dois modelos que produzam o mesmo plano produzem o mesmo hash.
    """
    return sha256_of(
        {
            "plan_version": plan.plan_version,
            "question_id": plan.question_id,
            "objective": plan.objective,
            "as_of": plan.as_of,
            "scope": plan.scope,
            "steps": plan.steps,
            "outputs": plan.outputs,
            "assumptions": plan.assumptions,
            "unresolved": plan.unresolved,
        }
    )


def program_id(program) -> str:
    """Identidade de um programa de pesquisa.

    Nada de `PlanProvenance` entra - nem a do estagio 1, nem a do estagio 2.
    E o mesmo recorte de `plan_id`, e pela mesma razao: dois modelos que
    produzam o mesmo programa produzem o mesmo hash, e metadado de chamada nao
    mexe na identidade do que foi decidido investigar.

    `questions_to_answer` ENTRA. Ele e prosa do planejador, mas prosa que muda
    o que um humano aprova ao revisar - dois programas com os mesmos passos e
    intencoes declaradas diferentes nao sao o mesmo programa.
    """
    return sha256_of(
        {
            "program_version": program.program_version,
            "question_id": program.question_id,
            "objective": program.objective,
            "as_of": program.as_of,
            "scope": program.scope,
            "compute": program.compute,
            "decompositions": program.decompositions,
            "evidence": program.evidence,
            "questions_to_answer": program.questions_to_answer,
            "unresolved": program.unresolved,
        }
    )


def capability_sha256(snapshot: CapabilitySnapshot) -> str:
    """Tudo menos `built_at` (D-4 do desenho: o retrato, nao o relogio)."""
    payload = {
        name: getattr(snapshot, name)
        for name in CapabilitySnapshot.model_fields
        if name != "built_at"
    }
    return sha256_of(payload)


def result_id(result_body: dict[str, Any]) -> str:
    """Enderecado por conteudo (D-4).

    Numero diferente => `result_id` diferente. Duas execucoes cujas citacoes
    divergem sao visivelmente execucoes diferentes, e isso e informacao.
    """
    return sha256_of(result_body)


def result_id_of(result: ComputationResult) -> str:
    """Recalcula a identidade de um resultado ja montado. Usado em teste."""
    return result_id(
        {
            "step_id": result.step_id,
            "kind": result.kind,
            "metric_result": result.metric_result,
            "derived": result.derived,
        }
    )

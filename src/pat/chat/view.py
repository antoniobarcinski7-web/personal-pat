"""`ChatTurn` -> o dicionario JSON que a UI consome (§7.3 da proposta).

Esta camada NAO formata numero. Ela copia `NumericClaim.rendered_value`, que ja
e string pronta, produzida pelo unico formatador de numero do sistema
(`research/render.py`). Ter um formatador so e o que torna conferivel a regra de
que o modelo nunca emite digito: se cada camada de apresentacao pudesse
arredondar, "o numero na tela e o numero do motor" viraria promessa. Um
`Decimal` que chegue aqui e erro de programacao, nao caso a tratar.

Tudo sai serializavel: `json.dumps` do resultado funciona sem `default=`. Datas
e datetimes viram ISO na fronteira, e nao na hora de gravar - converter tarde e
como um consumidor descobre `Decimal` cru num campo que ele achava que era
string.
"""

from __future__ import annotations

from datetime import date, datetime

from pat.contracts.chat import ChatTurn, RefusalKind

_MODEL_FAILURES = (RefusalKind.PLANNER_FAILED, RefusalKind.WRITER_FAILED)


def turn_view(turn: ChatTurn, *, entity_names: dict[str, str]) -> dict:
    """O turno inteiro, do jeito que a UI o le.

    `entity_names` e um join por `entity_id` sobre `EntityCard.denom_cia` do
    snapshot - o plano cru nao tem o nome, e a UI precisa dele. Join, e nao
    interpretacao: nenhuma linha aqui escolhe empresa por rotulo.
    """
    return {
        "turn_index": turn.turn_index,
        "session_id": turn.session_id,
        "as_of": _iso(turn.question.as_of),
        "asked_at": _iso(turn.asked_at),
        "question": turn.question.text,
        "status": _status(turn),
        "answer": _answer(turn),
        "refusal": _refusal(turn),
        "plan": _plan(turn, entity_names),
        "manifest": _manifest(turn),
        "provenance": {
            "planner": _provenance(turn.planner_provenance),
            "writer": _provenance(turn.writer_provenance),
        },
        "context_sha256": turn.context_sha256,
        "elapsed_ms": turn.elapsed_ms,
    }


def _status(turn: ChatTurn) -> str:
    if turn.answer is not None:
        return "answered"
    return "failed" if turn.refusal.kind in _MODEL_FAILURES else "refused"


def _iso(valor: date | datetime | None) -> str | None:
    return valor.isoformat() if valor is not None else None


def _answer(turn: ChatTurn) -> dict | None:
    answer = turn.answer
    if answer is None:
        return None

    # Numericos e interpretativos saem em chaves SEPARADAS, e nao numa lista so
    # com um campo `claim_kind` para a UI filtrar. A distincao entre "isto foi
    # medido" e "isto e uma leitura do modelo" so vale se sobreviver ate a
    # ultima perna, que e onde ela importa; juntar aqui deixaria um `map` no
    # frontend responsavel por ela.
    numericos = [
        {
            "token": claim.token,
            "rendered_value": claim.rendered_value,
            "unit": claim.unit,
            "means": claim.means,
            "result_id": claim.result_id,
        }
        for claim in answer.claims
        if claim.claim_kind == "numeric"
    ]
    interpretativos = [
        {"text": claim.text, "supports": list(claim.supports)}
        for claim in answer.claims
        if claim.claim_kind == "interpretive"
    ]
    return {
        "prose": answer.prose,
        "question_id": answer.question_id,
        "plan_id": answer.plan_id,
        "manifest_id": answer.manifest_id,
        "numeric_claims": numericos,
        "interpretive_claims": interpretativos,
        "warnings": [
            {"kind": str(aviso.kind), "message": aviso.message, "result_id": aviso.result_id}
            for aviso in answer.warnings
        ],
    }


def _refusal(turn: ChatTurn) -> dict | None:
    recusa = turn.refusal
    if recusa is None:
        return None
    return {
        "kind": str(recusa.kind),
        "summary": recusa.summary,
        "detail": recusa.detail,
        "codes": list(recusa.codes),
        "remedies": list(recusa.remedies),
        "candidates": list(recusa.candidates),
    }


def _plan(turn: ChatTurn, entity_names: dict[str, str]) -> dict | None:
    plan = turn.plan
    if plan is None:
        return None

    steps = []
    for step in plan.steps:
        if step.step_kind == "metric":
            steps.append(
                {
                    "step_id": step.step_id,
                    "step_kind": "metric",
                    "metric": str(step.metric),
                    "entity_id": step.entity_id,
                    "entity_name": entity_names.get(step.entity_id),
                    "period_end": _iso(step.period_end),
                }
            )
        else:
            steps.append(
                {
                    "step_id": step.step_id,
                    "step_kind": "derivation",
                    "op": str(step.op),
                    "inputs": list(step.inputs),
                    "params": {"years": step.params.years},
                }
            )

    return {
        "plan_id": turn.plan_id,
        "question_id": plan.question_id,
        "objective": plan.objective,
        "as_of": _iso(plan.as_of),
        "scope": str(plan.scope),
        "steps": steps,
        "outputs": list(plan.outputs),
        "assumptions": list(plan.assumptions),
        "unresolved": [
            {"kind": str(item.kind), "detail": item.detail, "candidates": list(item.candidates)}
            for item in plan.unresolved
        ],
    }


def _manifest(turn: ChatTurn) -> dict | None:
    manifest = turn.manifest
    if manifest is None:
        return None
    return {
        "manifest_id": manifest.manifest_id,
        "question_id": manifest.question_id,
        "plan_id": manifest.plan_id,
        "capability_sha256": manifest.capability_sha256,
        "as_of": _iso(manifest.as_of),
        "executed_at": _iso(manifest.executed_at),
        "outputs_available": manifest.outputs_available,
        "result_ids": list(manifest.result_ids),
        "metric_versions": list(manifest.metric_versions),
        "mapping_sha256s": list(manifest.mapping_sha256s),
        "fact_ids": list(manifest.fact_ids),
        "pat_version": manifest.pat_version,
        "python_version": manifest.python_version,
        "git_sha": manifest.git_sha,
    }


def _provenance(provenance) -> dict | None:
    if provenance is None:
        return None
    return {
        "model_id": provenance.model_id,
        "client_fingerprint": provenance.client_fingerprint,
        "system_prompt_sha256": provenance.system_prompt_sha256,
        "prompt_sha256": provenance.prompt_sha256,
        "response_sha256": provenance.response_sha256,
        "capability_sha256": provenance.capability_sha256,
        "called_at": _iso(provenance.called_at),
        "cached": provenance.cached,
        "max_tokens": provenance.max_tokens,
        # String, e nao float: temperatura e `Decimal` no contrato, e converter
        # para float na saida seria reintroduzir binario na unica ponta onde
        # ninguem olharia. Nula sai nula - "nao pedida" nunca vira zero.
        "temperature": str(provenance.temperature) if provenance.temperature is not None else None,
    }

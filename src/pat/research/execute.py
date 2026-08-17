"""Plano validado -> resultados. Deterministico.

Unico modulo de `pat.research` que segura um `Engine`. Nao tem relogio, nao
tem aleatoriedade, nao le variavel de ambiente e nao acessa rede: chamado duas
vezes sobre o mesmo plano e o mesmo warehouse, produz resultados identicos
byte a byte, `result_id` inclusive.

`scope` e `as_of` vem do plano, nunca do passo - nao existe campo de onde
errar. E o mesmo invariante que o motor da Fase 2 mantem, um andar acima.
"""

from __future__ import annotations

from dataclasses import dataclass

from pat.contracts.research import (
    ComputationFailure,
    ComputationResult,
    DerivationFailureReason,
    DerivationStep,
    DerivedValue,
    MetricStep,
    ResultKind,
)
from pat.contracts.semantics import MetricUnavailable
from pat.research.canonical import result_id
from pat.research.derive import derive
from pat.research.validate import ValidatedPlan


@dataclass(frozen=True)
class ExecutionOutcome:
    """O que a execucao produziu, e o que faltou.

    `outputs_available` e a pergunta que importa: se qualquer saida do plano
    falhou, nao existe resposta a montar. Meia serie que se apresenta como
    inteira e exatamente o "parcial que se le como completo".
    """

    results: tuple[ComputationResult, ...]
    failures: tuple[ComputationFailure, ...]
    outputs_available: bool
    fact_ids: tuple[tuple[str, tuple[str, ...]], ...] = ()
    """(step_id, fact_ids nas folhas). Coletado aqui porque so este modulo tem
    o motor - e descer o grafo de dependencia exige recalcular as metricas
    filhas, que e o que da acesso aos `fact_id` de verdade."""

    def leaf_facts(self) -> tuple[str, ...]:
        vistos: list[str] = []
        for _, ids in self.fact_ids:
            for fact_id in ids:
                if fact_id not in vistos:
                    vistos.append(fact_id)
        return tuple(vistos)

    def by_step(self) -> dict[str, ComputationResult]:
        return {result.step_id: result for result in self.results}

    def output_results(self, plan) -> tuple[ComputationResult, ...]:
        index = self.by_step()
        return tuple(index[step_id] for step_id in plan.outputs if step_id in index)


def _make_result(
    step_id: str,
    *,
    metric_result=None,
    derived: DerivedValue | None = None,
) -> ComputationResult:
    kind = ResultKind.METRIC if metric_result is not None else ResultKind.DERIVED
    body = {
        "step_id": step_id,
        "kind": kind,
        "metric_result": metric_result,
        "derived": derived,
    }
    return ComputationResult(
        result_id=result_id(body),
        step_id=step_id,
        kind=kind,
        metric_result=metric_result,
        derived=derived,
    )


def leaf_fact_ids(metric_result, *, engine, scope, as_of) -> tuple[str, ...]:
    """Desce ate as folhas do grafo de metricas e devolve os `fact_id`.

    Uma metrica composta nao cita fato nenhum diretamente: `ebitda@v1` cita
    `ebit@v1` e `d_and_a@v1`. A linhagem so vale se der para descer ate quem
    de fato leu uma linha da fonte, e para isso e preciso recalcular as
    filhas - que e exatamente o que o golden test da Fase 2 ja faz.
    """
    out: list[str] = []
    for ref in metric_result.inputs:
        if ref.fact_id:
            out.append(ref.fact_id)
        if not ref.is_metric:
            continue
        child = engine.compute(
            ref.role,
            entity_id=metric_result.entity_id,
            period_end=metric_result.period_end,
            scope=scope,
            as_of=as_of,
        )
        if isinstance(child, MetricUnavailable):  # pragma: no cover - pai ja resolveu
            continue
        out.extend(leaf_fact_ids(child, engine=engine, scope=scope, as_of=as_of))
    return tuple(out)


def execute_plan(validated: ValidatedPlan, *, engine) -> ExecutionOutcome:
    """Executa um plano ja certificado.

    A assinatura so aceita `ValidatedPlan`, cujo construtor exige o selo de
    `certify()`. Nao existe caminho que rode um plano cru.
    """
    plan = validated.plan
    produced: dict[str, ComputationResult] = {}
    failed: dict[str, ComputationFailure] = {}
    facts: list[tuple[str, tuple[str, ...]]] = []

    # Ordem de declaracao ja e topologica: o validador rejeita referencia para
    # frente, entao um passo so cita passos que vieram antes dele.
    for step in plan.steps:
        if isinstance(step, MetricStep):
            outcome = engine.compute(
                step.metric,
                entity_id=step.entity_id,
                period_end=step.period_end,
                scope=plan.scope,
                as_of=plan.as_of,
            )
            if isinstance(outcome, MetricUnavailable):
                failed[step.step_id] = ComputationFailure(
                    step_id=step.step_id,
                    reason=DerivationFailureReason.INPUT_FAILED,
                    message=f"{outcome.metric}@{outcome.metric_version} indisponivel "
                    f"({outcome.reason}): {outcome.message}",
                    remedy=outcome.remedy,
                    # O objeto da Fase 2 viaja inteiro: motivo, conceito que
                    # faltou e enderecos tentados chegam ao usuario sem
                    # reescrita.
                    unavailable=outcome,
                )
                continue
            produced[step.step_id] = _make_result(step.step_id, metric_result=outcome)
            facts.append(
                (
                    step.step_id,
                    leaf_fact_ids(outcome, engine=engine, scope=plan.scope, as_of=plan.as_of),
                )
            )

        elif isinstance(step, DerivationStep):
            inputs: list[ComputationResult | ComputationFailure] = []
            for ref in step.inputs:
                if ref in failed:
                    inputs.append(failed[ref])
                else:
                    inputs.append(produced[ref])

            derived = derive(step.step_id, step.op, inputs, step.params)
            if isinstance(derived, ComputationFailure):
                failed[step.step_id] = derived
                continue
            produced[step.step_id] = _make_result(step.step_id, derived=derived)

    outputs_available = all(step_id in produced for step_id in plan.outputs)

    return ExecutionOutcome(
        results=tuple(produced[step.step_id] for step in plan.steps if step.step_id in produced),
        failures=tuple(
            failed[step.step_id] for step in plan.steps if step.step_id in failed
        ),
        outputs_available=outputs_available,
        fact_ids=tuple(facts),
    )

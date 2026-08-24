"""Validacao estatica de um plano. Pura.

Sem banco, sem modelo, sem relogio, sem aleatoriedade, sem sistema de
arquivos. As unicas entradas sao o plano, a pergunta e o registro de metricas
- e por isso este e o unico modulo do sistema que da para testar
exaustivamente: o conjunto de planos invalidos e finito porque a gramatica e
fechada.

Devolve TODAS as violacoes, nao a primeira. Um plano com tres problemas tem
que reportar tres; consertar um de cada vez e o que transforma revisao em
sessao de adivinhacao.

O que ele NAO prova: que o plano e o *certo*. Boa forma nao e intencao. Um
plano perfeitamente valido pode pedir escopo individual quando o usuario
queria consolidado, e nenhuma checagem aqui pega isso - por isso `--dry-run`
existe e por isso a resposta sempre repete o plano em prosa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from pat.contracts.research import (
    DerivationOp,
    DerivationStep,
    MetricStep,
    PlanViolation,
    ResearchPlan,
    ResearchQuestion,
    ResolutionIssue,
    ViolationCode,
)
from pat.contracts.semantics import Dimension
from pat.research.canonical import question_id as compute_question_id

_STEP_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

BINARY_OPS = frozenset(
    {DerivationOp.DELTA, DerivationOp.DELTA_PCT, DerivationOp.RATIO, DerivationOp.CAGR}
)
VARIADIC_OPS = frozenset({DerivationOp.MIN, DerivationOp.MAX, DerivationOp.MEAN})

RATIO_OUTPUT_OPS = frozenset({DerivationOp.DELTA_PCT, DerivationOp.RATIO, DerivationOp.CAGR})


class PlanNotExecutable(RuntimeError):
    """Tentaram executar um plano que nao passou pelas duas conferencias."""

    def __init__(
        self,
        violations: tuple[PlanViolation, ...],
        issues: tuple[ResolutionIssue, ...],
    ) -> None:
        self.violations = violations
        self.issues = issues
        partes = [f"{v.code}: {v.message}" for v in violations]
        partes += [f"{i.code}: {i.message}" for i in issues]
        super().__init__("plano nao executavel — " + "; ".join(partes))


_CERTIFIED = object()


@dataclass(frozen=True)
class ValidatedPlan:
    """Prova de que o plano passou por `validate_plan` e `resolve_plan`.

    O executor so aceita este tipo. Nao e disciplina de quem chama: e o unico
    argumento que a assinatura aceita, e o construtor exige o selo que so
    `certify()` tem. "Plano invalido nao executa" vira propriedade de tipo.
    """

    plan: ResearchPlan
    question: ResearchQuestion
    _proof: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._proof is not _CERTIFIED:
            raise TypeError(
                "ValidatedPlan nao pode ser construido diretamente; use `certify()`, "
                "que exige zero violacoes e zero pendencias de resolucao."
            )


def certify(
    plan: ResearchPlan,
    question: ResearchQuestion,
    *,
    violations: tuple[PlanViolation, ...],
    issues: tuple[ResolutionIssue, ...],
) -> ValidatedPlan:
    if violations or issues:
        raise PlanNotExecutable(violations, issues)
    return ValidatedPlan(plan=plan, question=question, _proof=_CERTIFIED)


# ---------------------------------------------------------------------------


def _v(
    code: ViolationCode, message: str, *, step_id: str | None = None, remedy: str | None = None
) -> PlanViolation:
    return PlanViolation(code=code, message=message, step_id=step_id, remedy=remedy)


def validate_plan(
    plan: ResearchPlan,
    question: ResearchQuestion,
    registry,
) -> tuple[PlanViolation, ...]:
    """Todas as violacoes estruturais do plano, em ordem estavel."""
    out: list[PlanViolation] = []

    # -- identidade e eixo temporal -----------------------------------------
    expected_qid = compute_question_id(question)
    if plan.question_id != expected_qid:
        out.append(
            _v(
                ViolationCode.QUESTION_ID_MISMATCH,
                f"o plano diz responder a pergunta {plan.question_id[:12]}, mas a "
                f"pergunta recebida e {expected_qid[:12]}",
                remedy="replanejar a partir da pergunta correta",
            )
        )
    if plan.as_of != question.as_of:
        out.append(
            _v(
                ViolationCode.AS_OF_MISMATCH,
                f"plano com as_of={plan.as_of}, pergunta com as_of={question.as_of}",
            )
        )

    if len(plan.steps) > plan_limits().max_steps:
        out.append(
            _v(
                ViolationCode.PLAN_TOO_LARGE,
                f"{len(plan.steps)} passos, acima do limite de {plan_limits().max_steps}",
            )
        )

    entidades = {step.entity_id for step in plan.steps if isinstance(step, MetricStep)}
    if len(entidades) > plan_limits().max_entities:
        # `max_entities` viaja no snapshot e o planejador o le; um limite que o
        # prompt anuncia e o validador nao cobra e so um pedido educado.
        out.append(
            _v(
                ViolationCode.PLAN_TOO_LARGE,
                f"{len(entidades)} empresas no plano, acima do limite de "
                f"{plan_limits().max_entities}: {', '.join(sorted(entidades))}",
                remedy="dividir em planos menores; nao existe recorte parcial",
            )
        )

    if plan.unresolved:
        detalhes = "; ".join(f"{item.kind}: {item.detail}" for item in plan.unresolved)
        out.append(
            _v(
                ViolationCode.PLAN_NOT_EXECUTABLE,
                f"plano tem pendencias e nao deve executar — {detalhes}",
                remedy="responder a ambiguidade e replanejar; nunca chutar",
            )
        )

    # -- pinos do usuario sao soberanos -------------------------------------
    if question.pinned_scope is not None and plan.scope != question.pinned_scope:
        out.append(
            _v(
                ViolationCode.PIN_CONTRADICTED_SCOPE,
                f"usuario fixou escopo {question.pinned_scope}, plano usa {plan.scope}",
            )
        )

    known_ids = {step.step_id for step in plan.steps}
    seen: set[str] = set()

    for index, step in enumerate(plan.steps):
        if not _STEP_ID.match(step.step_id):
            out.append(
                _v(ViolationCode.BAD_STEP_ID, f"step_id invalido: {step.step_id!r}", step_id=step.step_id)
            )

        if isinstance(step, MetricStep):
            out.extend(_check_metric_step(step, plan, question, registry))
        elif isinstance(step, DerivationStep):
            out.extend(_check_derivation_step(step, plan, registry, known_ids, seen, index))

        seen.add(step.step_id)

    # -- saidas --------------------------------------------------------------
    for output in plan.outputs:
        if output not in known_ids:
            out.append(
                _v(
                    ViolationCode.DANGLING_OUTPUT,
                    f"a saida {output!r} nao corresponde a nenhum passo",
                    step_id=output,
                )
            )

    return tuple(out)


def plan_limits():
    from pat.contracts.research import SnapshotLimits

    return SnapshotLimits()


def _check_metric_step(
    step: MetricStep, plan: ResearchPlan, question: ResearchQuestion, registry
) -> list[PlanViolation]:
    out: list[PlanViolation] = []

    if not registry.has(step.metric):
        conhecidas = ", ".join(str(m.definition.ref) for m in registry.all())
        out.append(
            _v(
                ViolationCode.UNKNOWN_METRIC,
                f"metrica {step.metric} nao esta registrada. Registradas: {conhecidas}",
                step_id=step.step_id,
                remedy="use `pat metrics` para ver o que existe",
            )
        )

    if step.period_end > plan.as_of:
        out.append(
            _v(
                ViolationCode.PERIOD_AFTER_AS_OF,
                f"periodo {step.period_end} e posterior ao as_of {plan.as_of}: "
                "nada era conhecido sobre ele naquela data",
                step_id=step.step_id,
            )
        )

    if question.pinned_entities and step.entity_id not in question.pinned_entities:
        out.append(
            _v(
                ViolationCode.PIN_CONTRADICTED_ENTITY,
                f"usuario fixou {sorted(question.pinned_entities)}, passo usa {step.entity_id}",
                step_id=step.step_id,
            )
        )
    if question.pinned_periods and step.period_end not in question.pinned_periods:
        out.append(
            _v(
                ViolationCode.PIN_CONTRADICTED_PERIOD,
                f"usuario fixou {[str(p) for p in question.pinned_periods]}, "
                f"passo usa {step.period_end}",
                step_id=step.step_id,
            )
        )
    return out


def _check_derivation_step(
    step: DerivationStep,
    plan: ResearchPlan,
    registry,
    known_ids: set[str],
    seen: set[str],
    index: int,
) -> list[PlanViolation]:
    out: list[PlanViolation] = []

    for ref in step.inputs:
        if ref == step.step_id:
            out.append(
                _v(ViolationCode.PLAN_CYCLE, f"{step.step_id} depende de si mesmo", step_id=step.step_id)
            )
        elif ref not in known_ids:
            out.append(
                _v(
                    ViolationCode.DANGLING_INPUT,
                    f"{step.step_id} usa {ref!r}, que nao e passo do plano",
                    step_id=step.step_id,
                )
            )
        elif ref not in seen:
            # Ordem de declaracao e a ordem de execucao: uma referencia para
            # frente e ciclo ou erro de montagem, nunca intencao.
            out.append(
                _v(
                    ViolationCode.FORWARD_REFERENCE,
                    f"{step.step_id} usa {ref!r}, declarado depois dele",
                    step_id=step.step_id,
                )
            )

    arity = len(step.inputs)
    if step.op in BINARY_OPS and arity != 2:
        out.append(
            _v(
                ViolationCode.ARITY,
                f"{step.op} exige exatamente 2 insumos, recebeu {arity}",
                step_id=step.step_id,
            )
        )
    if step.op in VARIADIC_OPS and arity < 2:
        out.append(
            _v(
                ViolationCode.ARITY,
                f"{step.op} exige ao menos 2 insumos, recebeu {arity}",
                step_id=step.step_id,
            )
        )

    if step.op is DerivationOp.CAGR:
        if step.params.years is None:
            out.append(
                _v(
                    ViolationCode.MISSING_PARAM,
                    "CAGR exige `params.years`",
                    step_id=step.step_id,
                )
            )
        elif step.params.years <= 0:
            out.append(
                _v(
                    ViolationCode.BAD_PARAM,
                    f"CAGR com years={step.params.years}; precisa ser positivo",
                    step_id=step.step_id,
                )
            )

    # Dimensao e declarada na metrica, entao da para conferir sem dado.
    # Moeda nao e: ela so aparece quando o fato resolve. Por isso moeda e
    # recusa de tempo de derivacao, e nao regra daqui.
    dims = _declared_dimensions(step, plan, registry)
    if dims is not None and len(set(dims)) > 1:
        out.append(
            _v(
                ViolationCode.DIMENSION_MISMATCH,
                f"{step.op} sobre insumos de dimensoes diferentes: {sorted(set(dims))}",
                step_id=step.step_id,
            )
        )
    return out


def _declared_dimensions(
    step: DerivationStep,
    plan: ResearchPlan,
    registry,
    _visiting: frozenset[str] = frozenset(),
) -> list[Dimension] | None:
    """Dimensao de cada insumo, quando derivavel estaticamente.

    `_visiting` nao e zelo: um plano ciclico chega aqui - o ciclo e reportado
    em separado, mas as duas conferencias rodam na mesma passada - e sem o
    guarda a recursao estoura a pilha. Um plano adversarial nao pode derrubar
    o validador; ele tem que sair com codigo nomeado.
    """
    if step.step_id in _visiting:
        return None
    visiting = _visiting | {step.step_id}

    dims: list[Dimension] = []
    for ref in step.inputs:
        source = plan.step(ref)
        if isinstance(source, MetricStep):
            if not registry.has(source.metric):
                return None
            dims.append(registry.get(source.metric).definition.dimension)
        elif isinstance(source, DerivationStep):
            if source.op in RATIO_OUTPUT_OPS:
                dims.append(Dimension.RATIO)
            else:
                nested = _declared_dimensions(source, plan, registry, visiting)
                if not nested:
                    return None
                dims.append(nested[0])
        else:
            return None
    return dims

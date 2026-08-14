"""Registro de metricas: definicao (dado) + funcao de calculo (codigo).

Os dois ficam juntos aqui e nao em `contracts/`, para que a camada de
contratos continue sendo so contrato.

O registro valida o grafo no momento do import: ciclo, dependencia para
versao inexistente e referencia a conceito fora do catalogo viram erro de
import, nao surpresa em tempo de consulta. Metrica quebrada tem que impedir o
programa de subir.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping as MappingABC
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    ConsistencyCheck,
    Fidelity,
    InputRef,
    MetricDefinition,
    MetricRef,
    MetricResult,
    UnavailableReason,
)
from pat.semantics import concepts


class ComputationUnavailable(Exception):
    """Levantada por uma funcao de calculo quando o numero nao existe.

    Ex.: divisao por zero numa margem. Melhor que devolver None ou zero - o
    motor converte isso em `MetricUnavailable` com motivo nomeado.
    """

    def __init__(self, reason: UnavailableReason, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ConceptValue:
    """Um conceito ja resolvido em numero, com linhagem."""

    concept_id: str
    value: Decimal
    currency: str | None
    fidelity: Fidelity
    period_type: PeriodType
    period_start: date | None
    knowledge_date: date
    inputs: tuple[InputRef, ...]


@dataclass(frozen=True)
class Inputs:
    """Insumos ja resolvidos, entregues a funcao de calculo.

    A funcao nunca busca nada: recebe tudo pronto. E o que a mantem pura e
    testavel sem banco.
    """

    concepts: MappingABC[str, ConceptValue]
    metrics: MappingABC[str, MetricResult]

    def concept(self, concept_id: str) -> ConceptValue:
        try:
            return self.concepts[concept_id]
        except KeyError:
            raise KeyError(
                f"conceito {concept_id!r} nao foi resolvido; declare-o em "
                "`requires_concepts` da definicao"
            ) from None

    def optional(self, concept_id: str) -> ConceptValue | None:
        return self.concepts.get(concept_id)

    def metric(self, ref: str | MetricRef) -> MetricResult:
        key = str(ref)
        try:
            return self.metrics[key]
        except KeyError:
            raise KeyError(
                f"metrica {key!r} nao foi resolvida; declare-a em `requires_metrics`"
            ) from None

    def value(self, concept_id: str) -> Decimal:
        return self.concept(concept_id).value


@dataclass(frozen=True)
class CheckEval:
    """Resultado cru de um check. `None` no lugar deste objeto = pulado."""

    passed: bool
    observed: Decimal | None = None
    expected: Decimal | None = None


ComputeFn = Callable[[Inputs], Decimal]
CheckFn = Callable[[Inputs, Decimal, ConsistencyCheck], CheckEval | None]


@dataclass(frozen=True)
class Metric:
    definition: MetricDefinition
    compute: ComputeFn
    check_fns: dict[str, CheckFn] = field(default_factory=dict)

    @property
    def ref(self) -> MetricRef:
        return self.definition.ref


class RegistryError(ValueError):
    pass


class MetricRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}

    def register(self, metric: Metric) -> Metric:
        key = str(metric.ref)
        if key in self._metrics:
            raise RegistryError(f"metrica ja registrada: {key}")

        definition = metric.definition
        for concept_id in (*definition.requires_concepts, *definition.optional_concepts):
            if not concepts.exists(concept_id):
                raise RegistryError(
                    f"{key}: conceito desconhecido {concept_id!r}. O catalogo de "
                    "conceitos e a autoridade."
                )
        declared = set(definition.checks)
        for check in declared:
            if check.check_id not in metric.check_fns:
                raise RegistryError(f"{key}: check {check.check_id!r} declarado sem funcao")
        for check_id in metric.check_fns:
            if check_id not in {c.check_id for c in definition.checks}:
                raise RegistryError(f"{key}: funcao de check {check_id!r} nao declarada")

        self._metrics[key] = metric
        return metric

    def get(self, ref: str | MetricRef) -> Metric:
        key = str(ref)
        try:
            return self._metrics[key]
        except KeyError:
            raise RegistryError(
                f"metrica desconhecida: {key}. Registradas: {', '.join(sorted(self._metrics))}"
            ) from None

    def has(self, ref: str | MetricRef) -> bool:
        return str(ref) in self._metrics

    def all(self) -> tuple[Metric, ...]:
        return tuple(self._metrics[k] for k in sorted(self._metrics))

    def validate(self) -> None:
        """Toda dependencia existe e o grafo e aciclico."""
        for key, metric in self._metrics.items():
            for dep in metric.definition.requires_metrics:
                if str(dep) not in self._metrics:
                    raise RegistryError(
                        f"{key} depende de {dep}, que nao esta registrada. "
                        "Dependencia e pinada por versao de proposito."
                    )

        visiting: set[str] = set()
        done: set[str] = set()

        def visit(key: str, path: tuple[str, ...]) -> None:
            if key in done:
                return
            if key in visiting:
                raise RegistryError(f"ciclo entre metricas: {' -> '.join([*path, key])}")
            visiting.add(key)
            for dep in self._metrics[key].definition.requires_metrics:
                visit(str(dep), (*path, key))
            visiting.discard(key)
            done.add(key)

        for key in sorted(self._metrics):
            visit(key, ())


def default_registry() -> MetricRegistry:
    """Registro com as metricas embutidas, ja validado.

    Import local: os modulos de definicao importam deste modulo.
    """
    from pat.semantics.definitions import register_all

    registry = MetricRegistry()
    register_all(registry)
    registry.validate()
    return registry

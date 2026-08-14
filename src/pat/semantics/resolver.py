"""A porta entre a camada semantica e qualquer fonte concreta.

O motor de metricas depende deste Protocol, nunca de `pat.query.asof` nem de
`pat.store`. Trocar a CVM pela SEC e escrever outro `FactResolver`; nenhuma
metrica, nenhum conceito e nenhum contrato muda.

Uma implementacao de `FactResolver` e o unico lugar autorizado a interpretar
codigos de conta, identificadores locais de companhia e elementos de
taxonomia. Este modulo declara so a forma da porta.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    LineAddress,
    PeriodKind,
    ReportingScope,
    TaxonomyId,
    UnavailableReason,
)


class ResolvedFact:
    """Um fato ja localizado, com o suficiente para linhagem e nada especifico
    de regime."""

    __slots__ = (
        "value",
        "currency",
        "period_type",
        "period_start",
        "period_end",
        "knowledge_date",
        "fact_id",
        "locator",
        "label_as_reported",
    )

    def __init__(
        self,
        *,
        value: Decimal,
        currency: str,
        period_type: PeriodType,
        period_start: date | None,
        period_end: date,
        knowledge_date: date,
        fact_id: str,
        locator: str,
        label_as_reported: str | None = None,
    ) -> None:
        self.value = value
        self.currency = currency
        self.period_type = period_type
        self.period_start = period_start
        self.period_end = period_end
        self.knowledge_date = knowledge_date
        self.fact_id = fact_id
        self.locator = locator
        self.label_as_reported = label_as_reported


class ResolutionFailure:
    """Por que um endereco nao virou fato. Sempre um motivo nomeado - nunca
    None solto, que o chamador acabaria lendo como zero."""

    __slots__ = ("reason", "message")

    def __init__(self, reason: UnavailableReason, message: str) -> None:
        self.reason = reason
        self.message = message


@runtime_checkable
class FactResolver(Protocol):
    """Resolve um endereco de taxonomia num fato bitemporal, AS OF uma data."""

    taxonomy: TaxonomyId

    def resolve(
        self,
        *,
        entity_id: str,
        address: LineAddress,
        period_end: date,
        period_kind: PeriodKind,
        scope: ReportingScope,
        as_of: date,
    ) -> ResolvedFact | ResolutionFailure:
        """Nunca levanta por dado ausente: ausencia e resposta, e vem nomeada."""
        ...

    def entity_display(self, entity_id: str) -> tuple[str | None, tuple[tuple[str, str], ...]]:
        """(nome para exibicao, identificadores locais no regime da fonte)."""
        ...

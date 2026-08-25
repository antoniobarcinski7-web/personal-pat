"""`FactResolver` do XBRL da SEC.

A fronteira do lado americano. Acima dela so existem conceitos e metricas
universais; abaixo, elemento XBRL e dimensao. E o segundo modulo do sistema
autorizado a importar `pat.query.asof` - o primeiro e o da CVM.

O que este arquivo prova
------------------------
Nenhum conceito, nenhuma metrica e nenhuma decomposicao mudou para o regime
americano existir. `ebitda@v1` continua sendo `ebit@v1 + d_and_a@v1`, escrita
uma vez em 2025 pensando na CVM, e roda sobre a Intel sem uma linha de
alteracao. Era isso que `test_second_framework.py` vinha prometendo com um
regime ficticio; aqui a promessa encontra dado real.

Escopo, e a assimetria com o Brasil
-----------------------------------
`ReportingScope.CONSOLIDATED` e o unico escopo que resolve. A CVM publica DF
Individual e DF Consolidada lado a lado; o 10-K americano e consolidado por
definicao, e nao existe a outra metade. Pedir `PARENT_ONLY` aqui devolve
`SCOPE_NOT_AVAILABLE` com o motivo escrito - e nao um numero consolidado
disfarcado, que e o que aconteceria se o escopo fosse ignorado.

Periodo: a tolerancia do exercicio de 52/53 semanas
----------------------------------------------------
O exercicio da Intel fecha no ultimo sabado de dezembro - 2024-12-28,
2023-12-30 - e nao em 31/12. Quem classifica o tipo de periodo e o builder,
por duracao com tolerancia; aqui so se confere que o tipo bate com o que o
conceito pede. Um mapeamento que apontasse um conceito de fluxo para uma conta
de balanco vira WRONG_PERIOD_TYPE em vez de numero errado.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    LineAddress,
    PeriodKind,
    ReportingScope,
    TaxonomyId,
    UnavailableReason,
)
from pat.query.asof import AsOf
from pat.semantics.frameworks.us_gaap.adapter import parts_of
from pat.semantics.resolver import ResolutionFailure, ResolvedFact

_EXPECTED_PERIOD_TYPE = {
    PeriodKind.FLOW: PeriodType.YEAR,
    PeriodKind.STOCK: PeriodType.INSTANT,
}

TAXONOMY_STATEMENT = "us-gaap"
"""O valor gravado na coluna `statement` do gold para este regime.

O gold guarda o endereco em tres colunas genericas, e cada regime as preenche
com o seu vocabulario. Ver a nota em `build_sec.py`."""


class UsGaapResolver:
    taxonomy: ClassVar[TaxonomyId] = TaxonomyId.US_GAAP_XBRL

    def __init__(self, asof: AsOf) -> None:
        self._asof = asof
        self._entities: dict[str, object] = {}

    def _entity(self, entity_id: str):
        if entity_id not in self._entities:
            self._entities[entity_id] = self._asof.entity(entity_id)
        return self._entities[entity_id]

    def entity_display(self, entity_id: str) -> tuple[str | None, tuple[tuple[str, str], ...]]:
        ref = self._entity(entity_id)
        if ref is None:
            return None, ()
        locais = tuple(
            (linha[0], linha[1])
            for linha in self._asof.conn.execute(
                "SELECT scheme, local_id FROM entity WHERE entity_id = ? ORDER BY scheme",
                [entity_id],
            ).fetchall()
        )
        return ref.display_name, locais

    def resolve(
        self,
        *,
        entity_id: str,
        address: LineAddress,
        period_end: date,
        period_kind: PeriodKind,
        scope: ReportingScope,
        as_of: date,
        member: str | None = None,
    ) -> ResolvedFact | ResolutionFailure:
        if scope is not ReportingScope.CONSOLIDATED:
            return ResolutionFailure(
                UnavailableReason.SCOPE_NOT_AVAILABLE,
                "o regime us-gaap nao publica demonstracao individual: o 10-K e "
                "consolidado por definicao. Consulte em escopo consolidado.",
            )

        element, segment = parts_of(address)
        # O membro pedido SUBSTITUI a dimensao do endereco. E aqui que a
        # dimensao entra, e nao no motor: `BusinessSegments=IntelFoundry;` e
        # vocabulario da SEC, e o motor nao pode conhece-lo.
        if member is not None:
            segment = member
        ref = self._entity(entity_id)

        view = self._asof.value(
            entity_id=entity_id,
            cd_conta=element,
            period_end=period_end,
            as_of=as_of,
            statement=TAXONOMY_STATEMENT,
            consolidated=True,
            coluna_df=segment,
        )
        if view is None:
            if not self._has_any_fact(entity_id):
                return ResolutionFailure(
                    UnavailableReason.UNKNOWN_ENTITY,
                    f"nenhum fato no gold para entity_id={entity_id}. "
                    "Rode a ingestao da SEC para esta companhia.",
                )
            nome = ref.display_name if ref else entity_id
            dimensao = f" [{segment}]" if segment else ""
            return ResolutionFailure(
                UnavailableReason.MISSING_FACT_AS_OF,
                f"nada conhecido em {as_of} para us-gaap:{element}{dimensao} de "
                f"{nome} no periodo findo em {period_end}",
            )

        expected = _EXPECTED_PERIOD_TYPE[period_kind]
        if view.period_type != expected:
            return ResolutionFailure(
                UnavailableReason.WRONG_PERIOD_TYPE,
                f"us-gaap:{element} devolveu {view.period_type} e o conceito pede "
                f"{expected}. O mapeamento aponta para o tipo errado de linha.",
            )

        return ResolvedFact(
            value=view.value,
            currency=view.currency or "USD",
            period_type=view.period_type,
            period_start=view.period_start,
            period_end=view.period_end,
            knowledge_date=view.knowledge_date,
            fact_id=view.fact_id,
            locator=view.locator,
            label_as_reported=address.label_as_reported,
        )

    def _has_any_fact(self, entity_id: str) -> bool:
        return bool(
            self._asof.conn.execute(
                "SELECT 1 FROM gold_fact WHERE entity_id = ? LIMIT 1", [entity_id]
            ).fetchone()
        )

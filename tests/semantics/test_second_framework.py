"""A prova de que a camada semantica nao e brasileira.

O desenho promete que plugar outro regime custa um adapter e um resolver, sem
tocar em conceito, metrica ou contrato. Promessa de arquitetura que ninguem
exerce e so prosa; aqui ela e exercida.

Este teste monta um regime ficticio - taxonomia XBRL americana, dolar, so
escopo consolidado - e roda `ebitda@v1` e `margem_ebitda@v1` sobre ele. As
metricas usadas sao exatamente as mesmas do Brasil, importadas do mesmo
registro. Nao ha nada especifico de regime nelas para adaptar.

Nao e o comeco da Fase 6: nada disso vai para producao, e a taxonomia
americana continua sem implementacao. E um teste de arquitetura.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    Dimension,
    Fidelity,
    LineAddress,
    MetricUnavailable,
    PeriodKind,
    ReportingScope,
    StatementKind,
    TaxonomyId,
    UnavailableReason,
)
from pat.semantics import concepts
from pat.semantics.engine import Engine
from pat.semantics.frameworks import _ADAPTERS, AdapterError, register
from pat.semantics.loader import MappingSet, parse_mapping
from pat.semantics.registry import default_registry
from pat.semantics.resolver import ResolutionFailure, ResolvedFact

US = TaxonomyId.US_GAAP_XBRL
FY2023 = date(2023, 9, 30)
"""Exercicio fiscal encerrado em setembro, nao em dezembro.

De brinde, prova que nada na camada semantica assume ano civil: `period_end` e
a data que manda, e "FY2023" e rotulo de fonte, nunca chave."""
AS_OF = date(2024, 6, 30)
ENTITY = "us:cik:0000320193"

_STATEMENTS = {
    "income": StatementKind.INCOME,
    "cash_flow": StatementKind.CASH_FLOW,
}


class FakeXbrlAdapter:
    """Enderaca por elemento de taxonomia, nao por codigo hierarquico."""

    taxonomy = US

    def parse_address(self, raw: dict[str, object]) -> LineAddress:
        element = str(raw.get("element", "")).strip()
        if not element.startswith("us-gaap:"):
            raise AdapterError(f"elemento invalido: {element!r}")
        statement = str(raw.get("statement", "")).strip()
        if statement not in _STATEMENTS:
            raise AdapterError(f"demonstracao desconhecida: {statement!r}")
        label = raw.get("label_as_reported")
        return LineAddress(
            taxonomy=US,
            address=(("element", element),),
            statement_kind=_STATEMENTS[statement],
            label_as_reported=str(label) if label else None,
        )

    def describe(self, address: LineAddress) -> str:
        return dict(address.address)["element"]


FACTS = {
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax": Decimal("383285000000"),
    "us-gaap:OperatingIncomeLoss": Decimal("114301000000"),
    "us-gaap:DepreciationDepletionAndAmortization": Decimal("11519000000"),
}


class FakeSecResolver:
    """So publica consolidado, so em dolar - como a maioria dos regimes."""

    taxonomy = US

    def __init__(self, *, moeda_misturada: bool = False) -> None:
        self.facts = FACTS
        self.moeda_misturada = moeda_misturada

    def _currency(self, element: str) -> str:
        # So para o teste de moeda mista: publica a D&A noutra moeda.
        if self.moeda_misturada and "Depreciation" in element:
            return "BRL"
        return "USD"

    def resolve(self, *, entity_id, address, period_end, period_kind, scope, as_of):
        if scope is not ReportingScope.CONSOLIDATED:
            return ResolutionFailure(
                UnavailableReason.SCOPE_NOT_AVAILABLE,
                "o regime nao publica demonstracao individual",
            )
        element = dict(address.address)["element"]
        if element not in self.facts:
            return ResolutionFailure(
                UnavailableReason.MISSING_FACT_AS_OF, f"elemento ausente: {element}"
            )
        return ResolvedFact(
            value=self.facts[element],
            currency=self._currency(element),
            period_type=PeriodType.YEAR if period_kind is PeriodKind.FLOW else PeriodType.INSTANT,
            period_start=date(2022, 10, 1),
            period_end=period_end,
            knowledge_date=date(2023, 11, 3),
            fact_id=f"fake-{element}",
            locator=f"10-K#{element}",
        )

    def entity_display(self, entity_id: str):
        return "APPLE INC", (("cik", "0000320193"),)


MAPPING_TOML = """
mapping_id = "us-gaap/default"
mapping_version = "v1"
framework = "us_gaap"
taxonomy = "us-gaap.xbrl"
jurisdiction = "US"
source = "sec.edgar"
is_default_for_source = true

[[binding]]
concept_id = "revenue_net"
fidelity = "exact"
equivalence_basis = '''
RevenueFromContractWithCustomerExcludingAssessedTax e receita de contratos com
clientes liquida de tributos cobrados de terceiros - o mesmo conceito economico
que a linha de receita liquida brasileira. A equivalencia e afirmada aqui, e nao
deduzida de os dois rotulos conterem a palavra "revenue".
'''
[[binding.line]]
statement = "income"
element = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
sign = 1

[[binding]]
concept_id = "ebit_reported"
fidelity = "exact"
equivalence_basis = "OperatingIncomeLoss e o resultado operacional como reportado."
[[binding.line]]
statement = "income"
element = "us-gaap:OperatingIncomeLoss"
sign = 1

[[binding]]
concept_id = "d_and_a_pnl"
fidelity = "exact"
equivalence_basis = '''
DepreciationDepletionAndAmortization no fluxo de caixa e a D&A reconhecida no
resultado - a mesma grandeza que, no Brasil, exige a linha de reversao do DFC.
'''
[[binding.line]]
statement = "cash_flow"
element = "us-gaap:DepreciationDepletionAndAmortization"
sign = 1
"""


@pytest.fixture
def us_engine():
    """Registra o adapter ficticio e o remove no fim.

    O registro de taxonomias e global; deixar o regime de teste instalado
    faria outros testes passarem por motivo errado.
    """
    register(FakeXbrlAdapter())
    mappings = MappingSet((parse_mapping(MAPPING_TOML.encode(), where="us"),))

    def _build(resolver=None) -> Engine:
        return Engine(
            resolvers={US: resolver or FakeSecResolver()},
            mappings=mappings,
            registry=default_registry(),
            source="sec.edgar",
            pat_version="0.1.0",
        )

    yield _build
    _ADAPTERS.pop(US, None)


def _compute(engine, metric, scope=ReportingScope.CONSOLIDATED):
    return engine.compute(metric, entity_id=ENTITY, period_end=FY2023, scope=scope, as_of=AS_OF)


def test_as_mesmas_metricas_calculam_em_outro_regime(us_engine):
    """Nenhuma linha de `definitions/` foi tocada para isto funcionar."""
    result = _compute(us_engine(), "ebitda@v1")

    assert result.value == Decimal("114301000000") + Decimal("11519000000")
    assert result.currency == "USD"
    assert result.framework == "us_gaap"
    assert result.jurisdiction == "US"
    assert result.fidelity is Fidelity.EXACT


def test_a_margem_atravessa_o_grafo_inteiro_no_outro_regime(us_engine):
    result = _compute(us_engine(), "margem_ebitda@v1")

    esperado = (Decimal("114301000000") + Decimal("11519000000")) / Decimal("383285000000")
    assert result.value == esperado
    assert result.dimension is Dimension.RATIO
    assert result.currency is None


def test_escopo_inexistente_no_regime_e_dito_e_nao_substituido(us_engine):
    """A maioria dos regimes nao tem DF individual. A resposta e dizer isso."""
    result = _compute(us_engine(), "receita_liquida@v1", scope=ReportingScope.PARENT_ONLY)

    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.SCOPE_NOT_AVAILABLE


def test_moedas_diferentes_entre_insumos_param_o_calculo(us_engine):
    """EBIT em dolar somado a D&A em real daria um numero limpo e sem sentido.

    Conversao cambial nao acontece implicitamente em lugar nenhum do sistema.
    """
    result = _compute(us_engine(FakeSecResolver(moeda_misturada=True)), "ebitda@v1")

    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.MIXED_CURRENCY


def test_o_catalogo_de_conceitos_e_o_mesmo_nos_dois_regimes(us_engine):
    """Se o regime novo precisasse de conceitos proprios, a camada universal
    nao seria universal - seria duas camadas brasileiras."""
    result = _compute(us_engine(), "receita_liquida@v1")

    (ref,) = result.inputs
    assert ref.role == concepts.REVENUE_NET
    assert ref.address.startswith("us-gaap.xbrl[")

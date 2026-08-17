"""Fixtures da camada de pesquisa.

Reusa o warehouse do golden da Fase 2 - bronze de verdade, parser de verdade,
gold de verdade - em vez de montar fatos sinteticos. E o que faz o teste de
procedencia testar procedencia, e nao um mock dela.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from pat.contracts.research import (
    DerivationOp,
    DerivationParams,
    DerivationStep,
    MetricStep,
    OutputKind,
    PlanEnvelope,
    ResearchConstraints,
    ResearchPlan,
    ResearchQuestion,
)
from pat.contracts.semantics import MetricRef, ReportingScope
from pat.research.canonical import question_id
from tests.semantics import golden_gpa as gpa
from tests.semantics.conftest import MAPPINGS_DIR, load_zips  # noqa: F401 - reexport util

ASKED_AT = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
"""Fixo: `asked_at` nao entra no `question_id`, e um valor fixo deixa isso
visivel em vez de apenas afirmado."""

EXECUTED_AT = datetime(2025, 7, 1, 12, 30, tzinfo=UTC)
"""Injetado nos testes para que `manifest_id` seja comparavel entre corridas.
Em producao e o relogio - e o manifesto e o unico lugar onde ele entra."""


@dataclass(frozen=True)
class Warehouse:
    """Conexao e bronze juntos: a conexao do DuckDB nao aceita atributo novo,
    e o teste de procedencia precisa dos dois."""

    conn: object
    bronze: object


@pytest.fixture
def warehouse(tmp_path):
    """Warehouse do GPA com FY2023 e FY2024, pelo caminho de producao."""
    from pat.store.bronze import BronzeStore
    from pat.store.db import connect, migrate

    conn = connect(tmp_path / "research.duckdb")
    migrate(conn)
    bronze = BronzeStore(tmp_path / "bronze")
    load_zips(conn, bronze, gpa.zips())
    yield Warehouse(conn=conn, bronze=bronze)
    conn.close()


@pytest.fixture
def gpa_conn(warehouse):
    return warehouse.conn


@pytest.fixture
def gpa_bronze(warehouse):
    return warehouse.bronze


def make_question(**overrides) -> ResearchQuestion:
    base = {
        "text": "What was GPA's EBITDA margin in FY2024, and how did it compare with FY2023?",
        "as_of": gpa.FY2024.replace(year=2025, month=6, day=30),
        "asked_at": ASKED_AT,
        "requested_output": OutputKind.COMPARISON,
        "constraints": ResearchConstraints(),
    }
    base.update(overrides)
    return ResearchQuestion(**base)


def make_plan(question: ResearchQuestion, **overrides) -> ResearchPlan:
    """O plano do caso golden: duas margens e a variacao entre elas."""
    base = {
        "question_id": question_id(question),
        "objective": "comparar a margem EBITDA consolidada do GPA entre FY2024 e FY2023",
        "as_of": question.as_of,
        "scope": ReportingScope.CONSOLIDATED,
        "steps": (
            MetricStep(
                step_id="margin_fy2023",
                metric=MetricRef.parse("margem_ebitda@v1"),
                entity_id=gpa.ENTITY_ID,
                period_end=gpa.FY2023,
            ),
            MetricStep(
                step_id="margin_fy2024",
                metric=MetricRef.parse("margem_ebitda@v1"),
                entity_id=gpa.ENTITY_ID,
                period_end=gpa.FY2024,
            ),
            DerivationStep(
                step_id="change",
                op=DerivationOp.DELTA,
                inputs=("margin_fy2023", "margin_fy2024"),
                params=DerivationParams(),
            ),
        ),
        "outputs": ("margin_fy2023", "margin_fy2024", "change"),
        "assumptions": ('"FY" lido como exercicio social encerrado em 31 de dezembro',),
    }
    base.update(overrides)
    return ResearchPlan(**base)


def make_metric_result(**overrides):
    """`MetricResult` sintetico, para exercitar caminhos que o fixture do GPA
    nao produz - check falhando, fidelidade aproximada, mapeamento nao
    conferido. Nunca usado para afirmar valor: so para afirmar comportamento."""
    from decimal import Decimal

    from pat.contracts.common import PeriodType
    from pat.contracts.semantics import (
        Dimension,
        Fidelity,
        InputRef,
        MetricKind,
        MetricResult,
    )

    base = {
        "metric": "d_and_a",
        "metric_version": "v1",
        "kind": MetricKind.REPORTED,
        "entity_id": gpa.ENTITY_ID,
        "scope": ReportingScope.CONSOLIDATED,
        "period_type": PeriodType.YEAR,
        "period_start": None,
        "period_end": gpa.FY2023,
        "as_of": gpa.FY2024.replace(year=2025, month=6, day=30),
        "knowledge_date": gpa.KD_2025,
        "value": Decimal("1136000000"),
        "dimension": Dimension.MONEY,
        "currency": "BRL",
        "fidelity": Fidelity.EXACT,
        "inputs": (
            InputRef(
                role="d_and_a_pnl",
                is_metric=False,
                value=Decimal("1136000000"),
                fidelity=Fidelity.EXACT,
                fact_id="fato-sintetico",
                currency="BRL",
                knowledge_date=gpa.KD_2025,
            ),
        ),
        "mapping_id": "br:cnpj:47508411000156",
        "mapping_version": "v1",
        "mapping_sha256": "b" * 64,
        "mapping_confirmed": True,
        "framework": "ifrs_cpc_br",
        "jurisdiction": "BR",
        "pat_version": "0.1.0",
    }
    base.update(overrides)
    return MetricResult(**base)


@pytest.fixture
def gpa_metric_result():
    return make_metric_result()


@pytest.fixture
def check_falho():
    """D-2: `MetricResult` sintetico com `proxima_da_retida` falhando.

    O fixture offline do GPA tem os dois checks passando - em FY2023 a D&A do
    DFC (1.136) e a da DVA (1.133) ficam dentro da tolerancia de 5%. A
    divergencia grande e a de FY2022, que so existe nos dados vivos da CVM e
    continua provada em `tests/network/test_semantics_live.py`.

    Este objeto sintetico existe para provar o COMPORTAMENTO do aviso, nunca
    para afirmar um valor. Os numeros abaixo sao ilustrativos e nenhum teste
    os compara com a fonte.
    """
    from decimal import Decimal

    from pat.contracts.semantics import CheckOutcome

    return make_metric_result(
        checks=(
            CheckOutcome(check_id="nao_negativa", status="pass", observed=Decimal("1136000000")),
            CheckOutcome(
                check_id="proxima_da_retida",
                status="fail",
                observed=Decimal("1902000000"),
                expected=Decimal("1026000000"),
                tolerance_abs=Decimal(1000),
            ),
        )
    )


@pytest.fixture
def question() -> ResearchQuestion:
    return make_question()


@pytest.fixture
def plan(question) -> ResearchPlan:
    return make_plan(question)


@pytest.fixture
def envelope(question, plan) -> PlanEnvelope:
    return PlanEnvelope(question=question, plan=plan)

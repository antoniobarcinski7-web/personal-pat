"""Contratos da Fase 3: o que a fronteira recusa antes de qualquer execucao."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pat.contracts.research import (
    ComputationResult,
    DerivationOp,
    DerivationStep,
    DerivedValue,
    InterpretiveClaim,
    MetricStep,
    OutputKind,
    ResearchPlan,
    ResearchQuestion,
    ResultKind,
)
from pat.contracts.semantics import Dimension, Fidelity, MetricRef, ReportingScope

AGORA = datetime(2025, 7, 1, tzinfo=UTC)
SHA = "a" * 64


def test_pergunta_exige_as_of():
    """Sem default, sem atalho: a mesma regra do `Engine.compute`."""
    with pytest.raises(ValidationError):
        ResearchQuestion(
            text="quanto foi a margem?",
            asked_at=AGORA,
            requested_output=OutputKind.NUMBER,
        )


def test_pergunta_recusa_periodo_posterior_ao_as_of():
    with pytest.raises(ValidationError, match="posterior ao as_of"):
        ResearchQuestion(
            text="q",
            as_of=date(2024, 6, 30),
            asked_at=AGORA,
            requested_output=OutputKind.NUMBER,
            pinned_periods=(date(2024, 12, 31),),
        )


def test_pergunta_exige_pinos_ordenados():
    with pytest.raises(ValidationError, match="ordenado"):
        ResearchQuestion(
            text="q",
            as_of=date(2025, 6, 30),
            asked_at=AGORA,
            requested_output=OutputKind.NUMBER,
            pinned_periods=(date(2024, 12, 31), date(2023, 12, 31)),
        )


def test_plano_recusa_step_id_repetido():
    passo = MetricStep(
        step_id="a", metric=MetricRef.parse("ebitda@v1"), entity_id="e", period_end=date(2023, 12, 31)
    )
    with pytest.raises(ValidationError, match="step_id repetido"):
        ResearchPlan(
            question_id=SHA,
            objective="o",
            as_of=date(2025, 6, 30),
            scope=ReportingScope.CONSOLIDATED,
            steps=(passo, passo),
            outputs=("a",),
        )


def test_plano_recusa_step_id_fora_do_padrao():
    with pytest.raises(ValidationError):
        MetricStep(
            step_id="Margem FY23",
            metric=MetricRef.parse("ebitda@v1"),
            entity_id="e",
            period_end=date(2023, 12, 31),
        )


def test_plano_vazio_e_sem_saida_nao_existe():
    """EMPTY_PLAN e NO_OUTPUT sao pegos no contrato, nao no validador."""
    passo = MetricStep(
        step_id="a", metric=MetricRef.parse("ebitda@v1"), entity_id="e", period_end=date(2023, 12, 31)
    )
    with pytest.raises(ValidationError):
        ResearchPlan(
            question_id=SHA, objective="o", as_of=date(2025, 6, 30),
            scope=ReportingScope.CONSOLIDATED, steps=(), outputs=("a",),
        )
    with pytest.raises(ValidationError):
        ResearchPlan(
            question_id=SHA, objective="o", as_of=date(2025, 6, 30),
            scope=ReportingScope.CONSOLIDATED, steps=(passo,), outputs=(),
        )


def test_derivacao_sem_insumo_nao_existe():
    with pytest.raises(ValidationError):
        DerivationStep(step_id="d", op=DerivationOp.DELTA, inputs=())


def test_metric_ref_malformada_e_recusada():
    with pytest.raises(ValueError, match="referencia de metrica invalida"):
        MetricRef.parse("ebitda")


# -- resultados --------------------------------------------------------------


def _derived(**overrides) -> DerivedValue:
    base = {
        "op": DerivationOp.DELTA,
        "value": Decimal("1.5"),
        "dimension": Dimension.RATIO,
        "derived_from": (SHA,),
        "fidelity": Fidelity.EXACT,
        "knowledge_date": date(2025, 2, 18),
    }
    base.update(overrides)
    return DerivedValue(**base)


def test_valor_derivado_exige_pais():
    with pytest.raises(ValidationError):
        _derived(derived_from=())


def test_valor_derivado_recusa_nao_finito():
    """NaN e infinito nao chegam a existir como resultado.

    Quem recusa primeiro e o proprio pydantic, no tipo `Decimal`; o validador
    do contrato continua la como pos-condicao para caminhos que nao passam
    pela validacao normal. As duas camadas dizem a mesma coisa.
    """
    with pytest.raises(ValidationError):
        _derived(value=Decimal("NaN"))
    with pytest.raises(ValidationError):
        _derived(value=Decimal("Infinity"))


def test_valor_monetario_exige_moeda_e_ratio_recusa():
    with pytest.raises(ValidationError, match="sem moeda"):
        _derived(dimension=Dimension.MONEY)
    with pytest.raises(ValidationError, match="nao leva moeda"):
        _derived(dimension=Dimension.RATIO, currency="BRL")


def test_resultado_sem_metrica_e_sem_derivado_nao_existe():
    """A forma que um numero fabricado teria - e nao tem."""
    with pytest.raises(ValidationError, match="exatamente um"):
        ComputationResult(result_id=SHA, step_id="x", kind=ResultKind.DERIVED)


def test_resultado_com_os_dois_preenchidos_nao_existe(gpa_metric_result):
    with pytest.raises(ValidationError, match="exatamente um"):
        ComputationResult(
            result_id=SHA,
            step_id="x",
            kind=ResultKind.METRIC,
            metric_result=gpa_metric_result,
            derived=_derived(),
        )


def test_afirmacao_interpretativa_nao_tem_onde_por_numero():
    """`extra=forbid` recusa o campo; e o que impede leitura e valor de se
    confundirem."""
    with pytest.raises(ValidationError):
        InterpretiveClaim(text="melhorou por alavancagem", supports=(SHA,), value=Decimal(1))


def test_afirmacao_interpretativa_exige_sustentacao():
    with pytest.raises(ValidationError):
        InterpretiveClaim(text="melhorou", supports=())

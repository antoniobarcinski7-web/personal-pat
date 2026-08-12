"""O contrato bitemporal precisa rejeitar o que nao faz sentido no tempo."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pat.contracts.common import PeriodType
from pat.contracts.facts import Fact
from pat.contracts.lineage import Lineage

LINEAGE = Lineage(
    content_sha256="a" * 64,
    retrieval_id="r1",
    locator="dfp_cia_aberta_DRE_con_2024.csv#cd_conta=3.05",
    extractor="cvm_dfp",
    extractor_version="1.0.0",
    extraction_run_id="run1",
)


def _fact(**overrides):
    base = dict(
        fact_id="f1",
        entity_id="br:cnpj:33000167000101",
        metric="ebitda",
        metric_version="v1",
        value=Decimal("1234.56"),
        unit="BRL_thousand",
        currency="BRL",
        period_type=PeriodType.QUARTER,
        period_start=date(2024, 10, 1),
        period_end=date(2024, 12, 31),
        knowledge_date=date(2025, 2, 26),
        lineage=LINEAGE,
    )
    return Fact(**(base | overrides))


def test_fato_valido():
    fact = _fact()
    assert fact.value == Decimal("1234.56")
    assert fact.knowledge_date > fact.period_end


def test_conhecimento_antes_do_fim_do_periodo_e_rejeitado():
    """Um numero nao pode ser conhecido antes de o periodo terminar.

    Esta e a barreira mais barata contra lookahead bias.
    """
    with pytest.raises(ValidationError, match="knowledge_date anterior"):
        _fact(knowledge_date=date(2024, 11, 1))


def test_instant_nao_aceita_period_start():
    with pytest.raises(ValidationError, match="period_start deve ser nulo"):
        _fact(period_type=PeriodType.INSTANT)


def test_instant_valido_sem_period_start():
    fact = _fact(period_type=PeriodType.INSTANT, period_start=None)
    assert fact.period_start is None


def test_periodo_de_fluxo_exige_period_start():
    with pytest.raises(ValidationError, match="period_start obrigatorio"):
        _fact(period_start=None)


def test_periodo_invertido_e_rejeitado():
    with pytest.raises(ValidationError, match="posterior a period_end"):
        _fact(period_start=date(2025, 1, 1))


def test_fato_e_imutavel():
    fact = _fact()
    with pytest.raises(ValidationError):
        fact.value = Decimal("1")


def test_campo_desconhecido_e_rejeitado():
    """extra=forbid: erro aparece na fronteira, nao tres camadas adiante."""
    with pytest.raises(ValidationError):
        _fact(ebitda_ajustado_2=Decimal("1"))


def test_valor_e_decimal_nao_float():
    fact = _fact(value=Decimal("0.1"))
    assert isinstance(fact.value, Decimal)
    assert fact.value + Decimal("0.2") == Decimal("0.3")


def test_reapresentacao_coexiste_com_original():
    """Mesma chave, knowledge_dates diferentes: dois fatos, nao um update."""
    original = _fact(fact_id="f1", value=Decimal("100"), knowledge_date=date(2025, 2, 26))
    restated = _fact(
        fact_id="f2",
        value=Decimal("95"),
        knowledge_date=date(2025, 8, 14),
        is_restatement=True,
    )
    assert original.period_end == restated.period_end
    assert original.value != restated.value
    assert restated.is_restatement

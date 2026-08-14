"""Contratos da camada semantica: o que eles recusam.

O valor destes testes esta nas rejeicoes. Um contrato que so aceita o caso
feliz nao esta protegendo nada.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    AccountingFramework,
    BoundLine,
    ConceptBinding,
    Dimension,
    Fidelity,
    InputRef,
    LineAddress,
    Mapping,
    MetricDefinition,
    MetricKind,
    MetricNotAvailableError,
    MetricRef,
    MetricResult,
    MetricUnavailable,
    PeriodKind,
    ReportingScope,
    StatementKind,
    TaxonomyId,
    UnavailableReason,
    weakest,
)

CVM = TaxonomyId.CVM_PLANO_PADRONIZADO


def address(cd_conta: str = "3.01", statement: str = "DRE") -> LineAddress:
    return LineAddress(
        taxonomy=CVM,
        address=(("cd_conta", cd_conta), ("statement", statement)),
        statement_kind=StatementKind.INCOME,
    )


# -- endereco ----------------------------------------------------------------


def test_endereco_vazio_e_recusado():
    with pytest.raises(ValidationError, match="vazio"):
        LineAddress(taxonomy=CVM, address=(), statement_kind=StatementKind.INCOME)


def test_endereco_fora_de_ordem_e_recusado():
    """Ordenacao nao e estetica: e o que faz dois enderecos iguais terem a
    mesma identidade, e portanto o mesmo hash de cadeia."""
    with pytest.raises(ValidationError, match="ordenado"):
        LineAddress(
            taxonomy=CVM,
            address=(("statement", "DRE"), ("cd_conta", "3.01")),
            statement_kind=StatementKind.INCOME,
        )


def test_endereco_com_chave_repetida_e_recusado():
    with pytest.raises(ValidationError, match="repetidas"):
        LineAddress(
            taxonomy=CVM,
            address=(("cd_conta", "3.01"), ("cd_conta", "3.02")),
            statement_kind=StatementKind.INCOME,
        )


# -- binding -----------------------------------------------------------------


def test_sinal_so_pode_ser_mais_um_ou_menos_um():
    with pytest.raises(ValidationError, match="sign"):
        BoundLine(address=address(), sign=0)


def test_fidelity_nao_exata_exige_nota_de_divergencia():
    """A regra central do desenho: aproximar e permitido, aproximar em
    silencio nao."""
    with pytest.raises(ValidationError, match="divergence_note"):
        ConceptBinding(
            concept_id="revenue_net",
            lines=(BoundLine(address=address(), sign=1),),
            fidelity=Fidelity.APPROXIMATE,
            equivalence_basis="porque sim",
        )


def test_equivalencia_semantica_precisa_ser_justificada():
    with pytest.raises(ValidationError):
        ConceptBinding(
            concept_id="revenue_net",
            lines=(BoundLine(address=address(), sign=1),),
            fidelity=Fidelity.EXACT,
            equivalence_basis="",
        )


def test_binding_nao_mistura_taxonomias():
    outro = LineAddress(
        taxonomy=TaxonomyId.US_GAAP_XBRL,
        address=(("element", "us-gaap:Revenues"),),
        statement_kind=StatementKind.INCOME,
    )
    with pytest.raises(ValidationError, match="taxonomias"):
        ConceptBinding(
            concept_id="revenue_net",
            lines=(BoundLine(address=address(), sign=1), BoundLine(address=outro, sign=1)),
            fidelity=Fidelity.EXACT,
            equivalence_basis="x",
        )


def test_mapeamento_nao_liga_o_mesmo_conceito_duas_vezes():
    binding = ConceptBinding(
        concept_id="revenue_net",
        lines=(BoundLine(address=address(), sign=1),),
        fidelity=Fidelity.EXACT,
        equivalence_basis="x",
    )
    with pytest.raises(ValidationError, match="duas vezes"):
        Mapping(
            mapping_id="m",
            mapping_version="v1",
            framework=AccountingFramework.IFRS_CPC_BR,
            taxonomy=CVM,
            jurisdiction="BR",
            source="cvm.dfp",
            source_sha256="a" * 64,
            bindings=(binding, binding),
        )


# -- definicao ---------------------------------------------------------------


def _definition(**overrides) -> MetricDefinition:
    base = dict(
        name="m",
        version="v1",
        kind=MetricKind.CALCULATED,
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        requires_concepts=("revenue_net",),
        definition="d",
        rationale="r",
    )
    return MetricDefinition(**(base | overrides))


def test_metrica_estimada_exige_premissas_declaradas():
    with pytest.raises(ValidationError, match="assumptions"):
        _definition(kind=MetricKind.ESTIMATED)


def test_metrica_reportada_e_leitura_de_um_conceito_so():
    """REPORTED com aritmetica dentro seria CALCULATED disfarcada."""
    with pytest.raises(ValidationError, match="REPORTED"):
        _definition(kind=MetricKind.REPORTED, requires_concepts=("revenue_net", "cogs"))


def test_metrica_sem_insumo_e_recusada():
    with pytest.raises(ValidationError, match="sem nenhum insumo"):
        _definition(requires_concepts=())


def test_referencia_de_metrica_e_pinada_por_versao():
    ref = MetricRef.parse("ebitda@v1")
    assert (ref.name, ref.version) == ("ebitda", "v1")
    assert str(ref) == "ebitda@v1"
    with pytest.raises(ValueError, match="invalida"):
        MetricRef.parse("ebitda")


# -- resultado ---------------------------------------------------------------


def _result(**overrides) -> MetricResult:
    base = dict(
        metric="m",
        metric_version="v1",
        kind=MetricKind.REPORTED,
        entity_id="br:cnpj:00000000000191",
        scope=ReportingScope.CONSOLIDATED,
        period_type=PeriodType.YEAR,
        period_start=date(2023, 1, 1),
        period_end=date(2023, 12, 31),
        as_of=date(2024, 6, 30),
        knowledge_date=date(2024, 2, 21),
        value=Decimal(10),
        dimension=Dimension.MONEY,
        currency="BRL",
        fidelity=Fidelity.EXACT,
        inputs=(InputRef(role="revenue_net", is_metric=False, value=Decimal(10), fidelity=Fidelity.EXACT),),
        mapping_id="m",
        mapping_version="v1",
        mapping_sha256="a" * 64,
        mapping_confirmed=True,
        framework=AccountingFramework.IFRS_CPC_BR,
        jurisdiction="BR",
        pat_version="0.1.0",
    )
    return MetricResult(**(base | overrides))


def test_metrica_monetaria_sem_moeda_e_recusada():
    with pytest.raises(ValidationError, match="sem moeda"):
        _result(currency=None)


def test_razao_com_moeda_e_recusada():
    """Uma margem em BRL nao significa nada, e o contrato nao deixa existir."""
    with pytest.raises(ValidationError, match="nao carrega moeda"):
        _result(dimension=Dimension.RATIO, currency="BRL")


def test_resultado_nao_pode_usar_insumo_futuro():
    with pytest.raises(ValidationError, match="desconhecido na data"):
        _result(knowledge_date=date(2025, 2, 18), as_of=date(2024, 6, 30))


def test_resultado_nao_pode_ser_conhecido_antes_do_fim_do_periodo():
    with pytest.raises(ValidationError, match="anterior ao fim"):
        _result(knowledge_date=date(2023, 6, 1))


def test_contratos_sao_imutaveis():
    result = _result()
    with pytest.raises(ValidationError):
        result.value = Decimal(999)


# -- indisponibilidade -------------------------------------------------------


def test_indisponivel_levanta_em_vez_de_devolver_numero():
    """`.value` nunca devolve zero silenciosamente."""
    unavailable = MetricUnavailable(
        metric="ebitda",
        metric_version="v1",
        reason=UnavailableReason.MISSING_CONCEPT,
        message="faltou d_and_a_pnl",
        entity_id="br:cnpj:00000000000191",
        period_end=date(2023, 12, 31),
        as_of=date(2024, 6, 30),
        scope=ReportingScope.CONSOLIDATED,
    )
    with pytest.raises(MetricNotAvailableError, match="faltou d_and_a_pnl"):
        _ = unavailable.value


# -- fidelity ----------------------------------------------------------------


def test_a_cadeia_vale_o_elo_mais_fraco():
    assert weakest((Fidelity.EXACT, Fidelity.APPROXIMATE)) is Fidelity.APPROXIMATE
    assert weakest((Fidelity.APPROXIMATE, Fidelity.PARTIAL)) is Fidelity.PARTIAL
    assert weakest((Fidelity.EXACT, Fidelity.EXACT)) is Fidelity.EXACT
    assert weakest(()) is Fidelity.EXACT

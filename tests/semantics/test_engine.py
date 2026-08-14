"""Motor: o caminho default, e todas as maneiras de nao ter resposta.

A metade interessante e a segunda. O que separa esta camada de uma planilha e
que ela sabe dizer "nao sei", e por que.
"""

from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.semantics import (
    Fidelity,
    MetricUnavailable,
    ReportingScope,
    UnavailableReason,
)
from tests.conftest import CNPJ, COD_CVM, DocumentSpec, LineSpec, ZipSpec, build_dfp_zip
from tests.semantics.conftest import MAPPINGS_DIR

CONSOLIDADO = ReportingScope.CONSOLIDATED
ENTITY = "br:cnpj:12345678000199"
FY2023 = date(2023, 12, 31)
AS_OF = date(2024, 6, 30)

MIL = Decimal(1000)


def _line(cd_conta: str, valor: str, ds_conta: str = "linha") -> LineSpec:
    return LineSpec(
        cd_conta=cd_conta,
        valor=valor,
        ds_conta=ds_conta,
        dt_fim="2023-12-31",
        dt_ini="2023-01-01",
        ordem="ÚLTIMO",
    )


def _zips(*, com_dfc: bool = False, dre: list[LineSpec] | None = None) -> dict[int, bytes]:
    """Companhia generica: DRE completa e DVA, sem linha de D&A no fluxo de caixa.

    E o caso da esmagadora maioria das empresas antes de alguem sentar e
    escrever o mapeamento delas.
    """
    dfc = [_line("6.01.01.04", "60000", "Depreciação / amortização")] if com_dfc else []
    return {
        2023: build_dfp_zip(
            ZipSpec(
                year=2023,
                documents=[DocumentSpec("2023-12-31", 1, "2024-02-20", "900001")],
                dre_con=dre
                or [
                    _line("3.01", "1000000", "Receita de Venda de Bens e/ou Serviços"),
                    _line("3.03", "400000", "Resultado Bruto"),
                    _line("3.04", "-100000", "Despesas/Receitas Operacionais"),
                    _line("3.05", "300000", "Resultado Antes do Resultado Financeiro"),
                ],
                bpa_con=[
                    LineSpec("1.01.01", "70000", "2023-12-31", ds_conta="Caixa e Equivalentes")
                ],
                flow_members={
                    ("DVA", "con"): [
                        _line("7.04.01", "-50000", "Depreciação, Amortização e Exaustão")
                    ],
                    ("DFC_MI", "con"): dfc,
                },
            )
        )
    }


# -- caminho default: familia, sem mapeamento de empresa ---------------------


def test_empresa_sem_mapeamento_calcula_pela_familia_e_avisa(make_engine):
    engine = make_engine(_zips())
    result = engine.compute(
        "ebitda@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )

    assert result.value == Decimal("350000") * MIL  # EBIT 300 + D&A 50
    assert result.mapping_confirmed is False
    assert result.mapping_id == "cvm.plano_padronizado/nao_financeiro"


def test_aproximacao_do_d_and_a_contamina_o_ebitda(make_engine):
    """O requisito central: se o binding disponivel so aproxima, o resultado
    diz isso - em vez de sair indistinguivel de um exato."""
    engine = make_engine(_zips())

    d_and_a = engine.compute(
        "d_and_a@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    ebitda = engine.compute(
        "ebitda@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    receita = engine.compute(
        "receita_liquida@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )

    assert d_and_a.fidelity is Fidelity.APPROXIMATE
    assert ebitda.fidelity is Fidelity.APPROXIMATE, "a fraqueza tem que subir pelo grafo"
    # E nao contamina quem nao depende dela.
    assert receita.fidelity is Fidelity.EXACT


def test_d_and_a_da_familia_vem_da_dva_com_sinal_normalizado(make_engine):
    """A DVA publica retencao como negativa; o conceito e positivo=despesa."""
    engine = make_engine(_zips())
    result = engine.compute(
        "d_and_a@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )

    assert result.value == Decimal("50000") * MIL
    (ref,) = result.inputs
    assert ref.sign_applied == -1
    assert ref.value == Decimal("-50000") * MIL


# -- indisponibilidades ------------------------------------------------------


def test_entidade_fora_do_gold_e_dita_pelo_nome(make_engine):
    engine = make_engine(_zips())
    result = engine.compute(
        "receita_liquida@v1",
        entity_id="br:cnpj:00000000000191",
        period_end=FY2023,
        scope=CONSOLIDADO,
        as_of=AS_OF,
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.UNKNOWN_ENTITY
    assert "pat build" in result.message


def test_conta_ausente_vira_indisponibilidade_e_nao_zero(make_engine):
    dre_sem_ebit = [_line("3.01", "1000000", "Receita")]
    engine = make_engine(_zips(dre=dre_sem_ebit))

    result = engine.compute(
        "ebit@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.MISSING_FACT_AS_OF
    assert result.concept_id == "ebit_reported"
    assert "3.05" in " ".join(result.tried)


def test_margem_com_receita_zero_nao_vira_zero_nem_infinito(make_engine):
    dre = [
        _line("3.01", "0", "Receita"),
        _line("3.03", "400000", "Resultado Bruto"),
        _line("3.04", "-100000", "Despesas/Receitas Operacionais"),
        _line("3.05", "300000", "Resultado Antes do Resultado Financeiro"),
    ]
    engine = make_engine(_zips(dre=dre))

    result = engine.compute(
        "margem_ebitda@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.DIVISION_BY_ZERO
    assert "nao e zero" in result.message


def test_sem_familia_default_a_resposta_e_nao_sei(make_engine, tmp_path):
    """Bancos e seguradoras vao cair aqui ate existir a familia financeira."""
    destino = tmp_path / "mappings"
    shutil.copytree(MAPPINGS_DIR, destino)
    familia = destino / "br" / "cvm_padronizado_nao_financeiro.toml"
    familia.write_text(
        familia.read_text().replace("is_default_for_source = true", "is_default_for_source = false")
    )

    engine = make_engine(_zips(), mappings_dir=destino)
    result = engine.compute(
        "receita_liquida@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.NO_MAPPING


def test_conceito_sem_binding_diz_qual_conceito_falta(make_engine, tmp_path):
    destino = tmp_path / "mappings"
    shutil.copytree(MAPPINGS_DIR, destino)
    familia = destino / "br" / "cvm_padronizado_nao_financeiro.toml"
    texto = familia.read_text()
    corte = texto.index('[[binding]]\nconcept_id = "d_and_a_pnl"')
    familia.write_text(texto[:corte])

    engine = make_engine(_zips(), mappings_dir=destino)
    result = engine.compute(
        "d_and_a@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.MISSING_CONCEPT
    assert result.concept_id == "d_and_a_pnl"
    assert "binding" in result.remedy


def test_conceito_de_fluxo_ligado_a_conta_de_saldo_e_recusado(make_engine, tmp_path):
    """Sem esta checagem, o mapeamento devolveria um saldo pontual como se
    fosse fluxo anual: um numero, do tipo errado, sem nada acusando."""
    destino = tmp_path / "mappings"
    shutil.copytree(MAPPINGS_DIR, destino)
    familia = destino / "br" / "cvm_padronizado_nao_financeiro.toml"
    familia.write_text(
        familia.read_text().replace(
            'statement = "DRE"\ncd_conta  = "3.01"', 'statement = "BPA"\ncd_conta  = "1.01.01"'
        )
    )

    engine = make_engine(_zips(), mappings_dir=destino)
    result = engine.compute(
        "receita_liquida@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.WRONG_PERIOD_TYPE


def test_dependencia_indisponivel_propaga_o_motivo_original(make_engine):
    dre_sem_ebit = [_line("3.01", "1000000", "Receita")]
    engine = make_engine(_zips(dre=dre_sem_ebit))

    result = engine.compute(
        "margem_ebitda@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.DEPENDENCY_UNAVAILABLE
    # A causa raiz sobrevive a dois niveis de dependencia.
    assert result.concept_id == "ebit_reported"


# -- checks ------------------------------------------------------------------


def test_check_sem_insumo_e_pulado_e_nao_falha(make_engine):
    """`skipped_missing` e diferente de `fail`, e o relatorio precisa dos dois."""
    dre = [
        _line("3.01", "1000000", "Receita"),
        _line("3.05", "300000", "Resultado Antes do Resultado Financeiro"),
    ]
    engine = make_engine(_zips(dre=dre))

    result = engine.compute(
        "ebit@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    (check,) = result.checks
    assert check.status == "skipped_missing"


def test_check_que_nao_fecha_expoe_o_problema_sem_esconder_o_numero(make_engine):
    """Falha de consistencia e informacao, nao motivo para calar a resposta."""
    dre = [
        _line("3.01", "1000000", "Receita"),
        _line("3.03", "400000", "Resultado Bruto"),
        _line("3.04", "-100000", "Despesas/Receitas Operacionais"),
        _line("3.05", "999000", "Resultado Antes do Resultado Financeiro"),
    ]
    engine = make_engine(_zips(dre=dre))

    result = engine.compute(
        "ebit@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )
    (check,) = result.checks
    assert check.status == "fail"
    assert check.observed == Decimal("999000") * MIL
    assert check.expected == Decimal("300000") * MIL
    assert result.value == Decimal("999000") * MIL


def test_mapeamento_da_empresa_vence_a_familia(make_engine, tmp_path):
    """O fluxo por projeto: um arquivo por empresa, so com o que diverge."""
    destino = tmp_path / "mappings"
    shutil.copytree(MAPPINGS_DIR, destino)
    (destino / "br" / "teste.toml").write_text(
        """
mapping_id = "br:cnpj:12345678000199"
mapping_version = "v1"
framework = "ifrs_cpc_br"
taxonomy = "cvm.plano_padronizado"
jurisdiction = "BR"
source = "cvm.dfp"
parent = "cvm.plano_padronizado/nao_financeiro"
entity_id = "br:cnpj:12345678000199"

[[binding]]
concept_id = "d_and_a_pnl"
fidelity = "exact"
equivalence_basis = "linha de reversao de D&A no fluxo de caixa indireto"
[[binding.line]]
statement = "DFC_MI"
cd_conta = "6.01.01.04"
sign = 1
""",
        encoding="utf-8",
    )

    engine = make_engine(_zips(com_dfc=True), mappings_dir=destino)
    result = engine.compute(
        "d_and_a@v1", entity_id=ENTITY, period_end=FY2023, scope=CONSOLIDADO, as_of=AS_OF
    )

    assert result.value == Decimal("60000") * MIL  # DFC, nao os 50 da DVA
    assert result.fidelity is Fidelity.EXACT
    assert result.mapping_confirmed is True

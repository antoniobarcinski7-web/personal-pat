"""Consultas point-in-time sobre a camada gold.

A propriedade que estes testes defendem: nao existe leitura sem data de
conhecimento. Uma consulta feita "hoje" sobre 2023 tem que poder devolver o
numero que era publico em 2024, e nao a versao reapresentada depois.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from tests.conftest import account_line

from pat.query.asof import AsOf
from pat.store.gold import build_facts, write_facts

ORIGINAL = Decimal("1000")
REVISADO = Decimal("900")


def _grava(conn, **overrides) -> None:
    """Materializa uma linha silver como fato. `silver_id` distingue versoes."""
    facts, stats = build_facts([account_line(**overrides)])
    assert stats.skipped_total == 0, "fixture invalida: o fato foi descartado"
    write_facts(conn, facts)


@pytest.fixture
def asof(warehouse):
    """Um periodo (FY2023) com duas versoes conhecidas: a original de
    fevereiro/2024 e a reapresentada de fevereiro/2025."""
    _grava(warehouse, silver_id="1" * 32, vl_conta=ORIGINAL, dt_receb=date(2024, 2, 20))
    _grava(warehouse, silver_id="2" * 32, vl_conta=REVISADO, dt_receb=date(2025, 2, 20))
    return AsOf(warehouse)


CHAVE = dict(cod_cvm=99999, cd_conta="3.01", period_end=date(2023, 12, 31))


# -- o eixo de conhecimento -------------------------------------------------


def test_antes_da_reapresentacao_devolve_o_valor_original(asof):
    view = asof.value(**CHAVE, as_of=date(2024, 6, 30))

    assert view.value == ORIGINAL * 1000
    assert view.knowledge_date == date(2024, 2, 20)


def test_depois_da_reapresentacao_devolve_o_valor_revisado(asof):
    view = asof.value(**CHAVE, as_of=date(2025, 6, 30))

    assert view.value == REVISADO * 1000
    assert view.knowledge_date == date(2025, 2, 20)


def test_a_propria_data_de_conhecimento_ja_conta_como_conhecida(asof):
    """O corte e `knowledge_date <= as_of`: no dia da divulgacao o numero
    ja e publico."""
    view = asof.value(**CHAVE, as_of=date(2024, 2, 20))

    assert view.value == ORIGINAL * 1000


def test_antes_de_qualquer_divulgacao_nao_ha_valor(asof):
    """Nao inventar numero e parte do desenho: nada era conhecido ali."""
    assert asof.value(**CHAVE, as_of=date(2024, 1, 1)) is None


def test_latest_e_apenas_um_atalho_para_as_of_hoje(asof):
    assert asof.latest(**CHAVE).value == asof.value(**CHAVE, as_of=date.today()).value


def test_history_lista_da_mais_antiga_para_a_mais_recente(asof):
    versoes = asof.history(**CHAVE)

    assert [v.value for v in versoes] == [ORIGINAL * 1000, REVISADO * 1000]
    assert [v.knowledge_date for v in versoes] == [date(2024, 2, 20), date(2025, 2, 20)]
    # Cada versao continua endereçavel individualmente.
    assert len({v.fact_id for v in versoes}) == 2


def test_empate_de_data_e_resolvido_pela_versao_do_documento(warehouse):
    """Duas versoes do documento recebidas no mesmo dia: vence a maior. A
    ordenacao e total, entao a mesma consulta devolve sempre a mesma linha."""
    _grava(warehouse, silver_id="1" * 32, vl_conta=ORIGINAL, versao=1)
    _grava(warehouse, silver_id="2" * 32, vl_conta=REVISADO, versao=2)

    view = AsOf(warehouse).value(**CHAVE, as_of=date(2025, 1, 1))
    assert view.value == REVISADO * 1000
    assert view.source_doc_version == 2


# -- a chave logica nao pode colapsar --------------------------------------


def test_individual_e_consolidado_nao_se_misturam(warehouse):
    _grava(warehouse, silver_id="1" * 32, vl_conta=ORIGINAL, consolidated=True)
    _grava(warehouse, silver_id="2" * 32, vl_conta=REVISADO, consolidated=False)
    asof = AsOf(warehouse)

    assert asof.value(**CHAVE, as_of=date(2025, 1, 1)).value == ORIGINAL * 1000
    assert (
        asof.value(**CHAVE, as_of=date(2025, 1, 1), consolidated=False).value == REVISADO * 1000
    )


def test_componentes_do_pl_nao_colapsam_na_mesma_chave(warehouse):
    """Sem COLUNA_DF na chave, dois componentes do patrimonio liquido
    apareceriam como uma reapresentacao que nunca houve."""
    comum = dict(statement="DMPL", cd_conta="5.01")
    _grava(warehouse, silver_id="1" * 32, vl_conta=ORIGINAL, coluna_df="Capital Social", **comum)
    _grava(warehouse, silver_id="2" * 32, vl_conta=REVISADO, coluna_df="Reservas de Lucro", **comum)
    asof = AsOf(warehouse)

    chave = dict(cod_cvm=99999, cd_conta="5.01", period_end=date(2023, 12, 31), statement="DMPL")
    assert asof.value(**chave, as_of=date(2025, 1, 1), coluna_df="Capital Social").value == (
        ORIGINAL * 1000
    )
    assert asof.restatements() == []


def test_demonstracoes_diferentes_nao_se_misturam(warehouse):
    _grava(warehouse, silver_id="1" * 32, statement="DRE", cd_conta="3.01", vl_conta=ORIGINAL)
    _grava(
        warehouse,
        silver_id="2" * 32,
        statement="BPA",
        cd_conta="3.01",
        vl_conta=REVISADO,
        dt_ini_exerc=None,
    )
    asof = AsOf(warehouse)

    assert asof.value(**CHAVE, as_of=date(2025, 1, 1), statement="BPA").value == REVISADO * 1000
    assert asof.restatements() == []


# -- reapresentacoes --------------------------------------------------------


def test_restatement_e_detectado_com_as_duas_pontas(asof):
    (item,) = asof.restatements()

    assert item.original.value == ORIGINAL * 1000
    assert item.revised.value == REVISADO * 1000
    assert item.delta == Decimal("-100000")
    assert item.delta_pct == Decimal("-10")
    assert item.period_end == date(2023, 12, 31)


def test_valor_estavel_entre_documentos_nao_e_restatement(warehouse):
    """O mesmo numero republicado em outro documento nao e reapresentacao."""
    _grava(warehouse, silver_id="1" * 32, vl_conta=ORIGINAL, dt_receb=date(2024, 2, 20))
    _grava(warehouse, silver_id="2" * 32, vl_conta=ORIGINAL, dt_receb=date(2025, 2, 20))

    assert AsOf(warehouse).restatements() == []


def test_filtro_por_magnitude_minima(asof):
    assert len(asof.restatements(min_abs_pct=5)) == 1
    assert asof.restatements(min_abs_pct=50) == []


def test_filtros_de_recorte_da_busca_por_restatements(asof):
    assert len(asof.restatements(cod_cvm=99999)) == 1
    assert asof.restatements(cod_cvm=1) == []
    assert len(asof.restatements(cd_conta="3.01", statement="DRE")) == 1
    assert asof.restatements(cd_conta="3.99") == []


def test_reapresentacao_simultanea_de_individual_e_consolidado(warehouse):
    """Caso real: a companhia reapresenta os dois escopos no mesmo documento.

    Sem recorte, os dois aparecem - esconder um deles por default omitiria
    metade da reapresentacao.
    """
    for scope, sid in ((True, "1"), (False, "3")):
        _grava(warehouse, silver_id=sid * 32, vl_conta=ORIGINAL, consolidated=scope, versao=1)
        _grava(
            warehouse,
            silver_id=chr(ord(sid) + 1) * 32,
            vl_conta=REVISADO,
            consolidated=scope,
            versao=2,
        )
    asof = AsOf(warehouse)

    assert len(asof.restatements()) == 2
    (con,) = asof.restatements(consolidated=True)
    assert con.consolidated is True
    (ind,) = asof.restatements(consolidated=False)
    assert ind.consolidated is False


def test_delta_pct_indefinido_quando_o_valor_original_e_zero(warehouse):
    """Divisao por zero nao pode virar excecao no meio de um relatorio."""
    _grava(warehouse, silver_id="1" * 32, vl_conta=Decimal("0"), dt_receb=date(2024, 2, 20))
    _grava(warehouse, silver_id="2" * 32, vl_conta=REVISADO, dt_receb=date(2025, 2, 20))

    (item,) = AsOf(warehouse).restatements()
    assert item.delta_pct is None
    assert item.delta == REVISADO * 1000


def test_consulta_repetida_devolve_sempre_a_mesma_linha(asof):
    """Determinismo (I3): sem isso, dois runs do mesmo relatorio poderiam
    citar fact_ids diferentes para o mesmo numero."""
    ids = {asof.value(**CHAVE, as_of=date(2025, 6, 30)).fact_id for _ in range(5)}

    assert len(ids) == 1

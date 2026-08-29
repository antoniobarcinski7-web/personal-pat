"""N4 - patrimonio, retorno ao acionista e retorno sobre o capital.

Tres testes definem o milestone:

- `test_fluxo_sobre_saldo_e_declarado_e_nao_inferido` - o motor recusa misturar
  fluxo com saldo, e a excecao e por metrica. Deixar `RATIO` liberar
  automaticamente trocaria uma checagem por uma coincidencia de tipo.
- `test_denominador_nao_positivo_recusa_em_vez_de_inverter_o_sinal` - prejuizo
  sobre patrimonio negativo sai POSITIVO, e "ROE de 40%" numa companhia que
  destruiu capital e o pior tipo de numero.
- `test_ausencia_de_dividendo_e_fato_e_nao_lacuna` - companhia que nao paga
  dividendo faz `retorno_ao_acionista@v1` recusar, em vez de apresentar so a
  recompra sob um nome que promete os dois.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.common import PeriodType
from pat.contracts.semantics import Dimension, ReportingScope, UnavailableReason
from pat.semantics import build_engine, concepts
from pat.semantics.registry import default_registry
from tests.semantics.test_balanco_e_caixa import (
    AS_OF,
    FLUXOS,
    FY2024,
    NFLX_ENTITY,
    SALDOS,
    _linha,
)

# Transcricao a mao do 10-K FY2024 da Netflix, completando a N4.
FLUXOS_N4 = {
    **FLUXOS,
    "IncomeTaxExpenseBenefit": "1254026000",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "9965657000",
    "PaymentsForRepurchaseOfCommonStock": "6200000000",
}
SALDOS_N4 = {**SALDOS, "StockholdersEquity": "24743567000"}


@pytest.fixture
def nflx(tmp_path):
    from pat.build_sec import write_sec_facts
    from pat.store.db import connect, migrate

    conn = connect(tmp_path / "nflx.duckdb")
    migrate(conn)
    linhas = [
        _linha(el, v, ordinal=i, instantaneo=False)
        for i, (el, v) in enumerate(FLUXOS_N4.items())
    ] + [
        _linha(el, v, ordinal=100 + i, instantaneo=True)
        for i, (el, v) in enumerate(SALDOS_N4.items())
    ]
    write_sec_facts(conn, linhas)
    yield conn
    conn.close()


@pytest.fixture
def engine(nflx):
    return build_engine(nflx, source="sec.companyfacts")


def metrica(engine, ref: str, *, period_end: date = FY2024):
    return engine.compute(
        ref,
        entity_id=NFLX_ENTITY,
        period_end=period_end,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )


# -- os criterios do milestone ----------------------------------------------


def test_fluxo_sobre_saldo_e_declarado_e_nao_inferido(engine):
    """`roe@v1` divide resultado do periodo por patrimonio da data-base.

    O motor recusa a mistura por default, e com razao: somar um fluxo a um
    saldo e uma grandeza que nao existe. DIVIDIR um pelo outro e o que um
    retorno sobre capital e, e a excecao e declarada por metrica.
    """
    roe = metrica(engine, "roe@v1")
    assert roe.dimension is Dimension.RATIO
    # O resultado herda o periodo do insumo de FLUXO: um ROE carimbado como
    # INSTANT diria que o lucro do exercicio aconteceu num dia.
    assert roe.period_type is PeriodType.YEAR
    assert roe.period_start == date(2024, 1, 1)

    lucro = metrica(engine, "lucro_liquido@v1")
    patrimonio = metrica(engine, "patrimonio_liquido@v1")
    assert roe.value == lucro.value / patrimonio.value


def test_a_excecao_de_mistura_e_por_metrica_e_nao_por_dimensao():
    """Uma razao tambem pode ser uma mistura sem sentido.

    Deixar `RATIO` liberar automaticamente trocaria uma checagem por uma
    coincidencia de tipo - `margem_ebitda@v1` e RATIO e nao mistura nada.
    """
    registro = default_registry()
    misturam = {
        str(m.ref) for m in registro.all() if m.definition.mixes_period_kinds
    }
    assert misturam == {"roe@v1", "roic@v1"}

    margem = registro.get("margem_ebitda@v1").definition
    assert margem.dimension is Dimension.RATIO
    assert not margem.mixes_period_kinds


def test_denominador_nao_positivo_recusa_em_vez_de_inverter_o_sinal(tmp_path):
    """Prejuizo sobre patrimonio negativo sai POSITIVO.

    Um leitor distraido veria "ROE de 40%" numa companhia que destruiu
    capital: certo na aritmetica e errado em tudo. A metrica recusa.
    """
    # Warehouse proprio: gravar o patrimonio negativo por cima do positivo no
    # mesmo warehouse nao substituiria nada - mesmo periodo e mesmo `filed`
    # sao a mesma chave logica, e a escrita e idempotente por desenho.
    conn = _warehouse_negativo(tmp_path)
    engine = build_engine(conn, source="sec.companyfacts")
    resultado = engine.compute(
        "roe@v1",
        entity_id=NFLX_ENTITY,
        period_end=FY2024,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )
    assert hasattr(resultado, "reason")
    assert "nao e positivo" in resultado.message
    conn.close()


def _warehouse_negativo(tmp_path):
    from pat.build_sec import write_sec_facts
    from pat.store.db import connect, migrate

    conn = connect(tmp_path / "negativo.duckdb")
    migrate(conn)
    fluxos = {**FLUXOS_N4, "NetIncomeLoss": "-1000000000"}
    saldos = {**SALDOS_N4, "StockholdersEquity": "-5000000000"}
    write_sec_facts(
        conn,
        [
            _linha(el, v, ordinal=i, instantaneo=False)
            for i, (el, v) in enumerate(fluxos.items())
        ]
        + [
            _linha(el, v, ordinal=100 + i, instantaneo=True)
            for i, (el, v) in enumerate(saldos.items())
        ],
    )
    return conn


def test_ausencia_de_dividendo_e_fato_e_nao_lacuna(engine):
    """A Netflix nao paga dividendo, e nunca pagou.

    `retorno_ao_acionista@v1` recusa em vez de apresentar so a recompra: sob
    esse nome, a recompra sozinha diria que o dividendo e zero calculado
    quando o correto e que ele nao existe como linha.
    """
    recompras = metrica(engine, "recompras@v1")
    assert recompras.value == Decimal("6200000000")

    retorno = metrica(engine, "retorno_ao_acionista@v1")
    assert hasattr(retorno, "reason")
    assert retorno.reason is UnavailableReason.DEPENDENCY_UNAVAILABLE
    assert "dividends_paid" in retorno.message


# -- retorno sobre o capital ------------------------------------------------


def test_roic_usa_aliquota_efetiva_e_nao_nominal(engine):
    """A nominal e uma constante do pais e nao diz nada sobre a companhia."""
    aliquota = metrica(engine, "aliquota_efetiva@v1")
    ebit = metrica(engine, "ebit@v1")
    capital = metrica(engine, "capital_investido@v1")
    roic = metrica(engine, "roic@v1")

    nopat = ebit.value * (Decimal(1) - aliquota.value)
    assert roic.value == nopat / capital.value
    # A efetiva da Netflix em FY2024 fica bem abaixo da nominal americana.
    assert Decimal("0.10") < aliquota.value < Decimal("0.15")


def test_capital_investido_e_pela_otica_do_financiamento(engine):
    """Patrimonio mais divida liquida: duas grandezas que ja existem, cada uma
    com procedencia propria.

    A otica do ativo exigiria decidir conta a conta o que e operacional, e
    cada decisao dessas e um julgamento por companhia.
    """
    capital = metrica(engine, "capital_investido@v1")
    patrimonio = metrica(engine, "patrimonio_liquido@v1")
    divida = metrica(engine, "divida_liquida@v1")

    assert capital.value == patrimonio.value + divida.value
    assert capital.period_type is PeriodType.INSTANT


def test_aliquota_sobre_prejuizo_recusa(engine, tmp_path):
    """Uma companhia com prejuizo e outra com beneficio fiscal produziriam o
    mesmo sinal por razoes opostas."""
    from pat.build_sec import write_sec_facts
    from pat.store.db import connect, migrate

    conn = connect(tmp_path / "prejuizo.duckdb")
    migrate(conn)
    fluxos = {
        **FLUXOS_N4,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "-2000000000",
    }
    write_sec_facts(
        conn,
        [
            _linha(el, v, ordinal=i, instantaneo=False)
            for i, (el, v) in enumerate(fluxos.items())
        ],
    )
    engine_prejuizo = build_engine(conn, source="sec.companyfacts")
    resultado = engine_prejuizo.compute(
        "aliquota_efetiva@v1",
        entity_id=NFLX_ENTITY,
        period_end=FY2024,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )
    assert hasattr(resultado, "reason")
    assert "nao e positivo" in resultado.message
    conn.close()


# -- as duas jurisdicoes ----------------------------------------------------


def test_o_patrimonio_atribuivel_e_composto_onde_nao_e_publicado(make_engine):
    """A CVM publica o consolidado e a participacao de nao controladores.

    O atribuivel a controladora e a diferenca, e o binding soma as duas linhas
    PUBLICADAS - somar linhas publicadas e diferente de estimar.
    """
    from tests.semantics import golden_gpa as gpa

    engine_br = make_engine(gpa.zips())
    patrimonio = engine_br.compute(
        "patrimonio_liquido@v1",
        entity_id=gpa.ENTITY_ID,
        period_end=date(2024, 12, 31),
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )
    # 2.03 (2.935 MM) menos 2.03.09 (9 MM).
    assert patrimonio.value == Decimal("2926000000")
    assert patrimonio.currency == "BRL"


def test_os_conceitos_novos_declaram_convencao_de_sinal():
    novos = (
        concepts.EQUITY_CONTROLLING,
        concepts.DIVIDENDS_PAID,
        concepts.SHARE_REPURCHASES,
        concepts.INCOME_TAX_EXPENSE,
        concepts.PRETAX_INCOME,
    )
    for concept_id in novos:
        assert concepts.get(concept_id).sign_convention.strip()

    # As duas devolucoes de capital sao SAIDA, como o capex.
    assert "saida" in concepts.get(concepts.DIVIDENDS_PAID).sign_convention
    assert "saida" in concepts.get(concepts.SHARE_REPURCHASES).sign_convention

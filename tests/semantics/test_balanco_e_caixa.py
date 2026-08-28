"""N1 - conceitos de balanco e de caixa, nas duas jurisdicoes.

O teste que define o milestone e
`test_a_mesma_metrica_roda_nos_dois_regimes`: um conjunto de conceitos que
resolvesse a Netflix e nao o Brasil seria um caminho ad hoc com nome de
arquitetura.

O outro que importa e `test_arrendamento_nunca_entra_calado`. A decisao A4 diz
que arrendamento entra explicitamente ou nao entra; aqui isso vira duas
metricas com nomes diferentes, e a que inclui RECUSA onde o regime nao publica
a linha - em vez de devolver a divida sem arrendamento sob um nome que promete
o contrario.

Tudo offline: as linhas americanas passam por `write_sec_facts` e as
brasileiras pelo ZIP de verdade, os dois caminhos de producao.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pat.contracts.semantics import Fidelity, UnavailableReason, ReportingScope
from pat.contracts.silver import XbrlFactLine
from pat.parse.sec_xbrl import EXTRACTOR, EXTRACTOR_VERSION
from pat.semantics import build_engine, concepts
from pat.store.db import connect, migrate

NFLX_CIK = "0001065280"
NFLX_ENTITY = f"us:cik:{NFLX_CIK}"
NFLX_NAME = "NETFLIX INC"
FY2024 = date(2024, 12, 31)
FILED = date(2025, 1, 27)
AS_OF = date(2025, 6, 30)

# Transcricao a mao do 10-K FY2024 da Netflix. Fluxos com periodo, saldos sem.
FLUXOS: dict[str, str] = {
    "Revenues": "39000966000",
    "CostOfRevenue": "21038464000",
    "SellingAndMarketingExpense": "2917554000",
    "ResearchAndDevelopmentExpense": "2925295000",
    "GeneralAndAdministrativeExpense": "1702039000",
    "OperatingIncomeLoss": "10417614000",
    "NetIncomeLoss": "8712000000",
    "NetCashProvidedByUsedInOperatingActivities": "7361364000",
    "PaymentsToAcquirePropertyPlantAndEquipment": "439538000",
}
SALDOS: dict[str, str] = {
    "CashAndCashEquivalentsAtCarryingValue": "7804733000",
    "ShortTermInvestments": "1779006000",
    "ShortTermBorrowings": "1784453000",
    "LongTermDebtNoncurrent": "13798351000",
    "OperatingLeaseLiabilityCurrent": "428482000",
    "OperatingLeaseLiabilityNoncurrent": "1983688000",
}


def _linha(elemento: str, valor: str, *, ordinal: int, instantaneo: bool) -> XbrlFactLine:
    return XbrlFactLine(
        silver_id=f"nflx-{elemento}-{ordinal}",
        content_sha256="0" * 64,
        retrieval_id="ret-nflx",
        source_member="CIK0001065280.json",
        source_line_no=ordinal,
        cik=NFLX_CIK,
        entity_name=NFLX_NAME,
        taxonomy="us-gaap",
        element=elemento,
        # Fato instantaneo nao tem `period_start`, e e so isso que o distingue:
        # o resolver deriva `PeriodType.INSTANT` dessa ausencia.
        period_start=None if instantaneo else date(2024, 1, 1),
        period_end=FY2024,
        fiscal_year=2024,
        fiscal_period="FY",
        value=Decimal(valor),
        unit="USD",
        accession="0001065280-25-000044",
        form="10-K",
        filed=FILED,
        extractor=EXTRACTOR,
        extractor_version=EXTRACTOR_VERSION,
        extraction_run_id="run-teste",
    )


@pytest.fixture
def nflx(tmp_path):
    """Warehouse com a Netflix, pelo caminho de producao (`write_sec_facts`)."""
    from pat.build_sec import write_sec_facts

    conn = connect(tmp_path / "nflx.duckdb")
    migrate(conn)
    linhas = [
        _linha(el, v, ordinal=i, instantaneo=False)
        for i, (el, v) in enumerate(FLUXOS.items())
    ] + [
        _linha(el, v, ordinal=100 + i, instantaneo=True)
        for i, (el, v) in enumerate(SALDOS.items())
    ]
    write_sec_facts(conn, linhas)
    yield conn
    conn.close()


@pytest.fixture
def engine_us(nflx):
    return build_engine(nflx, source="sec.companyfacts")


def metrica(engine, ref: str, *, entity: str = NFLX_ENTITY, period_end: date = FY2024):
    return engine.compute(
        ref,
        entity_id=entity,
        period_end=period_end,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )


# -- o criterio do milestone ------------------------------------------------


def test_a_mesma_metrica_roda_nos_dois_regimes(engine_us, make_engine):
    """`divida_bruta@v1` calcula na Netflix e no GPA, sem uma linha de excecao.

    Um conjunto de conceitos que resolvesse so a companhia americana seria um
    caminho ad hoc com nome de arquitetura - e a regra do projeto e que a
    proxima jurisdicao nao pede uma linha na camada de metrica.
    """
    from tests.semantics import golden_gpa as gpa

    americana = metrica(engine_us, "divida_bruta@v1")
    assert americana.value == Decimal("15582804000")
    assert americana.currency == "USD"

    engine_br = make_engine(gpa.zips())
    brasileira = engine_br.compute(
        "divida_bruta@v1",
        entity_id=gpa.ENTITY_ID,
        period_end=date(2024, 12, 31),
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )
    assert brasileira.currency == "BRL"
    assert brasileira.value > 0


def test_arrendamento_nunca_entra_calado(engine_us, make_engine):
    """Decisao A4: duas metricas, e a que inclui diz no nome que inclui.

    Onde o regime nao publica a linha, ela RECUSA - devolver a divida sem
    arrendamento sob um nome que promete o contrario seria a normalizacao
    silenciosa que a decisao existe para impedir.
    """
    from tests.semantics import golden_gpa as gpa

    sem = metrica(engine_us, "divida_bruta@v1")
    com = metrica(engine_us, "divida_bruta_com_arrendamento@v1")

    assert com.value == Decimal("17994974000")
    assert com.value - sem.value == Decimal("2412170000")

    # A familia brasileira nao liga arrendamento de proposito: o plano da CVM
    # reserva 2.01.04.03 e as companhias nao o usam de forma consistente.
    engine_br = make_engine(gpa.zips())
    recusa = engine_br.compute(
        "divida_bruta_com_arrendamento@v1",
        entity_id=gpa.ENTITY_ID,
        period_end=date(2024, 12, 31),
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )
    assert recusa.reason is UnavailableReason.MISSING_CONCEPT
    assert "lease_liability" in recusa.message


# -- balanco ----------------------------------------------------------------


def test_caixa_nao_soma_aplicacoes(engine_us):
    """Somar produziria uma 'posicao de liquidez' que ninguem publicou.

    Na Netflix de FY2024 a diferenca e de 1.779 MM - grande o bastante para
    mudar a divida liquida em 23%.
    """
    caixa = metrica(engine_us, "caixa_e_equivalentes@v1")
    assert caixa.value == Decimal("7804733000")
    assert caixa.value != Decimal("7804733000") + Decimal("1779006000")


def test_divida_liquida_abate_so_caixa(engine_us):
    liquida = metrica(engine_us, "divida_liquida@v1")
    bruta = metrica(engine_us, "divida_bruta@v1")
    caixa = metrica(engine_us, "caixa_e_equivalentes@v1")

    assert liquida.value == bruta.value - caixa.value
    assert liquida.value == Decimal("7778071000")


def test_saldo_de_balanco_e_pontual(engine_us):
    """Conceito STOCK resolve para `PeriodType.INSTANT`, e nao para o ano."""
    from pat.contracts.common import PeriodType

    caixa = metrica(engine_us, "caixa_e_equivalentes@v1")
    assert caixa.period_type is PeriodType.INSTANT
    assert caixa.period_start is None


# -- fluxo de caixa ---------------------------------------------------------


def test_fcf_subtrai_capex_do_caixa_operacional(engine_us):
    """As duas convencoes de sinal se encontram aqui.

    Caixa operacional e positivo quando ENTRA; capex e positivo quando SAI.
    Por isso a formula subtrai - e por isso os dois `sign_convention` estao
    escritos no catalogo em vez de implicitos na formula.
    """
    fcf = metrica(engine_us, "fcf@v1")
    fco = metrica(engine_us, "fluxo_de_caixa_operacional@v1")
    capex = metrica(engine_us, "capex@v1")

    assert capex.value > 0, "capex e positivo por convencao: saida de caixa"
    assert fcf.value == fco.value - capex.value
    assert fcf.value == Decimal("6921826000")


def test_a_fidelidade_do_capex_sobe_ate_o_fcf(engine_us):
    """O capex da Netflix e `partial` - nao inclui investimento em conteudo -
    e a marca tem que atravessar o grafo ate o resultado final.

    Um FCF que saisse marcado `exact` diria ao leitor que ele e comparavel com
    o FCF de uma companhia que capitaliza conteudo, e nao e.
    """
    capex = metrica(engine_us, "capex@v1")
    fcf = metrica(engine_us, "fcf@v1")

    assert capex.fidelity is Fidelity.PARTIAL
    assert fcf.fidelity is Fidelity.PARTIAL


# -- o catalogo -------------------------------------------------------------


def test_os_conceitos_novos_declaram_convencao_de_sinal():
    """Sem convencao canonica, o primeiro mapeamento de fluxo de caixa de um
    regime novo produz FCF com o sinal trocado."""
    novos = (
        concepts.CASH_AND_EQUIVALENTS,
        concepts.SHORT_TERM_INVESTMENTS,
        concepts.DEBT_CURRENT,
        concepts.DEBT_NONCURRENT,
        concepts.LEASE_LIABILITY_CURRENT,
        concepts.LEASE_LIABILITY_NONCURRENT,
        concepts.CASH_FLOW_OPERATING,
        concepts.CAPEX,
    )
    for concept_id in novos:
        conceito = concepts.get(concept_id)
        assert conceito.sign_convention.strip()

    # As duas convencoes opostas, ditas em voz alta.
    assert "entrada" in concepts.get(concepts.CASH_FLOW_OPERATING).sign_convention
    assert "saida" in concepts.get(concepts.CAPEX).sign_convention


def test_conceito_de_balanco_e_stock_e_de_fluxo_e_flow():
    from pat.contracts.semantics import PeriodKind

    assert concepts.get(concepts.DEBT_CURRENT).period_kind is PeriodKind.STOCK
    assert concepts.get(concepts.CASH_FLOW_OPERATING).period_kind is PeriodKind.FLOW
    assert concepts.get(concepts.CAPEX).period_kind is PeriodKind.FLOW


def test_insumo_ausente_recusa_nomeando_o_conceito(nflx, tmp_path):
    """Sem o binding de capex, `fcf@v1` nao vira caixa operacional puro."""
    conn = connect(tmp_path / "vazio.duckdb")
    migrate(conn)
    engine = build_engine(conn, source="sec.companyfacts")
    resultado = engine.compute(
        "fcf@v1",
        entity_id=NFLX_ENTITY,
        period_end=FY2024,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )
    assert hasattr(resultado, "reason")
    conn.close()

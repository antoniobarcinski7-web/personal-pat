"""Golden test da Intel: o primeiro regime fora do Brasil.

Transcreve a mao os elementos do `companyfacts` da SEC e roda as MESMAS
metricas da Fase 2 sobre eles. Nenhum conceito, nenhuma metrica e nenhuma
decomposicao foi alterada para este arquivo existir - e essa e a afirmacao que
o teste faz.

O par offline/rede e o mesmo da Fase 2: este prova que o calculo esta certo;
`tests/network/test_sec_live.py` prova que os elementos ainda existem na SEC.

Numeros conferidos contra o 10-K da Intel (companyfacts, CIK 0000050863),
FY2023 findo em 2023-12-30 e FY2024 findo em 2024-12-28, em USD.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    MetricUnavailable,
    ReportingScope,
    TaxonomyId,
    UnavailableReason,
)
from pat.semantics.engine import Engine
from pat.semantics.frameworks import install_builtins
from pat.semantics.loader import load_dir
from pat.semantics.registry import default_registry
from pat.semantics.resolver import ResolutionFailure, ResolvedFact

INTEL = "us:cik:0000050863"
FY2023 = date(2023, 12, 30)
FY2024 = date(2024, 12, 28)
AS_OF = date(2026, 6, 30)
MM = Decimal(1_000_000)

# Transcricao a mao do 10-K, em milhoes de USD. Os quatro primeiros formam a
# cascata da DRE; os dois ultimos sao a D&A que a Intel publica em separado na
# conciliacao do fluxo de caixa.
PUBLICADO: dict[tuple[str, date], Decimal] = {
    ("RevenueFromContractWithCustomerExcludingAssessedTax", FY2023): Decimal("54228"),
    ("RevenueFromContractWithCustomerExcludingAssessedTax", FY2024): Decimal("53101"),
    ("CostOfGoodsAndServicesSold", FY2023): Decimal("32517"),
    ("CostOfGoodsAndServicesSold", FY2024): Decimal("35756"),
    ("GrossProfit", FY2023): Decimal("21711"),
    ("GrossProfit", FY2024): Decimal("17345"),
    ("OperatingExpenses", FY2023): Decimal("21618"),
    ("OperatingExpenses", FY2024): Decimal("29023"),
    ("OperatingIncomeLoss", FY2023): Decimal("93"),
    ("OperatingIncomeLoss", FY2024): Decimal("-11678"),
    ("Depreciation", FY2023): Decimal("7847"),
    ("Depreciation", FY2024): Decimal("9951"),
    ("AmortizationOfIntangibleAssets", FY2023): Decimal("1755"),
    ("AmortizationOfIntangibleAssets", FY2024): Decimal("1428"),
}


class ResolverXbrlFalso:
    """Devolve o publicado, na forma que o resolver real devolveria."""

    taxonomy = TaxonomyId.US_GAAP_XBRL

    def resolve(self, *, entity_id, address, period_end, period_kind, scope, as_of):
        if scope is not ReportingScope.CONSOLIDATED:
            return ResolutionFailure(
                UnavailableReason.SCOPE_NOT_AVAILABLE,
                "o regime us-gaap nao publica demonstracao individual",
            )
        partes = dict(address.address)
        chave = (partes["element"], period_end)
        if chave not in PUBLICADO:
            return ResolutionFailure(
                UnavailableReason.MISSING_FACT_AS_OF,
                f"nada para {partes['element']} em {period_end}",
            )
        return ResolvedFact(
            value=PUBLICADO[chave] * MM,
            currency="USD",
            period_type=PeriodType.YEAR,
            # O exercicio da Intel tem 52/53 semanas: o inicio nao e 1o de
            # janeiro, e o fim nao e 31 de dezembro.
            period_start=date(period_end.year - 1, 12, 31),
            period_end=period_end,
            knowledge_date=date(period_end.year + 1, 1, 26),
            fact_id=f"intel-{partes['element']}-{period_end}",
            locator=f"companyfacts.json#{partes['element']}",
        )

    def entity_display(self, entity_id):
        return "INTEL CORP", (("cik", "0000050863"),)


@pytest.fixture
def engine() -> Engine:
    install_builtins()
    return Engine(
        resolvers={TaxonomyId.US_GAAP_XBRL: ResolverXbrlFalso()},
        mappings=load_dir(),
        registry=default_registry(),
        source="sec.companyfacts",
        pat_version="teste",
    )


def _valor(engine: Engine, ref: str, period_end: date):
    return engine.compute(
        ref,
        entity_id=INTEL,
        period_end=period_end,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )


# -- O gate da frente 2 ------------------------------------------------------


@pytest.mark.parametrize(
    ("period_end", "esperado"),
    [(FY2023, Decimal("9695")), (FY2024, Decimal("-299"))],
    ids=["FY2023", "FY2024"],
)
def test_ebitda_da_intel_a_partir_da_sec(engine, period_end, esperado):
    """Intel -> SEC -> entity_id -> us-gaap -> ebitda@v1.

    FY2023: EBIT 93 + D&A 9.602 = 9.695 MM
    FY2024: EBIT -11.678 + D&A 11.379 = -299 MM

    O prejuizo operacional de FY2024 atravessa o sistema sem tratamento
    especial. Um EBITDA praticamente nulo depois de 9,7 bi no ano anterior e um
    achado, e nao um erro - e o sistema o apresenta como qualquer outro numero.
    """
    resultado = _valor(engine, "ebitda@v1", period_end)
    assert not isinstance(resultado, MetricUnavailable), resultado
    assert resultado.value == esperado * MM
    assert resultado.currency == "USD"
    assert resultado.fidelity == "exact"


def test_a_cascata_da_dre_fecha_nos_dois_exercicios(engine):
    """A identidade contabil, conferida contra o publicado.

    Se ela nao fechasse, o binding estaria apontando para a linha errada - e
    seria um numero plausivel, que e a pior classe de erro.
    """
    for period_end in (FY2023, FY2024):
        receita = _valor(engine, "receita_liquida@v1", period_end).value
        ebit = _valor(engine, "ebit@v1", period_end).value
        custo = PUBLICADO[("CostOfGoodsAndServicesSold", period_end)] * MM
        bruto = PUBLICADO[("GrossProfit", period_end)] * MM
        despesas = PUBLICADO[("OperatingExpenses", period_end)] * MM

        assert receita - custo == bruto
        assert bruto - despesas == ebit


def test_d_and_a_soma_os_dois_elementos_que_a_intel_publica(engine):
    """A Intel nao publica `DepreciationDepletionAndAmortization`.

    Ela reporta `Depreciation` e `AmortizationOfIntangibleAssets` em separado,
    e o binding soma os dois. Um sistema que procurasse o elemento "obvio" nao
    acharia nada e concluiria que a Intel nao divulga D&A.
    """
    for period_end, esperado in ((FY2023, Decimal("9602")), (FY2024, Decimal("11379"))):
        resultado = _valor(engine, "d_and_a@v1", period_end)
        assert not isinstance(resultado, MetricUnavailable), resultado
        assert resultado.value == esperado * MM
        assert resultado.fidelity == "exact"


def test_margem_ebitda_negativa_sai_como_negativa(engine):
    """FY2024 tem margem negativa, e ela nao e suprimida nem zerada."""
    resultado = _valor(engine, "margem_ebitda@v1", FY2024)
    assert not isinstance(resultado, MetricUnavailable)
    assert resultado.value < 0


# -- O que o regime NAO faz --------------------------------------------------


def test_escopo_individual_recusa_com_motivo_nomeado(engine):
    """O 10-K e consolidado por definicao: nao existe a outra metade.

    Devolver o consolidado disfarcado de individual seria a pior resposta
    possivel - um numero certo para uma pergunta diferente.
    """
    resultado = engine.compute(
        "receita_liquida@v1",
        entity_id=INTEL,
        period_end=FY2024,
        scope=ReportingScope.PARENT_ONLY,
        as_of=AS_OF,
    )
    assert isinstance(resultado, MetricUnavailable)
    assert resultado.reason is UnavailableReason.SCOPE_NOT_AVAILABLE


def test_periodo_de_calendario_nao_resolve(engine):
    """O exercicio da Intel fecha em 2024-12-28, e nao em 31/12.

    Pedir a data de calendario nao devolve "o ano mais proximo": devolve
    ausencia nomeada. Aproximar aqui compararia 52 semanas com outra coisa.
    """
    resultado = _valor(engine, "receita_liquida@v1", date(2024, 12, 31))
    assert isinstance(resultado, MetricUnavailable)
    assert resultado.reason is UnavailableReason.MISSING_FACT_AS_OF


# -- A afirmacao central da Fase 2, agora com dado real ----------------------


def test_nenhuma_metrica_precisou_mudar_para_o_regime_novo_existir(engine):
    """As definicoes nao mencionam us-gaap, XBRL nem elemento nenhum.

    `tests/semantics/test_layering.py` ja proibia `cd_conta` na camada
    universal; esta assercao fecha o outro lado, e junto com os valores acima
    prova que a promessa era real e nao retorica.
    """
    from pathlib import Path

    definicoes = Path("src/pat/semantics/definitions")
    for arquivo in definicoes.glob("*.py"):
        texto = arquivo.read_text(encoding="utf-8")
        for proibido in ("us-gaap", "us_gaap", "XBRL", "GrossProfit", "cik"):
            assert proibido not in texto, f"{arquivo.name} cita {proibido}"

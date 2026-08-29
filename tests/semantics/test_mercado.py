"""N8 - preco de mercado, e a pergunta que ele torna respondivel.

Ate aqui o sistema fazia analise de NEGOCIO. Sem preco nao ha multiplo, e a
pergunta que uma tese honesta faz antes das outras - "o que o preco de hoje ja
esta assumindo?" - nao tem contra o que resolver.

Tres testes definem o milestone:

- `test_um_mapeamento_cruza_taxonomias` - uma companhia e UMA so, e os dados
  dela vem de regimes diferentes. Fixar a taxonomia por arquivo obrigaria a
  Netflix a ter dois mapeamentos, e nenhuma metrica poderia combinar os dois -
  que e exatamente o que valor de mercado e.
- `test_a_janela_olha_para_tras_e_nunca_para_frente` - um pregao posterior a
  data pedida nao era conhecido nela. Usa-lo seria vazamento de futuro no eixo
  do calendario.
- `test_o_pregao_devolvido_e_o_que_aconteceu` - quem pede um sabado recebe o
  fechamento de sexta, e o resultado DIZ que e de sexta.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pat.contracts.common import PeriodType, SourceTier
from pat.contracts.facts import Fact
from pat.contracts.lineage import Lineage
from pat.contracts.semantics import Dimension, ReportingScope, TaxonomyId
from pat.parse.yahoo_chart import parse_chart
from pat.semantics import build_engine, concepts
from pat.store.gold import GoldFact, write_facts
from tests.semantics.test_balanco_e_caixa import NFLX_ENTITY, _linha
from tests.semantics.test_retorno_sobre_capital import FLUXOS_N4, SALDOS_N4

AS_OF = date(2026, 8, 29)
FY2024 = date(2024, 12, 31)

# Fechamentos reais da Netflix, ajustados pelo desdobramento de 10 para 1.
PREGOES = {
    date(2024, 12, 26): Decimal("90.10"),
    date(2024, 12, 27): Decimal("89.55"),
    date(2024, 12, 31): Decimal("89.13"),
    date(2025, 1, 2): Decimal("90.50"),
}


def _cotacao(pregao: date, fechamento: Decimal, ordinal: int) -> GoldFact:
    fact = Fact(
        fact_id=f"quote-{ordinal}",
        entity_id=NFLX_ENTITY,
        metric="market.quote:close",
        metric_version="1.0.0",
        value=fechamento,
        unit="USD",
        currency="USD",
        period_type=PeriodType.INSTANT,
        period_start=None,
        period_end=pregao,
        # A data da BUSCA, e nao a do pregao: a serie ajustada muda
        # retroativamente, e o numero passou a ser esse quando foi baixado.
        knowledge_date=date(2026, 8, 29),
        consolidated=True,
        lineage=Lineage(
            content_sha256="b" * 64,
            retrieval_id="ret-quote",
            locator=f"NFLX#{ordinal}:close",
            extractor="yahoo.chart",
            extractor_version="1.0.0",
            extraction_run_id="run-teste",
        ),
    )
    return GoldFact(
        fact=fact,
        cod_cvm=0,
        denom_cia="NFLX",
        statement="market.quote",
        consolidated=True,
        coluna_df="",
        cd_conta="close",
        ds_conta="NFLX fechamento ajustado",
        ordem_exerc="ULTIMO",
        source_doc_id="NFLX",
        source_doc_version=1,
        silver_id=fact.fact_id,
    )


@pytest.fixture
def warehouse_com_mercado(tmp_path):
    """Netflix com fatos do arquivamento E cotacoes, pelos caminhos de producao."""
    from pat.build_sec import write_sec_facts
    from pat.store.db import connect, migrate

    conn = connect(tmp_path / "mercado.duckdb")
    migrate(conn)

    saldos = {**SALDOS_N4, "CommonStockSharesOutstanding": "4277571000"}
    # As duas linhas de D&A da Netflix: imobilizado e conteudo. Sem a segunda,
    # `ebitda@v1` recusa - que e o comportamento certo e nao o que este teste
    # quer exercitar.
    fluxos = {
        **FLUXOS_N4,
        "DepreciationDepletionAndAmortization": "328914000",
        "CostofServicesAmortizationofStreamingContentAssets": "15301517000",
    }
    linhas = [
        _linha(el, v, ordinal=i, instantaneo=False)
        for i, (el, v) in enumerate(fluxos.items())
    ] + [
        _linha(el, v, ordinal=100 + i, instantaneo=True)
        for i, (el, v) in enumerate(saldos.items())
    ]
    # A contagem de acoes tem unidade propria: `shares`, sem moeda.
    linhas = [
        x.model_copy(update={"unit": "shares"})
        if x.element == "CommonStockSharesOutstanding"
        else x
        for x in linhas
    ]
    write_sec_facts(conn, linhas)
    write_facts(
        conn, [_cotacao(d, v, i) for i, (d, v) in enumerate(sorted(PREGOES.items()))]
    )
    yield conn
    conn.close()


@pytest.fixture
def engine(warehouse_com_mercado):
    return build_engine(warehouse_com_mercado, source="sec.companyfacts")


def metrica(engine, ref: str, *, period_end: date = FY2024, as_of: date = AS_OF):
    return engine.compute(
        ref,
        entity_id=NFLX_ENTITY,
        period_end=period_end,
        scope=ReportingScope.CONSOLIDATED,
        as_of=as_of,
    )


# -- os criterios do milestone ----------------------------------------------


def test_um_mapeamento_cruza_taxonomias(engine):
    """Preco vem de `market.quote`, acoes vem de `us-gaap.xbrl`, e a metrica
    combina os dois no mesmo calculo.

    O `LineAddress` sempre carregou `taxonomy` - era o contrato dizendo que
    isso vale por endereco. Ate a N8 a informacao existia e ninguem a usava.
    """
    valor = metrica(engine, "valor_de_mercado@v1")
    assert valor.value == Decimal("89.13") * Decimal("4277571000")
    assert valor.currency == "USD"

    enderecos = {ref.address for ref in valor.inputs}
    assert any("market.quote" in a for a in enderecos)
    assert any("us-gaap.xbrl" in a for a in enderecos)


def test_a_janela_olha_para_tras_e_nunca_para_frente(engine):
    """Um pregao posterior a data pedida nao era conhecido nela.

    Usa-lo seria vazamento de futuro - o mesmo erro que `as_of` existe para
    impedir, cometido no eixo do calendario em vez do da divulgacao.
    """
    # 2025-01-02 tem pregao e e POSTERIOR ao fim do exercicio; o preco usado
    # tem que ser o de 31/12.
    preco = metrica(engine, "preco_por_acao@v1")
    assert preco.value == Decimal("89.13")
    assert preco.period_end == date(2024, 12, 31)


def test_o_pregao_devolvido_e_o_que_aconteceu(engine):
    """Quem pede um sabado recebe sexta, e o resultado DIZ que e de sexta.

    As duas saidas ruins sao simetricas: recusar torna o valor de mercado
    inutilizavel em metade dos exercicios fiscais americanos, e substituir em
    silencio afirma que houve negociacao num dia em que nao houve.
    """
    sabado = date(2024, 12, 28)
    preco = metrica(engine, "preco_por_acao@v1", period_end=sabado)

    assert preco.value == Decimal("89.55")
    # O `MetricResult` carimba a data PEDIDA, e deve: uma metrica de FY2024
    # fala de FY2024. O deslocamento aparece no INSUMO, que carrega a data-base
    # do fato que de fato resolveu.
    assert preco.period_end == sabado
    (insumo,) = preco.inputs
    assert insumo.period_end == date(2024, 12, 27), "o pregao que aconteceu"
    assert insumo.fact_id


def test_papel_parado_ha_meses_recusa_em_vez_de_devolver_preco_velho(engine):
    """Dez dias cobrem feriado prolongado e nao cobrem papel suspenso.

    Uma cotacao de tres meses atras devolvida como se fosse a do fechamento
    seria um numero velho com cara de corrente.
    """
    muito_depois = date(2025, 6, 30)
    resultado = metrica(engine, "preco_por_acao@v1", period_end=muito_depois)
    assert hasattr(resultado, "reason")
    assert "para tras" in resultado.message


# -- multiplos ---------------------------------------------------------------


def test_ev_soma_divida_liquida_ao_valor_de_mercado(engine):
    ev = metrica(engine, "enterprise_value@v1")
    valor = metrica(engine, "valor_de_mercado@v1")
    divida = metrica(engine, "divida_liquida@v1")
    assert ev.value == valor.value + divida.value


def test_o_multiplo_mistura_saldo_com_fluxo_declaradamente(engine):
    """EV e saldo na data-base; EBITDA e fluxo do exercicio. Somar seria uma
    grandeza que nao existe; dividir e o que um multiplo E."""
    multiplo = metrica(engine, "ev_ebitda@v1")
    assert multiplo.dimension is Dimension.RATIO
    assert multiplo.period_type is PeriodType.YEAR

    ev = metrica(engine, "enterprise_value@v1")
    assert ev.period_type is PeriodType.INSTANT
    ebitda = metrica(engine, "ebitda@v1")
    assert multiplo.value == ev.value / ebitda.value


def test_multiplo_sobre_resultado_negativo_recusa(warehouse_com_mercado, tmp_path):
    """Um P/L negativo nao quer dizer barato."""
    from pat.build_sec import write_sec_facts
    from pat.store.db import connect, migrate

    conn = connect(tmp_path / "prejuizo.duckdb")
    migrate(conn)
    fluxos = {**FLUXOS_N4, "NetIncomeLoss": "-2000000000"}
    saldos = {**SALDOS_N4, "CommonStockSharesOutstanding": "4277571000"}
    linhas = [
        _linha(el, v, ordinal=i, instantaneo=False)
        for i, (el, v) in enumerate(fluxos.items())
    ] + [
        _linha(el, v, ordinal=100 + i, instantaneo=True).model_copy(
            update={"unit": "shares"} if el == "CommonStockSharesOutstanding" else {}
        )
        for i, (el, v) in enumerate(saldos.items())
    ]
    write_sec_facts(conn, linhas)
    write_facts(
        conn, [_cotacao(d, v, i) for i, (d, v) in enumerate(sorted(PREGOES.items()))]
    )
    engine_prejuizo = build_engine(conn, source="sec.companyfacts")
    resultado = engine_prejuizo.compute(
        "preco_lucro@v1",
        entity_id=NFLX_ENTITY,
        period_end=FY2024,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )
    assert hasattr(resultado, "reason")
    assert "nao quer dizer barato" in resultado.message
    conn.close()


# -- o parser ----------------------------------------------------------------


def test_o_parser_usa_o_fechamento_ajustado():
    """O bruto e uma armadilha numa serie historica: um desdobramento aparece
    como queda de 90% num dia em que ninguem perdeu nada."""
    import json

    payload = json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "meta": {"symbol": "NFLX", "currency": "USD", "gmtoffset": -18000},
                        "timestamp": [1735660800],
                        "indicators": {
                            "quote": [{"close": [891.32]}],
                            "adjclose": [{"adjclose": [89.132]}],
                        },
                    }
                ]
            }
        }
    ).encode()
    linhas, _ = parse_chart(payload)
    assert linhas[0].close == Decimal("89.132"), "o ajustado, e nao o bruto"


def test_o_parser_recusa_serie_sem_ajuste():
    """Trocar o ajustado pelo bruto em silencio faria a serie mudar de
    significado sem mudar de nome."""
    import json

    payload = json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "meta": {"symbol": "NFLX", "currency": "USD", "gmtoffset": 0},
                        "timestamp": [1735660800],
                        "indicators": {"quote": [{"close": [891.32]}]},
                    }
                ]
            }
        }
    ).encode()
    with pytest.raises(ValueError, match="adjclose"):
        parse_chart(payload)


def test_pregao_sem_fechamento_nao_e_preenchido():
    """O buraco e da fonte, e preenche-lo seria inventar negociacao."""
    import json

    payload = json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "meta": {"symbol": "NFLX", "currency": "USD", "gmtoffset": 0},
                        "timestamp": [1735660800, 1735747200],
                        "indicators": {"adjclose": [{"adjclose": [89.13, None]}]},
                    }
                ]
            }
        }
    ).encode()
    linhas, stats = parse_chart(payload)
    assert len(linhas) == 1
    assert stats.skipped_null == 1


# -- procedencia -------------------------------------------------------------


def test_o_preco_e_a_fonte_mais_fraca_do_catalogo():
    """Nenhum regulador publica preco, e a distincao viaja no retrieval."""
    from pat.sources.public.yahoo import YahooProvider

    assert YahooProvider.tier is SourceTier.PUBLIC_WEB
    assert YahooProvider.min_interval_s > 1.0, "terceiro sem contrato pede mais cautela"


def test_o_conceito_de_preco_diz_o_que_ele_nao_e():
    notas = " ".join(concepts.get(concepts.SHARE_PRICE).boundary_notes).lower()
    assert "nao e uma peca contabil" in notas
    assert "retroativamente" in notas


def test_a_taxonomia_de_mercado_tem_adapter_e_resolver(warehouse_com_mercado):
    from pat.semantics.frameworks import registered

    assert TaxonomyId.MARKET_QUOTE in registered()
    engine = build_engine(warehouse_com_mercado, source="sec.companyfacts")
    assert engine.resolver_for(TaxonomyId.MARKET_QUOTE) is not None

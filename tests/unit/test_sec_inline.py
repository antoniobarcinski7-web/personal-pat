"""N7 - inline XBRL: onde vivem os elementos do proprio emissor.

O teste que define o milestone e `test_o_valor_nao_e_o_texto_da_pagina`. Um
fato inline mostra `16,422,166` e vale 16.422.166.000, e as tres
transformacoes que separam uma coisa da outra - formato, escala e sinal - sao
declaradas no proprio fato. Errar qualquer uma produz um numero plausivel e
errado, que e a pior classe de erro que este projeto pode cometer.

O outro que importa e `test_formato_desconhecido_e_recusado_e_contado`:
adivinhar a convencao numerica erraria por ordens de grandeza no emissor que
usa a outra, e o erro sairia com cara de numero publicado.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.parse.sec_inline import FIXED_ZERO, FORMATS, parse_inline_xbrl

CIK = "0001065280"
COMUM = dict(
    content_sha256="a" * 64,
    retrieval_id="ret-1",
    extraction_run_id="run-1",
    cik=CIK,
    entity_name="NETFLIX INC",
    accession="0001065280-25-000044",
    form="10-K",
    filed=date(2025, 1, 27),
    source_member="nflx-20241231.htm",
)

CONTEXTOS = (
    "<xbrli:context id='c-1'><xbrli:entity>"
    f"<xbrli:identifier scheme='http://www.sec.gov/CIK'>{CIK}</xbrli:identifier>"
    "</xbrli:entity><xbrli:period>"
    "<xbrli:startDate>2024-01-01</xbrli:startDate>"
    "<xbrli:endDate>2024-12-31</xbrli:endDate>"
    "</xbrli:period></xbrli:context>"
    "<xbrli:context id='c-saldo'><xbrli:entity>"
    f"<xbrli:identifier scheme='http://www.sec.gov/CIK'>{CIK}</xbrli:identifier>"
    "</xbrli:entity><xbrli:period>"
    "<xbrli:instant>2024-12-31</xbrli:instant>"
    "</xbrli:period></xbrli:context>"
    "<xbrli:context id='c-dim'><xbrli:entity>"
    f"<xbrli:identifier scheme='http://www.sec.gov/CIK'>{CIK}</xbrli:identifier>"
    "<xbrli:segment><xbrldi:explicitMember dimension='srt:StatementGeographicalAxis'>"
    "country:US</xbrldi:explicitMember></xbrli:segment>"
    "</xbrli:entity><xbrli:period>"
    "<xbrli:startDate>2024-01-01</xbrli:startDate>"
    "<xbrli:endDate>2024-12-31</xbrli:endDate>"
    "</xbrli:period></xbrli:context>"
    "<xbrli:unit id='usd'><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>"
    "<xbrli:unit id='shares'><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>"
)


def documento(*fatos: str) -> bytes:
    corpo = "".join(fatos)
    return (
        "<?xml version='1.0'?><html xmlns:ix='http://www.xbrl.org/2013/inlineXBRL'>"
        f"<body><div style='display:none'>{CONTEXTOS}</div>{corpo}</body></html>"
    ).encode("utf-8")


def fato(
    nome: str,
    texto: str,
    *,
    contexto: str = "c-1",
    unidade: str = "usd",
    scale: str = "",
    sign: str = "",
    formato: str = "ixt:num-dot-decimal",
    nil: str = "",
) -> str:
    attrs = [f"name='{nome}'", f"contextRef='{contexto}'", f"unitRef='{unidade}'"]
    if scale:
        attrs.append(f"scale='{scale}'")
    if sign:
        attrs.append(f"sign='{sign}'")
    if formato:
        attrs.append(f"format='{formato}'")
    if nil:
        attrs.append(f"xsi:nil='{nil}'")
    return f"<ix:nonFraction {' '.join(attrs)}>{texto}</ix:nonFraction>"


def linhas(*fatos: str):
    return parse_inline_xbrl(documento(*fatos), **COMUM)


# -- o criterio do milestone ------------------------------------------------


def test_o_valor_nao_e_o_texto_da_pagina():
    """`16,422,166` com `scale=3` vale 16.422.166.000.

    A transcricao e do 10-K real da Netflix: a amortizacao de conteudo de
    FY2025. Ler o texto como numero daria 16 milhoes - tres ordens de grandeza
    abaixo, e ainda assim um numero plausivel.
    """
    saida, stats = linhas(
        fato("nflx:CostofServicesAmortizationofStreamingContentAssets", "16,422,166", scale="3")
    )
    assert len(saida) == 1
    assert saida[0].value == Decimal("16422166000")
    assert saida[0].taxonomy == "nflx"
    assert saida[0].element == "CostofServicesAmortizationofStreamingContentAssets"
    assert stats.facts_read == 1


def test_formato_desconhecido_e_recusado_e_contado():
    """Adivinhar a convencao numerica erraria por ordens de grandeza.

    E o descarte e CONTADO: um parser que devolve menos do que o documento tem,
    sem dizer quantos, faz o leitor achar que a companhia publicou menos.
    """
    saida, stats = linhas(
        fato("us-gaap:Revenues", "one hundred", formato="ixt-sec:numwordsen")
    )
    assert saida == []
    assert stats.skipped_unknown_format == 1
    assert stats.skipped_total == 1


def test_a_virgula_decimal_e_lida_como_decimal():
    """`num-comma-decimal` inverte virgula e ponto. Ler um pelo outro erra por
    tres ordens de grandeza."""
    ponto, _ = linhas(fato("us-gaap:Revenues", "1.234,56", formato="ixt:num-comma-decimal"))
    virgula, _ = linhas(fato("us-gaap:Revenues", "1,234.56", formato="ixt:num-dot-decimal"))
    assert ponto[0].value == Decimal("1234.56")
    assert virgula[0].value == Decimal("1234.56")


def test_o_sinal_negativo_e_do_atributo_e_nao_do_texto():
    """Uma despesa aparece POSITIVA na pagina e negativa no fato."""
    saida, _ = linhas(fato("us-gaap:CostOfRevenue", "21,038", scale="6", sign="-"))
    assert saida[0].value == Decimal("-21038000000")


def test_decimals_nao_e_escala():
    """O erro classico de quem le iXBRL pela primeira vez.

    `decimals='-3'` diz que o numero e preciso ate a casa dos milhares; ele NAO
    multiplica nada. Quem o usasse como escala erraria por mil.
    """
    documento_com_decimals = documento(
        "<ix:nonFraction name='us-gaap:Revenues' contextRef='c-1' unitRef='usd' "
        "decimals='-3' format='ixt:num-dot-decimal'>39,001</ix:nonFraction>"
    )
    saida, _ = parse_inline_xbrl(documento_com_decimals, **COMUM)
    assert saida[0].value == Decimal("39001")


# -- fronteiras --------------------------------------------------------------


def test_fato_dimensional_fica_de_fora():
    """Dado por segmento nao pode colidir com o consolidado na chave logica.

    E a mesma regra que o `companyfacts` segue, e aqui ela precisa ser
    explicita porque o inline XBRL traz as duas coisas no mesmo arquivo.
    """
    saida, stats = linhas(
        fato("us-gaap:Revenues", "39,001", scale="6"),
        fato("us-gaap:Revenues", "17,359", scale="6", contexto="c-dim"),
    )
    assert len(saida) == 1
    assert saida[0].value == Decimal("39001000000")
    assert stats.skipped_dimensional == 1


def test_saldo_pontual_nao_tem_periodo_inicial():
    """Contexto com `instant` vira fato de balanco."""
    saida, _ = linhas(
        fato("us-gaap:CashAndCashEquivalentsAtCarryingValue", "7,805", scale="6", contexto="c-saldo")
    )
    assert saida[0].period_start is None
    assert saida[0].period_end == date(2024, 12, 31)


def test_unidade_nao_monetaria_e_filtrada_na_leitura():
    """O documento inteiro continua no bronze; reprocessar com outro filtro nao
    depende de re-baixar."""
    saida, stats = linhas(
        fato("us-gaap:CommonStockSharesOutstanding", "427,756", unidade="shares")
    )
    assert saida == []
    assert stats.skipped_other_unit == 1

    com_acoes, _ = parse_inline_xbrl(
        documento(fato("us-gaap:CommonStockSharesOutstanding", "427,756", unidade="shares")),
        units=("shares",),
        **COMUM,
    )
    assert com_acoes[0].unit == "shares"


def test_fato_nulo_e_descartado_e_contado():
    """`xsi:nil` e o emissor declarando que nao ha valor - diferente de zero."""
    saida, stats = linhas(fato("us-gaap:Revenues", "", nil="true"))
    assert saida == []
    assert stats.skipped_nil == 1


def test_travessao_declarado_vira_zero_e_nao_ausencia():
    """`fixed-zero` e o valor zero apresentado como travessao.

    Descarta-lo faria um zero PUBLICADO aparecer como dado ausente, que e coisa
    diferente.
    """
    saida, _ = linhas(fato("us-gaap:Revenues", "—", formato="ixt:fixed-zero"))
    assert len(saida) == 1
    assert saida[0].value == Decimal(0)


def test_contexto_inexistente_e_descartado_e_contado():
    saida, stats = linhas(fato("us-gaap:Revenues", "39,001", contexto="c-fantasma"))
    assert saida == []
    assert stats.skipped_no_context == 1


def test_o_texto_pode_vir_quebrado_por_marcacao():
    """O emissor envolve partes do numero em `<span>`. O valor e o texto
    inteiro, e nao o primeiro pedaco."""
    documento_quebrado = documento(
        "<ix:nonFraction name='us-gaap:Revenues' contextRef='c-1' unitRef='usd' "
        "scale='6' format='ixt:num-dot-decimal'>39<span>,</span>001</ix:nonFraction>"
    )
    saida, _ = parse_inline_xbrl(documento_quebrado, **COMUM)
    assert saida[0].value == Decimal("39001000000")


def test_a_leitura_e_reproduzivel():
    primeira, _ = linhas(fato("us-gaap:Revenues", "39,001", scale="6"))
    segunda, _ = linhas(fato("us-gaap:Revenues", "39,001", scale="6"))
    assert [x.silver_id for x in primeira] == [x.silver_id for x in segunda]
    assert [x.value for x in primeira] == [x.value for x in segunda]


def test_as_tabelas_de_formato_sao_declaradas():
    """Um formato que entra em silencio e uma convencao numerica adotada sem
    ninguem decidir."""
    assert "num-dot-decimal" in FORMATS
    assert "num-comma-decimal" in FORMATS
    assert "fixed-zero" in FIXED_ZERO
    assert "numwordsen" not in FORMATS and "numwordsen" not in FIXED_ZERO

"""Formatacao para leitura humana.

O que se afirma aqui e que a apresentacao encurta e NUNCA volta a ser numero.
"""

from __future__ import annotations

from decimal import Decimal

from pat.display import format_money, format_number, format_percent, format_ratio


def test_a_ordem_de_grandeza_aparece():
    """`45183036000.0000000000 USD` esta correto e nao ajuda ninguem.

    Dez casas decimais num numero de 45 bilhoes nao sao precisao: sao ruido
    que esconde justamente a primeira coisa que alguem le.
    """
    assert format_money(Decimal("45183036000.0000000000"), "USD") == "US$ 45,18 bi"


def test_a_escala_sai_da_grandeza_e_nao_da_metrica():
    """A mesma metrica vale bilhoes numa companhia e milhoes noutra. Escala
    fixa por metrica imprimiria "0,04 bi" para a segunda."""
    assert format_money(Decimal("1234567890123"), "BRL") == "R$ 1,23 tri"
    assert format_money(Decimal("2500000000"), "BRL") == "R$ 2,50 bi"
    assert format_money(Decimal("2500000"), "BRL") == "R$ 2,50 mi"
    assert format_money(Decimal("2500"), "BRL") == "R$ 2,50 mil"
    # Abaixo de mil nao ha ordem de grandeza que precise ser resumida.
    assert format_money(Decimal("812.5"), "BRL") == "R$ 812,50"


def test_negativo_mantem_o_sinal_antes_do_simbolo():
    assert format_money(Decimal("-2100744000"), "USD") == "-US$ 2,10 bi"


def test_moeda_desconhecida_sai_com_o_codigo():
    """Feio e certo. Inventar simbolo por semelhanca seria a versao monetaria
    de casar conta por rotulo."""
    assert format_money(Decimal("1200000000"), "CAD") == "CAD 1,20 bi"


def test_separador_brasileiro_sem_mexer_no_locale():
    """`locale` e estado global do processo: alterar muda o comportamento de
    todo o resto do programa, inclusive de codigo que nunca pediu nada."""
    assert format_number(Decimal("1234567.891")) == "1.234.567,89"


def test_razao_e_percentual():
    assert format_ratio(Decimal("12.4013")) == "12,40x"
    # Recebe a FRACAO. Se cada chamador tivesse que multiplicar por 100, um
    # deles esqueceria e publicaria "0,2%" onde eram 23%.
    assert format_percent(Decimal("0.23412")) == "23,4%"


def test_a_formatacao_nunca_devolve_numero():
    """A regra que nao se negocia.

    Arredondar para exibir e permitido; arredondar e devolver ao sistema nao.
    Um valor formatado que voltasse a ser `Decimal` seria a porta por onde uma
    aproximacao entra num calculo sem ninguem notar.
    """
    for funcao, args in (
        (format_money, (Decimal("1"), "USD")),
        (format_number, (Decimal("1"),)),
        (format_ratio, (Decimal("1"),)),
        (format_percent, (Decimal("1"),)),
    ):
        assert isinstance(funcao(*args), str)

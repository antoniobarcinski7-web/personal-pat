"""Como um numero vira texto para um humano ler.

Camada de APRESENTACAO, e so isso. Nada aqui volta para o motor, nada aqui e
insumo de calculo, e o valor exato nunca e descartado - `format_money` devolve
o texto e quem chama continua com o `Decimal`.

Por que existe
--------------
`45183036000.0000000000 USD` esta correto e nao ajuda ninguem. Dez casas
decimais num numero de 45 bilhoes nao sao precisao: sao ruido que esconde a
ordem de grandeza, que e justamente a primeira coisa que alguem quer ler. A
disciplina do PAT e sobre o numero ARMAZENADO ser exato; imprimir todos os
digitos nao acrescenta garantia nenhuma e custa a leitura.

A regra que nao se negocia
--------------------------
Arredondar para exibir e permitido. Arredondar e devolver ao sistema nao e.
Por isso este modulo devolve `str` e nunca `Decimal`: um valor formatado que
voltasse a ser numero seria a porta por onde uma aproximacao entra num
calculo, e ninguem notaria - o mesmo modo de falha da lavagem de numero do
emissor, com outro disfarce.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

__all__ = ["format_money", "format_number", "format_ratio", "format_percent"]

_SIMBOLOS = {
    "BRL": "R$",
    "USD": "US$",
    "EUR": "€",
    "GBP": "£",
}
"""Moedas com simbolo conhecido. Qualquer outra sai com o codigo ISO na
frente - "CAD 1,2 bi" -, que e feio e certo. Inventar simbolo por semelhanca
seria a versao monetaria de casar conta por rotulo."""

_ESCALAS = (
    (Decimal("1e12"), "tri"),
    (Decimal("1e9"), "bi"),
    (Decimal("1e6"), "mi"),
    (Decimal("1e3"), "mil"),
)


def _br(texto: str) -> str:
    """Ponto de milhar e virgula decimal, sem depender de `locale`.

    `locale` e estado global do processo: uma biblioteca que o altera muda o
    comportamento de todo o resto do programa, inclusive de codigo que nunca
    pediu nada. Trocar dois caracteres e mais feio e nao tem efeito colateral.
    """
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_number(valor: Decimal, *, casas: int = 2) -> str:
    """Numero com separador brasileiro, sem escala e sem moeda."""
    quantizado = valor.quantize(Decimal(10) ** -casas, rounding=ROUND_HALF_UP)
    return _br(f"{quantizado:,.{casas}f}")


def format_money(valor: Decimal, currency: str, *, casas: int = 2) -> str:
    """`Decimal` + moeda -> "US$ 45,18 bi".

    A escala e escolhida pela GRANDEZA do valor, e nao fixada por metrica: a
    mesma metrica vale bilhoes numa companhia e milhoes noutra, e uma escala
    fixa imprimiria "0,04 bi" para a segunda.

    Valores pequenos saem inteiros - "US$ 812,00" -, sem sufixo. Abaixo de mil
    nao ha ordem de grandeza que precise ser resumida.
    """
    simbolo = _SIMBOLOS.get(currency.upper(), currency.upper())
    sinal = "-" if valor < 0 else ""
    absoluto = abs(valor)

    for limite, sufixo in _ESCALAS:
        if absoluto >= limite:
            reduzido = absoluto / limite
            return f"{sinal}{simbolo} {format_number(reduzido, casas=casas)} {sufixo}"

    return f"{sinal}{simbolo} {format_number(absoluto, casas=casas)}"


def format_ratio(valor: Decimal, *, casas: int = 2) -> str:
    """Multiplo: "12,40x". Para P/L, EV/EBITDA e afins."""
    return f"{format_number(valor, casas=casas)}x"


def format_percent(valor: Decimal, *, casas: int = 1) -> str:
    """Fracao -> percentual. `0.2341` vira "23,4%".

    Recebe a FRACAO, e nao o percentual ja multiplicado: o motor guarda razao,
    e multiplicar por 100 aqui uma vez e melhor do que cada chamador lembrar de
    fazer - ou esquecer, e publicar "0,2%" onde eram 23%.
    """
    return f"{format_number(valor * 100, casas=casas)}%"

"""Caso golden do GPA: linhas reais da DFP, transcritas a mao.

De onde vem cada numero
-----------------------
CIA BRASILEIRA DE DISTRIBUICAO (cod_cvm 14826), escopo consolidado, conferido
contra os arquivos que ja estao no bronze:

    DFP 2023, recebida pela CVM em 2024-02-21  -> FY2023 como ORDEM=ULTIMO
    DFP 2024, recebida pela CVM em 2025-02-18  -> FY2023 como ORDEM=PENULTIMO
                                                  (reapresentado) e FY2024

Valores em milhares, como a CVM publica; o gold multiplica por 1000.

Por que transcrever em vez de baixar: o golden test tem que rodar offline, em
milissegundos e sempre igual. A conferencia contra a fonte viva existe, e vive
em `tests/network/test_semantics_live.py` - se a CVM renumerar uma conta, e la
que aparece.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.conftest import DocumentSpec, LineSpec, ZipSpec, build_dfp_zip

CNPJ = "47.508.411/0001-56"
CNPJ_DIGITS = "47508411000156"
ENTITY_ID = f"br:cnpj:{CNPJ_DIGITS}"
COD_CVM = "014826"
DENOM = "CIA BRASILEIRA DE DISTRIBUICAO"

FY2023 = date(2023, 12, 31)
FY2024 = date(2024, 12, 31)

KD_2024 = date(2024, 2, 21)
KD_2025 = date(2025, 2, 18)

MIL = Decimal(1000)


def _line(cd_conta: str, ds_conta: str, valor: str, dt_fim: str, dt_ini: str, ordem: str):
    return LineSpec(
        cd_conta=cd_conta,
        ds_conta=ds_conta,
        valor=valor,
        dt_fim=dt_fim,
        dt_ini=dt_ini,
        ordem=ordem,
        cnpj=CNPJ,
        cod_cvm=COD_CVM,
        denom=DENOM,
    )


# --- DFP 2023: FY2023 como publicado originalmente --------------------------

_DRE_2023 = [
    _line("3.01", "Receita de Venda de Bens e/ou Serviços", "19250000", "2023-12-31", "2023-01-01", "ÚLTIMO"),
    _line("3.02", "Custo dos Bens e/ou Serviços Vendidos", "-14433000", "2023-12-31", "2023-01-01", "ÚLTIMO"),
    _line("3.03", "Resultado Bruto", "4817000", "2023-12-31", "2023-01-01", "ÚLTIMO"),
    _line("3.04", "Despesas/Receitas Operacionais", "-4140000", "2023-12-31", "2023-01-01", "ÚLTIMO"),
    _line("3.04.06", "Resultado de Equivalência Patrimonial", "768000", "2023-12-31", "2023-01-01", "ÚLTIMO"),
    _line("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", "677000", "2023-12-31", "2023-01-01", "ÚLTIMO"),
]
_DVA_2023 = [
    _line("7.04.01", "Depreciação, Amortização e Exaustão", "-1133000", "2023-12-31", "2023-01-01", "ÚLTIMO"),
]
_DFC_2023 = [
    _line("6.01.01.04", "Depreciação / amortização", "1136000", "2023-12-31", "2023-01-01", "ÚLTIMO"),
]

# --- DFP 2024: FY2023 reapresentado, e FY2024 -------------------------------

_DRE_2024 = [
    _line("3.01", "Receita de Venda de Bens e/ou Serviços", "18790000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.02", "Custo dos Bens e/ou Serviços Vendidos", "-13618000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.03", "Resultado Bruto", "5172000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.04", "Despesas/Receitas Operacionais", "-5608000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.04.06", "Resultado de Equivalência Patrimonial", "64000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", "-436000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    # Insumos da aliquota efetiva. O GPA teve PREJUIZO antes dos tributos em
    # FY2024, e por isso `aliquota_efetiva@v1` recusa nesse exercicio - o que e
    # o comportamento certo, e o que o teste confere.
    _line("3.07", "Resultado Antes dos Tributos sobre o Lucro", "-1677000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.08", "Imposto de Renda e Contribuição Social sobre o Lucro", "12000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    # FY2023 reapresentado:
    _line("3.01", "Receita de Venda de Bens e/ou Serviços", "17793000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.02", "Custo dos Bens e/ou Serviços Vendidos", "-13096000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.03", "Resultado Bruto", "4697000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.04", "Despesas/Receitas Operacionais", "-4037000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.04.06", "Resultado de Equivalência Patrimonial", "768000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", "660000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
]
_DVA_2024 = [
    _line("7.04.01", "Depreciação, Amortização e Exaustão", "-1161000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("7.04.01", "Depreciação, Amortização e Exaustão", "-1122000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
]
_DFC_2024 = [
    _line("6.01.01.04", "Depreciação / amortização", "1167000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("6.01.01.04", "Depreciação / amortização", "1136000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    # O total da secao operacional, insumo de `cash_flow_operating`.
    _line("6.01", "Caixa Líquido Atividades Operacionais", "1363000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
]

# --- Balanco, FY2024 --------------------------------------------------------
#
# Contas patrimoniais sao INSTANTANEAS. `_line` recebe `period_start` porque a
# assinatura e uma so, e o construtor do ZIP remove a coluna DT_INI_EXERC das
# demonstracoes de saldo - e essa ausencia que faz o parser produzir
# `PeriodType.INSTANT`, em vez de um campo dizendo "sou saldo".

_BPA_2024 = [
    _line("1.01.01", "Caixa e Equivalentes de Caixa", "2631000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("1.01.02", "Aplicações Financeiras", "15000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
]
_BPP_2024 = [
    _line("2.01.04", "Empréstimos e Financiamentos", "849000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("2.02.01", "Empréstimos e Financiamentos", "3196000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    # A CVM publica o patrimonio CONSOLIDADO e a participacao de nao
    # controladores em separado; o atribuivel a controladora e a diferenca, e o
    # binding a monta somando as duas linhas publicadas.
    _line("2.03", "Patrimônio Líquido Consolidado", "2935000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("2.03.09", "Participação dos Acionistas Não Controladores", "9000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
]


def zips() -> dict[int, bytes]:
    """Os dois anos de DFP do caso golden."""
    return {
        2023: build_dfp_zip(
            ZipSpec(
                year=2023,
                documents=[
                    DocumentSpec("2023-12-31", 1, "2024-02-21", "700001", cnpj=CNPJ,
                                 cod_cvm=COD_CVM, denom=DENOM)
                ],
                dre_con=_DRE_2023,
                flow_members={("DVA", "con"): _DVA_2023, ("DFC_MI", "con"): _DFC_2023},
            )
        ),
        2024: build_dfp_zip(
            ZipSpec(
                year=2024,
                documents=[
                    DocumentSpec("2024-12-31", 1, "2025-02-18", "700002", cnpj=CNPJ,
                                 cod_cvm=COD_CVM, denom=DENOM)
                ],
                dre_con=_DRE_2024,
                bpa_con=_BPA_2024,
                bpp_con=_BPP_2024,
                flow_members={("DVA", "con"): _DVA_2024, ("DFC_MI", "con"): _DFC_2024},
            )
        ),
    }


# --- valores esperados, conferidos a mao ------------------------------------
#
# Monetarios em BRL (ja com a escala MIL aplicada). Cada um sai de uma linha
# publicada ou de uma soma de duas linhas publicadas; nenhum foi produzido
# rodando o proprio codigo que os testes verificam.

EXPECTED = {
    # (metrica, period_end, as_of): valor
    ("receita_liquida@v1", FY2023, date(2024, 6, 30)): Decimal("19250000") * MIL,
    ("receita_liquida@v1", FY2023, date(2025, 6, 30)): Decimal("17793000") * MIL,
    ("receita_liquida@v1", FY2024, date(2025, 6, 30)): Decimal("18790000") * MIL,

    ("ebit@v1", FY2023, date(2024, 6, 30)): Decimal("677000") * MIL,
    ("ebit@v1", FY2023, date(2025, 6, 30)): Decimal("660000") * MIL,
    ("ebit@v1", FY2024, date(2025, 6, 30)): Decimal("-436000") * MIL,

    ("d_and_a@v1", FY2023, date(2024, 6, 30)): Decimal("1136000") * MIL,
    ("d_and_a@v1", FY2023, date(2025, 6, 30)): Decimal("1136000") * MIL,
    ("d_and_a@v1", FY2024, date(2025, 6, 30)): Decimal("1167000") * MIL,

    # EBIT + D&A, somado a mao:
    ("ebitda@v1", FY2023, date(2024, 6, 30)): Decimal("1813000") * MIL,   # 677 + 1.136
    ("ebitda@v1", FY2023, date(2025, 6, 30)): Decimal("1796000") * MIL,   # 660 + 1.136
    ("ebitda@v1", FY2024, date(2025, 6, 30)): Decimal("731000") * MIL,    # -436 + 1.167
}

# Margens conferidas a mao, arredondadas a 6 casas na comparacao.
EXPECTED_MARGEM = {
    (FY2023, date(2024, 6, 30)): Decimal("0.094182"),   # 1.813 / 19.250
    (FY2023, date(2025, 6, 30)): Decimal("0.100939"),   # 1.796 / 17.793
    (FY2024, date(2025, 6, 30)): Decimal("0.038904"),   # 731 / 18.790
}

KNOWLEDGE_DATES = {
    (FY2023, date(2024, 6, 30)): KD_2024,
    (FY2023, date(2025, 6, 30)): KD_2025,
    (FY2024, date(2025, 6, 30)): KD_2025,
}

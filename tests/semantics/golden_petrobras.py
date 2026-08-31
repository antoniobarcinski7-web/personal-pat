"""Caso golden da Petrobras: linhas reais da DFP, transcritas a mao.

De onde vem cada numero
-----------------------
PETROLEO BRASILEIRO S.A. PETROBRAS (cod_cvm 9512), escopo consolidado,
conferido contra os arquivos que ja estao no bronze:

    DFP 2023, doc 135086 v2, recebida em 2024-03-25 -> FY2023 como ULTIMO
                                                       FY2022 como PENULTIMO
    DFP 2024, doc 145077 v1, recebida em 2025-02-26 -> FY2024 como ULTIMO
                                                       FY2023 como PENULTIMO

Por que este caso existe, ao lado do GPA
----------------------------------------
O GPA prova que o mapeamento da empresa pode ser mais *preciso* que a familia.
A Petrobras prova algo mais forte: que ele pode estar *certo antes*.

Na DFP 2023 como publicada originalmente, as duas demonstracoes da companhia
discordavam entre si sobre a mesma grandeza:

    DFC 6.01.01.04 "Depreciação, depleção e amortização"   66.204.000 mil
    DVA 7.04.01    "Depreciação, Amortização e Exaustão"   76.020.000 mil

12,9% de diferenca dentro do mesmo documento. A DFP 2024 reapresentou a DVA
para 66.204.000 mil, alinhando-a ao DFC - ou seja, a linha do DFC era a certa
desde o inicio. Quem lesse FY2023 em junho/2024 pela familia default (que
aproxima d_and_a_pnl pela DVA) teria carregado 9,8 bilhoes a mais de EBITDA.

O mapeamento proprio da Petrobras aponta para o DFC, e por isso:

- da o numero que a companhia veio a confirmar um ano depois;
- e o check `proxima_da_retida` FALHA em 2024-06-30, expondo a divergencia em
  vez de escondê-la, e volta a passar depois da reapresentacao.

Um sistema que casasse por rotulo ou que tratasse DVA e DFC como sinonimos
nao teria como produzir nenhuma das duas coisas.

Por que transcrever em vez de baixar: o golden test tem que rodar offline, em
milissegundos e sempre igual. A conferencia contra a fonte viva vive em
`tests/network/test_semantics_live.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.conftest import DocumentSpec, LineSpec, ZipSpec, build_dfp_zip

CNPJ = "33.000.167/0001-01"
CNPJ_DIGITS = "33000167000101"
ENTITY_ID = f"br:cnpj:{CNPJ_DIGITS}"
COD_CVM = "009512"
DENOM = "PETROLEO BRASILEIRO S.A. PETROBRAS"

FY2022 = date(2022, 12, 31)
FY2023 = date(2023, 12, 31)
FY2024 = date(2024, 12, 31)

KD_2024 = date(2024, 3, 25)
KD_2025 = date(2025, 2, 26)

MIL = Decimal(1000)

DFC_DA = "6.01.01.04"
"""A linha que o mapeamento da Petrobras liga a d_and_a_pnl."""


def _line(cd_conta: str, ds_conta: str, valor: str, dt_fim: str, dt_ini: str, ordem: str,
          versao: int = 1):
    return LineSpec(
        cd_conta=cd_conta,
        ds_conta=ds_conta,
        valor=valor,
        dt_fim=dt_fim,
        dt_ini=dt_ini,
        ordem=ordem,
        versao=versao,
        cnpj=CNPJ,
        cod_cvm=COD_CVM,
        denom=DENOM,
    )


# --- DFP 2023: FY2023 como publicado originalmente, FY2022 como comparativo --
#
# As linhas de detalhe carregam VERSAO=2: a Petrobras protocolou dois
# documentos para o exercicio de 2023 (v1 em 2024-03-08, v2 em 2024-03-25), e o
# arquivo de detalhe da DFP 2023 e o da v2.

_DRE_2023 = [
    _line("3.01", "Receita de Venda de Bens e/ou Serviços", "511994000", "2023-12-31", "2023-01-01", "ÚLTIMO", 2),
    _line("3.02", "Custo dos Bens e/ou Serviços Vendidos", "-242061000", "2023-12-31", "2023-01-01", "ÚLTIMO", 2),
    _line("3.03", "Resultado Bruto", "269933000", "2023-12-31", "2023-01-01", "ÚLTIMO", 2),
    _line("3.04", "Despesas/Receitas Operacionais", "-80591000", "2023-12-31", "2023-01-01", "ÚLTIMO", 2),
    _line("3.04.06", "Resultado de Equivalência Patrimonial", "-1480000", "2023-12-31", "2023-01-01", "ÚLTIMO", 2),
    _line("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", "189342000", "2023-12-31", "2023-01-01", "ÚLTIMO", 2),
    _line("3.01", "Receita de Venda de Bens e/ou Serviços", "641256000", "2022-12-31", "2022-01-01", "PENÚLTIMO", 2),
    _line("3.02", "Custo dos Bens e/ou Serviços Vendidos", "-307156000", "2022-12-31", "2022-01-01", "PENÚLTIMO", 2),
    _line("3.03", "Resultado Bruto", "334100000", "2022-12-31", "2022-01-01", "PENÚLTIMO", 2),
    _line("3.04", "Despesas/Receitas Operacionais", "-39845000", "2022-12-31", "2022-01-01", "PENÚLTIMO", 2),
    _line("3.04.06", "Resultado de Equivalência Patrimonial", "1291000", "2022-12-31", "2022-01-01", "PENÚLTIMO", 2),
    _line("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", "294255000", "2022-12-31", "2022-01-01", "PENÚLTIMO", 2),
]
_DVA_2023 = [
    _line("7.04.01", "Depreciação, Amortização e Exaustão", "-76020000", "2023-12-31", "2023-01-01", "ÚLTIMO", 2),
    _line("7.04.01", "Depreciação, Amortização e Exaustão", "-75121000", "2022-12-31", "2022-01-01", "PENÚLTIMO", 2),
]
_DFC_2023 = [
    _line(DFC_DA, "Depreciação, depleção e amortização", "66204000", "2023-12-31", "2023-01-01", "ÚLTIMO", 2),
    _line(DFC_DA, "Depreciação, depleção e amortização", "68202000", "2022-12-31", "2022-01-01", "PENÚLTIMO", 2),
]

# --- DFP 2024: FY2024, e FY2023 com a DVA reapresentada ---------------------

_DRE_2024 = [
    _line("3.01", "Receita de Venda de Bens e/ou Serviços", "490829000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.02", "Custo dos Bens e/ou Serviços Vendidos", "-244367000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.03", "Resultado Bruto", "246462000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.04", "Despesas/Receitas Operacionais", "-109261000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.04.06", "Resultado de Equivalência Patrimonial", "-3467000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    _line("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", "137201000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    # FY2023 como comparativo: a DRE nao mudou.
    _line("3.01", "Receita de Venda de Bens e/ou Serviços", "511994000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.02", "Custo dos Bens e/ou Serviços Vendidos", "-242061000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.03", "Resultado Bruto", "269933000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.04", "Despesas/Receitas Operacionais", "-80591000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.04.06", "Resultado de Equivalência Patrimonial", "-1480000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
    _line("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", "189342000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
]
_DVA_2024 = [
    _line("7.04.01", "Depreciação, Amortização e Exaustão", "-67033000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    # A reapresentacao: 76.020.000 -> 66.204.000, alinhando a DVA ao DFC.
    _line("7.04.01", "Depreciação, Amortização e Exaustão", "-66204000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
]
_DFC_2024 = [
    _line(DFC_DA, "Depreciação, depleção e amortização", "67033000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
    # O DFC nao mudou: ja era 66.204.000 na publicacao original.
    _line(DFC_DA, "Depreciação, depleção e amortização", "66204000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
]


def zips() -> dict[int, bytes]:
    """Os dois anos de DFP do caso golden."""
    return {
        2023: build_dfp_zip(
            ZipSpec(
                year=2023,
                documents=[
                    DocumentSpec("2023-12-31", 1, "2024-03-08", "134555", cnpj=CNPJ,
                                 cod_cvm=COD_CVM, denom=DENOM),
                    DocumentSpec("2023-12-31", 2, "2024-03-25", "135086", cnpj=CNPJ,
                                 cod_cvm=COD_CVM, denom=DENOM),
                ],
                dre_con=_DRE_2023,
                flow_members={("DVA", "con"): _DVA_2023, ("DFC_MI", "con"): _DFC_2023},
            )
        ),
        2024: build_dfp_zip(
            ZipSpec(
                year=2024,
                documents=[
                    DocumentSpec("2024-12-31", 1, "2025-02-26", "145077", cnpj=CNPJ,
                                 cod_cvm=COD_CVM, denom=DENOM)
                ],
                dre_con=_DRE_2024,
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
    ("receita_liquida@v1", FY2022, date(2024, 6, 30)): Decimal("641256000") * MIL,
    ("receita_liquida@v1", FY2023, date(2024, 6, 30)): Decimal("511994000") * MIL,
    ("receita_liquida@v1", FY2023, date(2025, 6, 30)): Decimal("511994000") * MIL,
    ("receita_liquida@v1", FY2024, date(2025, 6, 30)): Decimal("490829000") * MIL,

    ("ebit@v1", FY2022, date(2024, 6, 30)): Decimal("294255000") * MIL,
    ("ebit@v1", FY2023, date(2024, 6, 30)): Decimal("189342000") * MIL,
    ("ebit@v1", FY2023, date(2025, 6, 30)): Decimal("189342000") * MIL,
    ("ebit@v1", FY2024, date(2025, 6, 30)): Decimal("137201000") * MIL,

    # Sempre a linha do DFC, nunca a da DVA - inclusive em 2024-06-30, quando a
    # DVA ainda dizia 76.020.000.
    ("d_and_a@v1", FY2022, date(2024, 6, 30)): Decimal("68202000") * MIL,
    ("d_and_a@v1", FY2023, date(2024, 6, 30)): Decimal("66204000") * MIL,
    ("d_and_a@v1", FY2023, date(2025, 6, 30)): Decimal("66204000") * MIL,
    ("d_and_a@v1", FY2024, date(2025, 6, 30)): Decimal("67033000") * MIL,

    # EBIT + D&A, somado a mao:
    ("ebitda@v1", FY2022, date(2024, 6, 30)): Decimal("362457000") * MIL,  # 294.255 + 68.202
    ("ebitda@v1", FY2023, date(2024, 6, 30)): Decimal("255546000") * MIL,  # 189.342 + 66.204
    ("ebitda@v1", FY2023, date(2025, 6, 30)): Decimal("255546000") * MIL,  # idem
    ("ebitda@v1", FY2024, date(2025, 6, 30)): Decimal("204234000") * MIL,  # 137.201 + 67.033
}

# Margens conferidas a mao, arredondadas a 6 casas na comparacao.
EXPECTED_MARGEM = {
    (FY2023, date(2024, 6, 30)): Decimal("0.499119"),   # 255.546 / 511.994
    (FY2023, date(2025, 6, 30)): Decimal("0.499119"),
    (FY2024, date(2025, 6, 30)): Decimal("0.416100"),   # 204.234 / 490.829
}

KNOWLEDGE_DATES = {
    (FY2022, date(2024, 6, 30)): KD_2024,
    (FY2023, date(2024, 6, 30)): KD_2024,
    (FY2023, date(2025, 6, 30)): KD_2025,
    (FY2024, date(2025, 6, 30)): KD_2025,
}

# A D&A retida do valor adicionado, para o teste que compara as duas grandezas.
DVA_RETIDA = {
    (FY2023, date(2024, 6, 30)): Decimal("76020000") * MIL,
    (FY2023, date(2025, 6, 30)): Decimal("66204000") * MIL,
}

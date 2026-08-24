"""Warehouse sintetico com Petrobras, Vale e WEG, para o E2E da conversa.

ATENCAO: OS VALORES AQUI SAO INVENTADOS.

Isto nao e um golden test, e a diferenca importa. `tests/semantics/golden_gpa.py`
e `golden_petrobras.py` transcrevem linhas REAIS da DFP a mao e afirmam numeros
reais - eles existem para provar que o calculo esta certo. Este modulo existe
para provar que o caminho CONVERSACIONAL esta certo: quatro turnos, contexto
entre eles, recusa no ultimo, e nenhum numero atravessando de um turno para o
seguinte.

Por isso `test_conversation_e2e.py` afirma comportamento e nunca um valor - a
mesma disciplina que `tests/research/conftest.py:make_metric_result` registra no
proprio docstring ("Nunca usado para afirmar valor: so para afirmar
comportamento"). Os numeros sao redondos e obviamente ficticios de proposito:
ninguem deve conseguir confundi-los com dado da CVM.

Por que as tres empresas, e nao o GPA que ja tinha fixture
----------------------------------------------------------
O caso de uso que motivou a M4.1 e "Compare o EBITDA de Petrobras, Vale e WEG",
e ele e exatamente o caso em que a M4.1 quebrava antes: tres empresas, mesma
metrica, mesmo periodo, tres descricoes identicas para o escritor. Um E2E com
uma companhia so nao exercitaria isso.

O que cada mapeamento exige
---------------------------
`ebitda@v1` = `ebit@v1` + `d_and_a@v1`, e `margem_ebitda@v1` = `ebitda@v1` sobre
`receita_liquida@v1`. As linhas necessarias:

    revenue_net       DRE     3.01
    ebit_reported     DRE     3.05
    d_and_a_retained  DVA     7.04.01
    d_and_a_pnl       DFC_MI  6.01.01.04 (Petrobras)
                              6.01.01.06 (Vale)
                              6.01.01.02 (WEG)

Os tres codigos de DFC sao especificos de cada companhia - e a razao de cada uma
ter mapeamento proprio, e por isso o fixture nao pode usar um codigo so para as
tres.

DFC e DVA saem com o mesmo modulo em todos os exercicios. Nao e descuido: o
binding de `d_and_a_pnl` carrega o check `proxima_da_retida`, que compara as
duas, e valores distantes fariam todo turno sair com `CHECK_FAILED` - um aviso
correto, mas que aqui so poluiria a leitura de um teste que e sobre outra coisa.
A divergencia real entre DFC e DVA tem teste proprio, no golden da Petrobras.
"""

from __future__ import annotations

from datetime import date

from tests.conftest import DocumentSpec, LineSpec, ZipSpec, build_dfp_zip

FY2023 = date(2023, 12, 31)
FY2024 = date(2024, 12, 31)

KD_2024 = date(2024, 2, 21)
"""Conhecimento das linhas do ZIP de 2023."""
KD_2025 = date(2025, 2, 26)
"""Conhecimento das linhas do ZIP de 2024."""

AS_OF = date(2026, 8, 20)
"""Depois das duas datas de recebimento: no E2E, tudo ja e conhecido."""


class Empresa:
    """Uma companhia do fixture, com o codigo de DFC que o mapeamento dela pede."""

    def __init__(
        self,
        *,
        cnpj: str,
        cod_cvm: str,
        denom: str,
        conta_dfc: str,
        rotulo_dfc: str,
        doc_base: int,
    ) -> None:
        self.cnpj_formatado = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
        self.entity_id = f"br:cnpj:{cnpj}"
        self.cod_cvm = cod_cvm
        self.denom = denom
        self.conta_dfc = conta_dfc
        self.rotulo_dfc = rotulo_dfc
        self.doc_base = doc_base


PETROBRAS = Empresa(
    cnpj="33000167000101",
    cod_cvm="009512",
    denom="PETROLEO BRASILEIRO S.A. PETROBRAS",
    conta_dfc="6.01.01.04",
    rotulo_dfc="Depreciação, depleção e amortização",
    doc_base=800001,
)
VALE = Empresa(
    cnpj="33592510000154",
    cod_cvm="004170",
    denom="VALE S.A.",
    conta_dfc="6.01.01.06",
    rotulo_dfc="Depreciação, amortização e exaustão",
    doc_base=800002,
)
WEG = Empresa(
    cnpj="84429695000111",
    cod_cvm="005410",
    denom="WEG S.A.",
    conta_dfc="6.01.01.02",
    rotulo_dfc="Depreciação, Amortização e Exaustão",
    doc_base=800003,
)

EMPRESAS = (PETROBRAS, VALE, WEG)

# Valores em MIL, como a CVM publica. Inventados, redondos, e escolhidos para
# que a Vale CAIA de FY2023 para FY2024 enquanto as outras duas sobem - o turno
# 4 da conversa pergunta justamente quem caiu mais, e um cenario em que ninguem
# cai tornaria a recusa indistinguivel de "nao havia o que responder".
#
#                        receita (3.01)   ebit (3.05)    D&A
_NUMEROS = {
    (PETROBRAS.entity_id, FY2023): ("500000000", "200000000", "60000000"),
    (PETROBRAS.entity_id, FY2024): ("520000000", "210000000", "62000000"),
    (VALE.entity_id, FY2023): ("200000000", "70000000", "20000000"),
    (VALE.entity_id, FY2024): ("190000000", "60000000", "21000000"),
    (WEG.entity_id, FY2023): ("30000000", "5000000", "600000"),
    (WEG.entity_id, FY2024): ("36000000", "6000000", "700000"),
}


def _line(empresa: Empresa, cd_conta: str, ds_conta: str, valor: str, fim: date, ordem: str):
    return LineSpec(
        cd_conta=cd_conta,
        ds_conta=ds_conta,
        valor=valor,
        dt_fim=fim.isoformat(),
        dt_ini=fim.replace(month=1, day=1).isoformat(),
        ordem=ordem,
        cnpj=empresa.cnpj_formatado,
        cod_cvm=empresa.cod_cvm,
        denom=empresa.denom,
    )


def _dre(empresa: Empresa, fim: date, ordem: str) -> list[LineSpec]:
    receita, ebit, _ = _NUMEROS[(empresa.entity_id, fim)]
    return [
        _line(empresa, "3.01", "Receita de Venda de Bens e/ou Serviços", receita, fim, ordem),
        _line(
            empresa,
            "3.05",
            "Resultado Antes do Resultado Financeiro e dos Tributos",
            ebit,
            fim,
            ordem,
        ),
    ]


def _dva(empresa: Empresa, fim: date, ordem: str) -> list[LineSpec]:
    _, _, da = _NUMEROS[(empresa.entity_id, fim)]
    # Negativa na DVA, positiva no DFC: e a convencao de sinal da propria CVM, e
    # os bindings ja a tratam. Inverter aqui esconderia o tratamento de sinal.
    return [
        _line(empresa, "7.04.01", "Depreciação, Amortização e Exaustão", f"-{da}", fim, ordem)
    ]


def _dfc(empresa: Empresa, fim: date, ordem: str) -> list[LineSpec]:
    _, _, da = _NUMEROS[(empresa.entity_id, fim)]
    return [_line(empresa, empresa.conta_dfc, empresa.rotulo_dfc, da, fim, ordem)]


def _zip_do_ano(ano: int, dt_receb: str) -> bytes:
    """Um ZIP com as TRES companhias, como a CVM publica.

    `LineSpec` carrega `cnpj`/`cod_cvm`/`denom` por linha, entao o arquivo anual
    da CVM e de fato um arquivo por ano e nao por empresa - e o fixture reproduz
    isso em vez de inventar uma estrutura mais conveniente.
    """
    fim = FY2023 if ano == 2023 else FY2024
    anterior = FY2023

    documentos = [
        DocumentSpec(
            fim.isoformat(),
            1,
            dt_receb,
            str(empresa.doc_base + ano),
            cnpj=empresa.cnpj_formatado,
            cod_cvm=empresa.cod_cvm,
            denom=empresa.denom,
        )
        for empresa in EMPRESAS
    ]

    dre: list[LineSpec] = []
    dva: list[LineSpec] = []
    dfc: list[LineSpec] = []
    for empresa in EMPRESAS:
        dre += _dre(empresa, fim, "ÚLTIMO")
        dva += _dva(empresa, fim, "ÚLTIMO")
        dfc += _dfc(empresa, fim, "ÚLTIMO")
        if ano == 2024:
            # FY2023 volta como PENÚLTIMO, com os MESMOS valores do ZIP de 2023:
            # divergir aqui seria uma reapresentacao, que e um fenomeno de
            # verdade com teste proprio - e que neste arquivo so faria a
            # conversa sair com um aviso que o teste nao esta investigando.
            dre += _dre(empresa, anterior, "PENÚLTIMO")
            dva += _dva(empresa, anterior, "PENÚLTIMO")
            dfc += _dfc(empresa, anterior, "PENÚLTIMO")

    return build_dfp_zip(
        ZipSpec(
            year=ano,
            documents=documentos,
            dre_con=dre,
            flow_members={("DVA", "con"): dva, ("DFC_MI", "con"): dfc},
        )
    )


def zips() -> dict[int, bytes]:
    return {2023: _zip_do_ano(2023, KD_2024.isoformat()), 2024: _zip_do_ano(2024, KD_2025.isoformat())}

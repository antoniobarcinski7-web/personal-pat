"""Utilitarios de teste: construcao de ZIPs de DFP sinteticos.

Permitem exercitar o parser e a logica bitemporal sem rede e em milissegundos,
reproduzindo a estrutura real da CVM - inclusive o eixo ULTIMO/PENULTIMO, que
e o mecanismo por onde uma reapresentacao aparece.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

ENCODING = "latin-1"

INDEX_COLUMNS = [
    "CNPJ_CIA", "DT_REFER", "VERSAO", "DENOM_CIA", "CD_CVM",
    "CATEG_DOC", "ID_DOC", "DT_RECEB", "LINK_DOC",
]
FLOW_COLUMNS = [
    "CNPJ_CIA", "DT_REFER", "VERSAO", "DENOM_CIA", "CD_CVM", "GRUPO_DFP",
    "MOEDA", "ESCALA_MOEDA", "ORDEM_EXERC", "DT_INI_EXERC", "DT_FIM_EXERC",
    "CD_CONTA", "DS_CONTA", "VL_CONTA", "ST_CONTA_FIXA",
]
# Contas patrimoniais nao tem DT_INI_EXERC: sao saldo pontual.
INSTANT_COLUMNS = [c for c in FLOW_COLUMNS if c != "DT_INI_EXERC"]
# DMPL tem a dimensao extra COLUNA_DF (componente do patrimonio liquido).
DMPL_COLUMNS = FLOW_COLUMNS[:11] + ["COLUNA_DF"] + FLOW_COLUMNS[11:]

CNPJ = "12.345.678/0001-99"
CNPJ_DIGITS = "12345678000199"
COD_CVM = "099999"
DENOM = "COMPANHIA DE TESTE S.A."


@dataclass
class DocumentSpec:
    """Um documento no indice: define a data de conhecimento das suas linhas."""

    dt_refer: str
    versao: int
    dt_receb: str
    doc_id: str
    cnpj: str = CNPJ
    cod_cvm: str = COD_CVM


@dataclass
class LineSpec:
    """Uma linha de conta em um arquivo de detalhe."""

    cd_conta: str
    valor: str
    dt_fim: str
    dt_ini: str | None = None
    ordem: str = "ÚLTIMO"
    ds_conta: str = "Receita de Venda de Bens e/ou Serviços"
    versao: int = 1
    dt_refer: str | None = None
    escala: str = "MIL"
    moeda: str = "REAL"
    coluna_df: str | None = None
    cnpj: str = CNPJ
    cod_cvm: str = COD_CVM


@dataclass
class ZipSpec:
    year: int
    documents: list[DocumentSpec] = field(default_factory=list)
    dre_con: list[LineSpec] = field(default_factory=list)
    bpa_con: list[LineSpec] = field(default_factory=list)
    dmpl_con: list[LineSpec] = field(default_factory=list)


def _csv(columns: list[str], rows: list[list[str]]) -> bytes:
    out = [";".join(columns)]
    out.extend(";".join(row) for row in rows)
    return ("\n".join(out) + "\n").encode(ENCODING)


def _flow_row(line: LineSpec, dt_refer: str) -> list[str]:
    return [
        line.cnpj, line.dt_refer or dt_refer, str(line.versao), DENOM, line.cod_cvm,
        "DF Consolidado - Demonstração do Resultado", line.moeda, line.escala,
        line.ordem, line.dt_ini or "", line.dt_fim,
        line.cd_conta, line.ds_conta, line.valor, "S",
    ]


def build_dfp_zip(spec: ZipSpec) -> bytes:
    dt_refer_default = f"{spec.year}-12-31"
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"dfp_cia_aberta_{spec.year}.csv",
            _csv(
                INDEX_COLUMNS,
                [
                    [
                        doc.cnpj, doc.dt_refer, str(doc.versao), DENOM, doc.cod_cvm,
                        "DFP", doc.doc_id, doc.dt_receb,
                        f"http://rad.cvm.gov.br/doc/{doc.doc_id}",
                    ]
                    for doc in spec.documents
                ],
            ),
        )

        if spec.dre_con:
            zf.writestr(
                f"dfp_cia_aberta_DRE_con_{spec.year}.csv",
                _csv(FLOW_COLUMNS, [_flow_row(l, dt_refer_default) for l in spec.dre_con]),
            )

        if spec.bpa_con:
            rows = []
            for line in spec.bpa_con:
                row = _flow_row(line, dt_refer_default)
                del row[9]  # remove DT_INI_EXERC
                rows.append(row)
            zf.writestr(f"dfp_cia_aberta_BPA_con_{spec.year}.csv", _csv(INSTANT_COLUMNS, rows))

        if spec.dmpl_con:
            rows = []
            for line in spec.dmpl_con:
                row = _flow_row(line, dt_refer_default)
                row.insert(11, line.coluna_df or "")
                rows.append(row)
            zf.writestr(f"dfp_cia_aberta_DMPL_con_{spec.year}.csv", _csv(DMPL_COLUMNS, rows))

    return buffer.getvalue()


def restatement_fixture() -> tuple[bytes, bytes]:
    """Dois anos de DFP reproduzindo uma reapresentacao real.

    FY2023 aparece duas vezes, como acontece na CVM:
      - DFP 2023, ORDEM=ULTIMO,    valor 1.000 mil, conhecido em 2024-02-20
      - DFP 2024, ORDEM=PENULTIMO, valor   900 mil, conhecido em 2025-02-20

    E a mesma forma do caso GPA usado no teste end-to-end contra a CVM real.
    """
    zip_2023 = build_dfp_zip(
        ZipSpec(
            year=2023,
            documents=[DocumentSpec("2023-12-31", 1, "2024-02-20", "111111")],
            dre_con=[
                LineSpec("3.01", "1000.0000000000", "2023-12-31", "2023-01-01", "ÚLTIMO"),
                LineSpec("3.01", "800.0000000000", "2022-12-31", "2022-01-01", "PENÚLTIMO"),
            ],
        )
    )
    zip_2024 = build_dfp_zip(
        ZipSpec(
            year=2024,
            documents=[DocumentSpec("2024-12-31", 1, "2025-02-20", "222222")],
            dre_con=[
                LineSpec("3.01", "2000.0000000000", "2024-12-31", "2024-01-01", "ÚLTIMO"),
                LineSpec("3.01", "900.0000000000", "2023-12-31", "2023-01-01", "PENÚLTIMO"),
            ],
        )
    )
    return zip_2023, zip_2024


def account_line(**overrides):
    """Uma linha silver valida, para exercitar gold sem passar pelo parser.

    O default e um exercicio anual em milhares, recebido depois do fim do
    periodo - ou seja, o caso comum que o contrato aceita. Cada teste altera
    so o campo que esta investigando.
    """
    from pat.contracts.silver import AccountLine

    defaults = dict(
        silver_id="0" * 32,
        content_sha256="a" * 64,
        retrieval_id="ret-1",
        source_member="dfp_cia_aberta_DRE_con_2023.csv",
        source_line_no=2,
        cnpj=CNPJ_DIGITS,
        cod_cvm=int(COD_CVM),
        denom_cia=DENOM,
        dt_refer=date(2023, 12, 31),
        versao=1,
        doc_id="111111",
        dt_receb=date(2024, 2, 20),
        link_doc=None,
        statement="DRE",
        consolidated=True,
        grupo_dfp=None,
        ordem_exerc="ULTIMO",
        dt_ini_exerc=date(2023, 1, 1),
        dt_fim_exerc=date(2023, 12, 31),
        coluna_df="",
        cd_conta="3.01",
        ds_conta="Receita de Venda de Bens e/ou Serviços",
        vl_conta=Decimal("1000"),
        st_conta_fixa=True,
        moeda="REAL",
        escala_moeda="MIL",
        extractor="cvm_dfp",
        extractor_version="1.1.0",
        extraction_run_id="run-1",
    )
    return AccountLine(**(defaults | overrides))


@pytest.fixture
def warehouse(tmp_path):
    """Banco vazio com o esquema aplicado."""
    from pat.store.db import connect, migrate

    conn = connect(tmp_path / "w.duckdb")
    migrate(conn)
    yield conn
    conn.close()

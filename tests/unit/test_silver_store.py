"""Persistencia da camada silver.

Silver e a copia tipada e fiel do CSV. Dois compromissos: nao interpretar
nada, e ser regravavel sem sujar o estado.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from conftest import DocumentSpec, LineSpec, ZipSpec, account_line, build_dfp_zip

from pat.parse.cvm_dfp import parse_dfp_zip
from pat.store.silver import count, write_lines


def test_gravar_o_mesmo_documento_duas_vezes_e_no_op(warehouse):
    """Ids deterministicos tornam o build re-executavel: rodar de novo nao
    duplica linha (I3)."""
    lines = [account_line()]

    assert write_lines(warehouse, lines) == 1
    assert write_lines(warehouse, lines) == 0
    assert count(warehouse) == 1


def test_escrita_vazia_e_no_op(warehouse):
    assert write_lines(warehouse, []) == 0
    assert count(warehouse) == 0


def test_a_linha_gravada_ainda_bate_com_o_csv(warehouse):
    """O valor persiste cru, na escala declarada. E o que permite conferir
    uma linha silver contra o arquivo original da CVM."""
    zip_bytes = build_dfp_zip(
        ZipSpec(
            year=2023,
            documents=[DocumentSpec("2023-12-31", 1, "2024-02-20", "111111")],
            dre_con=[LineSpec("3.01", "19250.5000000000", "2023-12-31", "2023-01-01")],
        )
    )
    lines, _ = parse_dfp_zip(
        zip_bytes,
        year=2023,
        content_sha256="a" * 64,
        retrieval_id="ret-1",
        extraction_run_id="run-1",
    )
    write_lines(warehouse, lines)

    row = warehouse.execute(
        "SELECT vl_conta, escala_moeda, moeda, dt_receb, ordem_exerc, source_line_no "
        "FROM silver_line"
    ).fetchone()
    assert row == (Decimal("19250.5000000000"), "MIL", "REAL", date(2024, 2, 20), "ULTIMO", 2)


def test_linhagem_sobrevive_a_persistencia(warehouse):
    write_lines(warehouse, [account_line()])

    row = warehouse.execute(
        "SELECT content_sha256, retrieval_id, source_member, extractor, extractor_version "
        "FROM silver_line"
    ).fetchone()
    assert row == ("a" * 64, "ret-1", "dfp_cia_aberta_DRE_con_2023.csv", "cvm_dfp", "1.1.0")

"""Persistencia da camada silver.

Escrita idempotente: os ids sao deterministicos, entao reprocessar o mesmo
documento e um no-op em vez de duplicar linhas. E o que permite rodar o build
quantas vezes for preciso sem sujar o estado (I3).
"""

from __future__ import annotations

from collections.abc import Sequence

import duckdb

from pat.contracts.silver import AccountLine

_COLUMNS = (
    "silver_id",
    "content_sha256",
    "retrieval_id",
    "source_member",
    "source_line_no",
    "cnpj",
    "cod_cvm",
    "denom_cia",
    "dt_refer",
    "versao",
    "doc_id",
    "dt_receb",
    "link_doc",
    "statement",
    "consolidated",
    "grupo_dfp",
    "ordem_exerc",
    "dt_ini_exerc",
    "dt_fim_exerc",
    "coluna_df",
    "cd_conta",
    "ds_conta",
    "vl_conta",
    "st_conta_fixa",
    "moeda",
    "escala_moeda",
    "extractor",
    "extractor_version",
    "extraction_run_id",
)

_INSERT = (
    f"INSERT INTO silver_line ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))}) "
    "ON CONFLICT (silver_id) DO NOTHING"
)


def write_lines(conn: duckdb.DuckDBPyConnection, lines: Sequence[AccountLine]) -> int:
    """Grava linhas silver. Retorna quantas de fato entraram."""
    if not lines:
        return 0

    before = conn.execute("SELECT COUNT(*) FROM silver_line").fetchone()[0]
    conn.executemany(
        _INSERT,
        [[getattr(line, column) for column in _COLUMNS] for line in lines],
    )
    after = conn.execute("SELECT COUNT(*) FROM silver_line").fetchone()[0]
    return after - before


def count(conn: duckdb.DuckDBPyConnection) -> int:
    return conn.execute("SELECT COUNT(*) FROM silver_line").fetchone()[0]


_XBRL_COLUMNS = (
    "silver_id",
    "content_sha256",
    "retrieval_id",
    "source_member",
    "source_line_no",
    "cik",
    "entity_name",
    "taxonomy",
    "element",
    "segments",
    "period_start",
    "period_end",
    "fiscal_year",
    "fiscal_period",
    "value",
    "unit",
    "accession",
    "form",
    "filed",
    "extractor",
    "extractor_version",
    "extraction_run_id",
)

_XBRL_INSERT = (
    f"INSERT INTO silver_xbrl_line ({', '.join(_XBRL_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_XBRL_COLUMNS))}) "
    "ON CONFLICT (silver_id) DO NOTHING"
)


def write_xbrl_lines(conn, lines) -> int:
    """Grava linhas XBRL. Mesma disciplina de `write_lines`: idempotente.

    Reprocessar o mesmo `companyfacts` e no-op, e nao duplicacao. E o que
    permite re-buscar a SEC quantas vezes for preciso - o que e necessario,
    porque um fato antigo ganha `filed` novo a cada 10-K que o repete como
    comparativo, e essa e a reapresentacao no sentido do I1.
    """
    if not lines:
        return 0
    before = conn.execute("SELECT COUNT(*) FROM silver_xbrl_line").fetchone()[0]
    conn.executemany(
        _XBRL_INSERT,
        [[getattr(line, coluna) for coluna in _XBRL_COLUMNS] for line in lines],
    )
    after = conn.execute("SELECT COUNT(*) FROM silver_xbrl_line").fetchone()[0]
    return int(after - before)

"""Catalogo DuckDB: conexao e esquema.

DuckDB embarcado em vez de um servidor: o banco inteiro e um arquivo, o que
torna snapshot, hash e versionamento triviais - requisito direto da
reprodutibilidade (I3).

O catalogo indexa e explica o bronze; ele nao o contem. Os bytes vivem em
disco enderecados por hash. Se o arquivo do banco for perdido, ele pode ser
reconstruido a partir dos sidecars do bronze; o inverso nao e verdade. Por
isso o bronze e a fonte de verdade e o catalogo e derivado.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingest_run (
    run_id          VARCHAR PRIMARY KEY,
    command         VARCHAR NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    status          VARCHAR NOT NULL,
    pat_version     VARCHAR NOT NULL,
    python_version  VARCHAR NOT NULL,
    git_sha         VARCHAR,
    notes           VARCHAR
);

-- Um conteudo unico. Imutavel. Identidade = hash dos bytes.
CREATE TABLE IF NOT EXISTS raw_document (
    content_sha256    VARCHAR PRIMARY KEY,
    size_bytes        BIGINT NOT NULL,
    media_type        VARCHAR,
    storage_path      VARCHAR NOT NULL,
    first_seen_at     TIMESTAMPTZ NOT NULL,
    first_seen_run_id VARCHAR NOT NULL
);

-- Uma observacao: em tal instante, tal URL devolveu tal conteudo.
-- Varios retrievals podem apontar para o mesmo raw_document (conteudo
-- inalterado); o mesmo (dataset_id, resource_key) apontando para hashes
-- diferentes ao longo do tempo e a evidencia de reapresentacao.
CREATE TABLE IF NOT EXISTS retrieval (
    retrieval_id     VARCHAR PRIMARY KEY,
    content_sha256   VARCHAR NOT NULL,
    provider_id      VARCHAR NOT NULL,
    provider_version VARCHAR NOT NULL,
    source_tier      VARCHAR NOT NULL,
    dataset_id       VARCHAR NOT NULL,
    resource_key     VARCHAR NOT NULL,
    url              VARCHAR NOT NULL,
    requested_at     TIMESTAMPTZ NOT NULL,
    retrieved_at     TIMESTAMPTZ NOT NULL,
    http_status      INTEGER,
    etag             VARCHAR,
    last_modified    VARCHAR,
    content_length   BIGINT,
    content_type     VARCHAR,
    final_url        VARCHAR,
    run_id           VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_resource
    ON retrieval (dataset_id, resource_key, retrieved_at);
CREATE INDEX IF NOT EXISTS idx_retrieval_content
    ON retrieval (content_sha256);
"""


def connect(path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def migrate(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(SCHEMA_SQL)

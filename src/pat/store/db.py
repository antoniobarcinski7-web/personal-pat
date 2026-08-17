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

-- ---------------------------------------------------------------------------
-- SILVER: uma linha por linha de CSV, tipada e fiel a fonte.
-- Escala monetaria preservada crua; aplica-la e interpretacao, e isso e gold.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_line (
    silver_id         VARCHAR PRIMARY KEY,
    content_sha256    VARCHAR NOT NULL,
    retrieval_id      VARCHAR NOT NULL,
    source_member     VARCHAR NOT NULL,
    source_line_no    INTEGER NOT NULL,

    cnpj              VARCHAR NOT NULL,
    cod_cvm           INTEGER NOT NULL,
    denom_cia         VARCHAR NOT NULL,
    dt_refer          DATE NOT NULL,
    versao            INTEGER NOT NULL,
    doc_id            VARCHAR NOT NULL,
    dt_receb          DATE NOT NULL,
    link_doc          VARCHAR,

    statement         VARCHAR NOT NULL,
    consolidated      BOOLEAN NOT NULL,
    grupo_dfp         VARCHAR,

    ordem_exerc       VARCHAR NOT NULL,
    dt_ini_exerc      DATE,
    dt_fim_exerc      DATE NOT NULL,

    coluna_df         VARCHAR NOT NULL DEFAULT '',
    cd_conta          VARCHAR NOT NULL,
    ds_conta          VARCHAR NOT NULL,
    vl_conta          DECIMAL(38,10) NOT NULL,
    st_conta_fixa     BOOLEAN,
    moeda             VARCHAR NOT NULL,
    escala_moeda      VARCHAR NOT NULL,

    extractor         VARCHAR NOT NULL,
    extractor_version VARCHAR NOT NULL,
    extraction_run_id VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_silver_company ON silver_line (cod_cvm, cd_conta, dt_fim_exerc);

-- ---------------------------------------------------------------------------
-- GOLD: fatos bitemporais. APPEND-ONLY - nunca ha UPDATE.
-- Reapresentacao nao sobrescreve nada: e uma linha nova com knowledge_date
-- posterior. A diferenca entre as duas e informacao, e permanece consultavel.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_fact (
    fact_id            VARCHAR PRIMARY KEY,
    entity_id          VARCHAR NOT NULL,
    cod_cvm            INTEGER NOT NULL,
    denom_cia          VARCHAR NOT NULL,

    statement          VARCHAR NOT NULL,
    consolidated       BOOLEAN NOT NULL,
    coluna_df          VARCHAR NOT NULL DEFAULT '',
    cd_conta           VARCHAR NOT NULL,
    ds_conta           VARCHAR NOT NULL,

    period_type        VARCHAR NOT NULL,
    period_start       DATE,
    period_end         DATE NOT NULL,
    knowledge_date     DATE NOT NULL,

    value              DECIMAL(38,10) NOT NULL,
    unit               VARCHAR NOT NULL,
    currency           VARCHAR,

    ordem_exerc        VARCHAR NOT NULL,
    source_doc_id      VARCHAR NOT NULL,
    source_doc_version INTEGER NOT NULL,

    silver_id          VARCHAR NOT NULL,
    content_sha256     VARCHAR NOT NULL,
    retrieval_id       VARCHAR NOT NULL,
    locator            VARCHAR NOT NULL,
    extractor          VARCHAR NOT NULL,
    extractor_version  VARCHAR NOT NULL,
    extraction_run_id  VARCHAR NOT NULL
);

-- Suporta o predicado central do AS OF: chave logica + corte por conhecimento.
CREATE INDEX IF NOT EXISTS idx_gold_asof
    ON gold_fact (cod_cvm, statement, consolidated, cd_conta, coluna_df, period_end, knowledge_date);

-- ---------------------------------------------------------------------------
-- RESEARCH_RUN: manifesto de uma execucao de pesquisa (Fase 3).
--
-- Tabela propria, e nao `ingest_run`: aquela e o manifesto de *ingestao*,
-- escrito pelo Catalog. Dobrar os dois significados num tipo so trocaria
-- clareza por economia de linha - a mesma razao pela qual o contrato
-- `ResearchRunManifest` nao herda de `Run`.
--
-- Os campos de lista guardam a tupla do manifesto na ordem em que ela existe
-- no contrato: `result_ids` segue a ordem dos passos, e ordem e significado.
-- Uma tabela de juncao normalizaria a forma e perderia isso.
--
-- Append-only, como gold_fact. Um `manifest_id` ja gravado nunca e atualizado:
-- reexecutar e uma corrida nova, e corrida antiga nao se reescreve.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research_run (
    manifest_id       VARCHAR PRIMARY KEY,
    question_id       VARCHAR NOT NULL,
    plan_id           VARCHAR NOT NULL,
    capability_sha256 VARCHAR NOT NULL,

    as_of             DATE NOT NULL,
    executed_at       TIMESTAMPTZ NOT NULL,
    outputs_available BOOLEAN NOT NULL,

    result_ids        VARCHAR[] NOT NULL,
    metric_versions   VARCHAR[] NOT NULL,
    mapping_sha256s   VARCHAR[] NOT NULL,
    fact_ids          VARCHAR[] NOT NULL,

    pat_version       VARCHAR NOT NULL,
    python_version    VARCHAR NOT NULL,
    git_sha           VARCHAR
);

-- Procedencia de modelo (planner/writer) nao tem coluna aqui: no caminho
-- deterministico ela e nula, e no Milestone 2 ela entra pela tabela `llm_call`,
-- que referencia `manifest_id`. Uma corrida sem LLM nao carrega colunas de LLM.

CREATE INDEX IF NOT EXISTS idx_research_run_plan
    ON research_run (plan_id, executed_at);
"""


def connect(path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def migrate(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(SCHEMA_SQL)

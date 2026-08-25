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
-- SILVER XBRL (Fase 5, M5.6): o analogo de `silver_line` no regime da SEC.
--
-- Tabela propria, e nao colunas novas em `silver_line`: aquela e uma linha de
-- CSV da CVM, com escala de moeda, ORDEM_EXERC e codigo de conta. Um fato XBRL
-- nao tem nenhuma das tres, e forcar os dois no mesmo formato deixaria metade
-- das colunas nulas em cada linha - a forma mentindo sobre o conteudo.
--
-- `filed` e o knowledge_date do lado americano; `segments` carrega a dimensao
-- e e o unico dado dimensional ESTRUTURADO do sistema.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_xbrl_line (
    silver_id         VARCHAR PRIMARY KEY,
    content_sha256    VARCHAR NOT NULL,
    retrieval_id      VARCHAR NOT NULL,
    source_member     VARCHAR NOT NULL,
    source_line_no    INTEGER NOT NULL,

    cik               VARCHAR NOT NULL,
    entity_name       VARCHAR NOT NULL,

    taxonomy          VARCHAR NOT NULL,
    element           VARCHAR NOT NULL,
    segments          VARCHAR NOT NULL DEFAULT '',

    period_start      DATE,
    period_end        DATE NOT NULL,
    fiscal_year       INTEGER,
    fiscal_period     VARCHAR,

    value             DECIMAL(38,10) NOT NULL,
    unit              VARCHAR NOT NULL,

    accession         VARCHAR NOT NULL,
    form              VARCHAR NOT NULL,
    filed             DATE NOT NULL,

    extractor         VARCHAR NOT NULL,
    extractor_version VARCHAR NOT NULL,
    extraction_run_id VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_silver_xbrl
    ON silver_xbrl_line (cik, element, period_end, filed);

-- ---------------------------------------------------------------------------
-- GOLD: fatos bitemporais. APPEND-ONLY - nunca ha UPDATE.
-- Reapresentacao nao sobrescreve nada: e uma linha nova com knowledge_date
-- posterior. A diferenca entre as duas e informacao, e permanece consultavel.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_fact (
    fact_id            VARCHAR PRIMARY KEY,
    -- `entity_id` e a UNICA referencia de entidade aqui, e e opaca. Os
    -- identificadores locais (cod_cvm, cnpj, cik) vivem em `entity`, um por
    -- jurisdicao. Ver a nota na definicao daquela tabela.
    entity_id          VARCHAR NOT NULL,

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
    ON gold_fact (entity_id, statement, consolidated, cd_conta, coluna_df, period_end, knowledge_date);

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
-- deterministico ela e nula, e ela entra pela tabela `llm_call` abaixo, que
-- referencia `manifest_id`. Uma corrida sem LLM nao carrega colunas de LLM.

CREATE INDEX IF NOT EXISTS idx_research_run_plan
    ON research_run (plan_id, executed_at);

-- ---------------------------------------------------------------------------
-- llm_call: o que o modelo respondeu, e sob que configuracao
-- ---------------------------------------------------------------------------
-- Indice, nao identidade. O cache em `data/llm/` e a fonte da verdade dos
-- bytes; esta tabela existe para responder perguntas de auditoria por consulta
-- em vez de varredura de diretorio.
--
-- `manifest_id` e NULAVEL de proposito (M-3). Um plano recusado pelo validador
-- produz uma chamada real - que custou dinheiro, gerou uma resposta e tem
-- rastro - e nenhum manifesto, porque nada executou. As alternativas seriam
-- inventar um manifest_id ou descartar o registro da chamada; a primeira
-- fabrica procedencia, a segunda perde o rastro de uma recusa, que e
-- justamente o que o D-11 manda guardar.
--
-- `call_sha256` e derivavel de `prompt_sha256` + `client_fingerprint`, e mesmo
-- assim tem coluna: aqui ele e indice, e "que entrada de cache serviu esta
-- corrida?" deve ser uma consulta e nao um recalculo. Em `PlanProvenance` ele
-- nao entra, porque la um valor derivavel seria campo sem significado proprio.
--
-- `temperature` e NULAVEL, e nulo e o caso normal: registra que nenhum override
-- de amostragem foi pedido, nao que ele foi zero.
--
-- Append-only, como todo o resto: uma chamada gravada nunca e reescrita.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_call (
    call_sha256          VARCHAR NOT NULL,
    kind                 VARCHAR NOT NULL,
    recorded_at          TIMESTAMPTZ NOT NULL,

    model_id             VARCHAR NOT NULL,
    client_fingerprint   VARCHAR NOT NULL,

    system_prompt_sha256 VARCHAR NOT NULL,
    prompt_sha256        VARCHAR NOT NULL,
    response_sha256      VARCHAR NOT NULL,
    capability_sha256    VARCHAR NOT NULL,

    temperature          DECIMAL(10, 6),
    max_tokens           INTEGER NOT NULL,

    called_at            TIMESTAMPTZ NOT NULL,
    cached               BOOLEAN NOT NULL,

    manifest_id          VARCHAR,

    -- Nao e `call_sha256` sozinho: a mesma chamada pode ser servida do cache em
    -- corridas diferentes, e cada uma dessas vezes e um fato distinto - com
    -- `called_at` identico e `recorded_at` proprio. O instante de gravacao e o
    -- que os separa, e por isso ele entra na chave.
    --
    -- `manifest_id` NAO participa da chave, de proposito: ele e nulo em toda
    -- chamada de plano recusado, e chave com componente nulo nao restringe
    -- nada. A unicidade que existe e a que da para garantir.
    PRIMARY KEY (call_sha256, recorded_at)
);

CREATE INDEX IF NOT EXISTS idx_llm_call_manifest
    ON llm_call (manifest_id);

CREATE INDEX IF NOT EXISTS idx_llm_call_prompt
    ON llm_call (prompt_sha256);

-- ---------------------------------------------------------------------------
-- CORPUS (Fase 5, M5.1): o lado qualitativo, com os mesmos dois eixos de tempo.
--
-- `source_document` e a interpretacao tipada de um blob que ja esta no bronze;
-- `document_id` E o sha256 do conteudo, entao uma reapresentacao de documento
-- e uma linha nova, nunca uma edicao. Append-only, como gold_fact.
--
-- `published_at` e o knowledge_date do texto: e o predicado que todo AS OF do
-- corpus usa, e por isso ele esta no indice.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_document (
    document_id          VARCHAR PRIMARY KEY,
    entity_id            VARCHAR NOT NULL,

    kind                 VARCHAR NOT NULL,
    title                VARCHAR NOT NULL,
    language             VARCHAR NOT NULL,

    source_tier          VARCHAR NOT NULL,
    published_at         DATE NOT NULL,
    published_at_basis   VARCHAR NOT NULL,
    reference_date       DATE,
    reference_date_basis VARCHAR,

    media_type           VARCHAR NOT NULL,
    byte_size            BIGINT NOT NULL,

    provider_id          VARCHAR NOT NULL,
    dataset_id           VARCHAR NOT NULL,
    resource_key         VARCHAR NOT NULL,
    source_url           VARCHAR NOT NULL,
    retrieval_id         VARCHAR NOT NULL,

    origin_category      VARCHAR,
    origin_version       VARCHAR,

    first_seen_at        TIMESTAMPTZ NOT NULL,
    first_seen_run_id    VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_document_asof
    ON source_document (entity_id, published_at, kind);

-- Unidade citavel. `extraction_version` entra em `unit_id`, entao reprocessar
-- com extrator novo INSERE ao lado em vez de sobrescrever - e a citacao antiga
-- continua resolvendo para o texto que ela queria dizer.
CREATE TABLE IF NOT EXISTS document_unit (
    unit_id            VARCHAR PRIMARY KEY,
    document_id        VARCHAR NOT NULL,
    ordinal            INTEGER NOT NULL,

    locator_scheme     VARCHAR NOT NULL,
    locator_page       INTEGER,
    locator_block      INTEGER,
    locator_node_path  VARCHAR,
    locator_turn       INTEGER,
    char_start         INTEGER NOT NULL,
    char_end           INTEGER NOT NULL,

    text               VARCHAR NOT NULL,
    char_count         INTEGER NOT NULL,
    section_path       VARCHAR[] NOT NULL DEFAULT [],

    speaker_name       VARCHAR,
    speaker_role       VARCHAR,
    speaker_is_qna     BOOLEAN,

    extraction_version VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_unit_doc
    ON document_unit (document_id, ordinal);

-- Falha de extracao e registro de primeira classe, e nao ausencia de linha:
-- documento sem unidade e sem falha e indistinguivel de documento que nunca
-- foi buscado, e a diferenca entre os dois muda o que um analista conclui.
CREATE TABLE IF NOT EXISTS extraction_failure (
    document_id        VARCHAR NOT NULL,
    extraction_version VARCHAR NOT NULL,
    reason             VARCHAR NOT NULL,
    message            VARCHAR NOT NULL,
    remedy             VARCHAR,
    failed_at          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (document_id, extraction_version)
);

-- Indice invertido. Derivado e descartavel: apagar e reconstruir a partir de
-- `document_unit` nao perde nada. `index_version` na chave permite duas
-- versoes coexistirem, para que um resultado antigo continue explicavel.
CREATE TABLE IF NOT EXISTS document_unit_token (
    unit_id       VARCHAR NOT NULL,
    index_version VARCHAR NOT NULL,
    token         VARCHAR NOT NULL,
    tf            INTEGER NOT NULL,
    unit_length   INTEGER NOT NULL,
    PRIMARY KEY (unit_id, index_version, token)
);

CREATE INDEX IF NOT EXISTS idx_unit_token_lookup
    ON document_unit_token (index_version, token);

-- ---------------------------------------------------------------------------
-- ENTITY (Fase 5, M5.6): a entidade universal, e seus nomes por regime.
--
-- Ate a M5.6 `gold_fact` carregava `cod_cvm` e `denom_cia` como colunas
-- obrigatorias - um endereco de REGIME dentro da tabela universal de fatos, o
-- mesmo erro de categoria que citar `cd_conta` em `concepts.py` seria.
-- Funcionava enquanto so existia o Brasil e quebrava na primeira companhia
-- americana, que nao tem `cod_cvm` nenhum.
--
-- Agora o fato guarda so `entity_id`. Uma jurisdicao nova nao pede coluna
-- nova aqui: pede uma LINHA. Era a terceira coluna que denunciaria o desenho
-- anterior, e ela nunca vai existir.
--
-- Uma entidade pode ter varias linhas: CNPJ e cod_cvm no Brasil, CIK e ticker
-- nos EUA. `is_primary` marca a canonica da jurisdicao; as outras sao apelidos
-- uteis para quem digita na linha de comando.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entity (
    entity_id     VARCHAR NOT NULL,
    jurisdiction  VARCHAR NOT NULL,
    scheme        VARCHAR NOT NULL,
    local_id      VARCHAR NOT NULL,
    display_name  VARCHAR NOT NULL,
    is_primary    BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (entity_id, scheme)
);

CREATE INDEX IF NOT EXISTS idx_entity_lookup
    ON entity (scheme, local_id);

-- Migracoes aplicadas. Explicita e versionada: uma migracao que nao deixa
-- rastro nao da para auditar depois, e a pergunta "este banco ja passou pela
-- M5.6?" precisa ter resposta sem inspecionar colunas.
CREATE TABLE IF NOT EXISTS schema_migration (
    migration_id VARCHAR PRIMARY KEY,
    applied_at   TIMESTAMPTZ NOT NULL,
    notes        VARCHAR
);
"""


def connect(path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


M56_ENTITY = "m5.6-entity-universal"


def migrate(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(SCHEMA_SQL)
    _migrate_entity_out_of_gold(conn)


def _migrate_entity_out_of_gold(conn: duckdb.DuckDBPyConnection) -> None:
    """Tira `cod_cvm` e `denom_cia` de `gold_fact` e os move para `entity`.

    Deterministica e idempotente: roda uma vez, deixa rastro em
    `schema_migration`, e nao faz nada nas execucoes seguintes.

    Preserva os fatos sem alteracao semantica. `fact_id`, `value`,
    `knowledge_date`, `period_end` e toda a linhagem ficam intactos - o que sai
    sao duas colunas que descreviam a ENTIDADE, e nao o fato. Um banco vazio
    (o caso de `pat init` e de todo teste) passa por aqui sem tocar em nada.

    O gold e derivado: se algo desse errado, `pat build` o reconstroi a partir
    do bronze, que e imutavel. Por isso a migracao pode ser direta em vez de
    copiar a tabela inteira para o lado.
    """
    ja_aplicada = conn.execute(
        "SELECT COUNT(*) FROM schema_migration WHERE migration_id = ?", [M56_ENTITY]
    ).fetchone()[0]
    if ja_aplicada:
        return

    # `PRAGMA table_info` devolve (cid, name, type, notnull, dflt, pk): o NOME
    # esta na posicao 1, e nao na 0. Ler a posicao errada faria a migracao
    # concluir que ja estava aplicada e nao fazer nada - falha silenciosa numa
    # migracao, que e o pior lugar possivel para uma.
    colunas = {
        linha[1]
        for linha in conn.execute("PRAGMA table_info('gold_fact')").fetchall()
    }
    if "cod_cvm" not in colunas:
        # Banco criado ja no esquema novo. Registra assim mesmo, para que a
        # pergunta "este banco passou pela M5.6?" tenha resposta uniforme.
        _record_migration(conn, "esquema ja nasceu sem cod_cvm em gold_fact")
        return

    # `max()` e determinista aqui porque todas as linhas de uma entidade
    # carregam o mesmo par - a CVM publica um cadastro por companhia. Se algum
    # dia divergir, a contagem abaixo acusa.
    divergentes = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT entity_id FROM gold_fact
            GROUP BY entity_id
            HAVING COUNT(DISTINCT cod_cvm) > 1 OR COUNT(DISTINCT denom_cia) > 1
        )
        """
    ).fetchone()[0]
    if divergentes:
        raise RuntimeError(
            f"{divergentes} entidade(s) com cod_cvm ou denom_cia divergente entre "
            "fatos. A migracao pararia com dois nomes para a mesma empresa, e "
            "escolher um em silencio seria inventar identidade."
        )

    conn.execute(
        """
        INSERT INTO entity (entity_id, jurisdiction, scheme, local_id, display_name, is_primary)
        SELECT entity_id, 'BR', 'cnpj',
               regexp_extract(entity_id, 'br:cnpj:(\\d+)', 1),
               MAX(denom_cia), TRUE
        FROM gold_fact
        WHERE entity_id LIKE 'br:cnpj:%'
        GROUP BY entity_id
        ON CONFLICT (entity_id, scheme) DO NOTHING
        """
    )
    conn.execute(
        """
        INSERT INTO entity (entity_id, jurisdiction, scheme, local_id, display_name, is_primary)
        SELECT entity_id, 'BR', 'cod_cvm', CAST(MAX(cod_cvm) AS VARCHAR),
               MAX(denom_cia), FALSE
        FROM gold_fact
        GROUP BY entity_id
        ON CONFLICT (entity_id, scheme) DO NOTHING
        """
    )

    # O indice antigo chaveava por `cod_cvm` e impede o DROP. Ele e derivado -
    # `SCHEMA_SQL` ja recriou o equivalente por `entity_id` - entao remove-lo
    # aqui nao perde nada.
    conn.execute("DROP INDEX IF EXISTS idx_gold_asof")
    conn.execute("ALTER TABLE gold_fact DROP COLUMN cod_cvm")
    conn.execute("ALTER TABLE gold_fact DROP COLUMN denom_cia")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gold_asof ON gold_fact "
        "(entity_id, statement, consolidated, cd_conta, coluna_df, period_end, knowledge_date)"
    )
    _record_migration(conn, "cod_cvm e denom_cia movidos de gold_fact para entity")


def _record_migration(conn: duckdb.DuckDBPyConnection, notes: str) -> None:
    from datetime import UTC, datetime

    conn.execute(
        "INSERT INTO schema_migration (migration_id, applied_at, notes) VALUES (?, ?, ?)",
        [M56_ENTITY, datetime.now(UTC), notes],
    )

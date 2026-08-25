"""A entidade universal: duas jurisdicoes, nenhuma dependendo da outra.

O teste que da nome ao arquivo e
`test_as_duas_jurisdicoes_nao_dependem_uma_da_outra`: Petrobras chega aos seus
fatos da CVM e Intel aos seus fatos da SEC, e nenhuma das duas precisa do
identificador da outra. Ate a M5.6 isso era impossivel - `gold_fact` exigia
`cod_cvm NOT NULL`, e uma companhia americana simplesmente nao cabia na tabela.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import duckdb
import pytest

from pat.contracts.entities import Company, EntityIdentity
from pat.query.asof import AsOf
from pat.store.db import M56_ENTITY, migrate
from pat.store.entity import resolve_entity, upsert_identities

PETROBRAS = "br:cnpj:33000167000101"
INTEL = "us:cik:0000050863"


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


def _fato(conn, fact_id: str, entity_id: str, valor: str, moeda: str) -> None:
    conn.execute(
        """
        INSERT INTO gold_fact (
            fact_id, entity_id, statement, consolidated, coluna_df, cd_conta,
            ds_conta, period_type, period_start, period_end, knowledge_date,
            value, unit, currency, ordem_exerc, source_doc_id, source_doc_version,
            silver_id, content_sha256, retrieval_id, locator, extractor,
            extractor_version, extraction_run_id
        ) VALUES (?, ?, 'DRE', TRUE, '', '3.01', 'Receita', 'year',
                  DATE '2023-01-01', DATE '2023-12-31', DATE '2024-03-01',
                  ?, ?, ?, 'ULTIMO', 'doc', 1, 'sv', 'sha', 'ret', 'loc',
                  'ext', '1', 'run')
        """,
        [fact_id, entity_id, Decimal(valor), moeda, moeda],
    )


@pytest.fixture
def duas_jurisdicoes(conn):
    upsert_identities(
        conn,
        [
            EntityIdentity(
                entity_id=PETROBRAS, jurisdiction="BR", scheme="cnpj",
                local_id="33000167000101",
                display_name="PETROLEO BRASILEIRO S.A. PETROBRAS", is_primary=True,
            ),
            EntityIdentity(
                entity_id=PETROBRAS, jurisdiction="BR", scheme="cod_cvm",
                local_id="9512", display_name="PETROLEO BRASILEIRO S.A. PETROBRAS",
            ),
            EntityIdentity(
                entity_id=INTEL, jurisdiction="US", scheme="cik",
                local_id="0000050863", display_name="INTEL CORP", is_primary=True,
            ),
            EntityIdentity(
                entity_id=INTEL, jurisdiction="US", scheme="ticker",
                local_id="INTC", display_name="INTEL CORP",
            ),
        ],
    )
    _fato(conn, "fato-petro", PETROBRAS, "511994000000", "BRL")
    _fato(conn, "fato-intel", INTEL, "54228000000", "USD")
    return conn


# -- O teste de regressao pedido ---------------------------------------------


def test_as_duas_jurisdicoes_nao_dependem_uma_da_outra(duas_jurisdicoes):
    """Petrobras -> entity_id -> fatos CVM, e Intel -> entity_id -> fatos SEC.

    Nenhuma das duas passa pelo identificador da outra. A Intel nao tem
    `cod_cvm` e nao precisa de um; a Petrobras nao tem CIK e nao precisa de um.
    """
    asof = AsOf(duas_jurisdicoes)

    # Cada uma entra pelo identificador da SUA jurisdicao.
    petro = asof.entity_by_local_id("cod_cvm", "9512")
    intel = asof.entity_by_local_id("cik", "0000050863")

    assert petro is not None and petro.entity_id == PETROBRAS
    assert petro.jurisdiction == "BR"
    assert intel is not None and intel.entity_id == INTEL
    assert intel.jurisdiction == "US"

    # E cada uma chega aos SEUS fatos.
    fato_petro = asof.value(
        entity_id=PETROBRAS, cd_conta="3.01", period_end=date(2023, 12, 31),
        as_of=date(2025, 6, 30),
    )
    fato_intel = asof.value(
        entity_id=INTEL, cd_conta="3.01", period_end=date(2023, 12, 31),
        as_of=date(2025, 6, 30),
    )
    assert fato_petro is not None and fato_petro.currency == "BRL"
    assert fato_intel is not None and fato_intel.currency == "USD"
    assert fato_petro.fact_id != fato_intel.fact_id

    # A independencia, dita de forma negativa: o identificador de uma nao
    # resolve para a outra, e nao resolve para nada.
    assert asof.entity_by_local_id("cik", "9512") is None
    assert asof.entity_by_local_id("cod_cvm", "0000050863") is None


def test_a_intel_nao_desaparece_por_nao_ter_cod_cvm(duas_jurisdicoes):
    """O motivo pelo qual a M5.6 existiu.

    Ate aqui `coverage()` usava `max(cod_cvm) is None` como sentinela de "nao
    ha nada". Uma companhia americana teria cod_cvm nulo COM fatos, e sumiria
    sem erro e sem aviso - como se nunca tivesse sido ingerida.
    """
    asof = AsOf(duas_jurisdicoes)
    cobertura = asof.coverage(INTEL)

    assert cobertura is not None, "a Intel desapareceu"
    assert cobertura.entity_id == INTEL
    assert cobertura.jurisdiction == "US"
    assert cobertura.display_name == "INTEL CORP"
    assert cobertura.period_ends == (date(2023, 12, 31),)


# -- As tres ausencias, agora distinguiveis ----------------------------------


def test_entidade_desconhecida_difere_de_entidade_sem_fatos(conn):
    """Rule 8 da decisao: as duas nao podem sair iguais.

    Elas pedem acoes opostas - conferir o identificador digitado numa,
    rodar a ingestao na outra.
    """
    asof = AsOf(conn)

    # (a) nao existe: `entity()` nao acha.
    assert asof.entity("us:cik:9999999999") is None
    assert asof.coverage("us:cik:9999999999") is None

    # (b) existe e nao tem fato: `entity()` ACHA, `coverage()` nao.
    upsert_identities(
        conn,
        [
            EntityIdentity(
                entity_id=INTEL, jurisdiction="US", scheme="cik",
                local_id="0000050863", display_name="INTEL CORP", is_primary=True,
            )
        ],
    )
    conhecida = asof.entity(INTEL)
    assert conhecida is not None, "entidade registrada tem que ser encontravel"
    assert conhecida.display_name == "INTEL CORP"
    assert asof.coverage(INTEL) is None, "sem fato, nao ha cobertura"


def test_a_cobertura_nao_usa_mais_identificador_como_sentinela(duas_jurisdicoes):
    """A pergunta virou "ha fato?", que e a que sempre deveria ter sido."""
    import inspect

    fonte = inspect.getsource(AsOf.coverage)
    assert "cod_cvm" not in fonte or "SENTINEL" in fonte.upper()
    assert "COUNT(*)" in fonte


# -- Identidade --------------------------------------------------------------


def test_entity_id_de_cik_preserva_os_zeros(conn):
    """Identificador que muda de forma conforme quem digitou deixa de ser
    identificador."""
    assert Company.entity_id_from_cik("50863") == INTEL
    assert Company.entity_id_from_cik("0000050863") == INTEL
    assert Company.entity_id_from_cik(50863) == INTEL


def test_resolve_entity_e_a_porta_unica(duas_jurisdicoes):
    assert resolve_entity(duas_jurisdicoes, scheme="ticker", local_id="INTC") == INTEL
    assert resolve_entity(duas_jurisdicoes, scheme="cnpj", local_id="33000167000101") == PETROBRAS
    assert resolve_entity(duas_jurisdicoes, scheme="cik", local_id="nao-existe") is None


def test_uma_entidade_pode_ter_varios_nomes_por_regime(duas_jurisdicoes):
    from pat.store.entity import identities_for

    ids = identities_for(duas_jurisdicoes, INTEL)
    assert {i.scheme for i in ids} == {"cik", "ticker"}
    assert sum(1 for i in ids if i.is_primary) == 1


# -- A migracao --------------------------------------------------------------


def test_gold_fact_nao_carrega_mais_identificador_de_regime(conn):
    """A propriedade estrutural da M5.6, conferida no esquema.

    Uma jurisdicao nova nao pede coluna nova aqui - pede uma LINHA em `entity`.
    Era a terceira coluna que denunciaria o desenho anterior.
    """
    colunas = {
        linha[1] for linha in conn.execute("PRAGMA table_info('gold_fact')").fetchall()
    }
    for proibida in ("cod_cvm", "denom_cia", "cik", "cnpj"):
        assert proibida not in colunas, (
            f"gold_fact voltou a carregar {proibida}: identificador de regime na "
            "tabela universal de fatos"
        )
    assert "entity_id" in colunas


def test_a_migracao_e_idempotente_e_deixa_rastro(conn):
    linhas = conn.execute(
        "SELECT COUNT(*) FROM schema_migration WHERE migration_id = ?", [M56_ENTITY]
    ).fetchone()[0]
    assert linhas == 1

    migrate(conn)  # de novo
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM schema_migration WHERE migration_id = ?", [M56_ENTITY]
        ).fetchone()[0]
        == 1
    )

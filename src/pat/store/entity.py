"""Persistencia da entidade universal e de seus nomes por regime.

Uma entidade tem uma linha por `scheme`: CNPJ e cod_cvm no Brasil, CIK e
ticker nos EUA. `entity_id` continua opaco, e quem consulta nao deve derivar
jurisdicao do prefixo - le `jurisdiction`.

Idempotente por (entity_id, scheme), como todo store do projeto. Diferente do
gold, aqui `DO UPDATE` no `display_name` e legitimo: o nome de uma companhia
muda (incorporacao, mudanca de razao social) e isso nao e um fato bitemporal -
e um rotulo corrente. O identificador, que e o que importa, nunca muda.
"""

from __future__ import annotations

from collections.abc import Sequence

import duckdb

from pat.contracts.entities import EntityIdentity

__all__ = [
    "identities_for",
    "resolve_entity",
    "upsert_identities",
]

_COLUMNS = ("entity_id", "jurisdiction", "scheme", "local_id", "display_name", "is_primary")


def upsert_identities(
    conn: duckdb.DuckDBPyConnection, identities: Sequence[EntityIdentity]
) -> int:
    if not identities:
        return 0
    antes = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    conn.executemany(
        f"INSERT INTO entity ({', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (entity_id, scheme) DO UPDATE SET "
        "display_name = excluded.display_name, local_id = excluded.local_id",
        [
            (
                i.entity_id,
                i.jurisdiction,
                i.scheme,
                i.local_id,
                i.display_name,
                i.is_primary,
            )
            for i in identities
        ],
    )
    depois = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    return int(depois - antes)


def identities_for(
    conn: duckdb.DuckDBPyConnection, entity_id: str
) -> list[EntityIdentity]:
    linhas = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM entity WHERE entity_id = ? ORDER BY scheme",
        [entity_id],
    ).fetchall()
    return [EntityIdentity(**dict(zip(_COLUMNS, linha, strict=True))) for linha in linhas]


def resolve_entity(
    conn: duckdb.DuckDBPyConnection, *, scheme: str, local_id: str
) -> str | None:
    """Identificador local -> `entity_id`. `None` quando nao existe.

    E a unica porta pela qual `--cod-cvm` e `--cik` viram `entity_id`. Sem ela
    cada comando faria a sua propria traducao, e a segunda jurisdicao
    espalharia `cik` pela CLI do mesmo jeito que `cod_cvm` estava espalhado.
    """
    linha = conn.execute(
        "SELECT entity_id FROM entity WHERE scheme = ? AND local_id = ?",
        [scheme, str(local_id)],
    ).fetchone()
    return linha[0] if linha else None


def display_name(conn: duckdb.DuckDBPyConnection, entity_id: str) -> str | None:
    """Nome de exibicao, preferindo o identificador primario da jurisdicao."""
    linha = conn.execute(
        "SELECT display_name FROM entity WHERE entity_id = ? "
        "ORDER BY is_primary DESC, scheme LIMIT 1",
        [entity_id],
    ).fetchone()
    return linha[0] if linha else None


def all_entities(conn: duckdb.DuckDBPyConnection) -> list[tuple[str, str, str]]:
    """(entity_id, jurisdiction, display_name), uma linha por entidade."""
    linhas = conn.execute(
        """
        SELECT entity_id, ANY_VALUE(jurisdiction),
               ARG_MAX(display_name, CAST(is_primary AS INTEGER))
        FROM entity GROUP BY entity_id ORDER BY entity_id
        """
    ).fetchall()
    return [(linha[0], linha[1], linha[2]) for linha in linhas]

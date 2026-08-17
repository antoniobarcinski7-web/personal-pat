"""Persistencia do manifesto de pesquisa.

Fica em `store/` e nao em `research/` pelo mesmo motivo que `write_facts` fica:
quem calcula nao grava. A camada de pesquisa produz um `ResearchRunManifest`
puro e entrega; a decisao de persistir e de quem tem a conexao de escrita.

Isso mantem o nucleo deterministico da Fase 3 sem qualquer import de `store`,
que e o que permite executar um plano inteiro em memoria, num teste, sem banco
nenhum.

Append-only e idempotente: `manifest_id` ja gravado nao e reescrito. Duas
execucoes do mesmo plano sao corridas distintas (o `executed_at` entra no
`manifest_id`), entao a colisao so acontece ao regravar o mesmo manifesto - e
ai nao ha o que atualizar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import duckdb

from pat.contracts.research import ResearchRunManifest

_COLUMNS = (
    "manifest_id",
    "question_id",
    "plan_id",
    "capability_sha256",
    "as_of",
    "executed_at",
    "outputs_available",
    "result_ids",
    "metric_versions",
    "mapping_sha256s",
    "fact_ids",
    "pat_version",
    "python_version",
    "git_sha",
)

_INSERT = (
    f"INSERT INTO research_run ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))}) "
    "ON CONFLICT (manifest_id) DO NOTHING"
)


@dataclass(frozen=True)
class ManifestRow:
    """O manifesto como saiu do banco.

    Tipo proprio, e nao o contrato reconstruido: `planner`/`writer` nao tem
    coluna nesta tabela, entao devolver um `ResearchRunManifest` daria a
    impressao de que a leitura recupera tudo que o contrato carrega.
    """

    manifest_id: str
    question_id: str
    plan_id: str
    capability_sha256: str
    as_of: date
    executed_at: datetime
    outputs_available: bool
    result_ids: tuple[str, ...]
    metric_versions: tuple[str, ...]
    mapping_sha256s: tuple[str, ...]
    fact_ids: tuple[str, ...]
    pat_version: str
    python_version: str
    git_sha: str | None


def write_manifest(conn: duckdb.DuckDBPyConnection, manifest: ResearchRunManifest) -> bool:
    """Grava o manifesto. True se entrou, False se ja estava la."""
    before = count(conn)
    conn.execute(
        _INSERT,
        [
            manifest.manifest_id,
            manifest.question_id,
            manifest.plan_id,
            manifest.capability_sha256,
            manifest.as_of,
            manifest.executed_at,
            manifest.outputs_available,
            list(manifest.result_ids),
            list(manifest.metric_versions),
            list(manifest.mapping_sha256s),
            list(manifest.fact_ids),
            manifest.pat_version,
            manifest.python_version,
            manifest.git_sha,
        ],
    )
    return count(conn) > before


def _to_row(row: tuple) -> ManifestRow:
    manifest_id, question_id, plan_id, capability, as_of, executed_at, outputs = row[:7]
    results, metrics, mappings, facts = row[7:11]
    pat_version, python_version, git_sha = row[11:]
    return ManifestRow(
        manifest_id=manifest_id,
        question_id=question_id,
        plan_id=plan_id,
        capability_sha256=capability,
        as_of=as_of,
        executed_at=executed_at,
        outputs_available=outputs,
        result_ids=tuple(results),
        metric_versions=tuple(metrics),
        mapping_sha256s=tuple(mappings),
        fact_ids=tuple(facts),
        pat_version=pat_version,
        python_version=python_version,
        git_sha=git_sha,
    )


def read_manifest(conn: duckdb.DuckDBPyConnection, manifest_id: str) -> ManifestRow | None:
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM research_run WHERE manifest_id = ?",
        [manifest_id],
    ).fetchone()
    return _to_row(row) if row else None


def recent_manifests(conn: duckdb.DuckDBPyConnection, limit: int = 20) -> list[ManifestRow]:
    """Da corrida mais recente para a mais antiga.

    Desempate por `manifest_id` para a listagem nao depender da ordem fisica
    das linhas quando duas corridas caem no mesmo instante.
    """
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM research_run "
        "ORDER BY executed_at DESC, manifest_id ASC LIMIT ?",
        [limit],
    ).fetchall()
    return [_to_row(row) for row in rows]


def count(conn: duckdb.DuckDBPyConnection) -> int:
    return conn.execute("SELECT COUNT(*) FROM research_run").fetchone()[0]

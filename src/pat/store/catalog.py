"""Indice consultavel sobre o bronze.

Guarda runs, documentos e retrievals. Nao guarda bytes: o bronze e a fonte
de verdade e este catalogo e derivado dele.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import duckdb

from pat.contracts.common import RunStatus
from pat.contracts.documents import RawDocument, Retrieval
from pat.contracts.lineage import Run


@dataclass(frozen=True)
class ResourceVersion:
    """Uma versao distinta do conteudo de um recurso logico."""

    content_sha256: str
    first_retrieved_at: datetime
    last_retrieved_at: datetime
    times_seen: int
    size_bytes: int
    last_modified: str | None


class Catalog:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    # -- runs ---------------------------------------------------------------

    def start_run(self, run: Run) -> None:
        self.conn.execute(
            """
            INSERT INTO ingest_run
                (run_id, command, started_at, finished_at, status,
                 pat_version, python_version, git_sha, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run.run_id,
                run.command,
                run.started_at,
                run.finished_at,
                str(run.status),
                run.pat_version,
                run.python_version,
                run.git_sha,
                run.notes,
            ],
        )

    def finish_run(self, run_id: str, status: RunStatus, finished_at: datetime) -> None:
        self.conn.execute(
            "UPDATE ingest_run SET status = ?, finished_at = ? WHERE run_id = ?",
            [str(status), finished_at, run_id],
        )

    # -- documentos e retrievals -------------------------------------------

    def record_document(self, document: RawDocument) -> None:
        """Insere se novo. Nunca atualiza: o bronze e imutavel."""
        self.conn.execute(
            """
            INSERT INTO raw_document
                (content_sha256, size_bytes, media_type, storage_path,
                 first_seen_at, first_seen_run_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (content_sha256) DO NOTHING
            """,
            [
                document.content_sha256,
                document.size_bytes,
                document.media_type,
                document.storage_path,
                document.first_seen_at,
                document.first_seen_run_id,
            ],
        )

    def record_retrieval(self, retrieval: Retrieval) -> None:
        self.conn.execute(
            """
            INSERT INTO retrieval
                (retrieval_id, content_sha256, provider_id, provider_version,
                 source_tier, dataset_id, resource_key, url, requested_at,
                 retrieved_at, http_status, etag, last_modified, content_length,
                 content_type, final_url, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                retrieval.retrieval_id,
                retrieval.content_sha256,
                retrieval.provider_id,
                retrieval.provider_version,
                str(retrieval.source_tier),
                retrieval.dataset_id,
                retrieval.resource_key,
                retrieval.url,
                retrieval.requested_at,
                retrieval.retrieved_at,
                retrieval.http.status_code,
                retrieval.http.etag,
                retrieval.http.last_modified,
                retrieval.http.content_length,
                retrieval.http.content_type,
                retrieval.http.final_url,
                retrieval.run_id,
            ],
        )

    # -- consultas ----------------------------------------------------------

    def resource_versions(self, dataset_id: str, resource_key: str) -> list[ResourceVersion]:
        """Versoes distintas de conteudo ja vistas para um recurso logico.

        Mais de uma linha significa que a fonte alterou o arquivo. Para
        DFP/ITR da CVM, e o indicio primario de reapresentacao e o gatilho
        para reprocessar aquele ano.
        """
        rows = self.conn.execute(
            """
            SELECT r.content_sha256,
                   MIN(r.retrieved_at) AS first_seen,
                   MAX(r.retrieved_at) AS last_seen,
                   COUNT(*)            AS times_seen,
                   ANY_VALUE(d.size_bytes)   AS size_bytes,
                   ANY_VALUE(r.last_modified) AS last_modified
            FROM retrieval r
            JOIN raw_document d USING (content_sha256)
            WHERE r.dataset_id = ? AND r.resource_key = ?
            GROUP BY r.content_sha256
            ORDER BY first_seen
            """,
            [dataset_id, resource_key],
        ).fetchall()
        return [
            ResourceVersion(
                content_sha256=row[0],
                first_retrieved_at=row[1],
                last_retrieved_at=row[2],
                times_seen=row[3],
                size_bytes=row[4],
                last_modified=row[5],
            )
            for row in rows
        ]

    def changed_resources(self) -> list[tuple[str, str, int]]:
        """Recursos com mais de uma versao de conteudo: candidatos a reapresentacao."""
        return self.conn.execute(
            """
            SELECT dataset_id, resource_key, COUNT(DISTINCT content_sha256) AS versions
            FROM retrieval
            GROUP BY dataset_id, resource_key
            HAVING COUNT(DISTINCT content_sha256) > 1
            ORDER BY versions DESC, dataset_id, resource_key
            """
        ).fetchall()

    def stats(self) -> dict[str, int]:
        docs, size = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM raw_document"
        ).fetchone()
        retrievals = self.conn.execute("SELECT COUNT(*) FROM retrieval").fetchone()[0]
        runs = self.conn.execute("SELECT COUNT(*) FROM ingest_run").fetchone()[0]
        return {
            "documents": docs,
            "bytes": int(size),
            "retrievals": retrievals,
            "runs": runs,
        }

    def recent_runs(self, limit: int = 20) -> list[tuple]:
        return self.conn.execute(
            """
            SELECT run_id, command, status, started_at, finished_at
            FROM ingest_run ORDER BY started_at DESC LIMIT ?
            """,
            [limit],
        ).fetchall()

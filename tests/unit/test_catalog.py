"""Garantias do catalogo DuckDB."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pat.audit.run import finish, new_run
from pat.contracts.common import RunStatus
from pat.contracts.documents import HttpMeta, RawDocument, Retrieval
from pat.store.catalog import Catalog
from pat.store.db import connect, migrate


@pytest.fixture
def catalog(tmp_path):
    conn = connect(tmp_path / "w.duckdb")
    migrate(conn)
    yield Catalog(conn)
    conn.close()


def _document(sha: str = "a" * 64) -> RawDocument:
    return RawDocument(
        content_sha256=sha,
        size_bytes=3,
        media_type="application/zip",
        storage_path=f"blobs/{sha[:2]}/{sha[2:4]}/{sha}",
        first_seen_at=datetime.now(UTC),
        first_seen_run_id="run1",
    )


def _retrieval(sha: str = "a" * 64, retrieval_id: str = "r1") -> Retrieval:
    now = datetime.now(UTC)
    return Retrieval(
        retrieval_id=retrieval_id,
        content_sha256=sha,
        provider_id="cvm",
        source_tier="primary_official",
        dataset_id="cvm.dfp",
        resource_key="2024",
        url="https://exemplo.invalid/2024.zip",
        requested_at=now,
        retrieved_at=now,
        http=HttpMeta(status_code=200),
        run_id="run1",
        provider_version="1.0.0",
    )


def test_migrate_e_idempotente(tmp_path):
    conn = connect(tmp_path / "w.duckdb")
    migrate(conn)
    migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM raw_document").fetchone()[0] == 0
    conn.close()


def test_documento_duplicado_nao_gera_erro_nem_duplicata(catalog):
    """O bronze e imutavel: registrar o mesmo documento duas vezes e um
    no-op, nunca um update."""
    catalog.record_document(_document())
    catalog.record_document(_document())
    assert catalog.stats()["documents"] == 1


def test_timestamps_voltam_com_timezone(catalog):
    """Se um timestamp voltar ingenuo do banco, a comparacao point-in-time
    silenciosamente muda de significado conforme a maquina."""
    catalog.record_document(_document())
    catalog.record_retrieval(_retrieval())

    (version,) = catalog.resource_versions("cvm.dfp", "2024")
    assert version.first_retrieved_at.tzinfo is not None
    assert version.first_retrieved_at.utcoffset() is not None


def test_ciclo_de_vida_do_run(catalog):
    run = new_run(command="fetch cvm.dfp")
    catalog.start_run(run)
    assert catalog.recent_runs()[0][2] == "running"

    done = finish(run, RunStatus.SUCCEEDED)
    catalog.finish_run(run.run_id, done.status, done.finished_at)

    run_id, command, status, started, finished = catalog.recent_runs()[0]
    assert run_id == run.run_id
    assert status == "succeeded"
    assert finished is not None and finished >= started


def test_recurso_sem_retrieval_retorna_vazio(catalog):
    assert catalog.resource_versions("cvm.dfp", "1999") == []

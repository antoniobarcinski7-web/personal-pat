"""Fixtures da camada semantica: um warehouse pequeno, montado pelo pipeline real.

Os testes nao inserem fatos direto no gold. Gravam os bytes no bronze,
registram o retrieval no catalogo e passam pelo parser e pelo builder de
verdade, como a Fase 1 faz. Custa milissegundos e compra duas coisas que
fixture sintetica nao compra:

- se o formato do `locator`, a escala MIL ou o tipo de periodo mudarem, o
  golden test quebra junto;
- a cadeia de procedencia existe de fato, ate um blob em disco com hash
  conferivel - entao o teste de linhagem testa linhagem, e nao um mock dela.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pat.contracts.common import SourceTier
from pat.contracts.documents import HttpMeta, Retrieval
from pat.parse.cvm_dfp import parse_dfp_zip
from pat.semantics import build_engine
from pat.semantics.loader import load_dir
from pat.semantics.registry import default_registry
from pat.store.bronze import BronzeStore
from pat.store.catalog import Catalog
from pat.store.db import connect, migrate
from pat.store.gold import build_facts, write_facts
from pat.store.silver import write_lines

MAPPINGS_DIR = Path(__file__).resolve().parents[2] / "src" / "pat" / "semantics" / "mappings"

RUN_ID = "run-test"


def load_zips(conn, bronze: BronzeStore, zips: dict[int, bytes], *, only_cod_cvm=None) -> None:
    """bronze -> catalogo -> silver -> gold, sem rede, pelo caminho de producao."""
    catalog = Catalog(conn)
    now = datetime.now(UTC)

    for year, content in sorted(zips.items()):
        put = bronze.put(content, run_id=RUN_ID, media_type="application/zip")
        catalog.record_document(put.document)

        retrieval_id = f"ret-{year}"
        catalog.record_retrieval(
            Retrieval(
                retrieval_id=retrieval_id,
                content_sha256=put.document.content_sha256,
                provider_id="cvm",
                provider_version="1.0.0",
                source_tier=SourceTier.PRIMARY_OFFICIAL,
                dataset_id="cvm.dfp",
                resource_key=str(year),
                url=f"https://example.invalid/dfp_cia_aberta_{year}.zip",
                requested_at=now,
                retrieved_at=now,
                http=HttpMeta(status_code=200),
                run_id=RUN_ID,
            )
        )

        lines, parse_stats = parse_dfp_zip(
            content,
            year=year,
            content_sha256=hashlib.sha256(content).hexdigest(),
            retrieval_id=retrieval_id,
            extraction_run_id=RUN_ID,
            only_cod_cvm=only_cod_cvm,
        )
        assert parse_stats.skipped_total == 0, parse_stats.as_dict()
        write_lines(conn, lines)

        facts, build_stats = build_facts(lines)
        assert build_stats.skipped_total == 0, build_stats.as_dict()
        write_facts(conn, facts)


@pytest.fixture
def make_engine(tmp_path):
    """(zips, **kwargs) -> engine, sobre um bronze e um banco novos."""
    conns: list = []

    def _make(zips: dict[int, bytes], *, mappings_dir: Path | None = None, **kwargs):
        index = len(conns)
        conn = connect(tmp_path / f"w{index}.duckdb")
        conns.append(conn)
        migrate(conn)
        bronze = BronzeStore(tmp_path / f"bronze{index}")
        load_zips(conn, bronze, zips)
        engine = build_engine(
            conn,
            mappings=load_dir(mappings_dir) if mappings_dir else load_dir(MAPPINGS_DIR),
            registry=default_registry(),
            **kwargs,
        )
        # Guardados para os testes que precisam reconferir a cadeia ate os bytes.
        engine.test_conn = conn
        engine.test_bronze = bronze
        return engine

    yield _make
    for conn in conns:
        conn.close()

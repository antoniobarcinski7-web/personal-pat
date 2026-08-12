"""Ingestao ponta a ponta com provider falso.

O teste central e `test_conteudo_alterado_na_origem_e_detectavel`: e a
propriedade da qual depende todo o tratamento de reapresentacao da CVM.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import pytest

from pat.audit.run import new_run
from pat.contracts.common import SourceTier
from pat.contracts.documents import HttpMeta, ResourceRef
from pat.ingest import ingest_dataset
from pat.sources.base import DatasetSpec, FetchResult, SourceProvider
from pat.sources.registry import Registry
from pat.store.bronze import BronzeStore
from pat.store.catalog import Catalog
from pat.store.db import connect, migrate


class FakeProvider(SourceProvider):
    """Provider controlavel: o conteudo servido pode mudar entre buscas,
    como o ZIP anual da CVM muda quando ha reapresentacao."""

    provider_id: ClassVar[str] = "fake"
    tier: ClassVar[SourceTier] = SourceTier.PRIMARY_OFFICIAL
    version: ClassVar[str] = "1.0.0"

    def __init__(self, content: bytes = b"v1") -> None:
        self.content = content
        self.fetch_count = 0

    def datasets(self):
        return (
            DatasetSpec(
                dataset_id="fake.annual",
                title="Dataset anual falso",
                tier=self.tier,
                params=("year",),
            ),
        )

    def resolve(self, dataset_id: str, **params: str):
        return (
            ResourceRef(
                provider_id=self.provider_id,
                dataset_id=dataset_id,
                resource_key=params["year"],
                url=f"https://exemplo.invalid/{params['year']}.zip",
                media_type="application/zip",
            ),
        )

    def fetch(self, ref: ResourceRef) -> FetchResult:
        self.fetch_count += 1
        now = datetime.now(UTC)
        return FetchResult(
            ref=ref,
            content=self.content,
            http=HttpMeta(status_code=200, content_length=len(self.content)),
            requested_at=now,
            retrieved_at=now,
        )


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "warehouse.duckdb")
    migrate(conn)
    provider = FakeProvider()
    registry = Registry(providers=(provider,))
    yield {
        "store": BronzeStore(tmp_path / "bronze"),
        "catalog": Catalog(conn),
        "registry": registry,
        "provider": provider,
    }
    conn.close()


def _ingest(env, command="fetch fake.annual"):
    run = new_run(command=command)
    env["catalog"].start_run(run)
    return ingest_dataset(
        "fake.annual",
        {"year": "2024"},
        registry=env["registry"],
        store=env["store"],
        catalog=env["catalog"],
        run=run,
    )


def test_ingestao_grava_documento_e_retrieval(env):
    (outcome,) = _ingest(env)

    assert outcome.is_new_content
    assert outcome.retrieval.source_tier is SourceTier.PRIMARY_OFFICIAL
    assert outcome.retrieval.url == "https://exemplo.invalid/2024.zip"
    assert env["store"].read(outcome.document.content_sha256) == b"v1"

    stats = env["catalog"].stats()
    assert stats["documents"] == 1
    assert stats["retrievals"] == 1


def test_rebusca_sem_mudanca_nao_duplica_documento(env):
    _ingest(env)
    (second,) = _ingest(env)

    assert not second.is_new_content
    stats = env["catalog"].stats()
    # Um conteudo, duas observacoes: re-buscar e barato e continua registrado.
    assert stats["documents"] == 1
    assert stats["retrievals"] == 2


def test_conteudo_alterado_na_origem_e_detectavel(env):
    """Mesma URL, bytes diferentes: as duas versoes coexistem.

    E assim que uma reapresentacao da CVM aparece no sistema. Nenhum dado
    antigo e perdido, e a mudanca fica datada.
    """
    (first,) = _ingest(env)
    env["provider"].content = b"v2-reapresentado"
    (second,) = _ingest(env)

    assert second.is_new_content
    assert first.document.content_sha256 != second.document.content_sha256

    # Ambas as versoes continuam legiveis: reprocessamento historico nao
    # depende de a origem ainda servir o conteudo antigo.
    assert env["store"].read(first.document.content_sha256) == b"v1"
    assert env["store"].read(second.document.content_sha256) == b"v2-reapresentado"

    versions = env["catalog"].resource_versions("fake.annual", "2024")
    assert len(versions) == 2
    assert [v.content_sha256 for v in versions] == [
        first.document.content_sha256,
        second.document.content_sha256,
    ]

    assert env["catalog"].changed_resources() == [("fake.annual", "2024", 2)]


def test_recurso_estavel_nao_aparece_como_alterado(env):
    _ingest(env)
    _ingest(env)
    assert env["catalog"].changed_resources() == []


def test_run_e_registrado_e_ligado_ao_documento(env):
    (outcome,) = _ingest(env, command="fetch fake.annual --year 2024")
    runs = env["catalog"].recent_runs()
    assert len(runs) == 1
    run_id = runs[0][0]
    assert outcome.document.first_seen_run_id == run_id
    assert outcome.retrieval.run_id == run_id


def test_sidecar_do_bronze_carrega_procedencia_completa(env):
    (outcome,) = _ingest(env)
    meta = env["store"]._read_meta(outcome.document.content_sha256)
    prov = meta["provenance"]
    assert prov["url"] == "https://exemplo.invalid/2024.zip"
    assert prov["source_tier"] == "primary_official"
    assert prov["dataset_id"] == "fake.annual"
    assert prov["provider_version"] == "1.0.0"
    assert "retrieved_at" in prov

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pat.contracts.common import SourceTier
from pat.contracts.documents import HttpMeta, RawDocument, Retrieval
from pat.contracts.entities import Company


def _doc(**overrides) -> RawDocument:
    base = dict(
        content_sha256="b" * 64,
        size_bytes=10,
        media_type="application/zip",
        storage_path="blobs/bb/bb/" + "b" * 64,
        first_seen_at=datetime.now(UTC),
        first_seen_run_id="run1",
    )
    return RawDocument(**(base | overrides))


def test_sha256_precisa_ser_hex_minusculo_de_64():
    with pytest.raises(ValidationError, match="sha256 invalido"):
        _doc(content_sha256="B" * 64)
    with pytest.raises(ValidationError, match="sha256 invalido"):
        _doc(content_sha256="abc")


def test_datetime_ingenuo_e_rejeitado():
    """Timestamp sem timezone quebra reprodutibilidade entre maquinas."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        _doc(first_seen_at=datetime(2026, 1, 1, 12, 0))


def test_retrieval_preserva_url_e_tier():
    now = datetime.now(UTC)
    retrieval = Retrieval(
        retrieval_id="r1",
        content_sha256="c" * 64,
        provider_id="cvm",
        source_tier=SourceTier.PRIMARY_OFFICIAL,
        dataset_id="cvm.dfp",
        resource_key="2024",
        url="https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2024.zip",
        requested_at=now,
        retrieved_at=now,
        http=HttpMeta(status_code=200, etag='"abc"', last_modified="Sun, 09 Aug 2026 10:23:18 GMT"),
        run_id="run1",
        provider_version="1.0.0",
    )
    assert retrieval.source_tier is SourceTier.PRIMARY_OFFICIAL
    assert retrieval.url.endswith("dfp_cia_aberta_2024.zip")
    assert retrieval.http.last_modified is not None


def test_cnpj_normalizado():
    company = Company(
        entity_id="br:cnpj:33000167000101",
        legal_name="PETROLEO BRASILEIRO S.A. PETROBRAS",
        cnpj="33.000.167/0001-01",
        cod_cvm=9512,
        tickers=("PETR3", "PETR4"),
    )
    assert company.cnpj == "33000167000101"
    assert Company.entity_id_from_cnpj("33.000.167/0001-01") == company.entity_id


def test_cnpj_invalido_falha():
    with pytest.raises(ValidationError, match="14 digitos"):
        Company(entity_id="x", legal_name="y", cnpj="123")

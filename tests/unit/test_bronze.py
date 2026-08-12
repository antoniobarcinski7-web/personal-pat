"""Garantias do bronze: imutabilidade, deduplicacao e verificabilidade."""

from __future__ import annotations

import os
import stat

import pytest

from pat.store.bronze import BronzeStore, BronzeStoreError, sha256_bytes


@pytest.fixture
def store(tmp_path):
    return BronzeStore(tmp_path / "bronze")


def test_put_grava_e_retorna_hash(store):
    content = b"conteudo de teste"
    result = store.put(content, run_id="run1", media_type="text/plain")
    assert result.is_new
    assert result.document.content_sha256 == sha256_bytes(content)
    assert result.document.size_bytes == len(content)
    assert store.read(result.document.content_sha256) == content


def test_put_e_idempotente_por_conteudo(store):
    content = b"mesmo conteudo"
    first = store.put(content, run_id="run1")
    second = store.put(content, run_id="run2")

    assert first.is_new
    assert not second.is_new
    assert first.document.content_sha256 == second.document.content_sha256
    # O run de origem permanece o primeiro: quem trouxe o conteudo ao sistema.
    assert second.document.first_seen_run_id == "run1"


def test_conteudos_diferentes_nao_colidem(store):
    a = store.put(b"a", run_id="run1")
    b = store.put(b"b", run_id="run1")
    assert a.document.content_sha256 != b.document.content_sha256
    assert store.read(a.document.content_sha256) == b"a"
    assert store.read(b.document.content_sha256) == b"b"


def test_blob_fica_somente_leitura(store):
    """Imutabilidade nao e so convencao: e permissao no sistema de arquivos."""
    result = store.put(b"imutavel", run_id="run1")
    path = store.path_of(result.document.content_sha256)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert not mode & stat.S_IWUSR


def test_read_detecta_corrupcao(store):
    result = store.put(b"original", run_id="run1")
    sha = result.document.content_sha256
    path = store.path_of(sha)

    os.chmod(path, 0o644)
    path.write_bytes(b"adulterado")

    with pytest.raises(BronzeStoreError, match="corrompido"):
        store.read(sha)


def test_verify_separa_integros_de_corrompidos(store):
    ok_sha = store.put(b"intacto", run_id="run1").document.content_sha256
    bad_sha = store.put(b"sera adulterado", run_id="run1").document.content_sha256

    bad_path = store.path_of(bad_sha)
    os.chmod(bad_path, 0o644)
    bad_path.write_bytes(b"outra coisa")

    ok, bad = store.verify()
    assert ok_sha in ok
    assert bad == [bad_sha]


def test_sidecar_guarda_procedencia(store):
    result = store.put(
        b"x",
        run_id="run1",
        provenance={"url": "https://exemplo/x.zip", "dataset_id": "cvm.dfp"},
    )
    meta = store._read_meta(result.document.content_sha256)
    assert meta["provenance"]["url"] == "https://exemplo/x.zip"
    assert meta["document"]["content_sha256"] == result.document.content_sha256


def test_store_vazio_verifica_sem_erro(tmp_path):
    ok, bad = BronzeStore(tmp_path / "inexistente").verify()
    assert ok == [] and bad == []

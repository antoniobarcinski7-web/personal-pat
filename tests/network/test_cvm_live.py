"""Testes contra a CVM real. Marcados como `network`.

Excluidos da suite padrao porque dependem de um servico externo. Rode-os
quando quiser confirmar que a CVM nao mudou o layout de URLs:

    pytest -m network
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from pat.sources.public.cvm import CVMProvider

pytestmark = pytest.mark.network


@pytest.fixture
def cvm():
    provider = CVMProvider()
    yield provider
    provider.close()


def test_cadastro_de_companhias_esta_acessivel(cvm):
    (ref,) = cvm.resolve("cvm.cad_cia_aberta")
    result = cvm.fetch(ref)

    assert result.http.status_code == 200
    assert len(result.content) > 100_000
    header = result.content[:400].decode("latin-1")
    assert "CNPJ_CIA" in header
    assert "CD_CVM" in header


def test_dfp_anual_e_um_zip_com_csvs(cvm):
    (ref,) = cvm.resolve("cvm.dfp", year="2024")
    result = cvm.fetch(ref)

    assert result.http.status_code == 200
    with zipfile.ZipFile(BytesIO(result.content)) as zf:
        names = zf.namelist()
    assert any(name.startswith("dfp_cia_aberta_DRE_con_") for name in names)
    assert any(name.startswith("dfp_cia_aberta_BPA_con_") for name in names)


def test_servidor_expoe_last_modified(cvm):
    """`last_modified` e o que permite explicar depois por que os bytes de um
    mesmo recurso mudaram. Se a CVM parar de enviar esse header, o
    diagnostico de reapresentacao fica mais pobre e queremos saber."""
    (ref,) = cvm.resolve("cvm.itr", year="2024")
    result = cvm.fetch(ref)
    assert result.http.last_modified is not None

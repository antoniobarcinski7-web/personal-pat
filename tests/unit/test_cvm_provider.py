"""Resolucao de recursos da CVM. Nao toca a rede."""

from __future__ import annotations

import pytest

from pat.contracts.common import SourceTier
from pat.sources.base import SourceError, UnknownDatasetError
from pat.sources.public.cvm import CVMProvider


@pytest.fixture
def cvm():
    return CVMProvider()


def test_url_de_dfp(cvm):
    (ref,) = cvm.resolve("cvm.dfp", year="2024")
    assert ref.url == (
        "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2024.zip"
    )
    assert ref.resource_key == "2024"
    assert ref.media_type == "application/zip"


def test_url_de_itr(cvm):
    (ref,) = cvm.resolve("cvm.itr", year="2023")
    assert ref.url.endswith("/DOC/ITR/DADOS/itr_cia_aberta_2023.zip")


def test_cadastro_nao_tem_parametros(cvm):
    (ref,) = cvm.resolve("cvm.cad_cia_aberta")
    assert ref.url.endswith("/CAD/DADOS/cad_cia_aberta.csv")
    assert ref.resource_key == "current"
    with pytest.raises(SourceError, match="nao aceita parametros"):
        cvm.resolve("cvm.cad_cia_aberta", year="2024")


def test_intervalo_de_anos(cvm):
    refs = cvm.resolve("cvm.dfp", year="2020-2024")
    assert [r.resource_key for r in refs] == ["2020", "2021", "2022", "2023", "2024"]


def test_lista_de_anos_deduplica_e_ordena(cvm):
    refs = cvm.resolve("cvm.dfp", year="2022,2020,2022")
    assert [r.resource_key for r in refs] == ["2020", "2022"]


def test_ano_antes_do_inicio_da_serie_falha(cvm):
    with pytest.raises(SourceError, match="nao publica"):
        cvm.resolve("cvm.dfp", year="2005")
    with pytest.raises(SourceError, match="nao publica"):
        cvm.resolve("cvm.itr", year="2010")


def test_ano_futuro_falha(cvm):
    with pytest.raises(SourceError, match="futuro"):
        cvm.resolve("cvm.dfp", year="2999")


def test_ano_malformado_falha(cvm):
    with pytest.raises(SourceError, match="ano invalido"):
        cvm.resolve("cvm.dfp", year="vinte-e-quatro")


def test_intervalo_invertido_falha(cvm):
    with pytest.raises(SourceError, match="invertido"):
        cvm.resolve("cvm.dfp", year="2024-2020")


def test_parametro_desconhecido_falha(cvm):
    with pytest.raises(SourceError, match="desconhecidos"):
        cvm.resolve("cvm.dfp", year="2024", trimestre="1")


def test_year_obrigatorio(cvm):
    with pytest.raises(SourceError, match="exige o parametro 'year'"):
        cvm.resolve("cvm.dfp")


def test_dataset_desconhecido_falha(cvm):
    with pytest.raises(UnknownDatasetError, match="desconhecido"):
        cvm.resolve("cvm.inexistente", year="2024")


def test_tier_e_oficial(cvm):
    assert cvm.tier is SourceTier.PRIMARY_OFFICIAL
    assert all(ds.tier is SourceTier.PRIMARY_OFFICIAL for ds in cvm.datasets())

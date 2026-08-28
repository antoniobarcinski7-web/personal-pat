"""Catalogo -> `CompanyProfile`, nas duas jurisdicoes.

O ponto do teste nao e que a leitura funciona: e que a MESMA funcao, sem
ramo por pais, produz o perfil das duas. Um `if jurisdiction == ...` aqui
seria o comeco de um Opportunity brasileiro.
"""

from __future__ import annotations

import pytest

from pat.contracts.entities import EntityIdentity
from pat.opportunity.company import UnknownEntity, profile_for
from pat.store.entity import upsert_identities


@pytest.fixture
def catalogo(warehouse):
    upsert_identities(
        warehouse,
        [
            EntityIdentity(
                entity_id="br:cnpj:47508411000156",
                jurisdiction="BR",
                scheme="cnpj",
                local_id="47508411000156",
                display_name="CIA BRASILEIRA DE DISTRIBUICAO",
                is_primary=True,
            ),
            EntityIdentity(
                entity_id="br:cnpj:47508411000156",
                jurisdiction="BR",
                scheme="cod_cvm",
                local_id="14826",
                display_name="GPA",
            ),
            EntityIdentity(
                entity_id="us:cik:0000050863",
                jurisdiction="US",
                scheme="cik",
                local_id="0000050863",
                display_name="INTEL CORP",
                is_primary=True,
            ),
            EntityIdentity(
                entity_id="us:cik:0000050863",
                jurisdiction="US",
                scheme="ticker",
                local_id="INTC",
                display_name="Intel",
            ),
        ],
    )
    return warehouse


@pytest.mark.parametrize(
    ("entity_id", "jurisdicao", "nome", "apelidos"),
    [
        (
            "br:cnpj:47508411000156",
            "BR",
            "CIA BRASILEIRA DE DISTRIBUICAO",
            (("cnpj", "47508411000156"), ("cod_cvm", "14826")),
        ),
        (
            "us:cik:0000050863",
            "US",
            "INTEL CORP",
            (("cik", "0000050863"), ("ticker", "INTC")),
        ),
    ],
)
def test_perfil_das_duas_jurisdicoes(catalogo, entity_id, jurisdicao, nome, apelidos):
    perfil = profile_for(catalogo, entity_id)
    assert perfil.entity_id == entity_id
    assert perfil.jurisdiction == jurisdicao
    # O nome vem do identificador primario, nao do primeiro em ordem alfabetica.
    assert perfil.display_name == nome
    assert perfil.identifiers == apelidos


def test_entidade_desconhecida_e_erro_nomeado(warehouse):
    with pytest.raises(UnknownEntity, match="nao tem identidade no catalogo"):
        profile_for(warehouse, "br:cnpj:00000000000000")


def test_jurisdicao_nao_e_derivada_do_prefixo(catalogo):
    """Le o campo, nunca o prefixo do `entity_id`.

    Um `entity_id` opaco continua opaco: o dia em que o formato mudar, ler o
    prefixo daria a jurisdicao errada em silencio.
    """
    upsert_identities(
        catalogo,
        [
            EntityIdentity(
                entity_id="opaco-sem-prefixo",
                jurisdiction="US",
                scheme="cik",
                local_id="0000320193",
                display_name="SEM PREFIXO",
                is_primary=True,
            )
        ],
    )
    assert profile_for(catalogo, "opaco-sem-prefixo").jurisdiction == "US"

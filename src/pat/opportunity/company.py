"""Catalogo do PAT -> `CompanyProfile`.

E o unico ponto do Opportunity que le a tabela `entity` diretamente, e existe
para que nenhum outro leia: identidade se resolve por `pat.store.entity`, que
ja e a unica porta pela qual `--cod-cvm` e `--cik` viram `entity_id`.

Nada aqui sabe o que e CVM ou SEC. A jurisdicao vem do campo `jurisdiction` da
entidade, e os `identifiers` saem como (scheme, local_id) sem que este modulo
precise saber que `cod_cvm` e brasileiro. Uma terceira jurisdicao nao muda
uma linha deste arquivo - que e o teste real da abstracao.
"""

from __future__ import annotations

import duckdb

from pat.contracts.opportunity import CompanyProfile
from pat.store.entity import identities_for

__all__ = ["UnknownEntity", "profile_for"]


class UnknownEntity(LookupError):
    """`entity_id` sem nenhuma identidade no catalogo.

    Nao e o mesmo que "empresa sem dados": uma empresa pode existir no
    catalogo e nao ter fato nenhum ainda, e isso e um workspace `draft`
    perfeitamente legitimo. O que nao da para fazer e abrir investigacao sobre
    uma entidade que o sistema nunca viu - nao havendo identidade, tambem nao
    ha jurisdicao, e sem jurisdicao nao ha mapeamento, moeda nem corpus.
    """


def profile_for(conn: duckdb.DuckDBPyConnection, entity_id: str) -> CompanyProfile:
    """Perfil congelado da empresa, como o catalogo a conhece agora."""
    identidades = identities_for(conn, entity_id)
    if not identidades:
        raise UnknownEntity(
            f"{entity_id} nao tem identidade no catalogo. "
            "Rode `pat build` para a companhia antes de abrir o workspace."
        )
    # O primario manda no nome de exibicao: ele e o identificador canonico da
    # jurisdicao (CNPJ no Brasil, CIK nos EUA), e os outros sao apelidos.
    primario = next((i for i in identidades if i.is_primary), identidades[0])
    jurisdicoes = {i.jurisdiction for i in identidades}
    if len(jurisdicoes) > 1:
        raise UnknownEntity(
            f"{entity_id} tem identidades em jurisdicoes diferentes {sorted(jurisdicoes)}. "
            "Uma entidade pertence a uma jurisdicao; isto e erro de catalogo."
        )
    return CompanyProfile(
        entity_id=entity_id,
        display_name=primario.display_name,
        jurisdiction=primario.jurisdiction,
        identifiers=tuple(sorted((i.scheme, i.local_id) for i in identidades)),
    )

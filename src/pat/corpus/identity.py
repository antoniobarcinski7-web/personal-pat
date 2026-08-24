"""Identidades enderecadas por conteudo da camada de corpus.

Mesmo desenho de `research/canonical.py`: o serializador generico mora em
`pat.canonical`, e aqui ficam as identidades *tipadas* desta camada - o que
entra em cada hash, e principalmente o que fica de fora dele.
"""

from __future__ import annotations

from pat.canonical import sha256_of
from pat.contracts.corpus import EvidenceQuery, UnitLocator

__all__ = ["query_id", "unit_id"]


def unit_id(*, document_id: str, extraction_version: str, locator: UnitLocator) -> str:
    """Identidade de uma unidade citavel.

    Os tres campos sao necessarios e nenhum e dispensavel:

    - `document_id` porque o mesmo trecho em documentos diferentes e outra
      citacao (uma reapresentacao repete paragrafos inteiros, e as duas
      versoes precisam continuar distinguiveis);
    - `extraction_version` porque reprocessar com extrator novo tem que criar
      unidade nova ao lado da antiga, e nao redefinir em silencio o que uma
      citacao de seis meses atras queria dizer;
    - `locator` porque e ele que enderecça o trecho dentro do documento.

    O TEXTO fica de fora, e isso e deliberado: se ele entrasse, a identidade
    seria redundante (o mesmo trio ja determina o texto) e a unidade deixaria
    de ter identidade *posicional* - duas paginas com o mesmo cabecalho
    colapsariam numa unidade so, e a citacao perderia de que pagina veio.
    """
    return sha256_of(
        {
            "document_id": document_id,
            "extraction_version": extraction_version,
            "locator": locator,
        }
    )


def query_id(query: EvidenceQuery) -> str:
    """Identidade de uma consulta ao corpus.

    Tudo que muda o conjunto devolvido entra. Nada mais entra - em especial,
    nem o instante da consulta nem o `index_version`: a mesma pergunta feita
    duas vezes e a mesma pergunta, e a versao do indice e propriedade do
    `EvidenceResult`, nao da pergunta que alguem fez.
    """
    return sha256_of(query)

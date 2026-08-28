"""A interface de documento do Opportunity. Sem jurisdicao, sem excecao.

Um `Protocol`, e nao uma classe base: o Opportunity nunca instancia um
provider concreto - `providers.py` faz isso, e e o unico arquivo desta camada
autorizado a saber que regimes existem. Aqui so mora a forma.

O inventario e generico de proposito
------------------------------------
`stored_documents` e `inventory` nao passam por provider nenhum: um documento
que ja esta no corpus e citavel independentemente de quem o trouxe, e a
consulta e a mesma nos dois regimes. Fazer isso passar pelo adapter obrigaria
cada regime novo a reimplementar uma leitura que nao tem nada de regional -
e a segunda implementacao divergiria da primeira.

O que o provider faz e so o que e regional: descobrir o que existe e trazer.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import duckdb
from pydantic import Field

from pat.contracts.common import Frozen
from pat.contracts.corpus import DocumentKind
from pat.contracts.opportunity import CompanyProfile
from pat.contracts.opportunity.documents import (
    DiscoveredDocument,
    ProviderProfile,
    ProviderUnavailable,
)

__all__ = [
    "DocumentInventory",
    "DocumentProvider",
    "StoredDocument",
    "inventory",
    "stored_documents",
]


class StoredDocument(Frozen):
    """Um documento que ja esta no corpus, com o que decide se ele e citavel.

    Carrega os campos que o milestone exige preservados - fonte, identidade,
    tipo, datas, URL, hash, versao de extracao, jurisdicao - e mais um que
    nenhum deles pede e que e o que mais importa aqui: `extraction_failed`.

    Um documento armazenado e nao extraido continua no inventario, marcado.
    Some-lo da lista faria a contagem de "documentos disponiveis" parecer
    menor e correta, quando ela esta menor e errada: os bytes estao la, e o
    que falta e o sistema saber ler.
    """

    document_id: str = Field(min_length=1, description="sha256 dos bytes; e a identidade")
    jurisdiction: str = Field(min_length=2, max_length=2)
    provider_id: str
    dataset_id: str
    kind: DocumentKind
    title: str
    published_at: date
    source_url: str
    byte_size: int = Field(ge=0)
    units: int = Field(default=0, ge=0)
    extraction_versions: tuple[str, ...] = ()
    extraction_failed: bool = False
    failure_reason: str | None = None

    @property
    def is_citable(self) -> bool:
        """Ha unidade extraida. Sem isso, o documento existe e nao se cita."""
        return self.units > 0


class DocumentInventory(Frozen):
    """O que o corpus tem sobre uma empresa, e o que ele nao conseguiu ler.

    `failed` fica ao lado de `documents` porque cobertura que so mostra o que
    deu certo mente sobre si mesma, e a mentira se le como evidencia de
    ausencia.
    """

    entity_id: str
    jurisdiction: str
    as_of: date
    documents: tuple[StoredDocument, ...] = ()
    provider: ProviderProfile | None = None

    @property
    def citable(self) -> tuple[StoredDocument, ...]:
        return tuple(d for d in self.documents if d.is_citable)

    @property
    def failed(self) -> tuple[StoredDocument, ...]:
        return tuple(d for d in self.documents if d.extraction_failed)

    @property
    def by_kind(self) -> tuple[tuple[DocumentKind, int], ...]:
        contagem: dict[DocumentKind, int] = {}
        for d in self.documents:
            contagem[d.kind] = contagem.get(d.kind, 0) + 1
        return tuple(sorted(contagem.items(), key=lambda kv: kv[0].value))

    @property
    def published_range(self) -> tuple[date, date] | None:
        if not self.documents:
            return None
        datas = [d.published_at for d in self.documents]
        return (min(datas), max(datas))


@runtime_checkable
class DocumentProvider(Protocol):
    """O que um regime precisa oferecer para o agente pesquisar texto nele.

    Duas operacoes regionais, e as duas podem recusar com motivo nomeado.
    Nenhuma devolve lista vazia por falta de capacidade: lista vazia quer
    dizer "procurei e nao ha", que e um achado, e nao "nao sei procurar", que
    e uma lacuna.
    """

    @property
    def profile(self) -> ProviderProfile:
        """O que este provider sabe e nao sabe fazer. Declarado, nao inferido."""
        ...

    def discover(
        self,
        conn: duckdb.DuckDBPyConnection,
        *,
        company: CompanyProfile,
        published_from: date,
        published_to: date,
    ) -> tuple[DiscoveredDocument, ...] | ProviderUnavailable:
        """O que a empresa publicou na janela, segundo o indice do regime."""
        ...

    def fetch(
        self,
        conn: duckdb.DuckDBPyConnection,
        *,
        company: CompanyProfile,
        documents: tuple[DiscoveredDocument, ...],
        limit: int | None = None,
    ) -> tuple[StoredDocument, ...] | ProviderUnavailable:
        """Traz os bytes, extrai e indexa. Idempotente."""
        ...


def stored_documents(
    conn: duckdb.DuckDBPyConnection, *, entity_id: str, as_of: date
) -> tuple[StoredDocument, ...]:
    """Documentos ja no corpus, publicados ate `as_of`.

    O corte por `as_of` esta aqui, e nao no que chama: um inventario que
    listasse documento posterior ao `as_of` faria o agente planejar uma
    citacao que a busca depois recusaria - e a explicacao pareceria um bug.
    """
    from pat.store.corpus import documents_for_entity, extraction_failures

    # O corte por `as_of` e da consulta, e nao um filtro depois: deixar a
    # lista completa passar por aqui e cortar em Python daria o mesmo
    # resultado hoje e outro no dia em que alguem acrescentasse um `limit`.
    documentos = documents_for_entity(conn, entity_id, as_of=as_of)
    falhas = {
        document_id: (reason, message)
        for document_id, reason, message, _ in extraction_failures(conn, entity_id)
    }

    linhas = conn.execute(
        """
        SELECT document_id, COUNT(*), list(DISTINCT extraction_version)
        FROM document_unit GROUP BY document_id
        """
    ).fetchall()
    unidades = {row[0]: (int(row[1]), tuple(sorted(row[2]))) for row in linhas}

    jurisdicao = _jurisdiction_of(conn, entity_id)
    saida = []
    for doc in documentos:
        contagem, versoes = unidades.get(doc.document_id, (0, ()))
        falha = falhas.get(doc.document_id)
        saida.append(
            StoredDocument(
                document_id=doc.document_id,
                jurisdiction=jurisdicao,
                provider_id=doc.provider_id,
                dataset_id=doc.dataset_id,
                kind=doc.kind,
                title=doc.title,
                published_at=doc.published_at,
                source_url=doc.source_url,
                byte_size=doc.byte_size,
                units=contagem,
                extraction_versions=versoes,
                extraction_failed=falha is not None,
                failure_reason=falha[0] if falha else None,
            )
        )
    return tuple(sorted(saida, key=lambda d: (d.published_at, d.document_id)))


def inventory(
    conn: duckdb.DuckDBPyConnection,
    *,
    company: CompanyProfile,
    as_of: date,
    provider: DocumentProvider | None = None,
) -> DocumentInventory:
    """O inventario completo, com o perfil do provider quando ha um.

    O perfil entra no inventario de proposito: quem le "3 documentos" precisa
    saber, na mesma estrutura, se sao 3 de 3 ou 3 de um regime cuja descoberta
    nao existe.
    """
    return DocumentInventory(
        entity_id=company.entity_id,
        jurisdiction=company.jurisdiction,
        as_of=as_of,
        documents=stored_documents(conn, entity_id=company.entity_id, as_of=as_of),
        provider=provider.profile if provider is not None else None,
    )


def _jurisdiction_of(conn: duckdb.DuckDBPyConnection, entity_id: str) -> str:
    from pat.store.entity import identities_for

    identidades = identities_for(conn, entity_id)
    return identidades[0].jurisdiction if identidades else "??"

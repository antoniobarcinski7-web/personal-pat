"""Contratos da camada bronze.

Duas nocoes deliberadamente separadas:

`RawDocument`  - um conteudo unico, identificado pelo SHA-256 dos bytes.
                 Imutavel. Armazenado uma unica vez.

`Retrieval`    - uma *observacao*: em tal instante, tal URL de tal provider
                 devolveu tal conteudo. Muitos retrievals podem apontar para
                 o mesmo RawDocument.

Essa separacao e o que torna reapresentacao detectavel. A CVM reescreve o
mesmo arquivo ZIP quando uma companhia reapresenta demonstracoes: a URL nao
muda, os bytes mudam. Dois retrievals da mesma URL apontando para hashes
diferentes sao a evidencia primaria do evento, e ambas as versoes do
documento continuam disponiveis para reprocessamento.
"""

from __future__ import annotations

from pydantic import Field

from pat.contracts.common import AwareDatetime, Frozen, Sha256, SourceTier


class ResourceRef(Frozen):
    """Um recurso que um provider sabe buscar, antes de ter sido buscado."""

    provider_id: str
    dataset_id: str = Field(description="Dataset dentro do provider, ex. 'cvm.dfp'")
    resource_key: str = Field(
        description="Chave estavel do recurso dentro do dataset, ex. '2024'. "
        "Junto com dataset_id identifica o recurso logico ao longo do tempo."
    )
    url: str
    media_type: str | None = None
    params: tuple[tuple[str, str], ...] = Field(
        default=(), description="Parametros de resolucao, como pares ordenados"
    )


class HttpMeta(Frozen):
    """Metadados de transporte preservados para auditoria.

    `etag` e `last_modified` sao o que permite explicar, meses depois, por que
    um mesmo recurso passou a ter conteudo diferente.
    """

    status_code: int
    etag: str | None = None
    last_modified: str | None = None
    content_length: int | None = None
    content_type: str | None = None
    final_url: str | None = Field(default=None, description="Apos redirecionamentos")


class RawDocument(Frozen):
    """Conteudo imutavel armazenado no bronze. Identidade = hash dos bytes."""

    content_sha256: Sha256
    size_bytes: int = Field(ge=0)
    media_type: str | None = None
    storage_path: str = Field(description="Caminho relativo a raiz do bronze")
    first_seen_at: AwareDatetime
    first_seen_run_id: str


class Retrieval(Frozen):
    """Uma busca concreta: quem, onde, quando, e o que voltou."""

    retrieval_id: str
    content_sha256: Sha256

    provider_id: str
    source_tier: SourceTier
    dataset_id: str
    resource_key: str

    url: str = Field(description="URL original solicitada, preservada literalmente")
    requested_at: AwareDatetime
    retrieved_at: AwareDatetime
    """Data de recuperacao: limite superior do knowledge_date de tudo que for
    extraido deste documento."""

    http: HttpMeta
    run_id: str
    provider_version: str = Field(
        description="Versao do provider que fez a busca; muda quando a logica "
        "de resolucao de URL muda, para que o passado permaneca explicavel."
    )

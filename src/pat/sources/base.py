"""Abstracao de fontes de dados.

    SourceProvider (ABC)
    |- PublicSourceProvider      HTTP publico, sem credencial
    |  |- CVMProvider            implementado na Fase 0
    |  |- B3Provider             pendente
    |  |- BCBProvider            pendente
    |  |- IBGEProvider           pendente
    |  |- CompanyIRProvider      pendente
    |- LicensedSourceProvider    ponto de extensao; nao implementado na Fase 0

O contrato existe para que a logica de pesquisa nunca conheca o provider.
Camadas acima pedem um dataset e recebem `ResourceRef` e bytes com
procedencia anexada; trocar CVM por um vendor licenciado e adicionar uma
subclasse, nao reescrever o sistema.

Regra que a abstracao impoe: um provider **busca e descreve**, nunca
interpreta. Parsing pertence a camada silver. Isso e o que permite
reprocessar anos de documentos com um parser corrigido sem re-baixar nada e
sem depender de um servidor externo ter a mesma resposta de antes.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import ClassVar

import httpx
from pydantic import Field

from pat.contracts.common import AwareDatetime, Frozen, SourceTier
from pat.contracts.documents import HttpMeta, ResourceRef


class DatasetSpec(Frozen):
    """Descricao estatica de um dataset oferecido por um provider."""

    dataset_id: str
    title: str
    tier: SourceTier
    params: tuple[str, ...] = Field(
        default=(), description="Nomes dos parametros exigidos por resolve()"
    )
    homepage: str | None = None
    license: str | None = None
    notes: str | None = None


class FetchResult(Frozen):
    """Bytes crus mais tudo que e necessario para registrar procedencia."""

    ref: ResourceRef
    content: bytes
    http: HttpMeta
    requested_at: AwareDatetime
    retrieved_at: AwareDatetime


class SourceError(RuntimeError):
    pass


class UnknownDatasetError(SourceError):
    pass


class SourceProvider(ABC):
    """Interface que toda fonte de dados implementa."""

    provider_id: ClassVar[str]
    tier: ClassVar[SourceTier]
    version: ClassVar[str]
    """Muda quando a logica de resolucao de recurso muda. Gravado em cada
    retrieval para que buscas antigas permanecam explicaveis."""

    @abstractmethod
    def datasets(self) -> tuple[DatasetSpec, ...]:
        """Datasets que este provider sabe servir."""

    @abstractmethod
    def resolve(self, dataset_id: str, **params: str) -> tuple[ResourceRef, ...]:
        """Traduz um pedido logico em recursos concretos e enderecaveis."""

    @abstractmethod
    def fetch(self, ref: ResourceRef) -> FetchResult:
        """Busca os bytes de um recurso. Nao interpreta o conteudo."""

    def spec(self, dataset_id: str) -> DatasetSpec:
        for ds in self.datasets():
            if ds.dataset_id == dataset_id:
                return ds
        known = ", ".join(d.dataset_id for d in self.datasets())
        raise UnknownDatasetError(f"dataset {dataset_id!r} desconhecido em {self.provider_id!r}. Disponiveis: {known}")


class PublicSourceProvider(SourceProvider):
    """Base para fontes publicas via HTTP, sem autenticacao.

    Implementa `fetch` de forma generica com timeout, retry e rate limit.
    Subclasses so precisam declarar `datasets()` e `resolve()`.

    A busca e sempre incondicional (sem If-None-Match): os bytes voltam
    inteiros e sao verificados por hash a cada vez. Um 304 economizaria
    banda, mas custaria a capacidade de detectar que o conteudo mudou, que e
    justamente o sinal de reapresentacao que o sistema precisa observar.
    """

    timeout_s: ClassVar[float] = 120.0
    max_attempts: ClassVar[int] = 4
    backoff_base_s: ClassVar[float] = 2.0
    min_interval_s: ClassVar[float] = 1.0
    """Intervalo minimo entre requisicoes ao mesmo host. Fontes publicas
    brasileiras sao infraestrutura compartilhada; ser educado e requisito."""

    user_agent: ClassVar[str] = "personal-pat/0.1 (pesquisa pessoal; contato via repositorio)"

    _RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._last_request_at: float | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.min_interval_s:
                time.sleep(self.min_interval_s - elapsed)
        self._last_request_at = time.monotonic()

    def fetch(self, ref: ResourceRef) -> FetchResult:
        client = self._get_client()
        requested_at = datetime.now(UTC)
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            try:
                response = client.get(ref.url)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code not in self._RETRY_STATUS:
                    if response.status_code != 200:
                        raise SourceError(
                            f"{self.provider_id}: HTTP {response.status_code} em {ref.url}"
                        )
                    return FetchResult(
                        ref=ref,
                        content=response.content,
                        http=HttpMeta(
                            status_code=response.status_code,
                            etag=response.headers.get("etag"),
                            last_modified=response.headers.get("last-modified"),
                            content_length=(
                                int(response.headers["content-length"])
                                if "content-length" in response.headers
                                else None
                            ),
                            content_type=response.headers.get("content-type"),
                            final_url=str(response.url),
                        ),
                        requested_at=requested_at,
                        retrieved_at=datetime.now(UTC),
                    )
                last_error = SourceError(f"HTTP {response.status_code}")

            if attempt < self.max_attempts:
                time.sleep(self.backoff_base_s ** attempt)

        raise SourceError(
            f"{self.provider_id}: falha ao buscar {ref.url} apos "
            f"{self.max_attempts} tentativas: {last_error}"
        ) from last_error


class LicensedSourceProvider(SourceProvider):
    """Ponto de extensao para provedores pagos. Nao implementado na Fase 0.

    Existe para fixar agora as obrigacoes que um vendor traz, de modo que
    plugar Bloomberg, LSEG, FactSet ou Capital IQ mais tarde seja uma
    subclasse e nao uma reforma do nucleo:

    - `tier` e sempre VENDOR_LICENSED, o que torna visivel na auditoria
      quanto de uma conclusao dependeu de dado licenciado;
    - `redistributable` informa as camadas acima se o valor bruto pode
      aparecer em uma saida exportada ou apenas ser usado em derivacoes;
    - credenciais vem do ambiente, nunca de arquivo de configuracao
      versionado.

    A camada bronze funciona igual: bytes crus, hash e retrieval registrado.
    O que muda e a politica de retencao e exportacao, nao a arquitetura.
    """

    tier: ClassVar[SourceTier] = SourceTier.VENDOR_LICENSED

    @property
    @abstractmethod
    def license_id(self) -> str:
        """Contrato de licenca sob o qual estes dados sao acessados."""

    @property
    @abstractmethod
    def redistributable(self) -> bool:
        """Se o valor bruto pode sair do sistema em uma exportacao."""

"""Registro de providers disponiveis.

Ponto unico de resolucao `provider_id -> instancia`. Camadas acima pedem um
dataset pelo id e nunca importam uma classe de provider diretamente; e o que
mantem a logica de pesquisa independente da fonte.
"""

from __future__ import annotations

from pat.sources.base import DatasetSpec, SourceProvider, UnknownDatasetError
from pat.sources.public.cvm import CVMProvider
from pat.sources.public.sec import SECProvider

_PROVIDER_CLASSES: tuple[type[SourceProvider], ...] = (CVMProvider, SECProvider)


class Registry:
    def __init__(self, providers: tuple[SourceProvider, ...] | None = None) -> None:
        instances = providers if providers is not None else tuple(c() for c in _PROVIDER_CLASSES)
        self._by_id: dict[str, SourceProvider] = {p.provider_id: p for p in instances}

    def providers(self) -> tuple[SourceProvider, ...]:
        return tuple(self._by_id.values())

    def provider(self, provider_id: str) -> SourceProvider:
        try:
            return self._by_id[provider_id]
        except KeyError:
            known = ", ".join(sorted(self._by_id))
            raise UnknownDatasetError(f"provider {provider_id!r} desconhecido. Disponiveis: {known}") from None

    def datasets(self) -> tuple[tuple[SourceProvider, DatasetSpec], ...]:
        return tuple((p, ds) for p in self._by_id.values() for ds in p.datasets())

    def for_dataset(self, dataset_id: str) -> SourceProvider:
        """Resolve um dataset_id para o provider que o serve."""
        for provider, spec in self.datasets():
            if spec.dataset_id == dataset_id:
                return provider
        known = ", ".join(sorted(ds.dataset_id for _, ds in self.datasets()))
        raise UnknownDatasetError(f"dataset {dataset_id!r} desconhecido. Disponiveis: {known}")

    def close(self) -> None:
        for provider in self._by_id.values():
            if (close := getattr(provider, "close", None)) is not None:
                close()

"""Camada de fontes: busca bytes com procedencia, nunca interpreta."""

from pat.sources.base import (
    DatasetSpec,
    FetchResult,
    LicensedSourceProvider,
    PublicSourceProvider,
    SourceError,
    SourceProvider,
    UnknownDatasetError,
)
from pat.sources.registry import Registry

__all__ = [
    "DatasetSpec",
    "FetchResult",
    "LicensedSourceProvider",
    "PublicSourceProvider",
    "Registry",
    "SourceError",
    "SourceProvider",
    "UnknownDatasetError",
]

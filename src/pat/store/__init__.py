"""Camada de armazenamento: bronze imutavel e catalogo DuckDB."""

from pat.store import gold, silver
from pat.store.bronze import BronzeStore, BronzeStoreError, PutResult, sha256_bytes
from pat.store.catalog import Catalog, ResourceVersion
from pat.store.db import connect, migrate

__all__ = [
    "BronzeStore",
    "BronzeStoreError",
    "Catalog",
    "PutResult",
    "ResourceVersion",
    "connect",
    "gold",
    "migrate",
    "sha256_bytes",
    "silver",
]

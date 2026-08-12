"""Orquestracao da ingestao: provider -> bronze -> catalogo.

Fluxo unico, deliberadamente curto, por onde todo dado entra no sistema:

    resolve   provider traduz o pedido logico em recursos concretos
    fetch     bytes crus + metadados de transporte
    put       bronze grava (ou reconhece) o conteudo por hash
    record    catalogo registra documento e retrieval

Nada aqui interpreta conteudo. Parsing e da camada silver (Fase 1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pat.contracts.documents import RawDocument, ResourceRef, Retrieval
from pat.contracts.lineage import Run
from pat.sources.registry import Registry
from pat.store.bronze import BronzeStore
from pat.store.catalog import Catalog


@dataclass(frozen=True)
class IngestOutcome:
    ref: ResourceRef
    document: RawDocument
    retrieval: Retrieval
    is_new_content: bool
    """False quando os bytes ja estavam no bronze. O retrieval e registrado
    de qualquer forma: saber que o conteudo *nao* mudou em determinada data
    e informacao, nao ruido."""


def ingest_dataset(
    dataset_id: str,
    params: dict[str, str],
    *,
    registry: Registry,
    store: BronzeStore,
    catalog: Catalog,
    run: Run,
) -> list[IngestOutcome]:
    provider = registry.for_dataset(dataset_id)
    spec = provider.spec(dataset_id)
    refs = provider.resolve(dataset_id, **params)

    outcomes: list[IngestOutcome] = []
    for ref in refs:
        result = provider.fetch(ref)

        put = store.put(
            result.content,
            run_id=run.run_id,
            media_type=ref.media_type or result.http.content_type,
            provenance={
                "provider_id": provider.provider_id,
                "provider_version": provider.version,
                "source_tier": str(spec.tier),
                "dataset_id": dataset_id,
                "resource_key": ref.resource_key,
                "url": ref.url,
                "retrieved_at": result.retrieved_at.isoformat(),
                "http": result.http.model_dump(mode="json"),
                "run_id": run.run_id,
            },
        )

        retrieval = Retrieval(
            retrieval_id=uuid.uuid4().hex,
            content_sha256=put.document.content_sha256,
            provider_id=provider.provider_id,
            source_tier=spec.tier,
            dataset_id=dataset_id,
            resource_key=ref.resource_key,
            url=ref.url,
            requested_at=result.requested_at,
            retrieved_at=result.retrieved_at,
            http=result.http,
            run_id=run.run_id,
            provider_version=provider.version,
        )

        catalog.record_document(put.document)
        catalog.record_retrieval(retrieval)

        outcomes.append(
            IngestOutcome(
                ref=ref,
                document=put.document,
                retrieval=retrieval,
                is_new_content=put.is_new,
            )
        )

    return outcomes

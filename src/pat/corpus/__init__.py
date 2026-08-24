"""L1.5 - corpus qualitativo: catalogo -> bronze -> unidades -> indice.

Fluxo unico, deliberadamente curto, por onde todo documento textual entra:

    catalogo   `cvm.ipe` do ano ja esta no bronze; `parse.cvm_ipe` o le
    fetch      `cvm.ipe_doc` busca os bytes de cada documento (ingest.py)
    document   bytes viram `SourceDocument` - interpretacao tipada do blob
    extract    bytes viram `DocumentUnit`s, ou uma falha nomeada
    index      unidades viram tokens

Cada etapa e idempotente e append-only. Rodar duas vezes nao duplica nada e
nao sobrescreve nada: os ids sao deterministicos e derivados do conteudo.

O bronze continua sendo a fonte de verdade. Documento, unidade e indice sao
derivados, nesta ordem, e cada um pode ser reconstruido a partir do anterior
sem rede.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import duckdb

from pat.contracts.corpus import (
    DateBasis,
    DocumentUnit,
    ExtractionFailureReason,
    SourceDocument,
)
from pat.contracts.lineage import Run
from pat.corpus.extract import EXTRACTION_VERSION, extract, extract_pdf_page_texts
from pat.corpus.index import build_index
from pat.ingest import ingest_dataset
from pat.parse.cvm_ipe import EXTRACTOR_VERSION, IpeCatalogEntry, parse_catalog
from pat.sources.registry import Registry
from pat.store import corpus as corpus_store
from pat.store.bronze import BronzeStore, BronzeStoreError
from pat.store.catalog import Catalog

__all__ = [
    "CATALOG_DATASET",
    "DOCUMENT_DATASET",
    "SyncOutcome",
    "catalog_entries",
    "sync_documents",
    "verify_unit",
]

CATALOG_DATASET = "cvm.ipe"
DOCUMENT_DATASET = "cvm.ipe_doc"

_LANGUAGE = "pt-BR"
"""Idioma do IPE. Declarado, e nao detectado: deteccao de idioma e um
classificador probabilistico, e um rotulo errado em silencio faria a busca
falhar de um jeito que ninguem consegue diagnosticar. Documento em outro
idioma entra quando houver base declarada para dizer qual e."""


@dataclass
class SyncOutcome:
    """O que a sincronizacao fez - inclusive o que ela nao conseguiu fazer."""

    entity_id: str
    catalog_entries: int = 0
    documents_fetched: int = 0
    documents_new: int = 0
    units_written: int = 0
    units_indexed: int = 0
    failures: list[tuple[str, ExtractionFailureReason, str]] = field(default_factory=list)
    skipped_existing: int = 0

    @property
    def failed(self) -> int:
        return len(self.failures)


def catalog_entries(
    conn: duckdb.DuckDBPyConnection,
    bronze: BronzeStore,
    *,
    year: int,
    cod_cvm: int,
) -> list[IpeCatalogEntry]:
    """Le do bronze o catalogo IPE do ano. Sem rede.

    Usa a versao de conteudo MAIS RECENTE do recurso: o catalogo e um indice
    corrente do que foi entregue, e nao um fato bitemporal. Os documentos
    apontados e que carregam data de entrega, e sao eles que sustentam o
    `AS OF`. Versoes antigas do catalogo continuam no bronze e continuam
    processaveis - o que muda e so qual delas e lida por default.
    """
    rows = conn.execute(
        """
        SELECT content_sha256, MAX(retrieved_at) AS latest
        FROM retrieval
        WHERE dataset_id = ? AND resource_key = ?
        GROUP BY content_sha256
        ORDER BY latest DESC
        LIMIT 1
        """,
        [CATALOG_DATASET, str(year)],
    ).fetchall()
    if not rows:
        raise LookupError(
            f"catalogo {CATALOG_DATASET} de {year} nao esta no bronze. "
            f"Rode `pat fetch {CATALOG_DATASET} --year {year}` primeiro."
        )
    return parse_catalog(bronze.read(rows[0][0]), cod_cvm=cod_cvm)


def sync_documents(
    conn: duckdb.DuckDBPyConnection,
    *,
    entity_id: str,
    entries: list[IpeCatalogEntry],
    registry: Registry,
    bronze: BronzeStore,
    catalog: Catalog,
    run: Run,
    limit: int | None = None,
) -> SyncOutcome:
    """Catalogo -> bronze -> documentos -> unidades -> indice.

    Idempotente em cada etapa. Um documento cujos bytes ja estao no bronze
    ainda gera um `Retrieval` novo - saber que o conteudo *nao* mudou naquela
    data e informacao - mas nao reextrai nem reindexa.
    """
    outcome = SyncOutcome(entity_id=entity_id, catalog_entries=len(entries))
    selected = entries if limit is None else entries[:limit]

    for entry in selected:
        results = ingest_dataset(
            DOCUMENT_DATASET,
            {"url": entry.url, "protocolo": entry.protocolo},
            registry=registry,
            store=bronze,
            catalog=catalog,
            run=run,
        )
        for result in results:
            outcome.documents_fetched += 1
            document_id = result.document.content_sha256

            if corpus_store.read_document(conn, document_id) is not None:
                outcome.skipped_existing += 1
                continue

            payload = bronze.read(document_id)
            document = _to_source_document(
                entry,
                entity_id=entity_id,
                document_id=document_id,
                payload=payload,
                retrieval_id=result.retrieval.retrieval_id,
                source_tier=result.retrieval.source_tier,
                first_seen_at=result.document.first_seen_at,
                first_seen_run_id=run.run_id,
            )
            outcome.documents_new += corpus_store.write_documents(conn, [document])

            extraction = extract(document_id, payload)
            if extraction.failure is not None:
                corpus_store.write_failure(conn, extraction.failure)
                outcome.failures.append(
                    (document_id, extraction.failure.reason, entry.title)
                )
                continue
            outcome.units_written += corpus_store.write_units(conn, extraction.units)

    outcome.units_indexed = build_index(conn)
    return outcome


def _to_source_document(
    entry: IpeCatalogEntry,
    *,
    entity_id: str,
    document_id: str,
    payload: bytes,
    retrieval_id: str,
    source_tier,
    first_seen_at: datetime,
    first_seen_run_id: str,
) -> SourceDocument:
    from pat.corpus.extract import sniff_media_type

    return SourceDocument(
        document_id=document_id,
        entity_id=entity_id,
        kind=entry.kind,
        title=entry.title,
        language=_LANGUAGE,
        source_tier=source_tier,
        # `Data_Entrega` e o que a companhia declarou ao regulador como data de
        # entrega. E metadado de protocolo, e nao leitura do documento - por
        # isso a base e FILING_METADATA, e ela viaja ate a citacao.
        published_at=entry.delivered_at,
        published_at_basis=DateBasis.FILING_METADATA,
        reference_date=entry.reference_date,
        reference_date_basis=(
            DateBasis.FILING_METADATA if entry.reference_date is not None else None
        ),
        media_type=sniff_media_type(payload) or "application/octet-stream",
        byte_size=len(payload),
        provider_id="cvm",
        dataset_id=DOCUMENT_DATASET,
        resource_key=entry.protocolo,
        source_url=entry.url,
        retrieval_id=retrieval_id,
        origin_category=entry.categoria,
        origin_version=entry.versao or None,
        first_seen_at=first_seen_at,
        first_seen_run_id=first_seen_run_id,
    )


@dataclass(frozen=True)
class UnitVerification:
    """O resultado de reconferir uma unidade contra os bytes do bronze."""

    unit_id: str
    document_id: str
    blob_found: bool
    blob_sha256_matches: bool
    text_matches: bool

    @property
    def ok(self) -> bool:
        return self.blob_found and self.blob_sha256_matches and self.text_matches


def verify_unit(
    unit: DocumentUnit, document: SourceDocument, bronze: BronzeStore
) -> UnitVerification:
    """Reextrai do blob e confere que o texto guardado veio mesmo dali.

    E a prova de ponta a ponta do lado qualitativo, e o analogo exato de
    `AsOf.verify_provenance` no lado numerico: nao "o registro diz que veio
    daqui", e sim "reprocessei os bytes e deu a mesma coisa".

    Repare que a conferencia so e possivel porque `extraction_version` esta
    gravado na unidade. Sem ele, reextrair com a biblioteca de hoje e comparar
    com um texto produzido pela de ontem acusaria divergencia onde nao ha erro
    nenhum - so passagem do tempo.
    """
    try:
        payload = bronze.read(document.document_id)
    except BronzeStoreError:
        return UnitVerification(unit.unit_id, document.document_id, False, False, False)

    import hashlib

    matches_hash = hashlib.sha256(payload).hexdigest() == document.document_id
    if unit.extraction_version != EXTRACTION_VERSION:
        # Extrator diferente do atual: o hash do blob ainda e conferivel, mas
        # o texto nao e comparavel. Dizer "nao bate" seria mentira; dizer
        # "bate" seria pior.
        return UnitVerification(
            unit.unit_id, document.document_id, True, matches_hash, False
        )

    pages = extract_pdf_page_texts(payload)
    page_index = (unit.locator.page or 1) - 1
    if page_index >= len(pages):
        return UnitVerification(unit.unit_id, document.document_id, True, matches_hash, False)

    slice_ = pages[page_index][unit.locator.char_start : unit.locator.char_end]
    return UnitVerification(
        unit.unit_id, document.document_id, True, matches_hash, slice_ == unit.text
    )


# Reexportado para que quem le o modulo saiba que a versao do parser do
# catalogo tambem e versionada, e por que ela e separada da versao do extrator
# de PDF: sao dois artefatos com ciclos de vida diferentes.
CATALOG_PARSER_VERSION = EXTRACTOR_VERSION

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
from pat.corpus.extract import (
    EXTRACTION_VERSION,
    HTML_EXTRACTION_VERSION,
    extract,
    extract_html_text,
    extract_pdf_page_texts,
)
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
    "SUBMISSIONS_DATASET",
    "catalog_entries",
    "sec_candidates",
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
    entries: list,
    registry: Registry,
    bronze: BronzeStore,
    catalog: Catalog,
    run: Run,
    limit: int | None = None,
) -> SyncOutcome:
    """Candidatos -> bronze -> documentos -> unidades -> indice.

    Idempotente em cada etapa. Um documento cujos bytes ja estao no bronze
    ainda gera um `Retrieval` novo - saber que o conteudo *nao* mudou naquela
    data e informacao - mas nao reextrai nem reindexa.

    `entries` sao `DocumentCandidate`, e o tipo e REGIME-NEUTRO de proposito:
    o catalogo IPE da CVM e o historico de arquivamentos da SEC descrevem a
    mesma coisa com vocabularios diferentes. Aceitar `IpeCatalogEntry` aqui
    obrigaria um ramo por regime, e o terceiro regime pediria um terceiro ramo.
    Por compatibilidade, uma entrada do IPE e convertida na entrada da funcao.
    """
    candidatos = [
        entry.as_candidate() if isinstance(entry, IpeCatalogEntry) else entry
        for entry in entries
    ]
    outcome = SyncOutcome(entity_id=entity_id, catalog_entries=len(candidatos))
    selected = candidatos if limit is None else candidatos[:limit]

    for entry in selected:
        results = ingest_dataset(
            entry.dataset_id,
            entry.fetch_params,
            registry=registry,
            store=bronze,
            catalog=catalog,
            run=run,
        )
        for result in results:
            outcome.documents_fetched += 1
            document_id = result.document.content_sha256

            payload = bronze.read(document_id)
            ja_existe = corpus_store.read_document(conn, document_id) is not None

            if ja_existe and corpus_store.units_for_document(conn, document_id):
                # Documento conhecido E extraido: nada a fazer. O `Retrieval`
                # novo ja foi gravado - saber que o conteudo nao mudou naquela
                # data e informacao.
                outcome.skipped_existing += 1
                continue

            if ja_existe:
                # Conhecido e SEM unidade: extracao falhou antes. Tentar de
                # novo e o comportamento certo - o extrator pode ter mudado
                # desde entao, e foi exatamente o que aconteceu quando o
                # extrator de HTML passou a existir. Pular aqui deixaria o
                # documento permanentemente nao citavel por causa de uma
                # limitacao que ja tinha sido removida.
                outcome.skipped_existing += 1
            else:
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
    entry,
    *,
    entity_id: str,
    document_id: str,
    payload: bytes,
    retrieval_id: str,
    source_tier,
    first_seen_at: datetime,
    first_seen_run_id: str,
) -> SourceDocument:
    """`DocumentCandidate` + bytes -> `SourceDocument`.

    O `document_id` so pode ser calculado aqui: ele E o sha256 do conteudo, e
    e o que faz uma reapresentacao virar documento novo em vez de edicao do
    antigo. O `media_type` tambem sai dos BYTES, e nao do que a origem
    declarou - a CVM responde `text/html` para PDF, e acreditar no header
    classificaria o corpus inteiro errado.
    """
    from pat.corpus.extract import sniff_media_type

    return SourceDocument(
        document_id=document_id,
        entity_id=entity_id,
        kind=entry.kind,
        title=entry.title,
        language=entry.language,
        source_tier=source_tier,
        published_at=entry.published_at,
        published_at_basis=entry.published_at_basis,
        reference_date=entry.reference_date,
        reference_date_basis=entry.reference_date_basis,
        media_type=sniff_media_type(payload) or "application/octet-stream",
        byte_size=len(payload),
        provider_id=entry.provider_id,
        dataset_id=entry.dataset_id,
        resource_key=entry.resource_key,
        source_url=entry.source_url,
        retrieval_id=retrieval_id,
        origin_category=entry.origin_category,
        origin_version=entry.origin_version,
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

    # O extrator a re-rodar sai da unidade, e nao do tipo do documento: uma
    # unidade guarda a versao que a produziu, e e ela que define como o texto
    # se reconstroi. Escolher pelo media type do documento daria o extrator
    # certo hoje e o errado no dia em que um segundo extrator ler o mesmo
    # formato.
    if unit.extraction_version == EXTRACTION_VERSION:
        paginas = extract_pdf_page_texts(payload)
        indice = (unit.locator.page or 1) - 1
        if indice >= len(paginas):
            return UnitVerification(
                unit.unit_id, document.document_id, True, matches_hash, False
            )
        fonte = paginas[indice]
    elif unit.extraction_version == HTML_EXTRACTION_VERSION:
        fonte = extract_html_text(payload)
    else:
        # Extrator diferente de qualquer um dos atuais: o hash do blob ainda e
        # conferivel, mas o texto nao e comparavel. Dizer "nao bate" seria
        # mentira; dizer "bate" seria pior.
        return UnitVerification(
            unit.unit_id, document.document_id, True, matches_hash, False
        )

    fatia = fonte[unit.locator.char_start : unit.locator.char_end]
    return UnitVerification(
        unit.unit_id, document.document_id, True, matches_hash, fatia == unit.text
    )


# Reexportado para que quem le o modulo saiba que a versao do parser do
# catalogo tambem e versionada, e por que ela e separada da versao do extrator
# de PDF: sao dois artefatos com ciclos de vida diferentes.
CATALOG_PARSER_VERSION = EXTRACTOR_VERSION


# ---------------------------------------------------------------------------
# Descoberta de arquivamentos americanos
# ---------------------------------------------------------------------------

SUBMISSIONS_DATASET = "sec.submissions"


def sec_candidates(
    conn: duckdb.DuckDBPyConnection,
    bronze: BronzeStore,
    *,
    cik: str,
    forms: tuple[str, ...] = (),
    since=None,
):
    """Le do bronze o indice de arquivamentos e devolve candidatos. Sem rede.

    O analogo exato de `catalog_entries` do lado brasileiro, ate na escolha da
    versao: usa a MAIS RECENTE do recurso, porque submissions e um indice
    corrente e nao um fato bitemporal. Os documentos apontados e que carregam
    data de entrega, e sao eles que sustentam o `AS OF`.
    """
    from pat.parse.sec_submissions import parse_submissions

    chave = str(cik).zfill(10)
    rows = conn.execute(
        """
        SELECT content_sha256, MAX(retrieved_at) AS latest
        FROM retrieval
        WHERE dataset_id = ? AND resource_key = ?
        GROUP BY content_sha256
        ORDER BY latest DESC
        LIMIT 1
        """,
        [SUBMISSIONS_DATASET, chave],
    ).fetchall()
    if not rows:
        raise LookupError(
            f"indice {SUBMISSIONS_DATASET} de {chave} nao esta no bronze. "
            f"Rode `pat fetch {SUBMISSIONS_DATASET} --cik {int(chave)}` primeiro."
        )
    return rows[0][0], parse_submissions(
        bronze.read(rows[0][0]), forms=forms, since=since
    )


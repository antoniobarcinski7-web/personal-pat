"""Persistencia da camada de corpus. Quem extrai nao grava.

Escrita idempotente, pela mesma razao da silver: os ids sao deterministicos,
entao reprocessar o mesmo documento e um no-op em vez de duplicar linha. E o
que permite rodar a sincronizacao quantas vezes for preciso sem sujar o
estado (I3).

Append-only de verdade: nao ha UPDATE em lugar nenhum deste modulo, e nao deve
passar a haver. Documento reapresentado tem bytes diferentes e portanto
`document_id` diferente - ele entra como linha nova, ao lado da anterior, e as
duas continuam consultaveis pela data em que cada uma era verdade.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import duckdb

from pat.contracts.corpus import (
    DocumentKind,
    DocumentUnit,
    ExtractionFailure,
    LocatorScheme,
    SourceDocument,
    SpeakerRef,
    SpeakerRole,
    UnitLocator,
)
from pat.contracts.common import SourceTier
from pat.contracts.corpus import DateBasis

__all__ = [
    "documents_for_entity",
    "extraction_failures",
    "read_document",
    "read_unit",
    "units_for_document",
    "write_documents",
    "write_failure",
    "write_units",
]

_DOCUMENT_COLUMNS = (
    "document_id",
    "entity_id",
    "kind",
    "title",
    "language",
    "source_tier",
    "published_at",
    "published_at_basis",
    "reference_date",
    "reference_date_basis",
    "media_type",
    "byte_size",
    "provider_id",
    "dataset_id",
    "resource_key",
    "source_url",
    "retrieval_id",
    "origin_category",
    "origin_version",
    "first_seen_at",
    "first_seen_run_id",
)

_UNIT_COLUMNS = (
    "unit_id",
    "document_id",
    "ordinal",
    "locator_scheme",
    "locator_page",
    "locator_block",
    "locator_node_path",
    "locator_turn",
    "char_start",
    "char_end",
    "text",
    "char_count",
    "section_path",
    "speaker_name",
    "speaker_role",
    "speaker_is_qna",
    "extraction_version",
)


def write_documents(
    conn: duckdb.DuckDBPyConnection, documents: Sequence[SourceDocument]
) -> int:
    """Grava documentos. Retorna quantos de fato entraram."""
    if not documents:
        return 0
    before = conn.execute("SELECT COUNT(*) FROM source_document").fetchone()[0]
    conn.executemany(
        f"INSERT INTO source_document ({', '.join(_DOCUMENT_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(_DOCUMENT_COLUMNS))}) "
        "ON CONFLICT (document_id) DO NOTHING",
        [
            (
                d.document_id,
                d.entity_id,
                str(d.kind),
                d.title,
                d.language,
                str(d.source_tier),
                d.published_at,
                str(d.published_at_basis),
                d.reference_date,
                str(d.reference_date_basis) if d.reference_date_basis else None,
                d.media_type,
                d.byte_size,
                d.provider_id,
                d.dataset_id,
                d.resource_key,
                d.source_url,
                d.retrieval_id,
                d.origin_category,
                d.origin_version,
                d.first_seen_at,
                d.first_seen_run_id,
            )
            for d in documents
        ],
    )
    after = conn.execute("SELECT COUNT(*) FROM source_document").fetchone()[0]
    return int(after - before)


def write_units(conn: duckdb.DuckDBPyConnection, units: Sequence[DocumentUnit]) -> int:
    if not units:
        return 0
    before = conn.execute("SELECT COUNT(*) FROM document_unit").fetchone()[0]
    conn.executemany(
        f"INSERT INTO document_unit ({', '.join(_UNIT_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(_UNIT_COLUMNS))}) "
        "ON CONFLICT (unit_id) DO NOTHING",
        [
            (
                u.unit_id,
                u.document_id,
                u.ordinal,
                str(u.locator.scheme),
                u.locator.page,
                u.locator.block,
                u.locator.node_path,
                u.locator.turn,
                u.locator.char_start,
                u.locator.char_end,
                u.text,
                u.char_count,
                list(u.section_path),
                u.speaker.name if u.speaker else None,
                str(u.speaker.role) if u.speaker else None,
                u.speaker.is_qna if u.speaker else None,
                u.extraction_version,
            )
            for u in units
        ],
    )
    after = conn.execute("SELECT COUNT(*) FROM document_unit").fetchone()[0]
    return int(after - before)


def write_failure(conn: duckdb.DuckDBPyConnection, failure: ExtractionFailure) -> None:
    conn.execute(
        "INSERT INTO extraction_failure "
        "(document_id, extraction_version, reason, message, remedy, failed_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (document_id, extraction_version) DO NOTHING",
        [
            failure.document_id,
            failure.extraction_version,
            str(failure.reason),
            failure.message,
            failure.remedy,
            failure.failed_at,
        ],
    )


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _to_document(row: tuple) -> SourceDocument:
    return SourceDocument(
        document_id=row[0],
        entity_id=row[1],
        kind=DocumentKind(row[2]),
        title=row[3],
        language=row[4],
        source_tier=SourceTier(row[5]),
        published_at=row[6],
        published_at_basis=DateBasis(row[7]),
        reference_date=row[8],
        reference_date_basis=DateBasis(row[9]) if row[9] else None,
        media_type=row[10],
        byte_size=row[11],
        provider_id=row[12],
        dataset_id=row[13],
        resource_key=row[14],
        source_url=row[15],
        retrieval_id=row[16],
        origin_category=row[17],
        origin_version=row[18],
        first_seen_at=row[19],
        first_seen_run_id=row[20],
    )


def _to_unit(row: tuple) -> DocumentUnit:
    speaker = (
        SpeakerRef(name=row[13], role=SpeakerRole(row[14]), is_qna=bool(row[15]))
        if row[13]
        else None
    )
    return DocumentUnit(
        unit_id=row[0],
        document_id=row[1],
        ordinal=row[2],
        locator=UnitLocator(
            scheme=LocatorScheme(row[3]),
            page=row[4],
            block=row[5],
            node_path=row[6],
            turn=row[7],
            char_start=row[8],
            char_end=row[9],
        ),
        text=row[10],
        char_count=row[11],
        section_path=tuple(row[12] or ()),
        speaker=speaker,
        extraction_version=row[16],
    )


def read_document(
    conn: duckdb.DuckDBPyConnection, document_id: str
) -> SourceDocument | None:
    row = conn.execute(
        f"SELECT {', '.join(_DOCUMENT_COLUMNS)} FROM source_document WHERE document_id = ?",
        [document_id],
    ).fetchone()
    return _to_document(row) if row else None


def read_unit(conn: duckdb.DuckDBPyConnection, unit_id: str) -> DocumentUnit | None:
    row = conn.execute(
        f"SELECT {', '.join(_UNIT_COLUMNS)} FROM document_unit WHERE unit_id = ?",
        [unit_id],
    ).fetchone()
    return _to_unit(row) if row else None


def units_for_document(
    conn: duckdb.DuckDBPyConnection, document_id: str
) -> list[DocumentUnit]:
    rows = conn.execute(
        f"SELECT {', '.join(_UNIT_COLUMNS)} FROM document_unit "
        "WHERE document_id = ? ORDER BY ordinal",
        [document_id],
    ).fetchall()
    return [_to_unit(row) for row in rows]


def documents_for_entity(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    *,
    as_of: date | None = None,
    kinds: Sequence[DocumentKind] = (),
) -> list[SourceDocument]:
    """Documentos de uma empresa, opcionalmente cortados por `as_of`.

    `as_of=None` e listagem de acervo, e nao consulta de pesquisa - serve para
    `pat docs`, onde ver o que existe depois da data de analise e util. Toda
    consulta de EVIDENCIA passa por `retrieve.py`, onde `as_of` e obrigatorio
    e nao tem default.
    """
    clauses = ["entity_id = ?"]
    params: list[object] = [entity_id]
    if as_of is not None:
        clauses.append("published_at <= ?")
        params.append(as_of)
    if kinds:
        clauses.append(f"kind IN ({', '.join('?' * len(kinds))})")
        params.extend(str(k) for k in kinds)

    rows = conn.execute(
        f"SELECT {', '.join(_DOCUMENT_COLUMNS)} FROM source_document "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY published_at DESC, document_id",
        params,
    ).fetchall()
    return [_to_document(row) for row in rows]


def extraction_failures(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str | None = None,
    *,
    include_resolved: bool = False,
) -> list[tuple[str, str, str, str]]:
    """(document_id, reason, message, title) das falhas AINDA em aberto.

    "Nao extraido" quer dizer "sem unidade hoje", e nao "falhou uma vez". A
    distincao passou a importar quando o extrator de HTML entrou: os tres 10-K
    da Netflix tinham falhado por `unsupported_media_type`, foram reprocessados
    com sucesso, e continuavam listados como nao extraidos - um workspace que
    dizia ter 5.432 unidades e tres documentos nao extraidos ao mesmo tempo.

    O REGISTRO da falha nao e apagado: ele e historia de uma tentativa, e
    apaga-lo perderia a informacao de que aquele documento ja foi dificil.
    `include_resolved=True` traz tudo, que e o que uma auditoria do extrator
    quer ver.
    """
    resolvidas = (
        ""
        if include_resolved
        else " AND NOT EXISTS (SELECT 1 FROM document_unit u WHERE u.document_id = f.document_id)"
    )
    if entity_id is None:
        rows = conn.execute(
            "SELECT f.document_id, f.reason, f.message, COALESCE(d.title, '(sem documento)') "
            "FROM extraction_failure f "
            "LEFT JOIN source_document d ON d.document_id = f.document_id "
            f"WHERE 1 = 1{resolvidas} "
            "ORDER BY f.failed_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT f.document_id, f.reason, f.message, d.title "
            "FROM extraction_failure f "
            "JOIN source_document d ON d.document_id = f.document_id "
            f"WHERE d.entity_id = ?{resolvidas} ORDER BY f.failed_at",
            [entity_id],
        ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]

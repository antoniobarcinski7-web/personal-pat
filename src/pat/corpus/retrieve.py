"""Consulta ao corpus: a unica porta de leitura de evidencia.

O analogo textual de `query/asof.py`. Duas propriedades que este modulo existe
para garantir, e as duas sao de forma, nao de disciplina:

1. **`as_of` e obrigatorio e nao tem default.** `EvidenceQuery.as_of` nao
   aceita `None`, e o filtro `published_at <= as_of` esta no SQL, nao numa
   checagem posterior. Nao existe caminho por onde uma consulta de evidencia
   escape do corte point-in-time, do mesmo jeito que nao existe consulta ao
   gold sem `AS OF`.

2. **Nenhum modelo participa.** Tokenizacao, ranking, corte e montagem de
   citacao sao deterministicos. A M5.1 inteira roda sem `ANTHROPIC_API_KEY`, e
   isso e o criterio, nao um detalhe: se a recuperacao nao presta sem modelo,
   ela nao vai prestar com um - vai so ficar dificil de perceber.

Empate e resolvido, nao sorteado
--------------------------------
Ordem: escore desc, depois `published_at` desc (evidencia mais recente
primeiro, dentro do que era conhecido), depois `unit_id` asc. O ultimo criterio
e arbitrario mas TOTAL - sem ele, duas execucoes identicas poderiam devolver
ordens diferentes, e o `EvidenceResult` deixaria de ser reproduzivel.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb

from pat.contracts.corpus import (
    DateBasis,
    DocumentKind,
    EvidenceHit,
    EvidenceQuery,
    EvidenceResult,
    EvidenceUnavailable,
    EvidenceUnavailableReason,
    LocatorScheme,
    QuoteClaim,
    SpeakerRef,
    SpeakerRole,
    UnitLocator,
)
from pat.contracts.common import SourceTier
from pat.corpus.identity import query_id
from pat.corpus.index import INDEX_VERSION, bm25, tokenize

__all__ = ["retrieve"]


def retrieve(
    conn: duckdb.DuckDBPyConnection, query: EvidenceQuery
) -> EvidenceResult | EvidenceUnavailable:
    """Consulta -> trechos verbatim, ou uma recusa nomeada.

    Nunca lista vazia sem motivo: "nao encontrei nada" e ambiguo entre 'a
    empresa nunca falou disso', 'os documentos nao foram ingeridos' e 'o
    indice esta desatualizado', e as tres pedem acoes diferentes de quem esta
    pesquisando.
    """
    scope = _scope(conn, query)
    if isinstance(scope, EvidenceUnavailable):
        return scope
    document_ids, excluded_by_as_of = scope

    tokens = _query_tokens(query)
    if not tokens:
        return EvidenceUnavailable(
            reason=EvidenceUnavailableReason.NO_MATCH,
            message=(
                f"nenhum termo indexavel em {list(query.terms)}: todos caem em "
                "stopword ou sao curtos demais"
            ),
            entity_id=query.entity_id,
            as_of=query.as_of,
            remedy="Use termos com pelo menos duas letras e sem ser palavra funcional.",
            documents_in_scope=len(document_ids),
            documents_excluded_by_as_of=excluded_by_as_of,
        )

    units = _units_in_scope(conn, document_ids, query)
    if not units:
        return EvidenceUnavailable(
            reason=EvidenceUnavailableReason.NO_UNITS_EXTRACTED,
            message=(
                f"{len(document_ids)} documento(s) no escopo, nenhum com unidade "
                "extraida e indexada"
            ),
            entity_id=query.entity_id,
            as_of=query.as_of,
            remedy="Rode `pat docs sync` para extrair e indexar, e confira as falhas registradas.",
            documents_in_scope=len(document_ids),
            documents_excluded_by_as_of=excluded_by_as_of,
        )

    ranked = _rank(conn, units, tokens)
    if not ranked:
        return EvidenceUnavailable(
            reason=EvidenceUnavailableReason.NO_MATCH,
            message=(
                f"nenhuma das {len(units)} unidades no escopo contem "
                f"{sorted(tokens)} em documentos publicados ate {query.as_of}"
            ),
            entity_id=query.entity_id,
            as_of=query.as_of,
            remedy=(
                "Termos diferentes, janela mais larga, ou `as_of` posterior. "
                f"{excluded_by_as_of} documento(s) existem mas sao posteriores ao as_of."
                if excluded_by_as_of
                else "Termos diferentes ou janela mais larga."
            ),
            documents_in_scope=len(document_ids),
            documents_excluded_by_as_of=excluded_by_as_of,
        )

    hits = tuple(
        EvidenceHit(
            rank=position,
            relevance=score,
            matched_terms=matched,
            quote=_quote(units[unit_id_value]),
        )
        for position, (unit_id_value, score, matched) in enumerate(
            ranked[: query.limit], start=1
        )
    )

    return EvidenceResult(
        query_id=query_id(query),
        entity_id=query.entity_id,
        as_of=query.as_of,
        hits=hits,
        index_version=INDEX_VERSION,
        extraction_versions=tuple(
            sorted({units[u].extraction_version for u, _, _ in ranked[: query.limit]})
        ),
        documents_in_scope=len(document_ids),
        units_in_scope=len(units),
        retrieved_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Escopo: o corte point-in-time acontece aqui, e so aqui
# ---------------------------------------------------------------------------


def _scope(
    conn: duckdb.DuckDBPyConnection, query: EvidenceQuery
) -> tuple[list[str], int] | EvidenceUnavailable:
    total = conn.execute(
        "SELECT COUNT(*) FROM source_document WHERE entity_id = ?", [query.entity_id]
    ).fetchone()[0]
    if not total:
        return EvidenceUnavailable(
            reason=EvidenceUnavailableReason.NO_DOCUMENTS_FOR_ENTITY,
            message=f"nenhum documento de {query.entity_id} no corpus",
            entity_id=query.entity_id,
            as_of=query.as_of,
            remedy="Rode `pat docs sync <entidade> --year ...` para popular o corpus.",
        )

    excluded = conn.execute(
        "SELECT COUNT(*) FROM source_document WHERE entity_id = ? AND published_at > ?",
        [query.entity_id, query.as_of],
    ).fetchone()[0]

    clauses = ["entity_id = ?", "published_at <= ?"]
    params: list[object] = [query.entity_id, query.as_of]
    if query.published_from is not None:
        clauses.append("published_at >= ?")
        params.append(query.published_from)
    if query.published_to is not None:
        clauses.append("published_at <= ?")
        params.append(query.published_to)
    if query.kinds:
        clauses.append(f"kind IN ({', '.join('?' * len(query.kinds))})")
        params.extend(str(k) for k in query.kinds)

    rows = conn.execute(
        f"SELECT document_id FROM source_document WHERE {' AND '.join(clauses)} "
        "ORDER BY document_id",
        params,
    ).fetchall()

    if not rows:
        return EvidenceUnavailable(
            reason=EvidenceUnavailableReason.NO_DOCUMENTS_AS_OF,
            message=(
                f"{total} documento(s) de {query.entity_id} no corpus, nenhum "
                f"elegivel em AS OF {query.as_of} com os filtros pedidos"
            ),
            entity_id=query.entity_id,
            as_of=query.as_of,
            remedy=(
                f"{excluded} documento(s) sao posteriores ao as_of. Um as_of "
                "posterior ou uma janela mais larga muda isso."
            ),
            documents_in_scope=0,
            documents_excluded_by_as_of=int(excluded),
        )

    return [row[0] for row in rows], int(excluded)


class _Unit:
    __slots__ = (
        "unit_id",
        "document_id",
        "text",
        "extraction_version",
        "locator",
        "speaker",
        "published_at",
        "published_at_basis",
        "kind",
        "title",
        "source_tier",
        "length",
    )

    def __init__(self, row: tuple) -> None:
        self.unit_id = row[0]
        self.document_id = row[1]
        self.text = row[2]
        self.extraction_version = row[3]
        self.locator = UnitLocator(
            scheme=LocatorScheme(row[4]),
            page=row[5],
            block=row[6],
            node_path=row[7],
            turn=row[8],
            char_start=row[9],
            char_end=row[10],
        )
        self.speaker = (
            SpeakerRef(name=row[11], role=SpeakerRole(row[12]), is_qna=bool(row[13]))
            if row[11]
            else None
        )
        self.published_at = row[14]
        self.published_at_basis = DateBasis(row[15])
        self.kind = DocumentKind(row[16])
        self.title = row[17]
        self.source_tier = SourceTier(row[18])
        self.length = int(row[19] or 0)


def _units_in_scope(
    conn: duckdb.DuckDBPyConnection, document_ids: list[str], query: EvidenceQuery
) -> dict[str, _Unit]:
    placeholders = ", ".join("?" * len(document_ids))
    clauses = [f"u.document_id IN ({placeholders})"]
    # A ordem dos parametros segue a ordem dos `?` no SQL, e o primeiro deles
    # esta no JOIN, nao no WHERE.
    params: list[object] = [INDEX_VERSION, *document_ids]
    if query.speaker_roles:
        clauses.append(f"u.speaker_role IN ({', '.join('?' * len(query.speaker_roles))})")
        params.extend(str(r) for r in query.speaker_roles)
    if query.sections:
        # Casa pelo PRIMEIRO nivel do caminho - "Item 1A" -, e nao pelo nome da
        # secao: o nome vem da tabela do regulador e pode ser reescrito; o
        # numero do item e o que o proprio formulario declara.
        clauses.append(
            f"len(u.section_path) > 0 AND u.section_path[1] IN "
            f"({', '.join('?' * len(query.sections))})"
        )
        params.extend(query.sections)

    # UMA versao de extrator por documento: a mais recente que existe para ele.
    #
    # Reprocessar cria unidades AO LADO das antigas - e a regra que faz uma
    # citacao antiga continuar querendo dizer o que dizia. O efeito colateral,
    # descoberto na primeira busca depois de um upgrade de extrator, e que a
    # mesma passagem voltava DUAS vezes, com dois `unit_id` e o mesmo texto.
    # Nao e duplicata de dado: sao duas leituras legitimas do mesmo blob. O que
    # nao pode e a busca apresentar as duas como se fossem duas evidencias.
    clauses.append(
        "u.extraction_version = ("
        "  SELECT MAX(v.extraction_version) FROM document_unit v"
        "  WHERE v.document_id = u.document_id"
        ")"
    )

    rows = conn.execute(
        f"""
        SELECT u.unit_id, u.document_id, u.text, u.extraction_version,
               u.locator_scheme, u.locator_page, u.locator_block,
               u.locator_node_path, u.locator_turn, u.char_start, u.char_end,
               u.speaker_name, u.speaker_role, u.speaker_is_qna,
               d.published_at, d.published_at_basis, d.kind, d.title, d.source_tier,
               MAX(t.unit_length)
        FROM document_unit u
        JOIN source_document d ON d.document_id = u.document_id
        JOIN document_unit_token t
          ON t.unit_id = u.unit_id AND t.index_version = ?
        WHERE {' AND '.join(clauses)}
        GROUP BY ALL
        """,
        params,
    ).fetchall()
    return {row[0]: _Unit(row) for row in rows}


def _query_tokens(query: EvidenceQuery) -> tuple[str, ...]:
    tokens: list[str] = []
    for term in query.terms:
        for token in tokenize(term):
            if token not in tokens:
                tokens.append(token)
    return tuple(tokens)


def _rank(
    conn: duckdb.DuckDBPyConnection, units: dict[str, _Unit], tokens: tuple[str, ...]
) -> list[tuple[str, Decimal, tuple[str, ...]]]:
    unit_ids = list(units)
    placeholders = ", ".join("?" * len(unit_ids))
    token_places = ", ".join("?" * len(tokens))
    rows = conn.execute(
        f"""
        SELECT unit_id, token, tf
        FROM document_unit_token
        WHERE index_version = ?
          AND unit_id IN ({placeholders})
          AND token IN ({token_places})
        ORDER BY unit_id, token
        """,
        [INDEX_VERSION, *unit_ids, *tokens],
    ).fetchall()

    by_unit: dict[str, list[tuple[str, int]]] = {}
    document_frequencies: dict[str, int] = {}
    for unit, token, frequency in rows:
        by_unit.setdefault(unit, []).append((token, int(frequency)))
        document_frequencies[token] = document_frequencies.get(token, 0) + 1

    lengths = [units[u].length for u in unit_ids if units[u].length > 0]
    average_length = (
        Decimal(sum(lengths)) / Decimal(len(lengths)) if lengths else Decimal(1)
    )

    scored: list[tuple[str, Decimal, tuple[str, ...]]] = []
    for unit, hits in by_unit.items():
        score = bm25(
            term_hits=hits,
            unit_length=units[unit].length,
            average_length=average_length,
            documents_in_scope=len(unit_ids),
            document_frequencies=document_frequencies,
        )
        scored.append((unit, score, tuple(token for token, _ in hits)))

    # Ordem total: escore, recencia dentro do conhecido, e id como desempate
    # final. Sem o ultimo criterio, duas execucoes identicas poderiam divergir.
    scored.sort(key=lambda item: (-item[1], -units[item[0]].published_at.toordinal(), item[0]))
    return scored


def _quote(unit: _Unit) -> QuoteClaim:
    """Unidade -> citacao. O texto sai INTACTO.

    Nao ha `strip()`, nao ha colapso de espaco, nao ha reticencia. O que
    entra em `QuoteClaim.text` e byte-identico a `DocumentUnit.text`, e ha
    teste que confere isso - citacao parafraseada nao e citacao.
    """
    return QuoteClaim(
        unit_id=unit.unit_id,
        document_id=unit.document_id,
        text=unit.text,
        document_kind=unit.kind,
        published_at=unit.published_at,
        published_at_basis=unit.published_at_basis,
        source_tier=unit.source_tier,
        locator=unit.locator,
        speaker=unit.speaker,
        title=unit.title,
    )

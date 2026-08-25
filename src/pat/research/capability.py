"""O retrato do que o sistema sabe fazer.

Montado a partir do repositorio de verdade - catalogo de conceitos, registro
de metricas, mapeamentos carregados e cobertura do gold - e nunca a partir de
uma lista escrita a mao. Uma capacidade inventada aqui viraria um plano que
falha so na execucao, que e o lugar mais caro para descobrir.

A regra que este modulo existe para respeitar: **nenhum valor financeiro
atravessa daqui para fora.** O snapshot diz que periodos existem, nunca
quanto eles valem; diz que conceitos um mapeamento cobre, nunca em que linha
eles moram. Sem isso, a primeira coisa a vazar para um prompt seria o plano de
contas - e casamento por rotulo entraria pela porta dos fundos.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb

from pat.contracts.research import (
    CapabilitySnapshot,
    CorpusCard,
    DecompositionCard,
    ConceptCard,
    DerivationCard,
    DerivationOp,
    EntityCard,
    MappingCard,
    MetricCard,
    SnapshotLimits,
)
from pat.contracts.semantics import Fidelity, ReportingScope, weakest
from pat.query.asof import AsOf
from pat.research import DEFAULT_SOURCE
from pat.research.canonical import canonical_bytes, capability_sha256

DERIVATION_CARDS: tuple[DerivationCard, ...] = (
    DerivationCard(
        op=DerivationOp.CAGR,
        arity="2",
        output_dimension="ratio",
        refusals=("base <= 0", "valor final <= 0", "years <= 0"),
    ),
    DerivationCard(
        op=DerivationOp.DELTA,
        arity="2",
        output_dimension="igual aos insumos",
        refusals=("dimensoes diferentes", "moedas diferentes"),
    ),
    DerivationCard(
        op=DerivationOp.DELTA_PCT,
        arity="2",
        output_dimension="ratio",
        refusals=("primeiro insumo zero",),
    ),
    DerivationCard(
        op=DerivationOp.MAX, arity=">=2", output_dimension="igual aos insumos",
        refusals=("dimensoes diferentes", "moedas diferentes"),
    ),
    DerivationCard(
        op=DerivationOp.MEAN, arity=">=2", output_dimension="igual aos insumos",
        refusals=("dimensoes diferentes", "moedas diferentes"),
    ),
    DerivationCard(
        op=DerivationOp.MIN, arity=">=2", output_dimension="igual aos insumos",
        refusals=("dimensoes diferentes", "moedas diferentes"),
    ),
    DerivationCard(
        op=DerivationOp.RATIO,
        arity="2",
        output_dimension="ratio",
        refusals=("segundo insumo zero",),
    ),
)


class CapabilityTooLarge(RuntimeError):
    """O snapshot passou do limite declarado.

    Levanta em vez de truncar: um catalogo cortado pela metade faria o
    planejador ignorar metade do sistema sem que ninguem soubesse.
    """


def build_snapshot(
    conn: duckdb.DuckDBPyConnection | None = None,
    *,
    as_of: date | None = None,
    source: str = DEFAULT_SOURCE,
    now: datetime | None = None,
) -> CapabilitySnapshot:
    """Snapshot completo. Sem `conn`, sai so a parte de catalogo."""
    from pat.semantics import concepts
    from pat.semantics.loader import load_dir
    from pat.semantics.registry import default_registry

    concept_cards = tuple(
        ConceptCard(
            concept_id=concept.concept_id,
            label_en=concept.label_en,
            definition=concept.definition,
            dimension=concept.dimension,
            period_kind=concept.period_kind,
        )
        for concept in sorted(concepts.CATALOG.values(), key=lambda c: c.concept_id)
    )

    metric_cards = tuple(
        MetricCard(
            ref=str(metric.definition.ref),
            kind=str(metric.definition.kind),
            dimension=metric.definition.dimension,
            period_kind=metric.definition.period_kind,
            definition=metric.definition.definition,
            requires_concepts=metric.definition.requires_concepts,
            requires_metrics=tuple(str(m) for m in metric.definition.requires_metrics),
        )
        for metric in default_registry().all()
    )

    mappings = load_dir()
    mapping_cards = tuple(
        MappingCard(
            mapping_id=mapping.mapping_id,
            entity_id=mapping.entity_id,
            framework=str(mapping.framework),
            jurisdiction=mapping.jurisdiction,
            source=mapping.source,
            is_default_for_source=mapping.is_default_for_source,
            bound_concepts=tuple(sorted(b.concept_id for b in mapping.bindings)),
            weakest_fidelity=weakest(tuple(b.fidelity for b in mapping.bindings)),
        )
        for mapping in mappings.all()
    )

    entity_cards: tuple[EntityCard, ...] = ()
    if conn is not None:
        asof = AsOf(conn)
        cards: list[EntityCard] = []
        for ref in asof.entities(as_of=as_of):
            cover = asof.coverage(ref.entity_id, as_of=as_of)
            if cover is None:
                continue
            chain = mappings.resolve(ref.entity_id, source=source)
            cards.append(
                EntityCard(
                    entity_id=ref.entity_id,
                    jurisdiction=ref.jurisdiction,
                    display_name=ref.display_name,
                    period_ends=cover.period_ends,
                    scopes=tuple(
                        ReportingScope.CONSOLIDATED if flag else ReportingScope.PARENT_ONLY
                        for flag in sorted(cover.consolidated_scopes, reverse=True)
                    ),
                    has_own_mapping=bool(chain and chain.confirmed),
                )
            )
        entity_cards = tuple(cards)

    snapshot = CapabilitySnapshot(
        built_at=now or datetime.now(UTC),
        concepts=concept_cards,
        metrics=metric_cards,
        mappings=mapping_cards,
        entities=entity_cards,
        scopes=(ReportingScope.CONSOLIDATED, ReportingScope.PARENT_ONLY),
        derivations=DERIVATION_CARDS,
        decompositions=_decomposition_cards(),
        corpus=_corpus_cards(conn) if conn is not None else (),
        limits=SnapshotLimits(),
    )

    size = len(canonical_bytes(snapshot))
    if size > snapshot.limits.max_serialized_bytes:
        raise CapabilityTooLarge(
            f"snapshot com {size} bytes, acima do limite de "
            f"{snapshot.limits.max_serialized_bytes}. Truncar seria pior: "
            "filtre entidades por pergunta, o que e mudanca de desenho."
        )
    return snapshot


def _decomposition_cards() -> tuple[DecompositionCard, ...]:
    """As identidades registradas, com os termos e o sinal de cada um.

    Eixo sem fonte estruturada aparece marcado `available=False`, e nao some.
    Um planejador que nao soubesse que `SEGMENT` existe pediria a decomposicao
    achando que inventou a ideia, e receberia uma recusa que pareceria um bug;
    vendo-a bloqueada, ele planeja em volta.
    """
    from pat.contracts.decomposition import BreakdownAxis
    from pat.semantics import decompositions

    cards = []
    for definition in decompositions.all_definitions():
        disponivel = definition.axis is BreakdownAxis.COMPONENT
        cards.append(
            DecompositionCard(
                ref=definition.ref,
                axis=str(definition.axis),
                target_concept=definition.target_concept,
                target_label=definition.target_label,
                terms=tuple(
                    f"{'+' if term.sign > 0 else '-'}{term.concept_id}"
                    for term in definition.terms
                ),
                definition=definition.definition,
                available=disponivel,
                unavailable_reason=(
                    None
                    if disponivel
                    else "no_breakdown_source: o plano padronizado da CVM nao publica "
                    "esta dimensao"
                ),
            )
        )
    return tuple(cards)


def _corpus_cards(conn: duckdb.DuckDBPyConnection) -> tuple[CorpusCard, ...]:
    """Cobertura qualitativa por empresa. Contagens e datas, jamais texto.

    Nenhuma linha desta funcao le `document_unit.text`, e ha teste que confere
    que nenhum valor financeiro nem trecho entra no snapshot.
    """
    from pat.corpus.index import INDEX_VERSION

    try:
        linhas = conn.execute(
            """
            SELECT d.entity_id, d.kind, COUNT(*) AS docs,
                   MIN(d.published_at), MAX(d.published_at)
            FROM source_document d
            GROUP BY d.entity_id, d.kind
            ORDER BY d.entity_id, d.kind
            """
        ).fetchall()
    except duckdb.Error:
        # Warehouse anterior a M5.1 nao tem as tabelas de corpus. Snapshot sem
        # cobertura qualitativa e a resposta certa - inventar zero diria que a
        # empresa nao publicou nada.
        return ()

    if not linhas:
        return ()

    indexadas = dict(
        conn.execute(
            """
            SELECT d.entity_id, COUNT(DISTINCT t.unit_id)
            FROM source_document d
            JOIN document_unit u ON u.document_id = d.document_id
            JOIN document_unit_token t
              ON t.unit_id = u.unit_id AND t.index_version = ?
            GROUP BY d.entity_id
            """,
            [INDEX_VERSION],
        ).fetchall()
    )
    falhas = dict(
        conn.execute(
            """
            SELECT d.entity_id, COUNT(*)
            FROM extraction_failure f
            JOIN source_document d ON d.document_id = f.document_id
            GROUP BY d.entity_id
            """
        ).fetchall()
    )

    por_entidade: dict[str, list] = {}
    for entity_id, kind, docs, primeiro, ultimo in linhas:
        por_entidade.setdefault(entity_id, []).append((kind, int(docs), primeiro, ultimo))

    cards = []
    for entity_id in sorted(por_entidade):
        entradas = por_entidade[entity_id]
        cards.append(
            CorpusCard(
                entity_id=entity_id,
                documents=sum(quantidade for _, quantidade, _, _ in entradas),
                kinds=tuple(sorted((kind, quantidade) for kind, quantidade, _, _ in entradas)),
                published_from=min(primeiro for _, _, primeiro, _ in entradas),
                published_to=max(ultimo for _, _, _, ultimo in entradas),
                units_indexed=int(indexadas.get(entity_id, 0)),
                extraction_failures=int(falhas.get(entity_id, 0)),
            )
        )
    return tuple(cards)


def snapshot_sha256(snapshot: CapabilitySnapshot) -> str:
    return capability_sha256(snapshot)

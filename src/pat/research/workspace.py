"""Monta o workspace de uma empresa a partir do que existe em disco.

Deterministico e sem modelo. A prontidao e a CONJUNCAO de requisitos
conferiveis, e cada um que falha vira uma pendencia com nome e remedio.

Os requisitos, e por que cada um
--------------------------------
1. A empresa tem fatos no gold.
2. Pelo menos DOIS periodos. Sem dois nao ha variacao, e sem variacao nao ha
   pergunta causal a fazer - que e o que a Fase 5 existe para responder.
3. Mapeamento proprio e conferido. Cair na familia default e razoavel para
   explorar; declarar a empresa pronta para pesquisa profunda sem saber que
   caiu nela, nao.
4. Os conceitos que as decomposicoes registradas exigem estao ligados.
5. Ha documentos no corpus.
6. Ha unidades indexadas na versao corrente do indice.

Nenhum deles e opiniao, e nenhum tem tolerancia. Um requisito com "mais ou
menos" viraria a porta por onde um workspace incompleto se declara pronto.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb

from pat.canonical import sha256_of
from pat.contracts.workspace import (
    CompanyWorkspace,
    QualitativeCoverage,
    QuantitativeCoverage,
    ReadinessCode,
    ReadinessGap,
    WorkspaceState,
)

__all__ = ["MIN_PERIODS", "build_workspace"]

MIN_PERIODS = 2
"""Dois periodos e o minimo para existir variacao.

Nao e arbitrario: `decompose` abre a variacao ENTRE dois periodos, e um
workspace com um periodo so nao consegue responder "por que mudou" - a
pergunta central da fase. Declara-lo pronto seria prometer o que ele nao
faz."""


SOURCE_BY_JURISDICTION = {
    "BR": "cvm.dfp",
    "US": "sec.companyfacts",
}
"""Fonte default por jurisdicao. Tabela DECLARADA, pela mesma razao que
`Categoria -> DocumentKind` e uma tabela: derivar a fonte do prefixo de
`entity_id` faria o identificador opaco deixar de ser opaco."""

DEFAULT_JURISDICTION = "BR"


def build_workspace(
    conn: duckdb.DuckDBPyConnection,
    *,
    entity_id: str,
    display_name: str,
    cod_cvm: int | None = None,
    jurisdiction: str | None = None,
    source: str | None = None,
    as_of: date | None = None,
) -> CompanyWorkspace:
    """Estado de uma empresa. `jurisdiction` e `source` sao DERIVADOS da
    entidade quando nao vem informados.

    Os dois eram literais brasileiros no default. O mapeamento proprio vence
    a familia em qualquer fonte, entao a companhia americana com TOML proprio
    escapava - o que saia errado era o resto: `jurisdiction` ia para o
    contrato como "BR" para uma empresa dos EUA, e a pendencia de corpus
    mandava rodar `pat docs --cod-cvm --sync`, um comando que nao existe para
    ela. Um remedio que nao funciona e pior que pendencia sem remedio: manda
    o leitor gastar tempo antes de descobrir que nao era por ali.

    O caso que quebrava de verdade e a companhia americana SEM mapeamento
    proprio - o estado de toda empresa nova no primeiro dia -, que caia na
    familia da CVM e falhava apontando para o lugar errado.
    """
    from pat.query.asof import AsOf

    if jurisdiction is None:
        ref = AsOf(conn).entity(entity_id)
        jurisdiction = ref.jurisdiction if ref is not None else DEFAULT_JURISDICTION
    if source is None:
        source = SOURCE_BY_JURISDICTION.get(jurisdiction, SOURCE_BY_JURISDICTION["BR"])

    quantitativo, lacunas_q = _quantitative(conn, entity_id, source=source, as_of=as_of)
    qualitativo, lacunas_t = _qualitative(conn, entity_id, jurisdiction=jurisdiction)
    lacunas = tuple(lacunas_q + lacunas_t)

    return CompanyWorkspace(
        entity_id=entity_id,
        display_name=display_name,
        cod_cvm=cod_cvm,
        jurisdiction=jurisdiction,
        state=WorkspaceState.READY if not lacunas else WorkspaceState.DRAFT,
        gaps=lacunas,
        quantitative=quantitativo,
        qualitative=qualitativo,
        workspace_sha256=_workspace_sha256(quantitativo, qualitativo),
        built_at=datetime.now(UTC),
        as_of=as_of,
    )


def _quantitative(
    conn: duckdb.DuckDBPyConnection, entity_id: str, *, source: str, as_of: date | None
) -> tuple[QuantitativeCoverage, list[ReadinessGap]]:
    from pat.query.asof import AsOf
    from pat.semantics import decompositions
    from pat.semantics.loader import load_dir
    from pat.semantics.registry import default_registry

    asof = AsOf(conn)
    cobertura = asof.coverage(entity_id, as_of=as_of)
    fatos = conn.execute(
        "SELECT COUNT(*) FROM gold_fact WHERE entity_id = ?", [entity_id]
    ).fetchone()[0]

    chain = load_dir().resolve(entity_id, source=source)
    # A cadeia inteira, e nao so a cabeca: a empresa herda os bindings da
    # familia, e um conceito ligado no pai esta ligado para ela.
    conceitos_ligados = set()
    if chain is not None:
        for mapping in chain.chain:
            conceitos_ligados.update(b.concept_id for b in mapping.bindings)

    exigidos: set[str] = set()
    disponiveis: list[str] = []
    from pat.contracts.decomposition import BreakdownAxis

    eixos_declarados = {m.axis for m in (chain.head.segments if chain else ())}

    for definition in decompositions.all_definitions():
        if definition.axis is BreakdownAxis.COMPONENT:
            precisa = {definition.target_concept, *definition.concept_ids}
            exigidos.update(precisa)
            if precisa <= conceitos_ligados:
                disponiveis.append(definition.ref)
            continue

        # Eixo dimensional: disponivel quando o mapeamento DECLARA membros
        # daquele eixo. Sem membros declarados a decomposicao recusa com
        # NO_BREAKDOWN_SOURCE, e listar como disponivel seria prometer o que o
        # sistema nao faz - o que e o oposto do que este workspace existe para
        # fazer.
        if (
            definition.member_axis in eixos_declarados
            and definition.target_concept in conceitos_ligados
        ):
            disponiveis.append(definition.ref)

    faltando = tuple(sorted(exigidos - conceitos_ligados))

    coverage = QuantitativeCoverage(
        facts=int(fatos),
        period_ends=cobertura.period_ends if cobertura else (),
        scopes=tuple(
            "consolidated" if flag else "parent_only"
            for flag in sorted(cobertura.consolidated_scopes, reverse=True)
        )
        if cobertura
        else (),
        mapping_id=chain.head.mapping_id if chain else None,
        mapping_sha256=chain.sha256 if chain else None,
        mapping_confirmed=bool(chain and chain.confirmed),
        weakest_fidelity=None,
        metrics_available=tuple(
            str(m.definition.ref) for m in default_registry().all()
        ),
        decompositions_available=tuple(sorted(disponiveis)),
        missing_concepts=faltando,
    )

    lacunas: list[ReadinessGap] = []
    if not fatos:
        lacunas.append(
            ReadinessGap(
                code=ReadinessCode.NO_FACTS,
                message=f"nenhum fato no gold para {entity_id}",
                remedy="pat fetch cvm.dfp --year ... && pat build cvm.dfp --year ...",
            )
        )
    elif len(coverage.period_ends) < MIN_PERIODS:
        lacunas.append(
            ReadinessGap(
                code=ReadinessCode.TOO_FEW_PERIODS,
                message=(
                    f"{len(coverage.period_ends)} periodo(s) cobertos; sao precisos "
                    f"{MIN_PERIODS}. Sem dois periodos nao ha variacao, e sem variacao "
                    "nao ha pergunta causal a fazer."
                ),
                remedy="ingira mais um exercicio: pat build cvm.dfp --year <ano>",
            )
        )

    if chain is None:
        lacunas.append(
            ReadinessGap(
                code=ReadinessCode.NO_CONFIRMED_MAPPING,
                message=f"nenhum mapeamento cobre {entity_id} na fonte {source}",
                remedy=f"escreva src/pat/semantics/mappings/ para {entity_id}",
            )
        )
    elif not chain.confirmed:
        lacunas.append(
            ReadinessGap(
                code=ReadinessCode.NO_CONFIRMED_MAPPING,
                message=(
                    "a empresa caiu na familia default, sem mapeamento proprio "
                    "conferido"
                ),
                remedy="pat accounts + pat mapping-check, e escreva o TOML da empresa",
            )
        )

    if faltando:
        lacunas.append(
            ReadinessGap(
                code=ReadinessCode.MISSING_DECOMPOSITION_CONCEPTS,
                message=(
                    f"{len(faltando)} conceito(s) exigidos por decomposicoes registradas "
                    "nao estao ligados no mapeamento"
                ),
                remedy="acrescente [[binding]] com equivalence_basis para cada um",
                detail=faltando,
            )
        )

    return coverage, lacunas


def _qualitative(
    conn: duckdb.DuckDBPyConnection, entity_id: str, *, jurisdiction: str = "BR"
) -> tuple[QualitativeCoverage, list[ReadinessGap]]:
    from pat.corpus.index import INDEX_VERSION

    try:
        linhas = conn.execute(
            """
            SELECT kind, COUNT(*), MIN(published_at), MAX(published_at)
            FROM source_document WHERE entity_id = ? GROUP BY kind ORDER BY kind
            """,
            [entity_id],
        ).fetchall()
        indexadas = conn.execute(
            """
            SELECT COUNT(DISTINCT t.unit_id)
            FROM source_document d
            JOIN document_unit u ON u.document_id = d.document_id
            JOIN document_unit_token t
              ON t.unit_id = u.unit_id AND t.index_version = ?
            WHERE d.entity_id = ?
            """,
            [INDEX_VERSION, entity_id],
        ).fetchone()[0]
        # SQL direto em vez de `pat.store.corpus`: a camada de pesquisa nao
        # importa `pat.store`, e o invariante e "quem calcula nao grava". Uma
        # excecao para leitura seria a porta por onde a escrita entra depois.
        falhas = conn.execute(
            """
            SELECT f.document_id, f.reason
            FROM extraction_failure f
            JOIN source_document d ON d.document_id = f.document_id
            WHERE d.entity_id = ? ORDER BY f.failed_at
            """,
            [entity_id],
        ).fetchall()
    except duckdb.Error:
        # Warehouse anterior a M5.1. Cobertura qualitativa vazia e a resposta
        # honesta; inventar zero diria que a empresa nao publicou nada.
        linhas, indexadas, falhas = [], 0, []

    documentos = sum(int(q) for _, q, _, _ in linhas)
    coverage = QualitativeCoverage(
        documents=documentos,
        kinds=tuple(sorted((kind, int(q)) for kind, q, _, _ in linhas)),
        published_from=min((p for _, _, p, _ in linhas), default=None),
        published_to=max((u for _, _, _, u in linhas), default=None),
        units_indexed=int(indexadas),
        index_version=INDEX_VERSION if indexadas else None,
        extraction_failures=tuple((doc, motivo) for doc, motivo in falhas),
    )

    lacunas: list[ReadinessGap] = []
    if not documentos:
        lacunas.append(
            ReadinessGap(
                code=ReadinessCode.NO_DOCUMENTS,
                message=f"nenhum documento qualitativo para {entity_id}",
                remedy=(
                    "pat docs --cod-cvm ... --sync --year ..."
                    if jurisdiction == "BR"
                    # Recusa HONESTA para a jurisdicao americana: nao ha
                    # comando que resolva isso hoje. Sugerir `pat docs --sync`
                    # aqui mandaria o leitor rodar algo que falha, e o remedio
                    # de uma linha viraria uma pista falsa.
                    else (
                        "nao ha ingestao de corpus para esta jurisdicao: "
                        "`sec.filing_doc` existe no provider mas nao tem "
                        "consumidor, e o extrator so le PDF (filing e HTML)"
                    )
                ),
            )
        )
    elif not indexadas:
        lacunas.append(
            ReadinessGap(
                code=ReadinessCode.NO_UNITS_INDEXED,
                message=(
                    f"{documentos} documento(s) no corpus e nenhuma unidade indexada na "
                    f"versao corrente ({INDEX_VERSION})"
                ),
                remedy="rode `pat docs --sync` de novo; ele reindexa o que falta",
            )
        )

    return coverage, lacunas


def _workspace_sha256(
    quantitativo: QuantitativeCoverage, qualitativo: QualitativeCoverage
) -> str:
    """Cobre o que pode mudar uma resposta sem que a pergunta tenha mudado.

    Cadeia de mapeamento, conjunto de documentos, versao do indice e versoes de
    metrica. `built_at` fica de fora pela mesma razao que fica de fora do
    `capability_sha256`: um sistema inalterado tem que produzir o mesmo hash em
    toda invocacao, senao o campo nao significa nada.
    """
    return sha256_of(
        {
            "mapping_sha256": quantitativo.mapping_sha256,
            "period_ends": quantitativo.period_ends,
            "metrics": quantitativo.metrics_available,
            "decompositions": quantitativo.decompositions_available,
            "documents": qualitativo.documents,
            "kinds": qualitativo.kinds,
            "units_indexed": qualitativo.units_indexed,
            "index_version": qualitativo.index_version,
        }
    )

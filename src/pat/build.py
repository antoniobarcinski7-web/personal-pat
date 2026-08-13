"""Orquestracao bronze -> silver -> gold.

Deterministico e re-executavel: ids sao derivados do conteudo, entao rodar o
build duas vezes sobre o mesmo bronze produz exatamente o mesmo estado. Nao ha
rede envolvida - so bytes ja armazenados.

Quando um recurso tem mais de uma versao de conteudo no bronze (a CVM
reescreveu o ZIP), **todas** sao processadas. Cada versao carrega seus proprios
`DT_RECEB`, entao as duas coexistem no gold com datas de conhecimento
diferentes, que e exatamente o comportamento desejado.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

from pat.contracts.lineage import Run
from pat.parse.cvm_dfp import ParseStats, parse_dfp_zip
from pat.store import gold, silver
from pat.store.bronze import BronzeStore
from pat.store.gold import BuildStats

SUPPORTED_DATASETS = frozenset({"cvm.dfp"})


class BuildError(RuntimeError):
    pass


@dataclass
class ResourceReport:
    resource_key: str
    content_sha256: str
    parse: ParseStats
    build: BuildStats
    silver_written: int = 0
    gold_written: int = 0


@dataclass
class BuildReport:
    dataset_id: str
    resources: list[ResourceReport] = field(default_factory=list)

    @property
    def silver_written(self) -> int:
        return sum(r.silver_written for r in self.resources)

    @property
    def gold_written(self) -> int:
        return sum(r.gold_written for r in self.resources)

    @property
    def skipped_total(self) -> int:
        return sum(r.parse.skipped_total + r.build.skipped_total for r in self.resources)


def documents_for(
    conn: duckdb.DuckDBPyConnection, dataset_id: str, resource_keys: list[str] | None = None
) -> list[tuple[str, str, str, str]]:
    """(resource_key, content_sha256, retrieval_id, storage_path) por versao.

    Uma linha por versao distinta de conteudo. Quando o mesmo conteudo foi
    observado varias vezes, usa o retrieval mais antigo: a linhagem aponta para
    a primeira vez que aquele documento entrou no sistema.

    A escolha e por `retrieved_at`, nao por `retrieval_id`: o id e um uuid4, e
    ordena-lo daria uma observacao aleatoria entre as equivalentes. O proprio
    id entra so como desempate, para a consulta continuar deterministica
    quando duas buscas caem no mesmo instante.
    """
    params: list[object] = [dataset_id]
    predicate = "WHERE r.dataset_id = ?"
    if resource_keys:
        placeholders = ", ".join("?" * len(resource_keys))
        predicate += f" AND r.resource_key IN ({placeholders})"
        params.extend(resource_keys)

    return conn.execute(
        f"""
        SELECT r.resource_key,
               r.content_sha256,
               (ARRAY_AGG(r.retrieval_id ORDER BY r.retrieved_at, r.retrieval_id))[1]
                   AS retrieval_id,
               ANY_VALUE(d.storage_path) AS storage_path
        FROM retrieval r
        JOIN raw_document d USING (content_sha256)
        {predicate}
        GROUP BY r.resource_key, r.content_sha256
        ORDER BY r.resource_key, MIN(r.retrieved_at)
        """,
        params,
    ).fetchall()


def build_dataset(
    *,
    dataset_id: str,
    conn: duckdb.DuckDBPyConnection,
    bronze: BronzeStore,
    run: Run,
    resource_keys: list[str] | None = None,
    only_cod_cvm: frozenset[int] | None = None,
) -> BuildReport:
    if dataset_id not in SUPPORTED_DATASETS:
        raise BuildError(
            f"dataset {dataset_id!r} nao suportado no build. "
            f"Suportados: {', '.join(sorted(SUPPORTED_DATASETS))}"
        )

    documents = documents_for(conn, dataset_id, resource_keys)
    if not documents:
        raise BuildError(
            f"nenhum documento de {dataset_id} no bronze"
            + (f" para {resource_keys}" if resource_keys else "")
            + ". Rode `pat fetch` primeiro."
        )

    report = BuildReport(dataset_id=dataset_id)

    for resource_key, content_sha256, retrieval_id, _storage_path in documents:
        content = bronze.read(content_sha256)  # verifica o hash ao ler

        lines, parse_stats = parse_dfp_zip(
            content,
            year=int(resource_key),
            content_sha256=content_sha256,
            retrieval_id=retrieval_id,
            extraction_run_id=run.run_id,
            only_cod_cvm=only_cod_cvm,
        )
        silver_written = silver.write_lines(conn, lines)

        facts, build_stats = gold.build_facts(lines)
        gold_written = gold.write_facts(conn, facts)

        report.resources.append(
            ResourceReport(
                resource_key=resource_key,
                content_sha256=content_sha256,
                parse=parse_stats,
                build=build_stats,
                silver_written=silver_written,
                gold_written=gold_written,
            )
        )

    return report

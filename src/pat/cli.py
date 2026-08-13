"""Interface de linha de comando da Fase 0.

    pat init                            cria diretorios e esquema
    pat sources                         lista providers e datasets
    pat fetch cvm.dfp --year 2020-2024  ingere um dataset
    pat status                          estatisticas do bronze
    pat history cvm.dfp 2024            versoes ja vistas de um recurso
    pat changed                         recursos que mudaram (reapresentacoes)
    pat verify                          reconfere o hash de cada blob
    pat runs                            execucoes recentes
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime

from pat import __version__
from pat.audit.run import new_run
from pat.build import BuildError, build_dataset
from pat.config import resolve_paths
from pat.contracts.common import RunStatus
from pat.ingest import ingest_dataset
from pat.query.asof import AsOf
from pat.sources.base import SourceError
from pat.sources.public.cvm import CVMProvider
from pat.sources.registry import Registry
from pat.store import gold, silver
from pat.store.bronze import BronzeStore
from pat.store.catalog import Catalog
from pat.store.db import connect, migrate


def _human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:,.1f} TB"


def _open(args) -> tuple[BronzeStore, Catalog, object]:
    paths = resolve_paths(args.home).ensure()
    conn = connect(paths.warehouse)
    migrate(conn)
    return BronzeStore(paths.bronze), Catalog(conn), conn


def cmd_init(args) -> int:
    paths = resolve_paths(args.home).ensure()
    conn = connect(paths.warehouse)
    migrate(conn)
    conn.close()
    print(f"PAT_HOME     {paths.home}")
    print(f"bronze       {paths.bronze}")
    print(f"warehouse    {paths.warehouse}")
    print("esquema criado.")
    return 0


def cmd_sources(args) -> int:
    registry = Registry()
    for provider in registry.providers():
        print(f"{provider.provider_id}  (tier={provider.tier}, v{provider.version})")
        for spec in provider.datasets():
            params = f"  params: {', '.join(spec.params)}" if spec.params else ""
            print(f"    {spec.dataset_id:<24} {spec.title}{params}")
            if spec.notes:
                print(f"    {'':<24} nota: {spec.notes}")
        print()
    registry.close()
    return 0


def cmd_fetch(args) -> int:
    params: dict[str, str] = {}
    if args.year:
        params["year"] = args.year

    store, catalog, conn = _open(args)
    registry = Registry()
    run = new_run(command=f"fetch {args.dataset} {params}".strip())
    catalog.start_run(run)

    status = RunStatus.FAILED
    try:
        outcomes = ingest_dataset(
            args.dataset,
            params,
            registry=registry,
            store=store,
            catalog=catalog,
            run=run,
        )
        for outcome in outcomes:
            marker = "novo" if outcome.is_new_content else "inalterado"
            print(
                f"[{marker:>10}] {outcome.ref.dataset_id} {outcome.ref.resource_key}  "
                f"{outcome.document.content_sha256[:12]}  "
                f"{_human_bytes(outcome.document.size_bytes)}"
            )
        new_count = sum(1 for o in outcomes if o.is_new_content)
        print(f"\n{len(outcomes)} recurso(s), {new_count} com conteudo novo. run_id={run.run_id}")
        status = RunStatus.SUCCEEDED
        return 0
    except SourceError as exc:
        print(f"erro de fonte: {exc}", file=sys.stderr)
        return 1
    finally:
        catalog.finish_run(run.run_id, status, datetime.now(UTC))
        registry.close()
        conn.close()


def cmd_status(args) -> int:
    store, catalog, conn = _open(args)
    stats = catalog.stats()
    print(f"documentos   {stats['documents']:,}")
    print(f"volume       {_human_bytes(stats['bytes'])}")
    print(f"retrievals   {stats['retrievals']:,}")
    print(f"runs         {stats['runs']:,}")
    changed = catalog.changed_resources()
    if changed:
        print(f"\nrecursos com mais de uma versao de conteudo: {len(changed)}")
        for dataset_id, key, versions in changed[:10]:
            print(f"  {dataset_id} {key}: {versions} versoes")
        print("  (use `pat history <dataset> <resource>` para detalhar)")
    conn.close()
    return 0


def cmd_history(args) -> int:
    store, catalog, conn = _open(args)
    versions = catalog.resource_versions(args.dataset, args.resource)
    if not versions:
        print(f"nenhum retrieval para {args.dataset} {args.resource}")
    else:
        print(f"{args.dataset} {args.resource}: {len(versions)} versao(oes) de conteudo\n")
        for i, v in enumerate(versions, 1):
            print(f"  {i}. {v.content_sha256}")
            print(f"     visto {v.times_seen}x, de {v.first_retrieved_at} a {v.last_retrieved_at}")
            print(f"     {_human_bytes(v.size_bytes)}   last-modified: {v.last_modified}")
        if len(versions) > 1:
            print("\n  conteudo mudou na origem: reapresentacao provavel.")
    conn.close()
    return 0


def cmd_changed(args) -> int:
    store, catalog, conn = _open(args)
    rows = catalog.changed_resources()
    if not rows:
        print("nenhum recurso com conteudo alterado ate agora.")
    for dataset_id, key, versions in rows:
        print(f"{dataset_id:<24} {key:<12} {versions} versoes")
    conn.close()
    return 0


def cmd_verify(args) -> int:
    paths = resolve_paths(args.home)
    store = BronzeStore(paths.bronze)
    ok, bad = store.verify()
    print(f"blobs verificados: {len(ok)}")
    if bad:
        print(f"CORROMPIDOS: {len(bad)}", file=sys.stderr)
        for sha in bad:
            print(f"  {sha}", file=sys.stderr)
        return 1
    print("bronze integro.")
    return 0


def _fmt_money(value, unit: str) -> str:
    """DFP e publicada em milhares; mostrar os dois evita erro de leitura."""
    return f"{value:>22,.2f} {unit}  ({value / 1000:,.0f} mil)"


def _print_fact(view, prefix: str = "") -> None:
    escopo = "consolidado" if view.consolidated else "individual"
    print(f"{prefix}{view.denom_cia} ({view.cod_cvm})")
    print(f"{prefix}  demonstr.  {view.statement} {escopo}")
    print(f"{prefix}  conta      {view.cd_conta}  {view.ds_conta}")
    if view.coluna_df:
        print(f"{prefix}  coluna     {view.coluna_df}")
    print(f"{prefix}  periodo    {view.period_start or '-'} .. {view.period_end}  [{view.period_type}]")
    print(f"{prefix}  valor      {_fmt_money(view.value, view.unit)}")
    print(f"{prefix}  conhecido  {view.knowledge_date}  (doc {view.source_doc_id} v{view.source_doc_version}, {view.ordem_exerc})")
    print(f"{prefix}  fact_id    {view.fact_id}")


def cmd_build(args) -> int:
    """bronze -> silver -> gold. Deterministico, sem rede."""
    paths = resolve_paths(args.home).ensure()
    conn = connect(paths.warehouse)
    migrate(conn)
    bronze = BronzeStore(paths.bronze)

    years = None
    if args.year:
        years = [str(y) for y in CVMProvider._parse_years(args.year, CVMProvider.DFP_MIN_YEAR)]
    only = frozenset(args.cod_cvm) if args.cod_cvm else None

    run = new_run(command=f"build {args.dataset} year={args.year} cod_cvm={args.cod_cvm}")
    catalog = Catalog(conn)
    catalog.start_run(run)

    status = RunStatus.FAILED
    try:
        report = build_dataset(
            dataset_id=args.dataset,
            conn=conn,
            bronze=bronze,
            run=run,
            resource_keys=years,
            only_cod_cvm=only,
        )
        for resource in report.resources:
            print(
                f"[{resource.resource_key}] {resource.content_sha256[:12]}  "
                f"silver +{resource.silver_written:,}  gold +{resource.gold_written:,}"
            )
            if resource.parse.skipped_filtered_out:
                print(f"           filtradas por --cod-cvm: {resource.parse.skipped_filtered_out:,}")
            descartes = {
                k.removeprefix("skipped_"): v
                for k, v in (resource.parse.as_dict() | resource.build.as_dict()).items()
                if k.startswith("skipped_") and k != "skipped_filtered_out" and v
            }
            if descartes:
                print(f"           PERDIDAS por qualidade de dado: {descartes}")
        print(
            f"\nsilver total {silver.count(conn):,} linhas · "
            f"gold total {gold.count(conn):,} fatos · "
            f"perdidas neste build {report.skipped_total:,}"
        )
        status = RunStatus.SUCCEEDED
        return 0
    except BuildError as exc:
        print(f"erro de build: {exc}", file=sys.stderr)
        return 1
    finally:
        catalog.finish_run(run.run_id, status, datetime.now(UTC))
        conn.close()


def cmd_asof(args) -> int:
    paths = resolve_paths(args.home)
    conn = connect(paths.warehouse, read_only=True)
    asof = AsOf(conn)
    view = asof.value(
        cod_cvm=args.cod_cvm,
        cd_conta=args.conta,
        period_end=date.fromisoformat(args.period_end),
        as_of=date.fromisoformat(args.as_of),
        statement=args.statement,
        consolidated=not args.individual,
        coluna_df=args.coluna,
    )
    if view is None:
        print(f"nada era conhecido em {args.as_of} para essa chave.")
        conn.close()
        return 1
    print(f"AS OF {args.as_of}")
    _print_fact(view, prefix="  ")
    conn.close()
    return 0


def cmd_fact_history(args) -> int:
    paths = resolve_paths(args.home)
    conn = connect(paths.warehouse, read_only=True)
    asof = AsOf(conn)
    views = asof.history(
        cod_cvm=args.cod_cvm,
        cd_conta=args.conta,
        period_end=date.fromisoformat(args.period_end),
        statement=args.statement,
        consolidated=not args.individual,
        coluna_df=args.coluna,
    )
    if not views:
        print("nenhum fato para essa chave.")
        conn.close()
        return 1
    print(f"{len(views)} versao(oes) conhecidas, da mais antiga para a mais recente:\n")
    for i, view in enumerate(views, 1):
        print(f"  {i}.")
        _print_fact(view, prefix="     ")
        print()
    conn.close()
    return 0


def cmd_restatements(args) -> int:
    paths = resolve_paths(args.home)
    conn = connect(paths.warehouse, read_only=True)
    items = AsOf(conn).restatements(
        cod_cvm=args.cod_cvm,
        cd_conta=args.conta,
        statement=args.statement,
        min_abs_pct=args.min_pct,
    )
    if not items:
        print("nenhuma reapresentacao detectada com esses filtros.")
        conn.close()
        return 0
    print(f"{len(items)} reapresentacao(oes):\n")
    for item in items:
        pct = f"{item.delta_pct:+.2f}%" if item.delta_pct is not None else "n/d"
        escopo = "con" if item.consolidated else "ind"
        coluna = f" [{item.coluna_df}]" if item.coluna_df else ""
        print(
            f"  {item.denom_cia} ({item.cod_cvm})  {item.statement}/{escopo} "
            f"{item.cd_conta}{coluna} {item.period_end}"
        )
        print(f"    {item.original.knowledge_date}  {item.original.value / 1000:>18,.0f} mil")
        print(f"    {item.revised.knowledge_date}  {item.revised.value / 1000:>18,.0f} mil   delta {pct}")
        print()
    conn.close()
    return 0


def cmd_provenance(args) -> int:
    paths = resolve_paths(args.home)
    conn = connect(paths.warehouse, read_only=True)
    asof = AsOf(conn)
    prov = asof.provenance(args.fact_id)
    if prov is None:
        print(f"fact_id desconhecido: {args.fact_id}", file=sys.stderr)
        conn.close()
        return 1

    bronze = BronzeStore(paths.bronze)
    print(f"fato        {prov.fact_id}")
    print(f"  conhecido em {prov.knowledge_date} (doc CVM {prov.source_doc_id} v{prov.source_doc_version})")
    print(f"extracao    {prov.extractor} v{prov.extractor_version}  run={prov.extraction_run_id}")
    print(f"  locator    {prov.locator}")
    print(f"retrieval   {prov.retrieval_id}")
    print(f"  provider   {prov.provider_id}  tier={prov.source_tier}")
    print(f"  recurso    {prov.dataset_id} {prov.resource_key}")
    print(f"  url        {prov.url}")
    print(f"  em         {prov.retrieved_at}")
    print(f"documento   {prov.content_sha256}")
    print(f"  {_human_bytes(prov.size_bytes)} em {prov.bronze_path(paths.bronze)}")
    try:
        bronze.read(prov.content_sha256)
        print("  bytes reconferidos: hash confere.")
    except Exception as exc:  # noqa: BLE001 - relatar, nao mascarar
        print(f"  FALHA ao reconferir bytes: {exc}", file=sys.stderr)
        conn.close()
        return 1
    conn.close()
    return 0


def cmd_runs(args) -> int:
    store, catalog, conn = _open(args)
    for run_id, command, status, started, finished in catalog.recent_runs(args.limit):
        print(f"{run_id}  {status:<10} {started}  {command}")
    conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pat", description="Personal Investment Research Agent")
    parser.add_argument("--version", action="version", version=f"personal-pat {__version__}")
    parser.add_argument("--home", default=None, help="Sobrescreve PAT_HOME")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="cria diretorios e esquema").set_defaults(func=cmd_init)
    sub.add_parser("sources", help="lista providers e datasets").set_defaults(func=cmd_sources)

    p_fetch = sub.add_parser("fetch", help="ingere um dataset para o bronze")
    p_fetch.add_argument("dataset", help="ex. cvm.dfp, cvm.itr, cvm.cad_cia_aberta")
    p_fetch.add_argument("--year", help="2024, 2020-2024 ou 2020,2022")
    p_fetch.set_defaults(func=cmd_fetch)

    sub.add_parser("status", help="estatisticas do bronze").set_defaults(func=cmd_status)

    p_hist = sub.add_parser("history", help="versoes de conteudo de um recurso")
    p_hist.add_argument("dataset")
    p_hist.add_argument("resource")
    p_hist.set_defaults(func=cmd_history)

    sub.add_parser("changed", help="recursos alterados na origem").set_defaults(func=cmd_changed)
    sub.add_parser("verify", help="reconfere o hash de cada blob").set_defaults(func=cmd_verify)

    p_build = sub.add_parser("build", help="bronze -> silver -> gold (sem rede)")
    p_build.add_argument("dataset", nargs="?", default="cvm.dfp")
    p_build.add_argument("--year", help="2024, 2020-2024 ou 2020,2022")
    p_build.add_argument(
        "--cod-cvm",
        dest="cod_cvm",
        type=int,
        action="append",
        help="materializa so estas companhias; repetivel. Base do fluxo por projeto.",
    )
    p_build.set_defaults(func=cmd_build)

    def _fact_key(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--cod-cvm", dest="cod_cvm", type=int, required=True)
        parser.add_argument("--conta", required=True, help="codigo de conta CVM, ex. 3.01")
        parser.add_argument("--period-end", dest="period_end", required=True, help="AAAA-MM-DD")
        parser.add_argument("--statement", default="DRE")
        parser.add_argument("--individual", action="store_true", help="usa DF individual")
        parser.add_argument(
            "--coluna", default="", help="COLUNA_DF, obrigatoria so na DMPL"
        )

    p_asof = sub.add_parser("asof", help="valor conhecido em uma data")
    _fact_key(p_asof)
    p_asof.add_argument("--as-of", dest="as_of", required=True, help="AAAA-MM-DD")
    p_asof.set_defaults(func=cmd_asof)

    p_fh = sub.add_parser("fact-history", help="todas as versoes conhecidas de um fato")
    _fact_key(p_fh)
    p_fh.set_defaults(func=cmd_fact_history)

    p_rest = sub.add_parser("restatements", help="fatos cujo valor mudou entre versoes")
    p_rest.add_argument("--cod-cvm", dest="cod_cvm", type=int)
    p_rest.add_argument("--conta")
    p_rest.add_argument("--statement")
    p_rest.add_argument("--min-pct", dest="min_pct", type=float, help="delta minimo em %%")
    p_rest.set_defaults(func=cmd_restatements)

    p_prov = sub.add_parser("provenance", help="cadeia completa de um fato ate os bytes")
    p_prov.add_argument("fact_id")
    p_prov.set_defaults(func=cmd_provenance)

    p_runs = sub.add_parser("runs", help="execucoes recentes")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.set_defaults(func=cmd_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

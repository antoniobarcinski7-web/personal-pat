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
from datetime import UTC, datetime

from pat import __version__
from pat.audit.run import new_run
from pat.config import resolve_paths
from pat.contracts.common import RunStatus
from pat.ingest import ingest_dataset
from pat.sources.base import SourceError
from pat.sources.registry import Registry
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

    p_runs = sub.add_parser("runs", help="execucoes recentes")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.set_defaults(func=cmd_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

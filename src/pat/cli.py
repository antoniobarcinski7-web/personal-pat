"""Interface de linha de comando.

Dados (Fases 0-1)

    pat init                            cria diretorios e esquema
    pat sources                         lista providers e datasets
    pat fetch cvm.dfp --year 2020-2024  ingere um dataset
    pat build cvm.dfp --year 2024       bronze -> silver -> gold
    pat status                          estatisticas do bronze
    pat history cvm.dfp 2024            versoes ja vistas de um recurso
    pat changed                         recursos que mudaram (reapresentacoes)
    pat verify                          reconfere o hash de cada blob
    pat asof / fact-history / restatements / provenance
    pat runs                            execucoes recentes

Semantica (Fase 2)

    pat concepts                        catalogo universal de conceitos
    pat metrics                         metricas registradas
    pat mappings                        conceito -> linha, por regime
    pat metric ebitda@v1 --cod-cvm ...  calcula uma metrica canonica
    pat accounts --statement DVA ...    plano de contas efetivo (para mapear)
    pat mapping-check --cod-cvm ...     os bindings ainda resolvem?

Pesquisa (Fase 3)

    pat capability                      o que o sistema sabe executar
    pat ask --plan-file p.json          executa um plano ja escrito
    pat ask --plan-file p.json --dry-run   valida e para antes de executar

O caminho com modelo sao DUAS invocacoes, de proposito: o plano sai em disco,
um humano le o que o modelo escolheu, e so entao alguma coisa executa.

    pat plan "<pergunta>" --out p.json  pergunta -> plano (usa a API)
    pat ask --plan-file p.json --writer executa e redige (usa a API)

Os dois exigem ANTHROPIC_API_KEY, e falham dizendo isso em vez de cair para um
modo degradado. Sem `--writer`, `pat ask` nao chama modelo nenhum.

Toda consulta da Fase 2 exige `--as-of`, como as da Fase 1: nao existe atalho
que devolva "o valor" sem dizer segundo quando.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime

import duckdb

from pat import __version__
from pat.audit.run import new_run
from pat.build import BuildError, build_dataset
from pat.config import resolve_paths
from pat.contracts.common import RunStatus
from pat.ingest import ingest_dataset
from pat.query.asof import AsOf
from pat.research import DEFAULT_SOURCE
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


def _open_readonly(args):
    """Conexao somente-leitura, ou None com mensagem util.

    Sem isto o DuckDB levanta IOException crua quando o warehouse ainda nao
    existe - o que e o estado normal de quem acabou de clonar o repositorio,
    e portanto merece uma instrucao em vez de um stack trace.
    """
    paths = resolve_paths(args.home)
    if not paths.warehouse.exists():
        print(
            f"warehouse nao encontrado em {paths.warehouse}.\n"
            "Rode `pat init` e depois `pat fetch`/`pat build`.",
            file=sys.stderr,
        )
        return None
    return connect(paths.warehouse, read_only=True)


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
    if (conn := _open_readonly(args)) is None:
        return 1
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
    if (conn := _open_readonly(args)) is None:
        return 1
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
    if (conn := _open_readonly(args)) is None:
        return 1
    items = AsOf(conn).restatements(
        cod_cvm=args.cod_cvm,
        cd_conta=args.conta,
        statement=args.statement,
        consolidated=args.consolidated,
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
    if (conn := _open_readonly(args)) is None:
        return 1
    asof = AsOf(conn)
    prov = asof.provenance(args.fact_id)
    if prov is None:
        print(f"fact_id desconhecido: {args.fact_id}", file=sys.stderr)
        conn.close()
        return 1

    paths = resolve_paths(args.home)
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


# ---------------------------------------------------------------------------
# Fase 5 M5.3 - programa de pesquisa
# ---------------------------------------------------------------------------


def cmd_program(args) -> int:
    """Pergunta -> programa, em dois estagios. Nao executa nada.

    Mesma disciplina de `pat plan`: o programa sai em disco, um humano le o
    que o modelo escolheu investigar, e so entao `pat run-program` roda. A
    diferenca e que aqui o estagio 1 EXECUTA o calculo - deterministicamente,
    sem escrever nada - para que o estagio 2 possa ver a FORMA dos resultados
    antes de escolher o que procurar no corpus. Sem isso ele planejaria a
    busca no escuro, e "por que a receita caiu" viraria uma varredura por
    palavras da pergunta.
    """
    from pathlib import Path

    from pat.contracts.program import ProgramEnvelope
    from pat.research.canonical import program_id as compute_program_id
    from pat.research.capability import build_snapshot
    from pat.research.llm import LLMError
    from pat.research.llm.cache import call_sha256_of
    from pat.research.planner import PlannerError
    from pat.research.program import run_program
    from pat.research.program_planner import plan_compute_stage, plan_evidence_stage
    from pat.semantics import build_engine

    question = _question_from_args(args)

    try:
        llm = _llm_client(args)
    except LLMError as exc:
        print(f"cliente de modelo indisponivel: {exc}", file=sys.stderr)
        return 2

    if (conn := _open_readonly(args)) is None:
        return 1

    provenances = []
    try:
        snapshot = build_snapshot(conn, source=DEFAULT_SOURCE)
        capability = _capability_sha(snapshot)

        try:
            program, prov1 = plan_compute_stage(
                question, snapshot, llm=llm, model=args.model, max_tokens=args.max_tokens
            )
            provenances.append(("compute", prov1))
        except PlannerError as exc:
            print(f"estagio 1 nao produziu um programa: {exc}", file=sys.stderr)
            print(f"  resposta  {exc.response_sha256[:12]}", file=sys.stderr)
            if exc.detail:
                print(f"  trecho    {exc.detail[:200]}", file=sys.stderr)
            return 1
        except LLMError as exc:
            print(f"chamada ao modelo falhou: {exc}", file=sys.stderr)
            return 2

        if program.is_refusal:
            print("PROGRAMA RECUSADO")
            for item in program.unresolved:
                print(f"  {item.kind}: {item.detail}")
                for candidato in item.candidates:
                    print(f"    candidato: {candidato}")
            return 1

        # O estagio intermediario: executa o que ja da para executar, sem
        # persistir, so para extrair a forma. Nao ha escrita nenhuma aqui.
        parcial = run_program(
            conn,
            program,
            engine=build_engine(conn, source=DEFAULT_SOURCE),
            question=question,
            program_id=compute_program_id(program),
            capability_sha256=capability,
        )

        try:
            program, prov2 = plan_evidence_stage(
                question,
                snapshot,
                program,
                parcial.shapes,
                llm=llm,
                model=args.model,
                max_tokens=args.max_tokens,
            )
            provenances.append(("evidence", prov2))
        except PlannerError as exc:
            print(f"estagio 2 nao produziu buscas: {exc}", file=sys.stderr)
            print(f"  resposta  {exc.response_sha256[:12]}", file=sys.stderr)
            return 1
        except LLMError as exc:
            print(f"chamada ao modelo falhou: {exc}", file=sys.stderr)
            return 2
    finally:
        conn.close()

    for _papel, provenance in provenances:
        _record_call(args, provenance, call_sha256=call_sha256_of(provenance.prompt_sha256, llm.fingerprint))

    _print_program(program, compute_program_id(program))

    if args.out:
        envelope = ProgramEnvelope(
            question=question,
            program=program,
            planner_compute=provenances[0][1],
            planner_evidence=provenances[1][1] if len(provenances) > 1 else None,
        )
        Path(args.out).write_text(
            envelope.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
        )
        print(f"\nprograma gravado em {args.out}")
        print(f"Leia antes de executar:  pat run-program --program-file {args.out}")
    return 0


def _print_program(program, program_id: str) -> None:
    print(f"PROGRAMA  {program_id[:12]}")
    print(f"  objetivo   {program.objective}")
    print(f"  AS OF      {program.as_of}   escopo {program.scope}")
    for pergunta in program.questions_to_answer:
        print(f"  pergunta   {pergunta}")

    if program.compute is not None and program.compute.steps:
        print(f"\nCALCULO ({len(program.compute.steps)} passo(s))")
        for passo in program.compute.steps:
            if passo.step_kind == "metric":
                print(
                    f"  {passo.step_id:<22} {passo.metric}  {passo.entity_id}  {passo.period_end}"
                )
            else:
                print(f"  {passo.step_id:<22} {passo.op}({', '.join(passo.inputs)})")

    if program.decompositions:
        print(f"\nDECOMPOSICOES ({len(program.decompositions)})")
        for pedido in program.decompositions:
            print(
                f"  {pedido.request_id:<22} {pedido.decomposition}  "
                f"{pedido.period_from} -> {pedido.period_to}"
            )

    if program.evidence:
        print(f"\nEVIDENCIA ({len(program.evidence)} busca(s))")
        for pedido in program.evidence:
            tipos = f"  [{', '.join(k.value for k in pedido.kinds)}]" if pedido.kinds else ""
            print(f"  {pedido.request_id:<22} {' '.join(pedido.terms)}{tipos}")
            print(f"    porque: {pedido.rationale}")


def cmd_run_program(args) -> int:
    """Executa um programa ja escrito. NENHUMA chamada de modelo.

    O caminho determinístico completo: calculo, decomposicao e evidencia, os
    tres a partir de um arquivo que um humano pode ler antes. E o que prova
    que o resto nao depende do modelo.
    """
    from pathlib import Path

    from pat.contracts.program import ProgramEnvelope
    from pat.research.canonical import program_id as compute_program_id
    from pat.research.capability import build_snapshot
    from pat.research.program import run_program
    from pat.semantics import build_engine

    try:
        envelope = ProgramEnvelope.model_validate_json(
            Path(args.program_file).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        print(f"nao consegui ler o programa: {exc}", file=sys.stderr)
        return 1

    if (conn := _open_readonly(args)) is None:
        return 1
    try:
        snapshot = build_snapshot(conn, source=DEFAULT_SOURCE)
        result = run_program(
            conn,
            envelope.program,
            engine=build_engine(conn, source=DEFAULT_SOURCE),
            question=envelope.question,
            program_id=compute_program_id(envelope.program),
            capability_sha256=_capability_sha(snapshot),
        )
        _print_program_result(result, envelope.program)
        return 0 if result.has_any_output else 1
    finally:
        conn.close()


def _print_program_result(result, program) -> None:
    from pat.research.render import describe, format_value

    print(f"PROGRAMA  {result.program_id[:12]}   AS OF {result.as_of}")

    if result.computations:
        print(f"\nCALCULO ({len(result.computations)})")
        for computation in result.computations:
            print(f"  {computation.step_id:<22} {format_value(computation)}")
            print(f"    {describe(computation)}")
    for falha in result.computation_failures:
        print(f"  {falha.step_id:<22} INDISPONIVEL ({falha.reason})")
        print(f"    {falha.message}")

    for outcome in result.decompositions:
        print()
        if outcome.unavailable is not None:
            print(f"DECOMPOSICAO {outcome.request_id}: INDISPONIVEL")
            print(f"  motivo   {outcome.unavailable.reason}")
            print(f"  {outcome.unavailable.message}")
            continue
        _print_decomposition(outcome.result, outcome.result.entity_id)

    for outcome in result.evidence:
        print()
        pedido = next(
            (p for p in program.evidence if p.request_id == outcome.request_id), None
        )
        titulo = f"EVIDENCIA {outcome.request_id}"
        if pedido is not None:
            titulo += f"   ({' '.join(pedido.terms)})"
        print(titulo)
        if pedido is not None:
            print(f"  porque: {pedido.rationale}")
        if outcome.unavailable is not None:
            print(f"  SEM EVIDENCIA ({outcome.unavailable.reason})")
            print(f"  {outcome.unavailable.message}")
            continue
        for hit in outcome.result.hits:
            quote = hit.quote
            print(
                f"  [{hit.rank}] {quote.published_at}  {quote.document_kind.value}  "
                f"{quote.title[:48]}"
            )
            print(f"      unit {quote.unit_id[:12]}  {quote.locator.as_text()}")
            for linha in _clip(quote.text, 320).splitlines():
                print(f"      | {linha}")

    if result.index_version:
        print(f"\nindice {result.index_version}")
    print("Trechos sao verbatim. Numeros vieram do motor. Nada aqui foi escrito por modelo.")


def _capability_sha(snapshot) -> str:
    from pat.research.canonical import capability_sha256

    return capability_sha256(snapshot)


# ---------------------------------------------------------------------------
# Fase 5 M5.2 - decomposicao quantitativa
# ---------------------------------------------------------------------------


def cmd_decompositions(args) -> int:
    """As identidades declaradas. Nenhuma delas menciona plano de contas."""
    from pat.semantics import decompositions

    for definition in decompositions.all_definitions():
        print(f"{definition.ref}  [eixo {definition.axis.value}]")
        print(f"  {definition.definition}")
        print(f"  alvo       {definition.target_concept}  ({definition.target_label})")
        for term in definition.terms:
            sinal = "+" if term.sign > 0 else "-"
            print(f"    {sinal} {term.concept_id:<26} {term.label}")
        print(f"  tolerancia {definition.tolerance_abs:,.0f} (residual acima disso: nao fecha)")
        print(f"  porque     {' '.join(definition.rationale.split())}")
        print()
    return 0


def cmd_decompose(args) -> int:
    """Variacao de um total -> contribuicoes + residual. Sem modelo nenhum."""
    from pat.contracts.decomposition import DecompositionUnavailable
    from pat.research.decompose import decompose
    from pat.semantics import build_engine

    if (conn := _open_readonly(args)) is None:
        return 1
    try:
        asof = AsOf(conn)
        entity = _resolve_entity(asof, args)
        if entity is None:
            return 1
        entity_id, denom = entity

        result = decompose(
            build_engine(conn),
            args.decomposition,
            entity_id=entity_id,
            period_from=args.period_from,
            period_to=args.period_to,
            scope=_scope(args),
            as_of=args.as_of,
        )

        if isinstance(result, DecompositionUnavailable):
            print(f"{args.decomposition}: INDISPONIVEL", file=sys.stderr)
            print(f"  motivo   {result.reason}", file=sys.stderr)
            print(f"  {result.message}", file=sys.stderr)
            if result.member_id:
                print(f"  membro   {result.member_id}", file=sys.stderr)
            if result.remedy:
                print(f"  saida    {result.remedy}", file=sys.stderr)
            return 1

        _print_decomposition(result, denom)
        return 0
    finally:
        conn.close()


def _print_decomposition(result, denom: str) -> None:
    escopo = "consolidado" if result.scope == "consolidated" else "individual"
    print(f"{result.decomposition_id}@{result.decomposition_version}   [eixo {result.axis.value}]")
    print(f"  empresa    {denom}  ({result.entity_id})")
    print(f"  escopo     {escopo}   periodo {result.period_type}")
    print(f"  AS OF      {result.as_of}   conhecido em {result.knowledge_date}")
    print()

    pct = (
        f"  ({result.target_delta_pct * 100:+,.1f}%)"
        if result.target_delta_pct is not None
        else ""
    )
    print(f"{result.target_label}")
    print(
        f"  {result.period_from}  {result.target_from:>22,.2f} {result.currency}\n"
        f"  {result.period_to}  {result.target_to:>22,.2f} {result.currency}\n"
        f"  variacao    {result.target_delta:>22,.2f} {result.currency}{pct}"
    )
    print()
    print(f"{'CONTRIBUICAO':<36} {'DE':>18} {'PARA':>18} {'EFEITO':>18}  PARTE")
    for contribution in result.ranked():
        share = f"{contribution.share * 100:>6,.1f}%" if contribution.share is not None else "     -"
        print(
            f"{contribution.member_label:<36} {contribution.value_from:>18,.0f} "
            f"{contribution.value_to:>18,.0f} {contribution.contribution:>18,.0f} {share}"
        )

    # O residual sai SEMPRE, inclusive quando e zero. Um relatorio que so o
    # mostra quando incomoda ensina o leitor a nao procurar por ele.
    residual_share = (
        f"{result.residual_share * 100:>6,.1f}%" if result.residual_share is not None else "     -"
    )
    marca = "" if result.closes else "   <-- NAO FECHA"
    print(
        f"{'RESIDUAL (nao explicado)':<36} {'':>18} {'':>18} "
        f"{result.residual:>18,.0f} {residual_share}{marca}"
    )
    print()
    print(f"  fidelidade {result.fidelity}")
    if result.fidelity != "exact":
        print("             ^ montada sobre binding aproximado; ver `pat mappings`")
    print(f"  mapeamento {result.mapping_sha256[:12]}")
    print(f"  tolerancia {result.tolerance_abs:,.0f}")
    if not result.closes:
        print(
            "  O residual excede a tolerancia: as partes nao explicam o todo. "
            "Isso e um ACHADO, nao um detalhe de arredondamento - normalmente "
            "significa que os termos nao vieram da mesma cascata."
        )


# ---------------------------------------------------------------------------
# Fase 5 M5.1 - corpus qualitativo
# ---------------------------------------------------------------------------


def _entity_for_cod_cvm(conn, cod_cvm: int) -> tuple[str, str] | None:
    ref = AsOf(conn).entity_by_cod_cvm(cod_cvm)
    if ref is None:
        print(
            f"nenhum fato no gold para cod_cvm={cod_cvm}. "
            f"Rode `pat build cvm.dfp --cod-cvm {cod_cvm}` primeiro - o corpus "
            "qualitativo se pendura na mesma entidade do quantitativo.",
            file=sys.stderr,
        )
        return None
    return ref.entity_id, ref.denom_cia


def _kinds(raw: list[str]):
    from pat.contracts.corpus import DocumentKind

    out = []
    for value in raw:
        try:
            out.append(DocumentKind(value))
        except ValueError:
            known = ", ".join(sorted(k.value for k in DocumentKind))
            print(f"tipo desconhecido: {value!r}. Disponiveis: {known}", file=sys.stderr)
            return None
    return out


def cmd_docs(args) -> int:
    """O acervo qualitativo de uma empresa - e o que faltou dele."""
    from pat.store import corpus as corpus_store

    kinds = _kinds(args.kind)
    if kinds is None:
        return 1

    if args.sync:
        return _docs_sync(args)

    if (conn := _open_readonly(args)) is None:
        return 1
    resolved = _entity_for_cod_cvm(conn, args.cod_cvm)
    if resolved is None:
        conn.close()
        return 1
    entity_id, denom = resolved

    if args.failures:
        failures = corpus_store.extraction_failures(conn, entity_id)
        if not failures:
            print(f"{denom}: nenhuma falha de extracao registrada.")
        for document_id, reason, message, title in failures:
            print(f"{document_id[:12]}  {reason:<24} {title}")
            print(f"              {message}")
        conn.close()
        return 0

    documents = corpus_store.documents_for_entity(
        conn, entity_id, as_of=args.as_of, kinds=kinds
    )
    if not documents:
        escopo = f" conhecidos em {args.as_of}" if args.as_of else ""
        print(f"{denom}: nenhum documento{escopo} no corpus.")
        print("Popule com `pat docs --cod-cvm ... --sync --year ...`.")
        conn.close()
        return 0

    counts = conn.execute(
        "SELECT document_id, COUNT(*) FROM document_unit GROUP BY document_id"
    ).fetchall()
    units_by_document = dict(counts)

    print(f"{denom}  ({entity_id})")
    if args.as_of:
        print(f"AS OF {args.as_of} - documentos publicados depois disso nao aparecem")
    print(f"{len(documents)} documento(s)\n")
    print(f"{'PUBLICADO':<12} {'TIPO':<21} {'UNID':>5}  TITULO")
    for document in documents:
        units = units_by_document.get(document.document_id, 0)
        marker = "  " if units else " !"
        print(
            f"{document.published_at.isoformat():<12} {document.kind.value:<21} "
            f"{units:>5}{marker}{document.title[:60]}"
        )
    if any(not units_by_document.get(d.document_id) for d in documents):
        print("\n! sem unidade extraida - `pat docs --cod-cvm ... --failures` diz por que")
    conn.close()
    return 0


def _docs_sync(args) -> int:
    from pat.corpus import catalog_entries, sync_documents

    if not args.year:
        print("--sync exige --year (o catalogo IPE e anual)", file=sys.stderr)
        return 1

    bronze, catalog, conn = _open(args)
    resolved = _entity_for_cod_cvm(conn, args.cod_cvm)
    if resolved is None:
        conn.close()
        return 1
    entity_id, denom = resolved

    run = new_run(f"docs --sync --cod-cvm {args.cod_cvm} --year {args.year}")
    catalog.start_run(run)
    registry = Registry()
    try:
        entries = catalog_entries(conn, bronze, year=args.year, cod_cvm=args.cod_cvm)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        catalog.finish_run(run.run_id, RunStatus.FAILED, datetime.now(UTC))
        conn.close()
        return 1

    kinds = _kinds(args.kind)
    if kinds:
        entries = [e for e in entries if e.kind in kinds]

    print(f"{denom}: {len(entries)} documento(s) no catalogo IPE de {args.year}")
    try:
        outcome = sync_documents(
            conn,
            entity_id=entity_id,
            entries=entries,
            registry=registry,
            bronze=bronze,
            catalog=catalog,
            run=run,
            limit=args.limit,
        )
    except SourceError as exc:
        print(f"falha na origem: {exc}", file=sys.stderr)
        catalog.finish_run(run.run_id, RunStatus.FAILED, datetime.now(UTC))
        conn.close()
        return 1
    finally:
        registry.close()

    catalog.finish_run(run.run_id, RunStatus.SUCCEEDED, datetime.now(UTC))
    print(
        f"buscados {outcome.documents_fetched} | novos {outcome.documents_new} | "
        f"ja tinha {outcome.skipped_existing}"
    )
    print(f"unidades {outcome.units_written} | indexadas {outcome.units_indexed}")
    if outcome.failures:
        print(f"\n{outcome.failed} documento(s) NAO extraidos - registrados, nao silenciados:")
        for document_id, reason, title in outcome.failures:
            print(f"  {document_id[:12]}  {reason}  {title[:56]}")
    conn.close()
    return 0


def cmd_evidence(args) -> int:
    """Trechos verbatim. Nenhum modelo participa deste caminho."""
    from pat.contracts.corpus import EvidenceQuery, EvidenceUnavailable
    from pat.corpus.retrieve import retrieve

    kinds = _kinds(args.kind)
    if kinds is None:
        return 1
    if (conn := _open_readonly(args)) is None:
        return 1
    resolved = _entity_for_cod_cvm(conn, args.cod_cvm)
    if resolved is None:
        conn.close()
        return 1
    entity_id, denom = resolved

    terms = tuple(dict.fromkeys(t for t in args.query.split() if t.strip()))
    if not terms:
        print("consulta vazia", file=sys.stderr)
        conn.close()
        return 1

    try:
        query = EvidenceQuery(
            entity_id=entity_id,
            terms=terms,
            as_of=args.as_of,
            kinds=tuple(kinds),
            published_from=args.published_from,
            published_to=args.published_to,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"consulta invalida: {exc}", file=sys.stderr)
        conn.close()
        return 1

    result = retrieve(conn, query)
    if isinstance(result, EvidenceUnavailable):
        print(f"SEM EVIDENCIA  ({result.reason})")
        print(f"  {result.message}")
        if result.documents_excluded_by_as_of:
            print(
                f"  {result.documents_excluded_by_as_of} documento(s) existem mas sao "
                f"posteriores a {result.as_of}"
            )
        if result.remedy:
            print(f"  {result.remedy}")
        conn.close()
        return 1

    print(f"{denom}  AS OF {result.as_of}")
    print(
        f"{result.documents_in_scope} documento(s) e {result.units_in_scope} unidade(s) "
        f"no escopo; {len(result.hits)} trecho(s)"
    )
    print(f"indice {result.index_version}\n")

    for hit in result.hits:
        quote = hit.quote
        print(f"[{hit.rank}] {quote.published_at}  {quote.document_kind.value}  {quote.title[:56]}")
        print(
            f"    unit {quote.unit_id[:12]}  doc {quote.document_id[:12]}  "
            f"{quote.locator.as_text()}  tier={quote.source_tier.value}"
        )
        print(f"    data por: {quote.published_at_basis.value}")
        if quote.speaker:
            marca = "Q&A" if quote.speaker.is_qna else "apresentacao"
            print(f"    quem fala: {quote.speaker.name} ({quote.speaker.role.value}, {marca})")
        text = quote.text if args.full else _clip(quote.text, 420)
        for line in text.splitlines():
            print(f"    | {line}")
        print()

    print("Trechos sao verbatim. `pat provenance-unit <unit_id>` reconfere contra o blob.")
    conn.close()
    return 0


def _clip(text: str, limit: int) -> str:
    """Corta para exibicao, e DIZ que cortou.

    O texto guardado continua inteiro; isto e so a tela. Um corte silencioso
    faria uma citacao parcial parecer completa, que e a forma mais facil de
    tirar uma frase de contexto sem ninguem perceber.
    """
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... {len(text) - limit} caracteres; use --full]"


def cmd_provenance_unit(args) -> int:
    """De uma citacao ate os bytes. O analogo qualitativo de `pat provenance`."""
    from pat.corpus import verify_unit
    from pat.store import corpus as corpus_store

    if (conn := _open_readonly(args)) is None:
        return 1
    unit = corpus_store.read_unit(conn, args.unit_id)
    if unit is None:
        print(f"unit_id desconhecido: {args.unit_id}", file=sys.stderr)
        conn.close()
        return 1
    document = corpus_store.read_document(conn, unit.document_id)
    if document is None:
        print(f"unidade orfa: documento {unit.document_id} ausente", file=sys.stderr)
        conn.close()
        return 1

    paths = resolve_paths(args.home)
    bronze = BronzeStore(paths.bronze)

    print(f"unidade     {unit.unit_id}")
    print(f"  locator    {unit.locator.as_text()}  ordinal={unit.ordinal}")
    print(f"  extracao   {unit.extraction_version}")
    print(f"  {unit.char_count} caracteres")
    print(f"documento   {document.document_id}")
    print(f"  titulo     {document.title}")
    print(f"  tipo       {document.kind.value}  tier={document.source_tier.value}")
    print(f"  publicado  {document.published_at} (base: {document.published_at_basis.value})")
    if document.reference_date:
        print(f"  referencia {document.reference_date} (base do emissor, nao periodo coberto)")
    print(f"  origem     {document.origin_category} v{document.origin_version}")
    print(f"  url        {document.source_url}")
    print(f"  retrieval  {document.retrieval_id}")
    print(f"blob        {_human_bytes(document.byte_size)} em {bronze.path_of(document.document_id)}")

    verification = verify_unit(unit, document, bronze)
    print("\nreconferencia contra os bytes:")
    print(f"  blob presente       {'sim' if verification.blob_found else 'NAO'}")
    print(f"  hash confere        {'sim' if verification.blob_sha256_matches else 'NAO'}")
    if unit.extraction_version != _current_extraction_version():
        print(
            f"  texto confere       n/a (unidade extraida por {unit.extraction_version}, "
            f"o extrator atual e {_current_extraction_version()})"
        )
        print("  Nao e divergencia: e passagem do tempo. A unidade antiga continua valida.")
    else:
        print(f"  texto reextraido    {'IDENTICO' if verification.text_matches else 'DIVERGE'}")

    print("\ntrecho (verbatim):")
    for line in unit.text.splitlines():
        print(f"  | {line}")

    conn.close()
    return 0 if verification.blob_found and verification.blob_sha256_matches else 1


def _current_extraction_version() -> str:
    from pat.corpus.extract import EXTRACTION_VERSION

    return EXTRACTION_VERSION


# ---------------------------------------------------------------------------
# Fase 2 - camada semantica
# ---------------------------------------------------------------------------


def _scope(args):
    from pat.contracts.semantics import ReportingScope

    return ReportingScope.PARENT_ONLY if args.individual else ReportingScope.CONSOLIDATED


def _resolve_entity(asof, args) -> tuple[str, str] | None:
    """(entity_id, denominacao). O usuario fala codigo CVM; o sistema, entity_id."""
    ref = asof.entity_by_cod_cvm(args.cod_cvm)
    if ref is None:
        print(
            f"nenhum fato no gold para cod_cvm={args.cod_cvm}. "
            f"Rode `pat build cvm.dfp --cod-cvm {args.cod_cvm}` primeiro.",
            file=sys.stderr,
        )
        return None
    return ref.entity_id, ref.denom_cia


def cmd_concepts(args) -> int:
    """O catalogo universal. Nao menciona plano de contas de proposito."""
    from pat.semantics import concepts

    for concept in sorted(concepts.CATALOG.values(), key=lambda c: c.concept_id):
        print(f"{concept.concept_id}  ({concept.dimension}, {concept.period_kind})")
        print(f"  {concept.label_en}")
        print(f"  {concept.definition}")
        print(f"  sinal: {concept.sign_convention}")
        for nota in concept.boundary_notes:
            print(f"  - {nota}")
        print()
    return 0


def cmd_metrics(args) -> int:
    from pat.semantics.registry import default_registry

    for metric in default_registry().all():
        d = metric.definition
        print(f"{d.name}@{d.version}  [{d.kind}, {d.dimension}]")
        print(f"  {d.definition}")
        if d.requires_concepts:
            print(f"  conceitos:   {', '.join(d.requires_concepts)}")
        if d.requires_metrics:
            print(f"  depende de:  {', '.join(str(m) for m in d.requires_metrics)}")
        for check in d.checks:
            print(f"  check {check.check_id}: {check.description.splitlines()[0]}")
        print(f"  porque:      {' '.join(d.rationale.split())}")
        print()
    return 0


def cmd_mappings(args) -> int:
    from pat.semantics.loader import load_dir

    for mapping in load_dir().all():
        escopo = f"empresa {mapping.entity_id}" if mapping.entity_id else "familia"
        default = "  [default da fonte]" if mapping.is_default_for_source else ""
        print(f"{mapping.mapping_id}  ({escopo}){default}")
        print(f"  {mapping.framework} · {mapping.taxonomy} · {mapping.jurisdiction} · {mapping.source}")
        if mapping.parent:
            print(f"  herda de   {mapping.parent}")
        if mapping.verified_against:
            print(f"  conferido  {mapping.verified_against} ({mapping.verified_by})")
        print(f"  sha256     {mapping.source_sha256[:16]}")
        for binding in mapping.bindings:
            marca = "" if binding.fidelity == "exact" else f"  <-- {binding.fidelity}"
            print(f"    {binding.concept_id:<24}{marca}")
            for line in binding.lines:
                sinal = "+" if line.sign > 0 else "-"
                print(f"      {sinal} {line.address.as_str()}")
        print()
    return 0


def _print_metric(result) -> None:
    from pat.contracts.semantics import Dimension

    escopo = "consolidado" if result.scope == "consolidated" else "individual"
    ident = " ".join(f"{k}={v}" for k, v in result.local_ids)
    print(f"{result.metric}@{result.metric_version}   [{result.kind}]")
    print(f"  empresa    {result.display_name or result.entity_id}  {ident}")
    print(f"  escopo     {escopo}")
    print(f"  periodo    {result.period_start or '-'} .. {result.period_end}  [{result.period_type}]")
    if result.dimension is Dimension.MONEY:
        print(f"  valor      {result.value:>22,.2f} {result.currency}  ({result.value / 1_000_000:,.1f} milhoes)")
    else:
        print(f"  valor      {result.value:>22,.6f}  ({result.value * 100:,.2f}%)")
    print(f"  conhecido  {result.knowledge_date}   (AS OF {result.as_of})")
    print(f"  fidelidade {result.fidelity}")
    if result.fidelity != "exact":
        print("             ^ montada sobre binding aproximado; ver `pat mappings`")
    print(f"  mapeamento {result.mapping_id} {result.mapping_version} ({result.mapping_sha256[:12]})")
    if not result.mapping_confirmed:
        print("             ^ SEM mapeamento conferido para esta empresa: caiu na familia default")
    print(f"  regime     {result.framework} · {result.jurisdiction}")

    if result.checks:
        print("  checks")
        for check in result.checks:
            linha = f"    {check.status:<15} {check.check_id}"
            if check.observed is not None and check.expected is not None:
                linha += f"   observado {check.observed:,.0f} · esperado {check.expected:,.0f}"
            print(linha)

    print("  insumos")
    for ref in result.inputs:
        if ref.is_metric:
            print(f"    {ref.role:<22} {ref.value:>20,.2f}  (metrica)")
        else:
            sinal = "+" if (ref.sign_applied or 1) > 0 else "-"
            print(f"    {ref.role:<22} {ref.value:>20,.2f}  {sinal} {ref.address}")
            print(f"      fact_id {ref.fact_id}   conhecido {ref.knowledge_date}")


def cmd_metric(args) -> int:
    if (conn := _open_readonly(args)) is None:
        return 1
    try:
        asof = AsOf(conn)
        entity = _resolve_entity(asof, args)
        if entity is None:
            return 1

        from pat.contracts.semantics import MetricUnavailable
        from pat.semantics import build_engine

        engine = build_engine(conn)
        result = engine.compute(
            args.metric,
            entity_id=entity[0],
            period_end=date.fromisoformat(args.period_end),
            scope=_scope(args),
            as_of=date.fromisoformat(args.as_of),
        )
        if isinstance(result, MetricUnavailable):
            print(f"{result.metric}@{result.metric_version}: INDISPONIVEL", file=sys.stderr)
            print(f"  motivo   {result.reason}", file=sys.stderr)
            print(f"  {result.message}", file=sys.stderr)
            if result.concept_id:
                print(f"  conceito {result.concept_id}", file=sys.stderr)
            for endereco in result.tried:
                print(f"  tentado  {endereco}", file=sys.stderr)
            if result.remedy:
                print(f"  saida    {result.remedy}", file=sys.stderr)
            return 1

        _print_metric(result)
        return 0
    finally:
        conn.close()


def cmd_accounts(args) -> int:
    """Plano de contas efetivo de uma companhia: a ferramenta de quem escreve
    mapeamento. O casamento continua sendo feito por um humano."""
    if (conn := _open_readonly(args)) is None:
        return 1
    try:
        asof = AsOf(conn)
        entity = _resolve_entity(asof, args)
        if entity is None:
            return 1

        rows = asof.accounts(
            cod_cvm=args.cod_cvm,
            statement=args.statement,
            period_end=date.fromisoformat(args.period_end),
            as_of=date.fromisoformat(args.as_of),
            consolidated=not args.individual,
        )
        if not rows:
            print(f"nenhuma conta de {args.statement} para essa chave.")
            return 1

        print(f"{entity[1]} ({args.cod_cvm}) · {args.statement} · {args.period_end}\n")
        for row in rows:
            profundidade = row.cd_conta.count(".")
            recuo = "  " * profundidade
            print(f"  {row.cd_conta:<14} {recuo}{row.ds_conta[:44]:<46} {row.value / 1_000_000:>14,.1f} MM")
        print(f"\n{len(rows)} contas. Escolha a linha e escreva o binding com equivalence_basis.")
        return 0
    finally:
        conn.close()


def cmd_mapping_check(args) -> int:
    """Confere que todo binding em uso ainda resolve, e com o rotulo esperado."""
    if (conn := _open_readonly(args)) is None:
        return 1
    try:
        asof = AsOf(conn)
        entity = _resolve_entity(asof, args)
        if entity is None:
            return 1

        from pat.semantics import build_engine
        from pat.semantics.check import OK, check_chain
        from pat.semantics.loader import load_dir

        engine = build_engine(conn)
        chain = load_dir().resolve(entity[0], source="cvm.dfp")
        if chain is None:
            print(f"nenhum mapeamento cobre {entity[0]}.", file=sys.stderr)
            return 1

        resolver = engine.resolver_for(chain.head.taxonomy)
        results = check_chain(
            chain,
            resolver,
            entity_id=entity[0],
            period_end=date.fromisoformat(args.period_end),
            as_of=date.fromisoformat(args.as_of),
            scope=_scope(args),
        )

        print(f"{entity[1]} ({args.cod_cvm}) · cadeia {' <- '.join(m.mapping_id for m in chain.chain)}")
        if not chain.confirmed:
            print("  ATENCAO: sem mapeamento proprio; usando a familia default.\n")
        else:
            print()

        for item in results:
            marca = "ok  " if item.status == OK else "FALHA"
            print(f"  [{marca}] {item.concept_id:<22} {item.address}")
            if item.failed:
                print(f"          {item.status}: {item.detail}")

        falhas = [r for r in results if r.failed]
        print(f"\n{len(results)} binding(s), {len(falhas)} com problema.")
        return 1 if falhas else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fase 3 - camada de pesquisa (caminho deterministico; o modelo entra abaixo)
# ---------------------------------------------------------------------------


def cmd_capability(args) -> int:
    """O que o sistema sabe fazer. Sem warehouse, sai so a parte de catalogo."""
    from pat.research.capability import build_snapshot, snapshot_sha256

    conn = None
    paths = resolve_paths(args.home)
    if paths.warehouse.exists():
        conn = connect(paths.warehouse, read_only=True)
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        snapshot = build_snapshot(conn, as_of=as_of)

        if args.json:
            from pat.research.canonical import canonical_bytes

            print(canonical_bytes(snapshot).decode("utf-8"))
            return 0

        print(f"capability_sha256  {snapshot_sha256(snapshot)}")
        if as_of:
            print(f"cobertura AS OF    {as_of}")
        print()

        print(f"conceitos ({len(snapshot.concepts)})")
        for card in snapshot.concepts:
            print(f"  {card.concept_id:<24} {card.dimension}/{card.period_kind}")
        print(f"\nmetricas ({len(snapshot.metrics)})")
        for card in snapshot.metrics:
            deps = ", ".join(card.requires_metrics) or ", ".join(card.requires_concepts)
            print(f"  {card.ref:<24} [{card.dimension}]  <- {deps}")
        print(f"\nmapeamentos ({len(snapshot.mappings)})")
        for card in snapshot.mappings:
            escopo = f"empresa {card.entity_id}" if card.entity_id else "familia"
            marca = "  [default]" if card.is_default_for_source else ""
            print(f"  {card.mapping_id:<40} {escopo}{marca}  fidelidade {card.weakest_fidelity}")
        print(f"\nderivacoes ({len(snapshot.derivations)})")
        for card in snapshot.derivations:
            print(f"  {card.op:<12} aridade {card.arity:<5} -> {card.output_dimension}")

        print(f"\nentidades ({len(snapshot.entities)})")
        if not snapshot.entities:
            print("  nenhuma. Rode `pat init`, `pat fetch` e `pat build` primeiro.")
        for card in snapshot.entities:
            periodos = ", ".join(str(p) for p in card.period_ends)
            conferido = "mapeamento proprio" if card.has_own_mapping else "familia default"
            print(f"  {card.entity_id}  ({card.denom_cia})")
            print(f"    cod_cvm {card.cod_cvm} · {conferido}")
            print(f"    periodos {periodos}")
        return 0
    finally:
        if conn is not None:
            conn.close()


def _print_research(outcome, *, plan, question) -> None:
    print(f"QUESTION  {plan.question_id[:12]}  as_of {plan.as_of}  scope {plan.scope}")
    print(f"PLAN      {outcome.plan_id[:12]}  {plan.objective}")
    print(f"CAPABILITY {outcome.capability_sha256[:12]}")
    print("\n  steps")
    for step in plan.steps:
        if step.step_kind == "metric":
            print(
                f"    {step.step_id:<16} metric  {str(step.metric):<20} "
                f"{step.entity_id}  {step.period_end}"
            )
        else:
            print(
                f"    {step.step_id:<16} deriv   {step.op:<20} "
                f"[{', '.join(step.inputs)}]"
            )
    print(f"\n  outputs      {', '.join(plan.outputs)}")
    for premissa in plan.assumptions:
        print(f"  assumption   {premissa}")
    print(f"  unresolved   {len(plan.unresolved) or '(nenhuma)'}")


def _print_problems(outcome) -> None:
    for violation in outcome.violations:
        onde = f" [{violation.step_id}]" if violation.step_id else ""
        print(f"  {violation.code}{onde}: {violation.message}", file=sys.stderr)
        if violation.remedy:
            print(f"    saida: {violation.remedy}", file=sys.stderr)
    for issue in outcome.issues:
        onde = f" [{issue.step_id}]" if issue.step_id else ""
        print(f"  {issue.code}{onde}: {issue.message}", file=sys.stderr)
        if issue.remedy:
            print(f"    saida: {issue.remedy}", file=sys.stderr)


def _record_manifest(args, manifest) -> bool | None:
    """Grava o manifesto da corrida. None quando nao houve execucao.

    Conexao propria, de escrita, aberta *depois* que a de leitura fechou: a
    execucao le o warehouse em modo somente-leitura e isso nao muda. O
    manifesto e registro de auditoria, nao dado analitico, e entra pela unica
    porta que escreve.
    """
    if manifest is None:
        return None

    from pat.store.research import write_manifest

    conn = connect(resolve_paths(args.home).warehouse)
    try:
        migrate(conn)  # research_run e aditiva: warehouse antigo migra sozinho
        return write_manifest(conn, manifest)
    finally:
        conn.close()


def cmd_ask(args) -> int:
    """Executa um plano ja escrito. Sem `--writer`, nao chama modelo nenhum.

    O escritor e opt-in e continuara sendo. Nao e cautela: `pat ask` e o
    comando que prova que a camada deterministica roda inteira sem
    credencial, sem rede e sem custo, e liga-lo por default apagaria essa
    prova - o caminho barato viraria um caminho que ninguem mais exercita.
    """
    from pathlib import Path

    from pat.audit.run import current_git_sha
    from pat.research import load_envelope, review_plan, run_plan
    from pat.research.llm import LLMError
    from pat.research.llm.cache import call_sha256_of
    from pat.research.writer import WriterError

    if not args.plan_file:
        print(
            "passe --plan-file com um envelope {question, plan}. "
            "Use `pat plan` para produzir um a partir de uma pergunta.",
            file=sys.stderr,
        )
        return 2

    payload = Path(args.plan_file).read_bytes()
    envelope = load_envelope(payload)
    plan, question = envelope.plan, envelope.question

    if (conn := _open_readonly(args)) is None:
        return 1
    try:
        if args.dry_run:
            outcome = review_plan(conn, plan=plan, question=question)
            _print_research(outcome, plan=plan, question=question)
            print()
            if not outcome.executable:
                print("VALIDACAO  RECUSADO", file=sys.stderr)
                _print_problems(outcome)
                return 1
            print("VALIDACAO  ok - 0 violacoes, 0 pendencias")
            print("nao executado (--dry-run)")
            return 0

        escritor = None
        if not args.no_writer:
            try:
                escritor = _llm_client(args)
            except LLMError as exc:
                print(f"cliente de modelo indisponivel: {exc}", file=sys.stderr)
                return 2

        try:
            outcome = run_plan(
                conn,
                plan=plan,
                question=question,
                git_sha=current_git_sha(),
                llm=escritor,
                model=args.model if escritor else None,
            )
        except WriterError as exc:
            # Erro nomeado, nao stack trace, e nao ha caminho de fallback para
            # a prosa deterministica: cair para ela em silencio apresentaria um
            # texto que ninguem pediu como se fosse o pedido. Quem quer o
            # deterministico roda sem --writer.
            print(f"o modelo nao produziu uma resposta: {exc}", file=sys.stderr)
            print(f"  resposta  {exc.response_sha256[:12]}", file=sys.stderr)
            if exc.detail:
                print(f"  trecho    {exc.detail[:200]}", file=sys.stderr)
            return 1
        except LLMError as exc:
            print(f"chamada ao modelo falhou: {exc}", file=sys.stderr)
            return 2

        # Libera o lock do warehouse antes de gravar: o DuckDB nao aceita uma
        # conexao de escrita enquanto a de leitura do mesmo arquivo esta aberta.
        conn.close()
        gravado = _record_manifest(args, outcome.manifest)
        if outcome.writer is not None:
            _record_call(
                args,
                outcome.writer.provenance,
                call_sha256=call_sha256_of(
                    outcome.writer.provenance.prompt_sha256, escritor.fingerprint
                ),
                kind="writer",
                manifest_id=outcome.manifest.manifest_id,
            )

        _print_research(outcome, plan=plan, question=question)
        print()
        if not outcome.executable:
            print("VALIDACAO  RECUSADO", file=sys.stderr)
            _print_problems(outcome)
            return 1

        for result in outcome.execution.results:
            if result.metric_result is not None:
                print()
                _print_metric(result.metric_result)
            else:
                derivada = result.derived
                print(f"\n{result.step_id}   [{derivada.op}]")
                print(f"  valor      {derivada.value}")
                print(f"  fidelidade {derivada.fidelity}")
                print(f"  derivado de {', '.join(d[:12] for d in derivada.derived_from)}")

        for failure in outcome.execution.failures:
            print(f"\n{failure.step_id}: INDISPONIVEL", file=sys.stderr)
            print(f"  motivo   {failure.reason}", file=sys.stderr)
            print(f"  {failure.message}", file=sys.stderr)
            if failure.unavailable and failure.unavailable.tried:
                for endereco in failure.unavailable.tried:
                    print(f"  tentado  {endereco}", file=sys.stderr)
            if failure.remedy:
                print(f"  saida    {failure.remedy}", file=sys.stderr)

        if outcome.answer is None:
            print("\nsem resposta: alguma saida do plano nao foi calculada.", file=sys.stderr)
            return 1

        print("\nRESPOSTA")
        print(f"  {outcome.answer.prose}")
        for aviso in outcome.answer.warnings:
            print(f"  aviso [{aviso.kind}] {aviso.message}")
        if not outcome.answer.warnings:
            print("  (sem ressalvas)")

        print("\nCITACOES")
        for claim in outcome.answer.claims:
            if claim.claim_kind == "numeric":
                print(f"  {claim.token:<28} {claim.rendered_value:<16} {claim.result_id[:12]}")
                print(f"    {claim.means}")

        # Impressas separadas, e nunca no mesmo bloco dos numeros: a distincao
        # entre "isto foi medido" e "isto e uma leitura" so vale se ela
        # sobreviver ate a tela. Cada leitura sai com os result_id em que se
        # apoia, para que a base seja conferivel sem sair do terminal.
        leituras = [c for c in outcome.answer.claims if c.claim_kind == "interpretive"]
        if leituras:
            print("\nLEITURAS  (afirmacao do modelo, nao medicao)")
            for claim in leituras:
                print(f"  {claim.text}")
                print(f"    apoia-se em {', '.join(s[:12] for s in claim.supports)}")

        if outcome.writer is not None:
            _print_provenance(outcome.writer.provenance)

        manifesto = outcome.manifest
        print("\nMANIFESTO")
        print(f"  manifest_id  {manifesto.manifest_id}")
        print(f"  plan_id      {manifesto.plan_id}")
        print(f"  metricas     {', '.join(manifesto.metric_versions)}")
        print(f"  mapeamentos  {', '.join(s[:12] for s in manifesto.mapping_sha256s)}")
        print(f"  fatos        {len(manifesto.fact_ids)} folha(s)")
        print(f"  pat          {manifesto.pat_version} · git {manifesto.git_sha or '-'}")
        print(f"  registrado   {'sim' if gravado else 'ja constava'} em research_run")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fase 3 - Milestone 2.3: o caminho real ate o modelo
# ---------------------------------------------------------------------------

DEFAULT_PLANNER_MODEL = "claude-opus-5"
"""Escolher o modelo e decisao de produto, e por isso ela mora aqui e nao na
biblioteca: `build_request` recusa default proprio de proposito. Aqui e a
borda do programa, onde uma escolha padrao e configuracao e nao regra."""


def _llm_client(args):
    """O cliente concreto, montado na borda. Serve o planejador e o escritor.

    Um ponto de montagem para os dois, e nao um por uso: quem escolhe provider,
    cache e credencial e a mesma decisao nos dois casos, e duplicar a montagem
    faria `pat plan` e `pat ask --writer` poderem divergir em silencio - por
    exemplo, um com cache e outro sem.

    Esta funcao existe separada por duas razoes. A primeira e arquitetural: a
    camada de pesquisa fala com o modelo por Protocol e nao pode construir um
    adapter sem ganhar saida de rede no import - montar aqui e o que mantem o
    guard de camada dizendo o que ele diz. A segunda e de teste: com um ponto
    de montagem so, o caminho inteiro do CLI roda contra um cliente falso sem
    que nada do resto precise de um seam proprio.

    O cache envolve o adapter, nunca o contrario. `CachedLLMClient` delega o
    `fingerprint` para quem de fato responde, entao envolver nao muda a
    identidade da chamada - se mudasse, a chave passaria a depender de estar em
    cache, que e circular.
    """
    from pat.research.llm.anthropic import AnthropicClient
    from pat.research.llm.cache import CachedLLMClient
    from pat.research.llm.store import FileLLMCache

    client = AnthropicClient()
    if args.no_cache:
        return client
    return CachedLLMClient(client, FileLLMCache(resolve_paths(args.home).ensure().llm))


def _question_from_args(args):
    """Os `pinned_*` do CLI viram os `pinned_*` do contrato, ordenados.

    A ordenacao nao e estetica: `question_id` e hash da pergunta canonica, e o
    contrato exige tupla ordenada justamente para que `--entity A --entity B` e
    `--entity B --entity A` sejam a mesma pergunta. Sem isso, a ordem em que
    alguem digitou os argumentos viraria uma pergunta diferente, e o cache
    nunca acertaria duas vezes seguidas.
    """
    from pat.contracts.research import OutputKind, ResearchQuestion
    from pat.contracts.semantics import ReportingScope

    return ResearchQuestion(
        text=args.text,
        as_of=date.fromisoformat(args.as_of) if args.as_of else date.today(),
        asked_at=datetime.now(UTC),
        requested_output=OutputKind(args.output),
        pinned_entities=tuple(sorted(set(args.entity or ()))),
        pinned_periods=tuple(sorted({date.fromisoformat(p) for p in (args.period or ())})),
        pinned_scope=ReportingScope(args.scope) if args.scope else None,
    )


def _print_provenance(provenance) -> None:
    print("\nPROCEDENCIA")
    print(f"  modelo       {provenance.model_id}")
    print(f"  cliente      {provenance.client_fingerprint}")
    print(f"  chamado em   {provenance.called_at}  {'(cache)' if provenance.cached else ''}")
    temperatura = provenance.temperature
    print(f"  temperatura  {'nao pedida' if temperatura is None else temperatura}")
    print(f"  max_tokens   {provenance.max_tokens}")
    print(f"  prompt       {provenance.prompt_sha256[:12]}")
    print(f"  resposta     {provenance.response_sha256[:12]}")
    print(f"  capability   {provenance.capability_sha256[:12]}")


def _record_call(
    args, provenance, *, call_sha256: str, kind: str = "planner", manifest_id: str | None = None
) -> None:
    """Grava a chamada em `llm_call`.

    `manifest_id` nulo e o caso normal do planejador, e nao uma lacuna: `pat
    plan` planeja e para, e manifesto so existe quando algo executa. E
    precisamente a situacao que fez a coluna ser nulavel (M-3) - a chamada
    custou dinheiro e tem que ter rastro mesmo quando o plano e recusado.
    `pat runs` nao a mostra; ela sai em `orphan_calls`.

    A do escritor e o caso oposto e por isso passa o manifesto: ela so existe
    porque uma execucao existiu, e a ligacao entre o texto e a corrida que o
    produziu e exatamente o que se quer poder consultar depois.
    """
    from pat.store.llm_calls import write_call

    conn = connect(resolve_paths(args.home).warehouse)
    try:
        migrate(conn)  # llm_call e aditiva: warehouse antigo migra sozinho
        write_call(
            conn, provenance, call_sha256=call_sha256, kind=kind, manifest_id=manifest_id
        )
    finally:
        conn.close()


def cmd_plan(args) -> int:
    """Pergunta -> modelo -> plano validado. Nao executa.

    A separacao entre planejar e executar continua sendo duas invocacoes de
    proposito: o plano sai em disco com `--out`, um humano le, e so entao `pat
    ask` roda. Encadear os dois num comando so faria a primeira execucao de um
    plano acontecer antes de qualquer chance de ler o que o modelo escolheu.
    """
    from pathlib import Path

    from pat.contracts.research import PlanEnvelope
    from pat.research import plan_for_question, review_plan
    from pat.research.llm import LLMError
    from pat.research.llm.cache import call_sha256_of
    from pat.research.planner import PlannerError

    question = _question_from_args(args)

    try:
        llm = _llm_client(args)
    except LLMError as exc:
        print(f"cliente de modelo indisponivel: {exc}", file=sys.stderr)
        return 2

    if (conn := _open_readonly(args)) is None:
        return 1

    try:
        try:
            planned = plan_for_question(
                conn, question=question, llm=llm, model=args.model, max_tokens=args.max_tokens
            )
        except PlannerError as exc:
            # Erro nomeado, nao stack trace: o `response_sha256` recupera o
            # texto cru no cache, entao o diagnostico nao depende de a
            # mensagem ter copiado o suficiente da resposta.
            print(f"o modelo nao produziu um plano: {exc}", file=sys.stderr)
            print(f"  resposta  {exc.response_sha256[:12]}", file=sys.stderr)
            if exc.detail:
                print(f"  trecho    {exc.detail[:200]}", file=sys.stderr)
            return 1
        except LLMError as exc:
            print(f"chamada ao modelo falhou: {exc}", file=sys.stderr)
            return 2

        plan = planned.plan
        outcome = review_plan(conn, plan=plan, question=question)
    finally:
        conn.close()

    # Depois de fechar a leitura: o DuckDB nao aceita conexao de escrita
    # enquanto a de leitura do mesmo arquivo esta aberta. A gravacao acontece
    # mesmo quando a validacao recusa - uma chamada recusada e exatamente a
    # que precisa de rastro.
    chave = call_sha256_of(planned.provenance.prompt_sha256, llm.fingerprint)
    _record_call(args, planned.provenance, call_sha256=chave)

    _print_research(outcome, plan=plan, question=question)
    _print_provenance(planned.provenance)

    if args.out:
        envelope = PlanEnvelope(question=question, plan=plan)
        # `model_dump_json`, e nao `canonical_bytes`: a forma canonica existe
        # para hashear e nao para reler - ela achata `MetricRef` em
        # "nome@versao", que e ambiguo na volta. O arquivo aqui e entrada de
        # `pat ask`, entao precisa da forma que o contrato aceita de volta.
        Path(args.out).write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
        print(f"\n  plano gravado em {args.out}")
        print(f"  execute com: pat ask --plan-file {args.out}")

    print()
    if not outcome.executable:
        print("VALIDACAO  RECUSADO", file=sys.stderr)
        _print_problems(outcome)
        return 1

    print("VALIDACAO  ok - 0 violacoes, 0 pendencias")
    print("nao executado: `pat plan` planeja e para.")
    return 0


def cmd_serve(args) -> int:
    """Sobe o chat local. Uma casca sobre `pat plan` + `pat ask --writer`.

    Recusa na PARTIDA, e nao no primeiro turno: sem warehouse ou sem
    credencial, o servico nao tem o que fazer, e descobrir isso depois de
    digitar a primeira pergunta desperdicaria a unica coisa que o usuario ainda
    nao tinha - a pergunta escrita.

    O cliente vem de `_llm_client`, o mesmo ponto de montagem de `pat plan` e
    `pat ask --writer`. Montar outro aqui faria o chat poder divergir deles em
    silencio - por exemplo, um com cache e outro sem.
    """
    from pat.audit.run import current_git_sha
    from pat.chat import ChatService
    from pat.chat.http import HOST, serve
    from pat.research.llm import LLMError

    paths = resolve_paths(args.home)
    if not paths.warehouse.exists():
        print(
            f"warehouse nao encontrado em {paths.warehouse}.\n"
            "Rode `pat init` e depois `pat fetch`/`pat build`.",
            file=sys.stderr,
        )
        return 1

    try:
        llm = _llm_client(args)
    except LLMError as exc:
        print(f"cliente de modelo indisponivel: {exc}", file=sys.stderr)
        return 2

    paths.ensure()
    service = ChatService(
        paths=paths,
        llm=llm,
        model=args.model,
        git_sha=current_git_sha(),
        default_as_of=date.fromisoformat(args.as_of) if args.as_of else None,
    )
    service.snapshot()  # falha aqui, antes de aceitar conexao, se o warehouse nao servir

    print(f"pat chat em http://{HOST}:{args.port}")
    print(f"  modelo     {args.model}")
    print(f"  warehouse  {paths.warehouse}")
    print("  ctrl-c para parar")
    try:
        serve(service, port=args.port)
    except KeyboardInterrupt:
        print("\nencerrado.")
    return 0


def cmd_runs(args) -> int:
    """Corridas de ingestao por default; de pesquisa com --research.

    Duas listas separadas porque sao dois manifestos distintos: um prova de
    onde vieram os *bytes*, outro de onde veio um *numero*.
    """
    if args.research:
        return _print_research_runs(args)

    store, catalog, conn = _open(args)
    for run_id, command, status, started, finished in catalog.recent_runs(args.limit):
        print(f"{run_id}  {status:<10} {started}  {command}")
    conn.close()
    return 0


def _print_research_runs(args) -> int:
    from pat.store.research import read_manifest, recent_manifests

    if (conn := _open_readonly(args)) is None:
        return 1
    try:
        if isinstance(args.research, str):
            row = read_manifest(conn, args.research)
            if row is None:
                print(f"manifesto desconhecido: {args.research}", file=sys.stderr)
                return 1
            rows = [row]
        else:
            rows = recent_manifests(conn, args.limit)
            if not rows:
                print("nenhuma corrida de pesquisa registrada. Rode `pat ask`.")
                return 0
    except duckdb.CatalogException:
        print(
            "warehouse sem a tabela research_run. Rode `pat init` para migrar.",
            file=sys.stderr,
        )
        return 1
    finally:
        conn.close()

    for row in rows:
        estado = "ok" if row.outputs_available else "SAIDA INDISPONIVEL"
        print(f"{row.manifest_id}  {estado}")
        print(f"  executado   {row.executed_at}  AS OF {row.as_of}")
        print(f"  plano       {row.plan_id[:12]}  pergunta {row.question_id[:12]}")
        print(f"  capability  {row.capability_sha256[:12]}")
        print(f"  metricas    {', '.join(row.metric_versions) or '-'}")
        print(f"  mapeamentos {', '.join(s[:12] for s in row.mapping_sha256s) or '-'}")
        print(f"  resultados  {len(row.result_ids)}  ·  fatos folha {len(row.fact_ids)}")
        print(f"  pat         {row.pat_version} · python {row.python_version} · "
              f"git {row.git_sha or '-'}")
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
    # Tri-estado: sem flag, lista individual e consolidado. Uma reapresentacao
    # costuma atingir os dois, e omitir um deles daria uma visao parcial.
    escopo = p_rest.add_mutually_exclusive_group()
    escopo.add_argument(
        "--consolidated", dest="consolidated", action="store_const", const=True, default=None
    )
    escopo.add_argument(
        "--individual", dest="consolidated", action="store_const", const=False
    )
    p_rest.add_argument("--min-pct", dest="min_pct", type=float, help="delta minimo em %%")
    p_rest.set_defaults(func=cmd_restatements)

    p_prov = sub.add_parser("provenance", help="cadeia completa de um fato ate os bytes")
    p_prov.add_argument("fact_id")
    p_prov.set_defaults(func=cmd_provenance)

    # -- Fase 2: camada semantica -------------------------------------------

    sub.add_parser("concepts", help="catalogo universal de conceitos").set_defaults(
        func=cmd_concepts
    )
    sub.add_parser("metrics", help="metricas registradas e suas definicoes").set_defaults(
        func=cmd_metrics
    )
    sub.add_parser("mappings", help="mapeamentos conceito -> linha, por regime").set_defaults(
        func=cmd_mappings
    )

    def _metric_key(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--cod-cvm", dest="cod_cvm", type=int, required=True)
        parser.add_argument("--period-end", dest="period_end", required=True, help="AAAA-MM-DD")
        parser.add_argument(
            "--as-of", dest="as_of", required=True, help="AAAA-MM-DD; nao ha consulta sem ela"
        )
        parser.add_argument(
            "--individual", action="store_true", help="usa a DF individual (default: consolidada)"
        )

    p_metric = sub.add_parser("metric", help="calcula uma metrica canonica")
    p_metric.add_argument("metric", help="nome@versao, ex. ebitda@v1")
    _metric_key(p_metric)
    p_metric.set_defaults(func=cmd_metric)

    p_accounts = sub.add_parser(
        "accounts", help="plano de contas efetivo de uma companhia (para escrever mapeamento)"
    )
    _metric_key(p_accounts)
    p_accounts.add_argument("--statement", default="DRE", help="DRE, BPA, BPP, DFC_MI, DVA...")
    p_accounts.set_defaults(func=cmd_accounts)

    p_mcheck = sub.add_parser(
        "mapping-check", help="confere que os bindings ainda resolvem na fonte"
    )
    _metric_key(p_mcheck)
    p_mcheck.set_defaults(func=cmd_mapping_check)

    # -- Fase 3: camada de pesquisa ------------------------------------------

    p_cap = sub.add_parser("capability", help="o que o sistema sabe executar")
    p_cap.add_argument("--as-of", dest="as_of", help="AAAA-MM-DD; recorta a cobertura")
    p_cap.add_argument("--json", action="store_true", help="bytes canonicos do snapshot")
    p_cap.set_defaults(func=cmd_capability)

    p_ask = sub.add_parser("ask", help="executa um plano de pesquisa (sem LLM)")
    p_ask.add_argument(
        "--plan-file",
        dest="plan_file",
        required=True,
        help="JSON com {question, plan}; produza um com `pat plan --out`",
    )
    p_ask.add_argument("--dry-run", dest="dry_run", action="store_true", help="valida e para")
    # O escritor e opt-in, e o default nao vai mudar: `pat ask` e o comando que
    # prova que a camada deterministica roda inteira sem credencial, sem rede e
    # sem custo. `--no-writer` continua aceito porque ja era o default - existe
    # para poder ser escrito por extenso num script, nao para desligar algo.
    p_ask.add_argument(
        "--writer",
        dest="no_writer",
        action="store_false",
        default=True,
        help="redige a resposta com o modelo (usa a API); sem isso, prosa deterministica",
    )
    p_ask.add_argument(
        "--no-writer", dest="no_writer", action="store_true", help="prosa deterministica (default)"
    )
    p_ask.add_argument("--model", default=DEFAULT_PLANNER_MODEL, help="so com --writer")
    p_ask.add_argument(
        "--no-cache",
        dest="no_cache",
        action="store_true",
        help="ignora o cache de chamadas e paga a chamada de novo",
    )
    p_ask.set_defaults(func=cmd_ask)

    p_plan = sub.add_parser("plan", help="pergunta -> plano, pelo modelo (usa a API)")
    p_plan.add_argument("text", help="a pergunta, em linguagem natural")
    p_plan.add_argument("--as-of", dest="as_of", help="data de conhecimento (default: hoje)")
    p_plan.add_argument(
        "--output",
        default="number",
        choices=["number", "series", "comparison", "narrative"],
        help="formato pedido",
    )
    p_plan.add_argument(
        "--entity", action="append", help="fixa uma entidade (repetivel); o modelo nao contradiz"
    )
    p_plan.add_argument("--period", action="append", help="fixa um period_end (repetivel)")
    p_plan.add_argument("--scope", choices=["consolidated", "parent_only"], help="fixa o escopo")
    p_plan.add_argument("--model", default=DEFAULT_PLANNER_MODEL)
    p_plan.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
    p_plan.add_argument(
        "--no-cache",
        dest="no_cache",
        action="store_true",
        help="ignora o cache de chamadas e paga a chamada de novo",
    )
    p_plan.add_argument("--out", help="grava o envelope {question, plan} para `pat ask`")
    p_plan.set_defaults(func=cmd_plan)

    # -- Fase 5 M5.3: programa de pesquisa -----------------------------------
    p_prog = sub.add_parser(
        "program", help="pergunta -> programa de pesquisa, em dois estagios (usa a API)"
    )
    p_prog.add_argument("text", help="a pergunta, em linguagem natural")
    p_prog.add_argument("--as-of", dest="as_of", help="data de conhecimento (default: hoje)")
    p_prog.add_argument(
        "--output", default="narrative",
        choices=["number", "series", "comparison", "narrative"],
    )
    p_prog.add_argument("--entity", action="append")
    p_prog.add_argument("--period", action="append")
    p_prog.add_argument("--scope", choices=["consolidated", "parent_only"])
    p_prog.add_argument("--model", default=DEFAULT_PLANNER_MODEL)
    p_prog.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
    p_prog.add_argument("--no-cache", dest="no_cache", action="store_true")
    p_prog.add_argument("--out", help="grava o envelope {question, program}")
    p_prog.set_defaults(func=cmd_program)

    p_runp = sub.add_parser(
        "run-program", help="executa um programa ja escrito (SEM modelo)"
    )
    p_runp.add_argument("--program-file", dest="program_file", required=True)
    p_runp.set_defaults(func=cmd_run_program)

    # -- Fase 4: a casca conversacional --------------------------------------

    p_serve = sub.add_parser("serve", help="sobe o chat local (usa a API)")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--model", default=DEFAULT_PLANNER_MODEL)
    p_serve.add_argument(
        "--as-of",
        dest="as_of",
        help="data de conhecimento das sessoes novas (default: hoje)",
    )
    p_serve.add_argument(
        "--no-cache",
        dest="no_cache",
        action="store_true",
        help="ignora o cache de chamadas e paga a chamada de novo",
    )
    # Sem `--host`: ver o cabecalho de `chat/http.py`.
    p_serve.set_defaults(func=cmd_serve)

    # -- Fase 5 M5.2: decomposicao quantitativa ------------------------------
    p_dec = sub.add_parser(
        "decompositions", help="identidades contabeis declaradas, e seus termos"
    )
    p_dec.set_defaults(func=cmd_decompositions)

    p_dc = sub.add_parser("decompose", help="abre a variacao de um total em contribuicoes")
    p_dc.add_argument("decomposition", help="'nome@versao', ex. ebit_by_line@v1")
    p_dc.add_argument("--cod-cvm", type=int, required=True)
    p_dc.add_argument("--from", dest="period_from", type=date.fromisoformat, required=True)
    p_dc.add_argument("--to", dest="period_to", type=date.fromisoformat, required=True)
    p_dc.add_argument("--as-of", type=date.fromisoformat, required=True)
    p_dc.add_argument("--individual", action="store_true", help="escopo individual")
    p_dc.set_defaults(func=cmd_decompose)

    # -- Fase 5 M5.1: corpus qualitativo ------------------------------------
    p_docs = sub.add_parser("docs", help="documentos qualitativos de uma empresa")
    p_docs.add_argument("--cod-cvm", type=int, required=True)
    p_docs.add_argument(
        "--sync",
        action="store_true",
        help="busca do catalogo IPE os documentos ainda ausentes (usa rede)",
    )
    p_docs.add_argument("--year", type=int, help="ano do catalogo IPE, exigido por --sync")
    p_docs.add_argument("--kind", action="append", default=[], help="filtra por DocumentKind")
    p_docs.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="lista so o que era conhecido nesta data; sem ela, lista o acervo",
    )
    p_docs.add_argument("--limit", type=int, default=None, help="teto de documentos no --sync")
    p_docs.add_argument("--failures", action="store_true", help="lista as falhas de extracao")
    p_docs.set_defaults(func=cmd_docs)

    p_ev = sub.add_parser("evidence", help="trechos verbatim, com procedencia (sem LLM)")
    p_ev.add_argument("--cod-cvm", type=int, required=True)
    p_ev.add_argument("--query", required=True, help="termos de busca, separados por espaco")
    p_ev.add_argument("--as-of", type=date.fromisoformat, required=True)
    p_ev.add_argument("--kind", action="append", default=[])
    p_ev.add_argument("--published-from", type=date.fromisoformat)
    p_ev.add_argument("--published-to", type=date.fromisoformat)
    p_ev.add_argument("--limit", type=int, default=5)
    p_ev.add_argument("--full", action="store_true", help="mostra o trecho inteiro")
    p_ev.set_defaults(func=cmd_evidence)

    p_pu = sub.add_parser(
        "provenance-unit", help="de uma citacao ate os bytes: unidade, documento, blob"
    )
    p_pu.add_argument("unit_id")
    p_pu.set_defaults(func=cmd_provenance_unit)

    p_runs = sub.add_parser("runs", help="execucoes recentes")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.add_argument(
        "--research",
        nargs="?",
        const=True,
        default=False,
        metavar="MANIFEST_ID",
        help="corridas de pesquisa; com um manifest_id, so aquela",
    )
    p_runs.set_defaults(func=cmd_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

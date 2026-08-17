# Fase 3 — Controlled Research Layer

**Implementation Plan técnico**

- **Baseline:** commit `3cf93d3` (Fase 2: camada semântica agnóstica de regime contábil)
- **Status:** **Milestone 1 implementado e fechado.** Milestones 2 e 3 pendentes.
- **O que existe hoje:** todo o núcleo determinístico — contratos, canonicalização, capability snapshot, validador, resolver, derivação, executor, renderer, answer, manifesto e sua persistência em `research_run` — mais `pat capability`, `pat ask --plan-file` e `pat runs --research`. Nenhuma linha de código de LLM, nenhuma dependência nova, nenhuma chamada de rede.
- **O que não existe:** `research/llm/`, `planner.py`, `writer.py`, `pat plan`, tabela `llm_call`, `data/llm/`. Ver §19, Milestones 2 e 3.

> Este documento nasceu como proposta e virou registro. Onde o texto descreve
> uma decisão em tempo futuro ("will", "must"), leia como especificação do que
> foi construído no Milestone 1 ou do que ainda será construído nos seguintes;
> os desvios entre proposta e implementação estão anotados no ponto em que
> ocorrem.

---

## Sumário

1. [Executive Summary](#1-executive-summary)
2. [Proposal vs Actual](#2-proposal-vs-actual)
3. [File-by-File Plan](#3-file-by-file-plan)
4. [Contracts](#4-contracts)
5. [Canonicalization & Hashing](#5-canonicalization--hashing)
6. [Capability Snapshot](#6-capability-snapshot)
7. [Validator](#7-validator)
8. [Resolver](#8-resolver)
9. [Derivation](#9-derivation)
10. [Executor](#10-executor)
11. [Renderer & Claims](#11-renderer--claims)
12. [Writer](#12-writer)
13. [LLM Client](#13-llm-client)
14. [LLM Storage & Provenance](#14-llm-storage--provenance)
15. [CLI](#15-cli)
16. [GPA Golden Test](#16-gpa-golden-test)
17. [Test Matrix](#17-test-matrix)
18. [Layering](#18-layering)
19. [Implementation Order](#19-implementation-order)
20. [Open Decisions](#20-open-decisions)
21. [Risks](#21-risks)
22. [Definition of Done](#22-definition-of-done)

---

# 1. Executive Summary

Phase 3 is seven small deterministic modules plus two thin LLM adapters. The deterministic half — contracts, capability snapshot, validator, resolver, derivation, executor, renderer, manifest — is buildable, testable and shippable **with zero LLM calls and zero new dependencies**. The LLM half is two functions behind one Protocol.

The structural claim to be enforced, not requested: **there is no node in the plan grammar where a literal number can be written, and no path by which a digit reaches the answer except through the renderer.** That is what makes "the LLM is not a source of financial truth" a property rather than a prompt instruction.

Total estimated new surface: ~14 new files, 4 modified, ~1,100 lines of source and ~900 of tests. Three milestones, each leaving the repo consistent and green.

Two things I got wrong in the earlier architectural proposal and am correcting here: the MVP golden question must be **FY2024 vs FY2023**, not FY2023 vs FY2022; and the failing consistency check I promised the MVP would surface **does not fail offline** — it fails only in the network tier.

The architectural decision being preserved is:

```
Question → Planner → ResearchPlan → Validator → Resolver → Executor → Results → Renderer → Writer → Answer
```

and **not**:

```
Question → Agent → Tools → Retriever → Coder → Sandbox → ...
```

For the MVP: no Coder, no sandbox, no tool-calling loop, no RAG, no separate Retriever, no LLM Critic, no Charter, no LLM access to DuckDB, no LLM access to facts, no LLM computing numbers.

The Planner is the only LLM call inside the initial data path. The Writer is a second LLM call, outside the data path. Calculation remains deterministic and executed by the existing Phase 2 engine.

**Separation to preserve:**

- **LLM** = intenção / linguagem
- **PAT** = planejamento validado + execução
- **Engine** = cálculo
- **Warehouse** = fonte de verdade
- **Provenance** = prova

---

# 2. Proposal vs Actual

| # | Previous proposal said | Actual code | Consequence for the plan |
|---|---|---|---|
| **P1** | MVP question: *"GPA FY2023 vs FY2022"* | `tests/semantics/golden_gpa.py` contains **FY2023 and FY2024 only**. FY2022 appears solely in `tests/network/test_semantics_live.py` | **MVP question changes to FY2024 vs FY2023 @ `as_of=2025-06-30`.** Both periods have fixture values; the comparison is dramatic (`0.100939` → `0.038904`) |
| **P2** | *"the MVP must surface a failing consistency check"* | `proxima_da_retida` **passes** offline: FY2023 D&A 1,136,000,000 vs DVA 1,133,000,000; tolerance is `max(1000, \|retida\|×0.05)` = 56,650,000 | Offline golden asserts checks are **carried through with `status="pass"` and both `observed`/`expected` populated**. A *failing* check is proven by (a) a unit test over a hand-built `ComputationResult`, and (b) the existing FY2022 network test. No fixture invention |
| **P3** | `ResearchRunManifest` "extends the `Run` pattern" | `Run` (`contracts/lineage.py`) is written only by `Catalog.start_run/finish_run` into table `ingest_run`; fields are `run_id, command, started_at, finished_at, status, pat_version, python_version, git_sha, notes` | `ResearchRunManifest` is a **separate contract and a separate table**. It reuses the three environment fields by convention, not by inheritance. `Run` is not touched |
| **P4** | "add an `anthropic` dependency" | `httpx>=0.27` is **already a direct dependency** | The Anthropic adapter can be ~80 lines of `httpx.post` against the Messages API with **no new dependency and no lockfile change**. Raised as Open Decision **D-6** |
| **P5** | implied `Engine` caches across calls | `Engine._compute` builds `cache: dict` **inside each `compute()` call** | A plan with 4 metric steps recomputes shared dependencies. Deterministic, correct, slightly wasteful. **No change proposed** — introducing a cross-call cache would introduce a mutable engine, and the cost is microseconds |
| **P6** | "planner emits `entity_id`" | CLI works in `--cod-cvm`; `AsOf.entity_by_cod_cvm()` is the translation. Nothing enumerates entities | Capability snapshot must expose `entity_id` **together with** `cod_cvm` and `denom_cia`, so the planner can resolve "GPA" → `br:cnpj:47508411000156`. Confirms `AsOf.entities()` is required |
| **P7** | — | `build_engine(conn)` is called by `cmd_metric` **without `git_sha`**, so `MetricResult.git_sha` is `None` in CLI use today | `pat ask` must pass `git_sha=current_git_sha()` (`pat/audit/run.py` already has the function). Small, real gap |
| **P8** | — | `UnavailableReason.SCOPE_NOT_AVAILABLE` is never produced; wrong scope yields `MISSING_FACT_AS_OF` (asserted at `test_golden_gpa.py:257`) | The resolver's scope pre-check must not promise a distinction the engine does not make. Documented, not fixed (Phase 2 is frozen) |
| **P9** | — | `Fidelity.PARTIAL` is produced by no binding in the repo | `max_fidelity` constraint is well-defined but currently unexercised at `PARTIAL`. Tested with a synthetic result, not a fixture |
| **P10** | — | GPA's `mapping_id` **equals** its `entity_id` (`br:cnpj:47508411000156`) | Cosmetic, but the capability snapshot must not conflate the two fields |

Everything else in the previous proposal survived inspection: the layering constraint at `test_layering.py:97-110` (which forces `research/` to be a sibling of `semantics/`), the `MetricResult` field set, the `FactResolver` port, `weakest()`, and the `MetricUnavailable` discipline.

---

# 3. File-by-File Plan

## NEW

| File | Change | Responsibility | Depends on |
|---|---|---|---|
| `src/pat/contracts/research.py` | create | All Phase 3 contracts. Pure data, no behaviour beyond validators | `contracts.common`, `contracts.semantics`. **Nothing else, ever** |
| `src/pat/research/__init__.py` | create | Composition root. The only module that wires an `LLMClient` to the pipeline; exposes `build_research(conn, llm=None)` | `pat.semantics`, `pat.query.asof`, `research.*` |
| `src/pat/research/canonical.py` | create | Canonical JSON + `sha256_of()`. One implementation, used by question/plan/result/snapshot hashing | stdlib only |
| `src/pat/research/capability.py` | create | Builds `CapabilitySnapshot` from catalog + registry + mappings + `AsOf`. Read-only | `pat.semantics.concepts`, `.registry`, `.loader`, `pat.query.asof` |
| `src/pat/research/validate.py` | create | Pure static plan validation. No DB, no LLM, no I/O | `contracts.research`, `pat.semantics.registry` (read-only introspection) |
| `src/pat/research/resolve.py` | create | Warehouse-dependent plan checks: entity exists, period covered, scope available, mapping confirmed | `pat.query.asof`, `pat.semantics.loader` |
| `src/pat/research/derive.py` | create | The closed `DerivationOp` implementations and their refusal conditions | `contracts.research`, `contracts.semantics` |
| `src/pat/research/execute.py` | create | Plan → `ComputationResult[]`. **The only module holding an `Engine`** | `pat.semantics.Engine`, `research.derive` |
| `src/pat/research/render.py` | create | **The only module that formats a number for display.** Produces `NumericClaim`s and the token table | `contracts.research` |
| `src/pat/research/answer.py` | create | Claim assembly, token substitution, the no-digit post-check | `contracts.research`, `research.render` |
| `src/pat/research/manifest.py` | create | `ResearchRunManifest` construction; environment capture | `contracts.research`, `pat.audit.run` |
| `src/pat/research/llm/__init__.py` | create | `LLMClient` Protocol + `LLMRequest`/`LLMResponse` + `FakeLLMClient` | stdlib + `contracts.research` |
| `src/pat/research/llm/anthropic.py` | create | The one concrete adapter (httpx) | `httpx`, `research.llm` |
| `src/pat/research/llm/store.py` | create | Content-addressed prompt/response store under `data/llm/` | `pat.config`, stdlib |
| `src/pat/research/planner.py` | create | Question + snapshot → `ResearchPlan`. Owns the prompt text | `research.llm`, `contracts.research` |
| `src/pat/research/writer.py` | create | Claims → prose with substitution tokens | `research.llm`, `contracts.research` |

Why each new file, in one line: `canonical.py` exists because four different objects need identical hashing and a second implementation would silently diverge; `validate.py` and `resolve.py` are split because one is pure and one needs a database, and only the pure half can be exhaustively tested; `render.py` is separate from `answer.py` so that "the only place digits are formatted" is a single importable module a layering test can assert about; `llm/store.py` is separate from `llm/anthropic.py` so caching is provider-independent.

## MODIFY

| File | New responsibility | Notes |
|---|---|---|
| `src/pat/query/asof.py` | `+ entities()`, `+ coverage(entity_id)` | Pure additions. No existing method's signature or SQL changes |
| `src/pat/cli.py` | `+ cmd_capability`, `+ cmd_plan`, `+ cmd_ask`; parsers | Follows the existing `_open_readonly` / `_scope` conventions |
| `src/pat/store/db.py` | `+ research_run` table (**feito**), `+ llm_call` table (Milestone 2) in `SCHEMA_SQL` | `migrate()` is `CREATE TABLE IF NOT EXISTS`, so it is additive on existing warehouses |
| `src/pat/store/research.py` | **Novo, não previsto na proposta.** `write_manifest` / `read_manifest` / `recent_manifests` | Fica em `store/` e não em `research/` pelo mesmo motivo que `write_facts` fica: quem calcula não grava. É o que mantém `pat.research` sem um único import de `pat.store`, verificado por teste de camada |
| `README.md` | Correct the phase table (it currently says Phase 3 = "sandbox + coder + charter") | Documentation only |

Explicitly **not** modified: `pyproject.toml` and `uv.lock` — under Open Decision D-6 (httpx-direct adapter), Phase 3 needs no new dependency. A new pytest marker (`llm`) does require a one-line `pyproject.toml` change; that is the sole exception and it touches no dependency.

## UNCHANGED — inspected, must not be touched

`src/pat/contracts/{common,documents,entities,facts,lineage,silver}.py`, `src/pat/contracts/semantics.py`, all of `src/pat/semantics/` (engine, registry, loader, resolver, concepts, check, definitions/, frameworks/, mappings/), `src/pat/store/{bronze,catalog,gold,silver}.py`, `src/pat/parse/`, `src/pat/sources/`, `src/pat/build.py`, `src/pat/ingest.py`, `src/pat/audit/run.py`, and every existing test.

Phase 3 adds a layer above Phase 2 and changes nothing inside it. If a Phase 2 file needs editing, that is a signal the boundary is wrong.

---

# 4. Contracts

All in `src/pat/contracts/research.py`. Every model inherits `Frozen` (frozen, `extra="forbid"`), matching every other contract in the project.

## `ResearchQuestion`

| field | type | req | default | validator |
|---|---|---|---|---|
| `question_version` | `Literal["v1"]` | ✔ | `"v1"` | — |
| `text` | `str` | ✔ | — | `min_length=1`, stripped |
| `as_of` | `date` | ✔ | **none** | ≥ every `pinned_periods` |
| `asked_at` | `AwareDatetime` | ✔ | — | reuses `contracts.common` |
| `pinned_entities` | `tuple[str, ...]` | | `()` | sorted, unique |
| `pinned_periods` | `tuple[date, ...]` | | `()` | sorted, unique |
| `pinned_scope` | `ReportingScope \| None` | | `None` | — |
| `requested_output` | `OutputKind` | ✔ | — | `NUMBER \| SERIES \| COMPARISON \| NARRATIVE` |
| `constraints` | `ResearchConstraints` | ✔ | — | — |

Invariants: `as_of` has **no default** — the same rule `Engine.compute` enforces. Pins are authoritative; the planner may add, never contradict.

## `ResearchConstraints`

| field | type | default | rationale |
|---|---|---|---|
| `min_source_tier` | `SourceTier` | `PRIMARY_OFFICIAL` | reuses the existing enum |
| `max_fidelity` | `Fidelity` | `APPROXIMATE` | weakest acceptable; comparison via the existing `.rank` |
| `allow_unconfirmed_mapping` | `bool` | `False` | refuses answers riding the default family |
| `allow_failed_checks` | `bool` | `True` | a failing check is surfaced, never suppressed |

Deliberately absent and to remain absent: any currency-conversion flag, any `allow_missing`, any "best effort" mode.

## `ResearchPlan`

| field | type | req | notes |
|---|---|---|---|
| `plan_version` | `Literal["v1"]` | ✔ | |
| `question_id` | `Sha256` | ✔ | reuses the `Sha256` annotated type |
| `objective` | `str` | ✔ | `min_length=1` |
| `as_of` | `date` | ✔ | |
| `scope` | `ReportingScope` | ✔ | **no default** |
| `steps` | `tuple[PlanStep, ...]` | ✔ | non-empty; `step_id` unique |
| `outputs` | `tuple[str, ...]` | ✔ | non-empty |
| `assumptions` | `tuple[str, ...]` | | `()` |
| `unresolved` | `tuple[UnresolvedItem, ...]` | | `()` |

Model validators (shape only — semantic checks live in `validate.py`): `step_id` matches `^[a-z][a-z0-9_]{0,63}$` and is unique; `outputs` ⊆ step ids. `plan_id` is a **derived property**, never a stored field.

## `MetricStep` / `DerivationStep`

```
MetricStep      step_id: str
                step_kind: Literal["metric"]
                metric: MetricRef            # reuses contracts.semantics
                entity_id: str
                period_end: date

DerivationStep  step_id: str
                step_kind: Literal["derivation"]
                op: DerivationOp
                inputs: tuple[str, ...]      # step_ids
                params: DerivationParams      # closed, per-op
```

`PlanStep = Annotated[MetricStep | DerivationStep, Field(discriminator="step_kind")]`.

**`scope` and `as_of` are not step fields.** They live on the plan and descend unchanged — mirroring the invariant `Engine._compute` already enforces, which is what makes a scope-mixing plan unrepresentable rather than merely invalid.

**There is no literal-value node in the grammar.** This is the single most load-bearing property of the design.

## `DerivationOp`

`StrEnum`: `DELTA · DELTA_PCT · RATIO · CAGR · MIN · MAX · MEAN`. Closed. `DerivationParams` carries only `years: int | None` (CAGR).

## `UnresolvedItem`

`kind: UnresolvedKind` (`AMBIGUOUS_ENTITY · AMBIGUOUS_PERIOD · AMBIGUOUS_SCOPE · UNSUPPORTED_METRIC · UNSUPPORTED_QUESTION`), `detail: str`, `candidates: tuple[str, ...]`. Non-empty `unresolved` ⇒ the plan is not executable. This is how ambiguity becomes a refusal instead of a guess.

## `ComputationResult`

```
result_id: Sha256
step_id: str
kind: ResultKind                   # METRIC | DERIVED
metric_result: MetricResult | None
derived: DerivedValue | None
```

Model validator: exactly one of `metric_result` / `derived` is set. **A `ComputationResult` cannot exist without either an embedded `MetricResult` or a `DerivedValue` naming the result_ids it came from.** A fabricated number has no shape it can take.

`MetricResult` is embedded whole, not re-flattened — it already carries value, currency, dimension, period, `as_of`, `knowledge_date`, `metric_version`, `kind`, `fidelity`, `inputs[]` with `fact_id`/`locator`, all four mapping fields, framework, jurisdiction, `pat_version`, `git_sha`.

## `DerivedValue`

`op`, `value: Decimal`, `dimension`, `currency: str | None`, `derived_from: tuple[Sha256, ...]` (non-empty), `fidelity` (= `weakest()` over inputs), `knowledge_date` (= max), `period_labels: tuple[str, ...]`. Same currency validator as `MetricResult`: `MONEY` requires a currency, non-`MONEY` forbids one.

## `ComputationFailure`

`step_id`, `reason: DerivationFailureReason`, `message`, `remedy: str | None`, plus `unavailable: MetricUnavailable | None` when a metric step failed — the Phase 2 object is passed through untouched, never re-summarised.

`DerivationFailureReason` is a **new enum in `contracts.research`**, not an addition to `UnavailableReason`: `ZERO_DENOMINATOR · NON_POSITIVE_CAGR_BASE · DIMENSION_MISMATCH · CURRENCY_MISMATCH · INPUT_FAILED · ARITY`. The semantic layer's enum stays the semantic layer's.

## Claims and answer

```
NumericClaim        claim_kind: Literal["numeric"]
                    token: str            # "{{s:margin_fy2024}}"
                    result_id: Sha256     # REQUIRED
                    rendered_value: str   # REQUIRED — from render.py only
                    unit: str | None

InterpretiveClaim   claim_kind: Literal["interpretive"]
                    text: str
                    supports: tuple[Sha256, ...]   # REQUIRED, non-empty
                    # NO value field. At all.

ResearchAnswer      question_id, plan_id, prose: str,
                    claims: tuple[Claim, ...],
                    warnings: tuple[Warning_, ...],
                    manifest_id: str
```

`Warning_` = `kind: WarningKind` (`APPROXIMATE_FIDELITY · UNCONFIRMED_MAPPING · CHECK_FAILED · PERIOD_MISSING · RESTATED_SINCE`), `message`, `result_id: Sha256 | None`.

The number/interpretation distinction is structural: `NumericClaim` is the only class with somewhere to put a value, and `InterpretiveClaim` is the only class without one.

## `ResearchRunManifest` and `PlanProvenance`

```
PlanProvenance         model_id, temperature, max_tokens,
                       system_prompt_sha256, prompt_sha256, response_sha256,
                       capability_sha256, called_at: AwareDatetime,
                       cached: bool

ResearchRunManifest    manifest_id, question_id, plan_id,
                       planner: PlanProvenance,
                       writer: PlanProvenance | None,
                       as_of, executed_at: AwareDatetime,
                       metric_versions: tuple[str, ...],
                       mapping_sha256s: tuple[Sha256, ...],
                       pat_version, python_version, git_sha
```

`PlanProvenance` is separate from `ResearchPlan` **precisely so it stays out of the plan hash** — two different models producing the same plan produce the same `plan_id`.

**No parallel versions of existing concepts.** `MetricRef`, `MetricResult`, `MetricUnavailable`, `ReportingScope`, `Fidelity`, `Dimension`, `SourceTier`, `Sha256`, `AwareDatetime`, `Frozen`, `weakest()` are all reused as-is.

---

# 5. Canonicalization & Hashing

One implementation in `research/canonical.py`, precise enough for two independent implementations to agree.

## Canonical JSON

1. Encoding UTF-8, no BOM.
2. Object keys sorted by Unicode code point.
3. Separators exactly `","` and `":"` — no whitespace anywhere.
4. `ensure_ascii=False`; non-ASCII characters emitted literally.
5. **Keys whose value is `None` are omitted.** Empty tuples/strings are **kept**.
6. Tuples → JSON arrays, order preserved (order is meaning for `steps`, `inputs`, `outputs`).
7. `date` → `"YYYY-MM-DD"`. `datetime` → RFC 3339 normalized to UTC with a `Z` suffix, **with explicit microseconds** (fixed width).

   *Implementado com um desvio da proposta original, que dizia apenas "RFC 3339".* Truncar no segundo faz duas execuções do mesmo plano dentro do mesmo segundo colidirem em `manifest_id` — e a segunda desaparece do `research_run` sem erro, porque a gravação é `ON CONFLICT DO NOTHING`. Uma execução que aconteceu e não deixou rastro é exatamente o que a tabela existe para impedir. O impacto é limitado a `manifest_id`: `question_id` exclui `asked_at`, `plan_id` exclui a procedência do modelo, o snapshot exclui `built_at`, e `result_id` só carrega `date`.
8. Enums (`StrEnum`) → their `.value`.
9. `bool` → `true`/`false`; `int` → bare integer.
10. `Decimal` → a **string** in plain (non-exponential) notation: sign, digits, optional `.`, no trailing zeros after the point, no bare trailing `.`, `"0"` for zero. Never a JSON float.
11. `MetricRef` → `"name@version"` (its `__str__`), not an object.

`sha256_of(obj) -> str` = lowercase hex of SHA-256 over those bytes. Consistent with `Sha256` and with `MappingChain.sha256`.

## `question_id`

Hashed fields: `question_version`, `text` (whitespace-normalized: strip, collapse internal runs to single spaces — so trailing-newline differences do not fork identity), `as_of`, `pinned_entities`, `pinned_periods`, `pinned_scope`, `requested_output`, `constraints` (all four fields).

**Excluded: `asked_at`.** Otherwise the same question asked twice would never be the same question.

## `plan_id`

Hashed fields: `plan_version`, `objective`, `as_of`, `scope`, `steps` (in order, each fully), `outputs` (in order), `assumptions` (in order), `unresolved` (in order), and `question_id`.

**Excluded: everything in `PlanProvenance`** — model id, temperature, prompt/response hashes, timestamps, whether the response came from cache. This is the mechanism by which LLM metadata cannot move the plan hash.

Determinism guarantees: no `dict` iteration reaches the serializer (all containers are tuples or sorted-key objects); no floats exist anywhere; `objective` and `assumptions` are hashed verbatim, so cosmetic LLM rewording *does* change `plan_id` — correct, since prose the user reads is part of the artifact.

## `result_id`

`sha256_of(canonical({step_id, kind, metric_result | derived}))` — **content-addressed**, so a different number yields a different `result_id`. Two runs whose citations differ are visibly different runs. (Alternative: address-addressed `sha256(plan_id|step_id)`. Raised as Open Decision **D-4**.)

## `capability_sha256`

`sha256_of(canonical(CapabilitySnapshot))`, over the full snapshot including the coverage section.

## `manifest_id`

`sha256_of(canonical({question_id, plan_id, as_of, executed_at, planner, writer, git_sha}))`.

---

# 6. Capability Snapshot

Deterministic, hashable, and containing **no financial values**.

```
CapabilitySnapshot
  snapshot_version: Literal["v1"]
  built_at: AwareDatetime          # NOT hashed
  concepts:  tuple[ConceptCard, ...]
  metrics:   tuple[MetricCard, ...]
  mappings:  tuple[MappingCard, ...]
  entities:  tuple[EntityCard, ...]
  scopes:    tuple[ReportingScope, ...]
  derivations: tuple[DerivationCard, ...]
  limits:    SnapshotLimits
```

| section | source | fields | ordering |
|---|---|---|---|
| `concepts` | `pat.semantics.concepts.CATALOG` | `concept_id`, `label_en`, `definition`, `dimension`, `period_kind` | by `concept_id` |
| `metrics` | `MetricRegistry.all()` (already sorted) | `ref` (`"ebitda@v1"`), `kind`, `dimension`, `period_kind`, `definition`, `requires_concepts`, `requires_metrics` | by ref |
| `mappings` | `MappingSet.all()` (already sorted) | `mapping_id`, `entity_id`, `framework`, `jurisdiction`, `source`, `is_default_for_source`, `confirmed_concepts` (concept ids only), `weakest_fidelity` | by `mapping_id` |
| `entities` | **`AsOf.entities()`** (new) | `entity_id`, `cod_cvm`, `denom_cia`, `period_ends`, `scopes`, `has_own_mapping` | by `entity_id` |
| `derivations` | `DerivationOp` enum + `derive.py` metadata | `op`, `arity`, `input_dimension_rule`, `output_dimension`, `refusal_conditions` | by op name |
| `limits` | constants | `max_steps=32`, `max_periods_per_entity=12`, `max_entities=4` | — |

**`built_at` is excluded from the hash**, so an unchanged system yields a stable `capability_sha256` across invocations — which is what makes the manifest field meaningful.

## Not passing financial data to the LLM

Three rules, each testable:

1. The snapshot contains **no `Decimal` field anywhere**. Asserted by a test that walks the serialized snapshot and fails on any numeric-looking value outside the whitelisted structural fields (`cod_cvm`, `arity`, limits, period dates).
2. `EntityCard.period_ends` lists *which* periods have data, never *what* the data is.
3. `MappingCard` carries `concept_id`s and fidelity, never `LineAddress`es. The planner does not need to know that revenue lives at `3.01`, and telling it would be the first step back toward lexical matching.

## Size budget

Current system, estimated: 8 concepts (~1.6 kB), 5 metrics (~1.5 kB), 2 mappings (~0.6 kB), 1–20 entities (~0.1 kB each), 7 derivations (~0.8 kB) ⇒ **~6 kB, ~1,700 tokens.** Comfortable.

Hard limit: **`SnapshotLimits.max_serialized_bytes = 65_536`.** Exceeding it raises `CapabilityTooLarge` rather than silently truncating. At the current growth rate that is roughly 100 companies or 60 metrics away — at which point the right move is to filter entities by the question, which is a design change deserving its own review, not a quiet truncation.

`pat capability` prints the human rendering plus the hash; `pat capability --json` prints the canonical bytes.

---

# 7. Validator

`research/validate.py`. Pure: no DB, no LLM, no filesystem, no clock, no randomness. Its only external input is a `MetricRegistry` (read-only introspection) and the `concepts` catalog.

```python
def validate_plan(plan, question, registry) -> tuple[PlanViolation, ...]
```

Returns **all** violations, not the first — a plan with three problems should report three. Empty tuple means "structurally executable"; it does not mean "correct" (see §21, R-1).

| Invalid plan | Error code | Detection point | Execution allowed? |
|---|---|---|---|
| `steps` empty | `EMPTY_PLAN` | contract validator | no |
| duplicate `step_id` | `DUPLICATE_STEP_ID` | contract validator | no |
| `step_id` fails the id regex | `BAD_STEP_ID` | contract validator | no |
| metric ref not in registry | `UNKNOWN_METRIC` | validator | no |
| metric ref malformed (`"ebitda"`, no `@`) | `BAD_METRIC_REF` | `MetricRef.parse` | no |
| `DerivationStep.inputs` names a nonexistent step | `DANGLING_INPUT` | validator | no |
| `DerivationStep` references a later step | `FORWARD_REFERENCE` | validator | no |
| cycle among derivation steps | `PLAN_CYCLE` | validator | no |
| `outputs` empty | `NO_OUTPUT` | contract validator | no |
| `outputs` names a nonexistent step | `DANGLING_OUTPUT` | validator | no |
| op arity mismatch (`DELTA` with 1 or 3 inputs) | `ARITY` | validator | no |
| `CAGR` without `params.years` | `MISSING_PARAM` | validator | no |
| `CAGR` with `years <= 0` | `BAD_PARAM` | validator | no |
| derivation over inputs of incompatible *declared* dimensions | `DIMENSION_MISMATCH` | validator (static, from metric definitions) | no |
| `period_end > plan.as_of` | `PERIOD_AFTER_AS_OF` | validator | no |
| `plan.as_of != question.as_of` | `AS_OF_MISMATCH` | validator | no |
| `plan.question_id != question_id(question)` | `QUESTION_ID_MISMATCH` | validator | no |
| entity used that is not in `pinned_entities` (when pins non-empty) | `PIN_CONTRADICTED_ENTITY` | validator | no |
| period used that is not in `pinned_periods` (when pins non-empty) | `PIN_CONTRADICTED_PERIOD` | validator | no |
| `plan.scope != question.pinned_scope` (when pinned) | `PIN_CONTRADICTED_SCOPE` | validator | no |
| `unresolved` non-empty | `PLAN_NOT_EXECUTABLE` | validator | no |
| step count > `max_steps` | `PLAN_TOO_LARGE` | validator | no |

Two notes on scope of responsibility. **Currency compatibility is not statically checkable** — a metric's currency is a property of the resolved facts, not of its definition — so it is a *derivation-time* refusal (§9), not a validator rule. **Dimension** compatibility *is* statically checkable, because `MetricDefinition.dimension` is declared, so it belongs here.

No validation is proposed that lacks a failure mode it prevents.

---

# 8. Resolver

`research/resolve.py`. Everything the validator cannot know without the warehouse. Read-only connection, obtained the same way `cmd_metric` does it (`_open_readonly` → `connect(..., read_only=True)`).

```python
def resolve_plan(plan, *, asof, mappings, constraints) -> tuple[ResolutionIssue, ...]
```

| check | source | issue code | blocking? |
|---|---|---|---|
| entity exists in gold | `AsOf.entity(entity_id)` | `UNKNOWN_ENTITY` | yes |
| `period_end` present for entity+scope | `AsOf.coverage()` | `PERIOD_NOT_COVERED` | yes |
| scope present for entity | `AsOf.coverage()` | `SCOPE_NOT_COVERED` | yes |
| a mapping chain exists | `MappingSet.resolve(entity_id, source=...)` | `NO_MAPPING` | yes |
| chain is entity-confirmed | `MappingChain.confirmed` | `UNCONFIRMED_MAPPING` | **yes iff `constraints.allow_unconfirmed_mapping is False`** |

The split is clean: **the validator answers "is this plan well-formed?"; the resolver answers "can this warehouse serve it?"** The first is a pure function of the plan and the code; the second changes when you run `pat build`.

Two honest limits, both consequences of Phase 2 as it stands:

- Coverage is derived from `gold_fact` presence, so `SCOPE_NOT_COVERED` means "no fact rows for that scope", not "the regime does not publish that scope". Since the CVM resolver never emits `SCOPE_NOT_AVAILABLE` (finding **P8**), the research layer must not promise a distinction the layer below does not make.
- Coverage says a period *exists*, not that every account the plan needs exists. A period that is covered can still yield `MISSING_FACT_AS_OF` at execution. That is correct and must stay: pre-flighting every address would duplicate the engine.

## New `AsOf` methods

```python
@dataclass(frozen=True)
class EntityCoverage:
    entity_id: str
    cod_cvm: int
    denom_cia: str
    period_ends: tuple[date, ...]       # ascending
    scopes: tuple[bool, ...]            # consolidated flags present
    earliest_knowledge_date: date
    latest_knowledge_date: date

def entities(self, *, as_of: date | None = None) -> list[EntityRef]: ...
def coverage(self, entity_id: str, *, as_of: date | None = None) -> EntityCoverage | None: ...
```

Both are `SELECT DISTINCT … GROUP BY` over `gold_fact`, both honour `knowledge_date <= as_of` when `as_of` is given — because coverage on 2024-06-30 is genuinely different from coverage today, and a capability snapshot that ignored that would let the planner pin a period the user could not have known about. The existing composite index `idx_gold_asof` does not lead on `entity_id`; at current volumes a scan is fine, and adding an index is a Phase 2 change I am not proposing.

Naming: `entities()` and `coverage()` as proposed both survive inspection. `EntityRef` already exists and is reused unchanged; `EntityCoverage` is new.

---

# 9. Derivation

`research/derive.py`. Every op is a pure function `(tuple[ComputationResult, ...], DerivationParams) -> DerivedValue | ComputationFailure`.

Universal rules, applied before any op body runs:

- **Missing input** — if any input is a `ComputationFailure`, the derivation short-circuits to `ComputationFailure(INPUT_FAILED)` carrying the upstream failure. Never a partial result.
- **Currency** — if any two monetary inputs disagree on currency → `CURRENCY_MISMATCH`. No conversion, ever, matching `Engine._compute`.
- **Fidelity** — output fidelity = `weakest(tuple of input fidelities)`, reusing the existing function.
- **knowledge_date** — output = `max` over inputs.
- **Provenance** — `derived_from` = the tuple of input `result_id`s, in declared order. Non-empty by contract.
- **Precision** — every division runs inside `with localcontext() as ctx: ctx.prec = 28`, copying the pattern already established in `margem_ebitda.py:43-58` and for the same reason: a result that depends on process-global decimal state is not reproducible.

| op | arity | input dim | output dim | currency | period semantics | refusal | code |
|---|---|---|---|---|---|---|---|
| `DELTA` | 2 | equal | same as inputs | inherited; must match | later − earlier | dims differ; currencies differ | `DIMENSION_MISMATCH` / `CURRENCY_MISMATCH` |
| `DELTA_PCT` | 2 | equal | `RATIO` | **none** | `(b − a) / \|a\|` | `a == 0` | `ZERO_DENOMINATOR` |
| `RATIO` | 2 | equal | `RATIO` | **none** | `a / b`, same period expected but not enforced | `b == 0` | `ZERO_DENOMINATOR` |
| `CAGR` | 2 | equal, `MONEY` | `RATIO` | none | `(b/a)^(1/years) − 1` | `a <= 0` or `b <= 0` or `years <= 0` | `NON_POSITIVE_CAGR_BASE` / `BAD_PARAM` |
| `MIN` | ≥2 | all equal | same | inherited; must match | pointwise | dims/currencies differ | `DIMENSION_MISMATCH` |
| `MAX` | ≥2 | all equal | same | inherited; must match | pointwise | as `MIN` | as `MIN` |
| `MEAN` | ≥2 | all equal | same | inherited; must match | arithmetic mean; `prec=28` | as `MIN`; `n == 0` unreachable (arity ≥2) | as `MIN` |

Edge cases, explicitly:

- **Zero denominator** — `DELTA_PCT` and `RATIO` refuse. Never `Decimal("Infinity")`, never `None`.
- **Negative or zero CAGR base** — refuses. A CAGR from a negative EBITDA is not a small number, it is a category error; `Decimal` would raise `InvalidOperation` on the fractional power and we must refuse *before* that, with a named reason rather than an exception.
- **`CAGR` fractional exponent** — `Decimal` has no `**` for non-integer exponents; the implementation is `(b/a).ln() * (1/years)` then `.exp()`, all inside the fixed-precision context. This is the one place a documented precision decision is unavoidable; it is deterministic for a fixed `prec`.
- **`NaN` / `Infinity`** — a post-condition on every op asserts `value.is_finite()`. Failing it is a programming error and raises, because a non-finite value escaping into a `ComputationResult` is the exact class of silent corruption the project exists to prevent.

**Period semantics**: `DELTA` and `CAGR` require two distinct `period_end`s and order their inputs by the input order given in the plan, not by date — so a plan that subtracts backwards produces a negative delta rather than a silent reordering. `RATIO` places no constraint on periods; that is deliberate, since a ratio across periods is sometimes exactly what is wanted.

---

# 10. Executor

`research/execute.py`. **The only module in `pat.research` that holds an `Engine`.** Enforced by a layering test.

```python
def execute_plan(plan, *, engine) -> ExecutionOutcome
```

Flow:

1. **Refuse to run an unvalidated plan.** `execute_plan` requires a `ValidatedPlan` — a thin wrapper produced only by `validate.py` + `resolve.py` returning zero issues. There is no code path that accepts a bare `ResearchPlan`. This makes "invalid plans do not execute" a type-level property, not a discipline.
2. **Order.** Topological sort over `step_id` dependencies; ties broken by declaration order in `plan.steps`. Deterministic and independent of dict iteration.
3. **Metric steps.** `engine.compute(step.metric, entity_id=step.entity_id, period_end=step.period_end, scope=plan.scope, as_of=plan.as_of)`. Note `scope` and `as_of` come from the plan, never from the step — there is no field to get them wrong from.
4. **`MetricUnavailable` propagation.** Wrapped in `ComputationFailure(step_id, reason=INPUT_FAILED, unavailable=<the object, untouched>)`. Its `reason`, `message`, `concept_id`, `tried` and `remedy` reach the CLI verbatim. Nothing is rewritten or summarised.
5. **Derivation steps.** Look up prior results by `step_id`; delegate to `derive.py`.
6. **Partial execution.** A failed step does not abort the run; downstream steps fail with `INPUT_FAILED`, independent steps still execute. `ExecutionOutcome` carries `results`, `failures`, and `outputs_available: bool` = every `plan.outputs` step produced a result. **If any output failed, no answer is produced** — the CLI reports the failures and exits non-zero. A partial answer is exactly the "partial that reads as complete" failure.
7. **Determinism.** No clock, no randomness, no environment. Called twice on the same plan and warehouse, byte-identical results including all `result_id`s.

---

# 11. Renderer & Claims

`research/render.py` is the **only** module permitted to convert a `Decimal` into a display string. Asserted by a layering test that greps for formatting constructs elsewhere in `pat.research`.

```python
def render_results(results, *, locale=DEFAULT) -> tuple[RenderedClaim, ...]
```

Formatting rules, fixed and versioned:

| dimension | rule | example shape |
|---|---|---|
| `MONEY`, abs ≥ 1e9 | `value / 1e9`, 2 dp, `" bn "` + currency | `"R$ 1.81 bn"` |
| `MONEY`, 1e6 ≤ abs < 1e9 | `value / 1e6`, 1 dp, `" MM "` + currency | `"R$ 731.0 MM"` |
| `MONEY`, abs < 1e6 | full value, 2 dp + currency | |
| `RATIO` | `value × 100`, 2 dp, `%` | `"10.09%"` |
| `RATIO` from `DELTA_PCT` | `value × 100`, 1 dp, `%`, explicit sign | `"−61.5%"` |
| `COUNT` | integer, thousands separator | |

All quantization uses `Decimal.quantize` with `ROUND_HALF_EVEN`, explicitly, so rounding does not depend on process state. The `MM` convention matches `cli.py:485` and `cmd_accounts`.

Each result yields one `NumericClaim`: `token = "{{s:" + step_id + "}}"`, `result_id`, `rendered_value`, `unit`.

## How "EBITDA margin improved from X to Y" gets built

1. Executor produces `margin_fy2023` and `margin_fy2024`.
2. Renderer produces tokens `{{s:margin_fy2023}}` → `"10.09%"`, `{{s:margin_fy2024}}` → `"3.89%"`, plus period tokens `{{p:fy2023}}` → `"FY2023"`.
3. The Writer receives **only the tokens and their semantic labels** — `{"token": "{{s:margin_fy2023}}", "means": "EBITDA margin, consolidated, fiscal year ending 2023-12-31, as of 2025-06-30"}`. **It never sees `"10.09%"`.**
4. The Writer returns: `"EBITDA margin fell from {{s:margin_fy2023}} in {{p:fy2023}} to {{s:margin_fy2024}} in {{p:fy2024}}."`
5. `answer.py` validates, then substitutes.

Step 3 is the point. The model cannot copy a number it was never shown, and cannot invent one because the substitution table is closed: an unknown token is a rejection, and a bare digit is a rejection.

## The no-digit check

```
1. Extract all {{...}} tokens. Any token not in the substitution table → UNKNOWN_TOKEN.
2. Strip every token from the text.
3. If the remainder matches /[0-9]/ → FORBIDDEN_LITERAL, quoting the offending span.
4. Substitute. Emit ResearchAnswer.
```

Rule 3 is why *period labels are tokens too* — otherwise "FY2024" would force a whitelist, and whitelists drift. With every digit-bearing string tokenized, the rule is a one-line regex with no exceptions, which is the only kind of rule that survives.

---

# 12. Writer

```python
def write_answer(question, tokens, *, assumptions, warnings, llm) -> WriterOutcome
```

**Receives:** the question text, the token table (token + `means` label, **no values**), `assumptions` from the plan, rendered `warnings`, and the entity/scope/period/`as_of` context in prose form.

**Never receives:** a database connection, an `Engine`, a `ComputationResult`, a `MetricResult`, a `rendered_value`, or a file path. Enforced by signature and by layering test.

**Produces:** one prose paragraph (target ≤ 120 words) containing only text and tokens.

Post-LLM validation, in order: length bound → token extraction → unknown-token check → forbidden-digit regex → minimum-citation check (at least one `{{s:}}` token, else the prose is untethered interpretation).

**On rejection: no repair loop.** Three reasons: a retry is a second influence path that must be separately hashed and manifested or provenance silently under-reports; a rejection is *information* (it means the prompt or the grammar is wrong) and a silent retry converts a design signal into latency; and "retry until it passes" is precisely how a validator degrades into a sampling loop that eventually emits something wrong-but-passing.

Behaviour on rejection: print the rejection code, the offending span, and the `response_sha256` (the raw response is in the cache and retrievable); exit non-zero. **The numbers are still printed** — the deterministic answer does not depend on the Writer, so a Writer failure costs you the prose, never the result.

Temperature 0 for both calls, with the caveat stated plainly in the docs: temperature 0 reduces variance, it does not produce determinism, and no part of the reproducibility story may rest on it.

---

# 13. LLM Client

`research/llm/__init__.py` — the port, mirroring how `FactResolver` ports the data side.

```python
@dataclass(frozen=True)
class LLMRequest:
    system: str
    user: str
    model: str
    max_tokens: int
    temperature: Decimal          # Decimal, not float — project rule
    stop_sequences: tuple[str, ...] = ()
    timeout_s: int = 60

@dataclass(frozen=True)
class LLMResponse:
    text: str
    model_id: str                 # as reported by the provider, not as requested
    stop_reason: str
    prompt_sha256: str
    response_sha256: str
    cached: bool

@runtime_checkable
class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...
```

Notes: `model_id` records what actually answered, which can differ from what was asked for (aliases). `prompt_sha256` = `sha256(canonical({system, user, model, max_tokens, temperature, stop_sequences}))` — the full request, so a system-prompt edit invalidates the cache. `response_sha256` = `sha256(text.encode())`.

Errors are three named exceptions, never provider types leaking upward: `LLMTimeout`, `LLMTransportError`, `LLMRefused`. No retries in the client (see §12); a timeout is a failed run.

`FakeLLMClient(responses: dict[str, str])` lives in the same module and is keyed by `prompt_sha256`, so planner tests are fully deterministic and CI-safe.

`research/llm/anthropic.py` is the sole concrete adapter: one `httpx.post` to the Messages API, `x-api-key` from `ANTHROPIC_API_KEY`, response parsing, error mapping. Roughly 80 lines. Nothing in `research/` outside this file references Anthropic — asserted by layering test.

---

# 14. LLM Storage & Provenance

## Decision: `data/llm/`, separate from bronze

| option | verdict |
|---|---|
| Store in bronze | **Rejected.** `CLAUDE.md` rule 2 and 3 define bronze as source documents fetched from providers with a `Retrieval`. Model output has no provider, no URL, no `resource_key`, and is not a source. Admitting it would make "bronze" mean two things, and the next reader would have to learn which |
| Store in DuckDB `llm_call` as a BLOB | **Rejected.** The warehouse is derived and reconstructible (`README.md`: "o bronze é a fonte de verdade; o DuckDB é derivado"). Prompt bytes are not reconstructible; putting them only in the warehouse makes it non-derived |
| **`data/llm/`, content-addressed, immutable, indexed by `llm_call` in DuckDB** | **Chosen** |
| No storage, hashes only | **Rejected.** A hash you cannot resolve to bytes is an assertion, not provenance |

## Structure

```
data/llm/
├── blobs/aa/bb/<sha256>          prompt and response bodies, mode 0444
└── meta/aa/bb/<sha256>.json      sidecar: model, params, called_at, kind
```

Identical mechanics to `BronzeStore` (two-level fan-out, read-only mode, hash verified on read), implemented as a small separate class in `research/llm/store.py` rather than by extending `BronzeStore` — reusing the class would re-import the semantic confusion the directory split exists to prevent. `data/llm/` is already covered by the existing `.gitignore` entry `data/`.

## `llm_call` table

`call_id · kind ('planner'|'writer') · model_id · prompt_sha256 · response_sha256 · temperature · max_tokens · called_at · cached · manifest_id`.

## `research_run` table

**Implementado.** Os campos do `ResearchRunManifest` achatados; `manifest_id` como chave primária; as quatro tuplas (`result_ids`, `metric_versions`, `mapping_sha256s`, `fact_ids`) como `VARCHAR[]`, preservando a ordem — em `result_ids` a ordem é a dos passos do plano, e ordem é significado. Aditiva via `CREATE TABLE IF NOT EXISTS`, então warehouse da Fase 1 migra sozinho.

Append-only, como `gold_fact`: `ON CONFLICT (manifest_id) DO NOTHING`. Uma execução recusada **também** é gravada (D-11) sempre que houve execução; um plano barrado na validação não gera manifesto, porque nada rodou.

`planner` e `writer` não têm coluna aqui: no caminho determinístico são nulos, e no Milestone 2 a procedência de modelo entra pela tabela `llm_call`, que referencia `manifest_id`. Uma corrida sem LLM não carrega colunas de LLM.

Escrita e leitura usam conexões distintas: a execução lê o warehouse em modo somente-leitura e isso não muda, e o manifesto é gravado depois, por uma conexão de escrita própria — o DuckDB não aceita as duas abertas ao mesmo tempo sobre o mesmo arquivo.

## Retrieval

`pat runs --research <manifest_id>` (or a follow-on command) reads `research_run`, joins `llm_call`, and resolves both hashes to bytes in `data/llm/`. Cache lookup on a new run is by `prompt_sha256`: hit → `LLMResponse(cached=True)`, no network, and a past run replays exactly.

**Bronze's meaning is preserved by construction**: nothing under `data/llm/` is ever registered as a `RawDocument`, gets a `Retrieval`, or is reachable from `AsOf.provenance()`. The two provenance chains are parallel and never merge — which is correct, because one proves where a *number* came from and the other proves where a *sentence* came from.

---

# 15. CLI

Three commands, following the existing conventions (`--home`, `_open_readonly`, `_scope`, required `--as-of`).

## `pat capability`

```
pat capability [--as-of YYYY-MM-DD] [--json]
```

Prints concepts, metrics with versions and dependencies, mappings with confirmation status, entities with covered periods and scopes, available derivations, and `capability_sha256`. `--json` emits the canonical bytes. Without `--as-of`, coverage is unfiltered; with it, coverage is what was knowable then. Works without a warehouse for the catalog sections; the entity section prints an instruction to run `pat build` (same pattern as `_open_readonly`).

## `pat plan`

```
pat plan "<question>" --as-of YYYY-MM-DD [--cod-cvm N] [--period-end D]... [--individual] [--json]
```

Calls the planner, validates, resolves, prints the plan and the verdict. **Never executes.** Exit 0 = executable; 1 = violations (each printed with code, offending step, and remedy).

## `pat ask`

```
pat ask "<question>" --as-of YYYY-MM-DD [--cod-cvm N] [--period-end D]...
        [--individual] [--dry-run] [--no-writer] [--plan-file PATH]
```

`--dry-run` is exactly `pat plan` (kept on `ask` because it is where users will look for it). `--no-writer` stops after deterministic rendering — useful, and the mode the golden test runs in. `--plan-file` executes a saved plan with **no LLM call at all**, which is how the golden test achieves determinism.

### MVP session

```
$ pat ask "What was GPA's EBITDA margin in FY2024, and how did it compare with FY2023?" \
      --as-of 2025-06-30 --cod-cvm 14826 --dry-run
```

prints:

```
QUESTION  b3f1…  as_of 2025-06-30  scope consolidated
PLAN      7a29…  objective: compare consolidated EBITDA margin across FY2024 and FY2023

  steps
    margin_fy2023   metric  margem_ebitda@v1  br:cnpj:47508411000156  2023-12-31
    margin_fy2024   metric  margem_ebitda@v1  br:cnpj:47508411000156  2024-12-31
    change          deriv   DELTA             [margin_fy2023, margin_fy2024]

  outputs      margin_fy2023, margin_fy2024, change
  assumptions  "FY" read as the fiscal year ending 31 December
  unresolved   (none)

VALIDATION  ok — 0 violations
RESOLUTION  ok — entity known, both periods covered, mapping confirmed (own mapping)
PLANNER     claude-… · prompt 9c1a… · response 4e77… · capability 2b8d…

not executed (--dry-run)
```

Then the executing form prints, per output step, the same block `_print_metric` uses today (value, period, `knowledge_date`, fidelity, mapping id + confirmation, checks with observed/expected, inputs with `fact_id`), followed by the derived value, the warnings, the prose, and the manifest id.

`-m llm` is a **pytest marker**, not a CLI flag — added to `pyproject.toml` alongside the existing `network` marker, with `addopts` extended to `-m 'not network and not llm'`.

---

# 16. GPA Golden Test

`tests/research/test_golden_gpa_research.py`, offline, reusing the existing `make_engine` fixture from `tests/semantics/conftest.py` and `tests/semantics/golden_gpa.py`.

**Question changed per finding P1.** FY2022 does not exist offline.

1. **Question** — *"What was GPA's EBITDA margin in FY2024, and how did it compare with FY2023?"*, `as_of=2025-06-30`, `scope=CONSOLIDATED`, entity `br:cnpj:47508411000156`.

2. **Expected plan** — committed as `tests/research/plans/gpa_margin_fy24_fy23.json`, with its `plan_id` asserted. **No LLM is involved**; the file is authored by hand, exactly as `golden_gpa.py` transcribes lines by hand.

3. **MetricSteps** — `margin_fy2023` (`margem_ebitda@v1`, `2023-12-31`), `margin_fy2024` (`margem_ebitda@v1`, `2024-12-31`).

4. **DerivationStep** — `change`: `DELTA`, inputs `[margin_fy2023, margin_fy2024]`, output dimension `RATIO`, currency `None`.

5. **Execution** — `execute_plan(validated, engine=make_engine(gpa.zips()))`.

6. **ComputationResults** — three. The two metric results carry the fixture values `EXPECTED_MARGEM[(FY2023, 2025-06-30)] = Decimal("0.100939")` and `EXPECTED_MARGEM[(FY2024, 2025-06-30)] = Decimal("0.038904")` **when quantized to 6 dp**, matching how `test_golden_gpa.py:62` already asserts them. The delta is asserted against `full_precision(fy2024) − full_precision(fy2023)`, computed from the results, **not** from the rounded fixture constants — quantize-then-subtract and subtract-then-quantize are not identical in general, and pinning a literal here would be inventing a value (see Open Decision **D-5**).

7. **Provenance** — for each of the two `NumericClaim`s: `result_id` → `ComputationResult.metric_result` → recursive descent through `inputs` where `is_metric` (reusing the `_folhas` pattern at `test_golden_gpa.py:196`) → leaf `fact_id` → `AsOf.provenance()` non-`None` → `verify_provenance(fact_id, engine.test_bronze)` is `True`. The `DELTA` claim is traced through `derived_from` to both parents, then to their leaves.

8. **Consistency check** — corrected per finding **P2**: `proxima_da_retida` **passes** offline. The assertion is that the check *travels*: the FY2023 `d_and_a@v1` result reachable from the margin's dependency graph carries `status="pass"` with `observed` and `expected` both populated. A *failing* check is proven separately — a unit test over a hand-built `ComputationResult` with `status="fail"` asserting a `CHECK_FAILED` warning appears in `ResearchAnswer.warnings`, plus the existing network test at `test_semantics_live.py:141-164` which already asserts the FY2022 failure against live CVM data.

9. **NumericClaims** — three, tokens `{{s:margin_fy2023}}`, `{{s:margin_fy2024}}`, `{{s:change}}`, each with a `result_id` and a `rendered_value` produced only by `render.py`.

10. **InterpretiveClaims** — none in the offline golden. `--no-writer` mode. Interpretive-claim behaviour is tested with `FakeLLMClient`, so the golden stays free of any model.

11. **Warnings** — expected empty for GPA: fidelity is `EXACT` (asserted at `test_golden_gpa.py:96`), `mapping_confirmed is True` (asserted at line 279), no failing check, both periods covered. **A GPA answer with warnings is a regression**, and asserting the empty set is what makes the warning machinery meaningful when it fires for someone else.

12. **Assertions** — plan_id stable across two constructions; execution byte-identical across two runs including all `result_id`s; the two margins match the fixture at 6 dp; delta sign negative; `currency is None` on all three (per `test_golden_gpa.py:65`); `fidelity is EXACT`; `knowledge_date == KD_2025` for both (per `KNOWLEDGE_DATES`); every `NumericClaim` traces to bronze; `warnings == ()`; no `InterpretiveClaim`; the rendered prose contains no bare digit.

A companion test uses `as_of=2024-06-30` for FY2023 alone and asserts `PERIOD_NOT_COVERED` for FY2024 — the resolver refusing a period that was not yet knowable, which is I4 arriving intact at the research layer.

---

# 17. Test Matrix

CI = default suite (`-m 'not network and not llm'`).

| Test | Layer | Input | Expected | Det.? | LLM? | CI |
|---|---|---|---|---|---|---|
| question requires `as_of` | contracts | question without `as_of` | `ValidationError` | ✔ | ✖ | ✔ |
| `as_of` before a pinned period | contracts | inconsistent pins | `ValidationError` | ✔ | ✖ | ✔ |
| `InterpretiveClaim` rejects a value field | contracts | `extra="forbid"` probe | `ValidationError` | ✔ | ✖ | ✔ |
| `ComputationResult` needs metric or derived | contracts | neither set | `ValidationError` | ✔ | ✖ | ✔ |
| `DerivedValue` empty `derived_from` | contracts | `()` | `ValidationError` | ✔ | ✖ | ✔ |
| `question_id` ignores `asked_at` | canonical | two timestamps | equal ids | ✔ | ✖ | ✔ |
| `plan_id` ignores `PlanProvenance` | canonical | two models, same plan | equal ids | ✔ | ✖ | ✔ |
| canonical JSON is byte-stable | canonical | reordered construction | equal bytes | ✔ | ✖ | ✔ |
| Decimal never serializes as float | canonical | plan with Decimals | no `.0` floats | ✔ | ✖ | ✔ |
| snapshot excludes financial values | capability | built snapshot | no `Decimal` fields | ✔ | ✖ | ✔ |
| snapshot ordering stable | capability | built twice | equal hash | ✔ | ✖ | ✔ |
| snapshot size under limit | capability | current system | < 64 KiB | ✔ | ✖ | ✔ |
| **22 adversarial plans** (§7 table) | validator | one per error code | that code, no execution | ✔ | ✖ | ✔ |
| validator reports all violations | validator | 3-fault plan | 3 violations | ✔ | ✖ | ✔ |
| unknown entity | resolver | absent `entity_id` | `UNKNOWN_ENTITY` | ✔ | ✖ | ✔ |
| period not covered | resolver | FY2024 @ as_of 2024-06-30 | `PERIOD_NOT_COVERED` | ✔ | ✖ | ✔ |
| unconfirmed mapping refused | resolver | company w/o own mapping, default constraints | `UNCONFIRMED_MAPPING` | ✔ | ✖ | ✔ |
| unconfirmed mapping allowed + warned | resolver | `allow_unconfirmed_mapping=True` | executes, warning present | ✔ | ✖ | ✔ |
| `AsOf.entities()` / `coverage()` | query | golden warehouse | GPA, both periods | ✔ | ✖ | ✔ |
| each op: happy path | derivation | fixture results | correct value | ✔ | ✖ | ✔ |
| zero denominator | derivation | `RATIO` by zero | `ZERO_DENOMINATOR` | ✔ | ✖ | ✔ |
| negative CAGR base | derivation | negative first | `NON_POSITIVE_CAGR_BASE` | ✔ | ✖ | ✔ |
| zero CAGR base | derivation | zero first | `NON_POSITIVE_CAGR_BASE` | ✔ | ✖ | ✔ |
| mixed currency | derivation | BRL + USD | `CURRENCY_MISMATCH` | ✔ | ✖ | ✔ |
| dimension mismatch | derivation | MONEY + RATIO | `DIMENSION_MISMATCH` | ✔ | ✖ | ✔ |
| failed input short-circuits | derivation | one failure | `INPUT_FAILED`, no partial | ✔ | ✖ | ✔ |
| no op yields non-finite | derivation | fuzz over fixtures | all `is_finite()` | ✔ | ✖ | ✔ |
| fidelity is weakest | derivation | exact + approximate | `APPROXIMATE` | ✔ | ✖ | ✔ |
| `knowledge_date` is max | derivation | two dates | later one | ✔ | ✖ | ✔ |
| invalid plan cannot execute | executor | bare `ResearchPlan` | `TypeError` | ✔ | ✖ | ✔ |
| `MetricUnavailable` passthrough | executor | missing D&A (reuse `_rebuild_sem_dfc`) | reason/`concept_id`/`tried` intact | ✔ | ✖ | ✔ |
| failed output ⇒ no answer | executor | failing output step | `outputs_available False` | ✔ | ✖ | ✔ |
| topological order | executor | shuffled steps | same results | ✔ | ✖ | ✔ |
| money formatting bands | renderer | bn / MM / small | correct band | ✔ | ✖ | ✔ |
| ratio as percent, 2 dp | renderer | `0.100939` | `"10.09%"` | ✔ | ✖ | ✔ |
| rounding is `HALF_EVEN` | renderer | tie values | fixed | ✔ | ✖ | ✔ |
| bare digit rejected | answer | prose with `9.4%` | `FORBIDDEN_LITERAL` | ✔ | ✖ | ✔ |
| unknown token rejected | answer | `{{s:nope}}` | `UNKNOWN_TOKEN` | ✔ | ✖ | ✔ |
| no-citation prose rejected | answer | zero `{{s:}}` | rejected | ✔ | ✖ | ✔ |
| valid tokens substitute | answer | valid prose | exact substitution | ✔ | ✖ | ✔ |
| every claim traces to bronze | provenance | GPA golden | `verify_provenance` True | ✔ | ✖ | ✔ |
| derived claim traces via parents | provenance | `change` step | both leaves reached | ✔ | ✖ | ✔ |
| manifest records versions + shas | provenance | golden run | all present | ✔ | ✖ | ✔ |
| execution twice identical | determinism | golden plan | byte-equal incl. hashes | ✔ | ✖ | ✔ |
| **GPA golden end-to-end** | integration | frozen plan, `--no-writer` | §16 assertions | ✔ | ✖ | ✔ |
| period-not-yet-knowable | integration | FY2024 @ 2024-06-30 | refusal | ✔ | ✖ | ✔ |
| planner parses a canned plan | fake LLM | `FakeLLMClient` | expected `plan_id` | ✔ | ✖ | ✔ |
| planner rejects malformed JSON | fake LLM | garbage response | rejection, no retry | ✔ | ✖ | ✔ |
| planner rejects pin contradiction | fake LLM | plan violating a pin | `PIN_CONTRADICTED_*` | ✔ | ✖ | ✔ |
| writer rejection surfaces, no retry | fake LLM | digit-bearing prose | rejected, one call | ✔ | ✖ | ✔ |
| cache hit avoids a call | fake LLM | same prompt twice | `cached=True` | ✔ | ✖ | ✔ |
| CLI `capability` w/o warehouse | cli | tmp home | exit 0, catalog printed | ✔ | ✖ | ✔ |
| CLI `ask --plan-file` | cli | golden plan | exit 0, numbers match | ✔ | ✖ | ✔ |
| layering rules (§18) | layering | AST scan | no forbidden import | ✔ | ✖ | ✔ |
| live planner produces a **valid** plan | live LLM | 5 questions | validator passes | ✖ | ✔ | `-m llm` |
| live planner honours pins | live LLM | pinned entity/period | no contradiction | ✖ | ✔ | `-m llm` |
| live writer emits no digits | live LLM | GPA tokens | passes no-digit check | ✖ | ✔ | `-m llm` |

Live tests assert **validity, never a specific plan**. Asserting a specific plan would be testing the model, which this suite cannot meaningfully do.

---

# 18. Layering

`tests/research/test_layering_research.py`, reusing the AST helpers from `tests/semantics/test_layering.py` (`_imports`, `_module_level_imports` — same technique, since a dead import inside a function is still a coupling).

| rule | forbidden | permitted |
|---|---|---|
| **R1** contracts stay universal | `contracts/research.py` may not import `pat.semantics.*`, `pat.query`, `pat.store`, `pat.research.*` | `pat.contracts.common`, `pat.contracts.semantics` |
| **R2** Phase 2 does not know Phase 3 | nothing under `src/pat/semantics/` may import `pat.research` | — |
| **R3** LLM layer is data-blind | nothing under `research/llm/`, nor `planner.py`, nor `writer.py`, may import `pat.query`, `pat.store`, `pat.semantics.engine`, `pat.semantics.loader`, or `pat.build` | `contracts.*`, `research.canonical` |
| **R4** one Engine holder | the set of files under `pat/research/` importing `pat.semantics.engine` or calling `Engine` is **exactly** `{execute.py, __init__.py}` | — |
| **R5** one AsOf holder | the set importing `pat.query` is **exactly** `{resolve.py, capability.py, __init__.py}` | — |
| **R6** validator is pure | `validate.py` may not import `pat.query`, `pat.store`, `pat.research.llm`, `httpx`, `datetime.now`, `random` | `pat.semantics.registry` (introspection only) |
| **R7** one Anthropic file | the set importing `httpx` is **exactly** `{llm/anthropic.py}` | — |
| **R8** one formatter | the set containing `Decimal` display formatting (`quantize`, `:,`, `%` format specs) is **exactly** `{render.py}` | `cli.py` is exempt for its own printing |
| **R9** no literals in the grammar | `contracts/research.py` contains no `Decimal` field on any `PlanStep` subclass | — |

R4, R5 and R7 use the exact-set form of `test_layering.py:97-110` rather than a negative assertion, because an exact set catches a *new* file that quietly reaches through, which a blacklist never does.

---

# 19. Implementation Order

## Milestone 1 — Deterministic Phase 3 core (steps 1–7) — ✅ CONCLUÍDO

No LLM, no new dependency, no network. The repo is shippable and green at the end of every step.

| # | Files | Objective | Prereq | Tests | Risk |
|---|---|---|---|---|---|
| 1 | `contracts/research.py`, `research/canonical.py` | Every contract + hashing | — | contracts, canonical (9 tests) | low — pure data |
| 2 | `query/asof.py` (+2 methods) | Entity enumeration and coverage | — | query (2 tests) | low — additive; risk is only touching a Phase 2 file, mitigated by pure addition |
| 3 | `research/capability.py`, `cli.py` (`capability`) | The snapshot + `pat capability` | 1, 2 | capability (3), cli (1) | low |
| 4 | `research/validate.py` | Pure validation | 1 | 22 adversarial + 1 (23) | **medium — the security-critical module.** Every table row is a test |
| 5 | `research/resolve.py` | Warehouse checks | 1, 2 | resolver (4) | low |
| 6 | `research/derive.py`, `research/execute.py` | Plan → results | 1, 4, 5 | derivation (10), executor (4) | **medium — CAGR precision, non-finite guards** |
| 7 | `research/render.py`, `answer.py`, `manifest.py`, `store/db.py`, `cli.py` (`ask --plan-file --no-writer`) | Rendering, no-digit rule, provenance | 6 | renderer (3), answer (4), provenance (3), determinism (1), **GPA golden (§16)**, layering | medium — the golden is where §2's corrections land |

**Milestone 1 exit — atingido.** `pat capability` funciona; `pat ask --plan-file …` produz as margens do GPA deterministicamente (FY2023 10.09%, FY2024 3.89%, delta −6.20%) com procedência completa até os bytes no bronze; cada execução fica registrada em `research_run`; a suíte offline inteira está verde (406); **não existe uma linha de código de LLM**. É o ponto em que a arquitetura está provada e daria para parar indefinidamente com um sistema útil e funcionando.

Um desvio em relação ao plano do passo 7: a persistência do manifesto não estava no escopo original do `store/db.py` além da criação da tabela, e ganhou módulo próprio (`store/research.py`) mais o comando de leitura `pat runs --research` — sem caminho de leitura, a tabela seria escrita que ninguém confere.

## Milestone 2 — LLM planner (steps 8–10) — pendente

| # | Files | Objective | Prereq | Tests | Risk |
|---|---|---|---|---|---|
| 8 | `research/llm/__init__.py` (Protocol + `FakeLLMClient`), `research/planner.py` | Planner against a fake client | 1, 4 | fake-LLM planner (3) | low — deterministic |
| 9 | `research/llm/anthropic.py`, `research/llm/store.py`, `store/db.py` (`llm_call`), `pyproject.toml` (marker) | Real client, cache, provenance | 8 | cache (1), live planner (2, `-m llm`) | **medium — first network path; API key handling; the only step that needs credentials** |
| 10 | `cli.py` (`plan`, `ask`) | Full CLI | 9 | cli (2) | low |

**Milestone 2 exit:** `pat plan "…" --dry-run` produces a validated plan from a real question; every plan is hashed, cached and manifested.

## Milestone 3 — Writer + complete MVP (step 11) — pendente

| # | Files | Objective | Prereq | Tests | Risk |
|---|---|---|---|---|---|
| 11 | `research/writer.py`, `cli.py`, `README.md` | Prose with no digits; correct the phase table | 10 | writer fake (1), live writer (1, `-m llm`) | low technically, **highest editorial risk** (§21 R-8) |

**Milestone 3 exit:** `pat ask` end to end, and the Definition of Done in §22 satisfied.

---

# 20. Open Decisions

| # | Decision | Options | Recommendation | Impact | Blocks? |
|---|---|---|---|---|---|
| **D-1** | MVP question, given no FY2022 offline | (a) FY2024 vs FY2023 @ 2025-06-30; (b) add FY2022 to the fixture; (c) FY2023 at two `as_of` dates | **(a)** — uses existing hand-checked values, needs no Phase 2 change. (b) means editing a Phase 2 golden fixture, which I would not do to serve a Phase 3 demo | Defines the golden test | **YES — blocks step 7** |
| **D-2** | How to prove a failing check surfaces | (a) unit test on a synthetic result + rely on the existing FY2022 network test; (b) craft a failing fixture | **(a)** — no invented values, and the network test already asserts exactly this | Warning machinery coverage | **YES — blocks step 7** |
| **D-3** | LLM byte storage | `data/llm/` · bronze · DuckDB blob · hashes only | **`data/llm/`** (§14) | Preserves bronze's meaning | **YES — blocks step 9** |
| **D-4** | `result_id` scheme | content-addressed vs `sha256(plan_id\|step_id)` | **Content-addressed** — different numbers get different ids, which is informative | Determinism test shape | **YES — blocks step 6** |
| **D-5** | Delta precision | (a) assert from full-precision results; (b) pin a literal | **(a)** — (b) would be inventing a value | Golden assertion | **YES — blocks step 7** |
| **D-6** | Anthropic SDK vs httpx | add `anthropic` dep · use existing `httpx` | **httpx** — zero new dependency, ~80 lines, consistent with "dependências mínimas". Revisit if streaming or tool use is ever needed | `pyproject.toml`, `uv.lock` | **YES — blocks step 9** |
| **D-7** | Where the prompt text lives | module constant in `planner.py` vs a `.txt` file | **Module constant** — versioned with the parser of its output, hash trivially derivable | Provenance simplicity | no — defaults to constant |
| **D-8** | Series completeness policy | all-or-nothing per output vs per-period unavailability rendered | **Per-period, explicitly rendered**; an output whose own step failed still blocks the answer | Executor semantics | no — decidable at step 6 |
| **D-9** | Does `ask` require `--cod-cvm`? | required · optional (planner resolves from the snapshot) | **Optional.** Making it required would prove nothing about planning; the pin remains available and authoritative | CLI ergonomics | no |
| **D-10** | Default `max_fidelity` | `EXACT` vs `APPROXIMATE` | **`APPROXIMATE`** — `EXACT` would refuse every company on the default family, which is most of them; the warning carries the caveat | Refusal rate | no |
| **D-11** | Should `pat ask` write a manifest row on failure? | yes · no | **Yes** — a refused run is a run, and its provenance is the audit trail of a refusal | `research_run` semantics | no |
| **D-12** | Answer language | Portuguese vs English prose | **Match the question's language**; the repo convention (English identifiers, Portuguese prose) governs code, not output | Writer prompt | no |

---

# 21. Risks

| # | Threat | Existing mitigation | Proposed mitigation | Residual |
|---|---|---|---|---|
| **R-1** | Planner produces a semantically wrong plan (right shape, wrong intent — e.g. parent-only when consolidated was meant) | none | `--dry-run` prints the plan before execution; the answer always restates entity/scope/periods/`as_of`/metric versions; pins override unconditionally | **HIGH and irreducible.** Validation proves well-formedness, never intent. Must be stated in the docs, not engineered around |
| **R-2** | Capability snapshot outgrows the prompt | none | 64 KiB hard limit, `CapabilityTooLarge` rather than truncation; size test in CI | LOW near-term. When it binds, entity filtering is a design change needing its own review |
| **R-3** | Plan reproducibility ≠ question reproducibility | none | `plan_id` is the reproducibility unit; `--plan-file` replays exactly; prompt/response cached and resolvable | **MEDIUM, accepted.** A recorded plan reproduces forever; a recorded question may re-plan differently. This is a real weakening of I3 and belongs in the README, plainly |
| **R-4** | Missing period silently shortens a series | `MetricUnavailable` at the engine | Resolver pre-flights coverage; per-period unavailability rendered explicitly (D-8); a failed output blocks the whole answer | LOW |
| **R-5** | CAGR edge cases produce nonsense | `ComputationUnavailable` precedent in `margem_ebitda.py` | Refuse `a<=0`, `b<=0`, `years<=0` *before* computing; fixed `prec=28` ln/exp; `is_finite()` post-condition on every op | LOW |
| **R-6** | Generated SQL bypasses the semantic layer | no codegen exists | Structural: no codegen in Phase 3. If it ever ships, an explicit prohibition backed by a layering test | **NONE now.** The single most likely way Phase 3 could damage Phase 2, and the MVP forecloses it entirely |
| **R-7** | Prompt injection via question text or capability content | none | Question text is one string in a delimited user block, never concatenated into instructions; snapshot content is repo-authored (concepts, metrics, mappings) plus `denom_cia` from CVM filings — the **only** externally-sourced text in the prompt. Injection's maximum effect is a bad plan, which the validator still constrains to a well-formed, pin-respecting, registry-bounded plan | **LOW.** Worth noting: the closed grammar is also the injection mitigation — there is no instruction a malicious `denom_cia` could carry that the plan grammar can express |
| **R-8** | Writer prose is technically digit-free but misleading ("margins expanded sharply" over 30 bp) | none | No-digit rule is a floor; every claim cites a `result_id`; the deterministic block is printed above the prose so the reader sees numbers first; `--no-writer` always available | **MEDIUM and irreducible.** No mechanical fix exists. Keep the writer last, small, and optional |
| **R-9** | Provenance chain breaks between layers | I2 + `verify_provenance()` | `derived_from` non-empty by contract; `ComputationResult` cannot exist without a metric result or parents; golden test walks every claim to bronze bytes | LOW |
| **R-10** | Contract versioning confusion (two axes) | metric versions are established | Rule stated once and enforced: **contract versions govern shape; metric versions govern meaning**; both recorded in the manifest; `Literal["v1"]` makes a shape change a hard parse failure | LOW |
| **R-11** | Unconfirmed mapping produces an unvetted number | `mapping_confirmed` reaches `MetricResult` | `allow_unconfirmed_mapping=False` **by default** — refuses rather than warns; when enabled, an `UNCONFIRMED_MAPPING` warning is attached to every affected claim | LOW |
| **R-12** | A failing consistency check is hidden | checks reach `MetricResult` | `CHECK_FAILED` warning per failing check with `observed`/`expected`; `allow_failed_checks` defaults `True` (surface, never suppress) and **no option suppresses a check** — the flag only decides whether it blocks | LOW |

---

# 22. Definition of Done

Phase 3 is complete when every item below is objectively true.

**Functional**

1. `pat capability` runs without a warehouse (catalog sections) and with one (entity coverage), printing a stable `capability_sha256`.
2. `pat plan "<question>" --as-of … --dry-run` produces a validated plan, or a refusal listing every violation with its code and remedy, and **executes nothing**.
3. `pat ask --plan-file tests/research/plans/gpa_margin_fy24_fy23.json --no-writer` executes deterministically and produces byte-identical results, including all `result_id`s, on two consecutive runs.
4. `pat ask "<question>" --as-of 2025-06-30` runs end to end: plan → validate → resolve → execute → render → write → answer.
5. The two GPA margins match `golden_gpa.EXPECTED_MARGEM` at 6 dp; the delta matches the full-precision difference of the computed results.

**Correctness and audit**

6. Every `NumericClaim` in the GPA golden traces `result_id` → `MetricResult` → leaf `fact_id` → `AsOf.provenance()` → `verify_provenance()` returning `True` against bronze bytes on disk.
7. A failing consistency check produces a `CHECK_FAILED` warning carrying `observed` and `expected` — proven by unit test and by the existing FY2022 network test. (Corrected from the prior proposal: the offline fixture's check passes.)
8. A company without its own mapping is **refused** under default constraints, and warned under `allow_unconfirmed_mapping=True`.
9. Writer prose containing a bare digit, or an unknown token, or no citation, is **rejected with no retry**, and the deterministic numbers still print.
10. The full offline suite is green: **406 tests** (243 pré-Fase 3 + 163 em `tests/research/`), `uv lock --check` passes, and `-m network` / `-m llm` remain excluded from the default run.

**Structural — verified by layering test, not by review**

11. No module in `pat.research.llm`, `planner.py`, or `writer.py` imports `pat.query`, `pat.store`, or `pat.semantics.engine`. The LLM has no path to the warehouse.
12. No generated code exists anywhere in the repository.
13. No sandbox exists, and no `subprocess`/`exec`/`eval` appears anywhere under `src/pat/research/`.
14. No tool-calling loop exists: `LLMClient.complete` is called at most once per planner run and at most once per writer run, asserted by a call-counting test against `FakeLLMClient`.
15. `contracts/research.py` contains no `Decimal` field on any `PlanStep`, so a literal number is unrepresentable in the plan grammar.
16. Nothing under `src/pat/semantics/` imports `pat.research`; Phase 2 remains unaware Phase 3 exists.
17. `git diff 3cf93d3..HEAD --stat` shows **no changes** to `src/pat/semantics/`, `src/pat/store/{bronze,catalog,gold,silver}.py`, `src/pat/parse/`, or `src/pat/sources/`.

---

## Aprovações que bloqueiam o início da implementação

**D-1** (MVP question → FY2024 vs FY2023), **D-2** (how the failing check is proven), **D-3** (`data/llm/`), **D-4** (content-addressed `result_id`), **D-5** (delta precision), **D-6** (httpx over the Anthropic SDK).

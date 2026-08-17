"""Conferencias que dependem do warehouse. Somente leitura.

A divisao com `validate.py` e limpa: o validador responde "este plano esta bem
formado?", que e funcao pura do plano e do codigo; o resolvedor responde "este
warehouse consegue servi-lo?", que muda quando alguem roda `pat build`.

Dois limites honestos, herdados da Fase 2 e que nao devem ser disfarcados
aqui:

- Cobertura sai da presenca de linhas no gold. `SCOPE_NOT_COVERED` significa
  "nao ha fatos desse escopo", nao "o regime nao publica esse escopo" - o
  resolver da CVM nunca emite `SCOPE_NOT_AVAILABLE`, e esta camada nao pode
  prometer uma distincao que a de baixo nao faz.
- Periodo coberto ainda pode dar `MISSING_FACT_AS_OF` na execucao, porque
  cobertura diz que o periodo existe, nao que toda conta necessaria existe.
  Conferir endereco a endereco aqui seria duplicar o motor.
"""

from __future__ import annotations

import duckdb

from pat.contracts.research import (
    MetricStep,
    ResearchConstraints,
    ResearchPlan,
    ResolutionCode,
    ResolutionIssue,
)
from pat.contracts.semantics import ReportingScope
from pat.query.asof import AsOf
from pat.research import DEFAULT_SOURCE


def _issue(
    code: ResolutionCode,
    message: str,
    *,
    step_id: str | None = None,
    entity_id: str | None = None,
    remedy: str | None = None,
) -> ResolutionIssue:
    return ResolutionIssue(
        code=code, message=message, step_id=step_id, entity_id=entity_id, remedy=remedy
    )


def resolve_plan(
    plan: ResearchPlan,
    *,
    conn: duckdb.DuckDBPyConnection,
    mappings,
    constraints: ResearchConstraints,
    source: str = DEFAULT_SOURCE,
) -> tuple[ResolutionIssue, ...]:
    """Pendencias do plano contra o warehouse, em ordem estavel."""
    asof = AsOf(conn)
    out: list[ResolutionIssue] = []
    consolidated = plan.scope is ReportingScope.CONSOLIDATED

    coverage_cache: dict[str, object] = {}
    checked_entities: set[str] = set()

    for step in plan.steps:
        if not isinstance(step, MetricStep):
            continue

        entity_id = step.entity_id
        if entity_id not in coverage_cache:
            coverage_cache[entity_id] = asof.coverage(entity_id, as_of=plan.as_of)
        cover = coverage_cache[entity_id]

        if cover is None:
            out.append(
                _issue(
                    ResolutionCode.UNKNOWN_ENTITY,
                    f"nenhum fato no gold para {entity_id} conhecido em {plan.as_of}",
                    step_id=step.step_id,
                    entity_id=entity_id,
                    remedy="rode `pat build cvm.dfp` para esta companhia",
                )
            )
            continue

        if step.period_end not in cover.period_ends:
            disponiveis = ", ".join(str(p) for p in cover.period_ends) or "nenhum"
            out.append(
                _issue(
                    ResolutionCode.PERIOD_NOT_COVERED,
                    f"{entity_id} nao tem o exercicio findo em {step.period_end} "
                    f"conhecido em {plan.as_of}. Disponiveis: {disponiveis}",
                    step_id=step.step_id,
                    entity_id=entity_id,
                    remedy="materialize o ano com `pat build` ou consulte outro periodo",
                )
            )

        if consolidated not in cover.consolidated_scopes:
            out.append(
                _issue(
                    ResolutionCode.SCOPE_NOT_COVERED,
                    f"{entity_id} nao tem fatos no escopo {plan.scope} conhecidos "
                    f"em {plan.as_of}",
                    step_id=step.step_id,
                    entity_id=entity_id,
                )
            )

        # Mapeamento: uma vez por companhia, nao uma vez por passo.
        if entity_id in checked_entities:
            continue
        checked_entities.add(entity_id)

        chain = mappings.resolve(entity_id, source=source)
        if chain is None:
            out.append(
                _issue(
                    ResolutionCode.NO_MAPPING,
                    f"nenhum mapeamento cobre {entity_id} na fonte {source}",
                    step_id=step.step_id,
                    entity_id=entity_id,
                    remedy=f"escreva src/pat/semantics/mappings/ para {entity_id}",
                )
            )
        elif not chain.confirmed and not constraints.allow_unconfirmed_mapping:
            out.append(
                _issue(
                    ResolutionCode.UNCONFIRMED_MAPPING,
                    f"{entity_id} nao tem mapeamento proprio conferido; cairia na "
                    f"familia default {chain.head.mapping_id}",
                    step_id=step.step_id,
                    entity_id=entity_id,
                    remedy=(
                        "escreva o mapeamento da empresa, ou passe "
                        "allow_unconfirmed_mapping=True aceitando o aviso"
                    ),
                )
            )

    return tuple(out)

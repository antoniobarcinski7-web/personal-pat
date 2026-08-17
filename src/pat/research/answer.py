"""Montagem da resposta, e a regra do digito.

Duas coisas moram aqui:

1. A conferencia que o texto tem que passar antes de virar resposta. No
   Milestone 1 o texto e montado por codigo; no Milestone 2 ele vem de um
   modelo. A conferencia e a MESMA, de proposito - o caminho deterministico
   exercita hoje o portao que o escritor vai enfrentar depois.

2. Os avisos. Nenhum deles suprime numero: fidelidade aproximada, mapeamento
   nao conferido e check que falhou viajam junto com o valor, ate a resposta.

A regra do digito, inteira:

    1. extrai todo {{...}}; token fora da tabela -> UNKNOWN_TOKEN
    2. remove os tokens do texto
    3. se sobrar qualquer [0-9] -> FORBIDDEN_LITERAL
    4. substitui e emite

O passo 3 so e uma linha porque rotulo de periodo tambem e token. A
alternativa seria uma lista de numerais permitidos, e lista de excecao
envelhece mal.
"""

from __future__ import annotations

import re

from pat.contracts.research import (
    Claim,
    ComputationResult,
    NumericClaim,
    ResearchAnswer,
    ResearchWarning,
    ResultKind,
    WarningKind,
)
from pat.contracts.semantics import Fidelity
from pat.research.render import period_token, period_tokens, render_results, value_token

_TOKEN = re.compile(r"\{\{[a-z]:[a-z0-9_]+\}\}")
_DIGIT = re.compile(r"[0-9]")


class ProseRejected(ValueError):
    """Texto que nao pode virar resposta."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def substitution_table(results: tuple[ComputationResult, ...]) -> dict[str, str]:
    """Tabela fechada: token -> texto ja formatado."""
    table = {claim.token: claim.rendered_value for claim in render_results(results)}
    table.update(period_tokens(results))
    return table


def check_prose(prose: str, table: dict[str, str]) -> None:
    """Levanta `ProseRejected` se o texto violar a regra do digito."""
    for token in _TOKEN.findall(prose):
        if token not in table:
            raise ProseRejected(
                "UNKNOWN_TOKEN",
                f"{token} nao esta na tabela de substituicao ({sorted(table)})",
            )
    resto = _TOKEN.sub("", prose)
    achado = _DIGIT.search(resto)
    if achado:
        inicio = max(0, achado.start() - 20)
        raise ProseRejected(
            "FORBIDDEN_LITERAL",
            f"numero literal fora de token em ...{resto[inicio:achado.end() + 20]!r}",
        )
    if not any(token.startswith("{{s:") for token in _TOKEN.findall(prose)):
        raise ProseRejected(
            "NO_CITATION", "texto sem nenhum {{s:...}}: interpretacao sem numero citado"
        )


def substitute(prose: str, table: dict[str, str]) -> str:
    """Substitui apos a conferencia. Nao chama `check_prose` por conta propria:
    quem monta decide quando conferir, e conferir duas vezes esconderia o
    ponto em que a rejeicao aconteceu."""
    return _TOKEN.sub(lambda m: table[m.group(0)], prose)


# ---------------------------------------------------------------------------
# Avisos
# ---------------------------------------------------------------------------


def collect_warnings(results: tuple[ComputationResult, ...]) -> tuple[ResearchWarning, ...]:
    """Toda ressalva que o resultado carrega, promovida a aviso da resposta."""
    out: list[ResearchWarning] = []
    for result in results:
        if result.fidelity is not Fidelity.EXACT:
            out.append(
                ResearchWarning(
                    kind=WarningKind.APPROXIMATE_FIDELITY,
                    message=(
                        f"{result.step_id} tem fidelidade {result.fidelity}: montado sobre "
                        "binding que aproxima o conceito. Ver `pat mappings`."
                    ),
                    result_id=result.result_id,
                )
            )
        if result.kind is not ResultKind.METRIC:
            continue

        metric = result.metric_result
        if not metric.mapping_confirmed:
            out.append(
                ResearchWarning(
                    kind=WarningKind.UNCONFIRMED_MAPPING,
                    message=(
                        f"{result.step_id} usou a familia default {metric.mapping_id}, "
                        "sem mapeamento conferido para esta companhia"
                    ),
                    result_id=result.result_id,
                )
            )
        for check in metric.checks:
            if check.status == "fail":
                out.append(
                    ResearchWarning(
                        kind=WarningKind.CHECK_FAILED,
                        message=(
                            f"{result.step_id}: check {check.check_id} FALHOU "
                            f"(observado {check.observed}, esperado {check.expected}). "
                            "O numero sai assim mesmo, marcado."
                        ),
                        result_id=result.result_id,
                    )
                )
    return tuple(out)


# ---------------------------------------------------------------------------
# Resposta deterministica
# ---------------------------------------------------------------------------


_ROTULO_OP = {
    "delta": "variacao",
    "delta_pct": "variacao percentual",
    "ratio": "razao",
    "cagr": "taxa composta",
    "min": "minimo",
    "max": "maximo",
    "mean": "media",
}


def deterministic_prose(plan, results: tuple[ComputationResult, ...]) -> str:
    """Prosa montada por codigo, so com tokens e palavras sem digito.

    Nao e o escritor - e o que permite fechar o Milestone 1 sem modelo, e
    passar a resposta pelo mesmo portao que o escritor vai enfrentar.

    O `objective` do plano NAO entra aqui, e nem o `step_id`: os dois contem
    digitos ("FY2024", "margin_fy2023") e no Milestone 2 o `objective` sera
    texto escrito por modelo. Deixar qualquer um dos dois passar abriria
    exatamente o buraco que a regra do digito existe para fechar - um numero
    na resposta que nao veio de substituicao.
    """
    por_passo = {result.step_id: result for result in results}
    frases: list[str] = []

    for step_id in plan.outputs:
        result = por_passo.get(step_id)
        if result is None:
            continue
        if result.metric_result is not None:
            rotulo = period_token(f"fy{result.metric_result.period_end.year}")
        else:
            rotulo = _ROTULO_OP.get(str(result.derived.op), "derivado")
        frases.append(f"{rotulo}: {value_token(step_id)}")

    return "; ".join(frases) + "."


def build_answer(
    *,
    plan,
    plan_id: str,
    results: tuple[ComputationResult, ...],
    manifest_id: str,
    prose: str | None = None,
    extra_claims: tuple[Claim, ...] = (),
) -> ResearchAnswer:
    """Resposta final. `prose` nulo usa a montagem deterministica."""
    table = substitution_table(results)
    raw = prose if prose is not None else deterministic_prose(plan, results)
    check_prose(raw, table)

    numeric: tuple[NumericClaim, ...] = render_results(results)
    return ResearchAnswer(
        question_id=plan.question_id,
        plan_id=plan_id,
        prose=substitute(raw, table),
        claims=(*numeric, *extra_claims),
        warnings=collect_warnings(results),
        manifest_id=manifest_id,
    )



"""Um turno: pergunta -> plano -> execucao -> resposta ou recusa.

A ordem das operacoes aqui nao e arbitraria, e ela e a mesma de `cmd_ask` e
`cmd_plan` porque o motivo e o mesmo: o DuckDB nao aceita uma conexao de
escrita enquanto a de leitura do mesmo arquivo esta aberta. Por isso o turno
abre leitura, planeja, FECHA, grava a chamada do planejador, reabre leitura,
executa, FECHA e so entao grava manifesto e chamada do escritor.

`run_turn` nao levanta por dado. Plano recusado, warehouse que nao serve,
metrica indisponivel, modelo que nao produziu saida valida - tudo isso vira
`TurnRefusal`, porque tudo isso e comportamento projetado e nao erro de
transporte. A unica excecao que escapa e `LLMError`, que o handler HTTP mapeia
para 502: essa sim e infra.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from pat.chat.record import record_planner_call, record_turn
from pat.chat.session import ConversationState, build_context
from pat.config import Paths
from pat.contracts.chat import ChatRequest, ChatTurn, RefusalKind, TurnRefusal
from pat.contracts.research import ResearchQuestion, ViolationCode
from pat.research import DEFAULT_SOURCE, plan_for_question, run_plan
from pat.research.canonical import plan_id as compute_plan_id
from pat.research.canonical import sha256_of
from pat.store.db import connect

__all__ = ["DEFAULT_SOURCE", "build_question", "run_turn"]


def build_question(request: ChatRequest, state: ConversationState) -> ResearchQuestion:
    """`ChatRequest` + `as_of` da sessao -> `ResearchQuestion`.

    Os `pinned_*` saem ordenados e sem repeticao pela mesma razao que em
    `cli.py:_question_from_args`: `question_id` e hash da pergunta canonica, e
    a ordem em que alguem clicou nos chips nao pode virar pergunta diferente.
    """
    return ResearchQuestion(
        text=request.text,
        as_of=state.as_of,
        asked_at=datetime.now(UTC),
        requested_output=request.requested_output,
        pinned_entities=tuple(sorted(set(request.pinned_entities))),
        pinned_periods=tuple(sorted(set(request.pinned_periods))),
        pinned_scope=request.pinned_scope,
    )


def run_turn(
    request: ChatRequest,
    state: ConversationState,
    *,
    paths: Paths,
    llm,
    model: str,
    source: str = DEFAULT_SOURCE,
    git_sha: str | None = None,
) -> ChatTurn:
    from pat.research.planner import PlannerError
    from pat.research.writer import WriterError

    comecou = time.monotonic()
    question = build_question(request, state)
    context = build_context(state)
    context_sha256 = sha256_of(context) if context is not None else None

    def _turn(**campos) -> ChatTurn:
        return ChatTurn(
            turn_index=len(state.turns),
            session_id=state.session_id,
            asked_at=question.asked_at,
            question=question,
            context_sha256=context_sha256,
            elapsed_ms=int((time.monotonic() - comecou) * 1000),
            **campos,
        )

    # -- planejar -----------------------------------------------------------
    conn = connect(paths.warehouse, read_only=True)
    try:
        try:
            planned = plan_for_question(
                conn, question=question, llm=llm, model=model, source=source, context=context
            )
        except PlannerError as exc:
            return _turn(refusal=_from_planner_error(exc))
    finally:
        conn.close()

    record_planner_call(paths, planned.provenance, fingerprint=llm.fingerprint)
    plan = planned.plan

    # -- executar -----------------------------------------------------------
    conn = connect(paths.warehouse, read_only=True)
    try:
        try:
            outcome = run_plan(
                conn,
                plan=plan,
                question=question,
                source=source,
                git_sha=git_sha,
                llm=llm,
                model=model,
            )
        except WriterError as exc:
            # Sem queda para `deterministic_prose`, hoje nem depois: apresentar
            # um texto que ninguem pediu como se fosse o pedido e pior que
            # falhar. Quem quer o deterministico roda `pat ask` sem --writer.
            # Retentativa interna tambem nao: seria um segundo caminho de
            # influencia que a procedencia nao registraria. A UI oferece um
            # turno novo, com chamada nova e rastro proprio.
            return _turn(
                plan=plan,
                plan_id=compute_plan_id(plan),
                planner_provenance=planned.provenance,
                refusal=_from_writer_error(exc),
            )
    finally:
        conn.close()

    # -- gravar -------------------------------------------------------------
    record_turn(
        paths,
        outcome.manifest,
        outcome.writer.provenance if outcome.writer is not None else None,
        fingerprint=llm.fingerprint,
    )

    base = {
        "plan": plan,
        "plan_id": outcome.plan_id,
        "manifest": outcome.manifest,
        "planner_provenance": planned.provenance,
        "writer_provenance": outcome.writer.provenance if outcome.writer is not None else None,
    }

    if outcome.answer is not None:
        return _turn(answer=outcome.answer, **base)
    return _turn(refusal=_from_outcome(outcome), **base)


# ---------------------------------------------------------------------------
# Traducao de falha em recusa (§14.1)
# ---------------------------------------------------------------------------


def _from_planner_error(exc) -> TurnRefusal:
    return TurnRefusal(
        kind=RefusalKind.PLANNER_FAILED,
        summary="o modelo nao produziu um plano valido",
        detail=f"{exc.failure} · resposta {exc.response_sha256[:12]}",
        codes=(str(exc.failure),),
    )


def _from_writer_error(exc) -> TurnRefusal:
    return TurnRefusal(
        kind=RefusalKind.WRITER_FAILED,
        summary="o modelo nao produziu uma resposta valida",
        detail=f"{exc.failure} · resposta {exc.response_sha256[:12]}",
        codes=(str(exc.failure),),
    )


def _from_outcome(outcome) -> TurnRefusal:
    """A recusa certa para o que a Fase 3 devolveu, sem reescrever motivo.

    A duvida do planejador e distinguida das demais violacoes de proposito:
    `plan.unresolved` nao-vazio significa que o modelo devolveu a duvida em vez
    de chutar - o que e o comportamento desejado -, e a apresentacao dela e
    outra (os `candidates` viram pino do usuario). Colapsar as duas faria a
    melhor coisa que o planejador pode fazer parecer um plano malformado.
    """
    if outcome.plan.unresolved:
        item = outcome.plan.unresolved[0]
        return TurnRefusal(
            kind=RefusalKind.PLANNER_UNRESOLVED,
            summary="preciso que voce desambigue antes de eu planejar",
            detail=item.detail,
            codes=tuple(str(pendencia.kind) for pendencia in outcome.plan.unresolved),
            candidates=item.candidates,
        )

    if outcome.violations:
        return TurnRefusal(
            kind=RefusalKind.PLAN_INVALID,
            summary="o plano nao passou na validacao",
            detail=outcome.violations[0].message,
            codes=tuple(str(v.code) for v in outcome.violations),
            remedies=tuple(v.remedy for v in outcome.violations if v.remedy),
        )

    if outcome.issues:
        return TurnRefusal(
            kind=RefusalKind.PLAN_UNRESOLVABLE,
            summary="nao tenho esse dado no warehouse",
            detail=outcome.issues[0].message,
            codes=tuple(str(i.code) for i in outcome.issues),
            remedies=tuple(i.remedy for i in outcome.issues if i.remedy),
        )

    # Sobrou o executor: alguma saida do plano nao foi calculada. Nunca zero,
    # nunca parcial - o `MetricUnavailable` da Fase 2 chega a tela sem reescrita,
    # com o motivo, o conceito que faltou e os enderecos tentados.
    falhas = tuple(outcome.execution.failures) if outcome.execution is not None else ()
    if falhas:
        falha = falhas[0]
        tentados = falha.unavailable.tried if falha.unavailable else ()
        detalhe = falha.message
        if tentados:
            # Os enderecos tentados vao no detalhe e NAO em `candidates`:
            # `candidates` e o que a UI transforma em pino do usuario, e um
            # endereco de taxonomia nao e escolha de ninguem.
            detalhe = f"{detalhe} · tentado: {', '.join(tentados)}"
        return TurnRefusal(
            kind=RefusalKind.METRIC_UNAVAILABLE,
            summary="nao consegui calcular",
            detail=detalhe,
            codes=tuple(str(f.reason) for f in falhas),
            remedies=tuple(f.remedy for f in falhas if f.remedy),
        )

    return TurnRefusal(
        kind=RefusalKind.METRIC_UNAVAILABLE,
        summary="nao consegui calcular",
        detail="alguma saida do plano nao foi calculada",
        codes=(str(ViolationCode.PLAN_NOT_EXECUTABLE),),
    )

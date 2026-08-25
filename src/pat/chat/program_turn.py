"""Um turno de conversa pelo caminho da Fase 5.

O que muda em relacao a `turn.py`
----------------------------------
`turn.py` roda `pat plan` + `pat ask --writer`: metricas e as sete derivacoes.
E o caminho da Fase 3, e ele continua valendo - e o que `pat ask --plan-file`
reexecuta sem modelo.

Este roda `pat program` + `pat run-program --writer`:

    estagio 1   pergunta -> plano de calculo + decomposicoes
    [executa DETERMINISTICAMENTE]
    estagio 2   FORMA dos resultados -> buscas no corpus
    [executa DETERMINISTICAMENTE]
    writer      grafo de afirmacoes -> leituras e conclusoes
    critic      mecanico, taxonomia fechada

E a diferenca pratica e a pergunta que o chat passa a conseguir responder:

    "por que a margem caiu?"

No caminho da Fase 3 isso era recusado com `unsupported_question`, e a recusa
estava certa - a gramatica do plano so tem metrica e derivacao, e nao havia
como PEDIR uma decomposicao. Aqui ha.

Quatro chamadas de modelo por turno
-----------------------------------
Estagio 1, estagio 2, escritor e - quando pedido - critic. Cada uma com prompt
proprio, hash proprio e linha propria em `llm_call`. E mais caro que o caminho
antigo de proposito: o que ele compra e a decomposicao entre o numero e a
narrativa, que e o que separa research de consulta.

Sem retentativa em nenhum dos quatro, pela razao de sempre.
"""

from __future__ import annotations

import time

from pat.canonical import sha256_of
from pat.config import Paths
from pat.chat.session import ConversationState, build_context
from pat.contracts.chat import ChatRequest, ChatTurn, ProgramSummary
from pat.contracts.claims import ClaimKind
from pat.research import DEFAULT_SOURCE
from pat.chat.record import record_planner_call
from pat.chat.turn import _from_planner_error, _from_writer_error, build_question
from pat.store.db import connect

__all__ = ["run_program_turn"]


def run_program_turn(
    request: ChatRequest,
    state: ConversationState,
    *,
    paths: Paths,
    llm,
    model: str,
    source: str = DEFAULT_SOURCE,
    git_sha: str | None = None,
    audit: bool = False,
) -> ChatTurn:
    from pat.research.canonical import capability_sha256, program_id as compute_program_id
    from pat.research import execute_program
    from pat.research.capability import build_snapshot
    from pat.research.claims import ground_claims
    from pat.research.critic import review
    from pat.research.planner import PlannerError
    from pat.research.program_planner import plan_compute_stage, plan_evidence_stage
    from pat.research.program_writer import substitute, write_report
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

    conn = connect(paths.warehouse, read_only=True)
    provenances: list = []
    try:
        snapshot = build_snapshot(conn, source=source)
        capability = capability_sha256(snapshot)

        # -- estagio 1: o que medir e decompor ------------------------------
        try:
            program, prov1 = plan_compute_stage(
                question, snapshot, llm=llm, model=model
            )
        except PlannerError as exc:
            return _turn(refusal=_from_planner_error(exc))
        provenances.append(prov1)

        if program.is_refusal:
            return _turn(refusal=_refusal_from_program(program))

        # -- executa, sem persistir, so para extrair a FORMA ----------------
        parcial = execute_program(
            conn,
            program,
            question=question,
            program_id=compute_program_id(program),
            capability_sha256=capability,
            source=source,
            git_sha=git_sha,
        )

        # -- estagio 2: o que procurar no corpus ----------------------------
        try:
            program, prov2 = plan_evidence_stage(
                question, snapshot, program, parcial.shapes, llm=llm, model=model
            )
        except PlannerError as exc:
            return _turn(refusal=_from_planner_error(exc))
        provenances.append(prov2)

        # -- executa de novo, agora com evidencia ---------------------------
        resultado = execute_program(
            conn,
            program,
            question=question,
            program_id=compute_program_id(program),
            capability_sha256=capability,
            source=source,
            git_sha=git_sha,
        )

        if not resultado.has_any_output:
            return _turn(refusal=_refusal_sem_saida(resultado))

        grounded = ground_claims(resultado)

        # -- escritor sobre o grafo -----------------------------------------
        try:
            escrito = write_report(question, grounded, snapshot, llm=llm, model=model)
        except WriterError as exc:
            return _turn(refusal=_from_writer_error(exc))
        provenances.append(escrito.provenance)

        # -- critic mecanico, ANTES da substituicao -------------------------
        #
        # A ordem e a unica em que da para distinguir "o modelo escreveu um
        # numero" de "o sistema substituiu um token".
        unidades = {
            hit.quote.unit_id: hit.quote.text
            for outcome in resultado.evidence
            if outcome.result is not None
            for hit in outcome.result.hits
        }
        parecer = review(
            escrito.graph,
            result=resultado,
            prose_blocks=escrito.blocks,
            values=grounded.values,
            unit_texts=unidades,
            warnings=tuple(grounded.warnings),
        )
        if parecer.blocks:
            return _turn(refusal=_refusal_do_critic(parecer))

        prosa = substitute(escrito.blocks, grounded.values)
    finally:
        conn.close()

    for provenance in provenances:
        record_planner_call(paths, provenance, fingerprint=llm.fingerprint)

    return _turn(
        program_id=compute_program_id(program),
        program_summary=_summary(program, escrito.graph),
        prose=prosa,
        critic_findings=tuple(
            (str(f.severity), str(f.code), f.message) for f in parecer.soft
        ),
        planner_provenance=provenances[0],
        writer_provenance=escrito.provenance,
        answer=None,
        refusal=None,
    )


# ---------------------------------------------------------------------------


def _summary(program, graph) -> ProgramSummary:
    especies = [
        (str(especie), len(graph.of_kind(especie)))
        for especie in ClaimKind
        if graph.of_kind(especie)
    ]
    return ProgramSummary(
        objective=program.objective or "(sem objetivo declarado)",
        questions_to_answer=program.questions_to_answer,
        metric_steps=len(program.compute.steps) if program.compute else 0,
        decompositions=tuple(p.decomposition for p in program.decompositions),
        evidence_queries=tuple(
            (" ".join(p.terms), p.rationale) for p in program.evidence
        ),
        claims=tuple(especies),
    )


def _refusal_from_program(program):
    from pat.contracts.chat import RefusalKind, TurnRefusal

    return TurnRefusal(
        kind=RefusalKind.PLANNER_UNRESOLVED,
        summary="preciso que voce desambigue antes de eu planejar",
        detail="; ".join(item.detail for item in program.unresolved),
        codes=tuple(str(item.kind) for item in program.unresolved),
        candidates=tuple(c for item in program.unresolved for c in item.candidates),
    )


def _refusal_sem_saida(resultado):
    from pat.contracts.chat import RefusalKind, TurnRefusal

    motivos = [
        str(o.unavailable.reason) for o in resultado.decompositions if o.unavailable
    ] + [str(o.unavailable.reason) for o in resultado.evidence if o.unavailable]
    return TurnRefusal(
        kind=RefusalKind.METRIC_UNAVAILABLE,
        summary="nada foi apurado: sem o que citar, um relatorio seria prosa sobre "
        "numeros que nao existem",
        detail="; ".join(
            f.message for f in resultado.computation_failures
        ) or "nenhum passo produziu resultado",
        codes=tuple(dict.fromkeys(motivos)),
    )


def _refusal_do_critic(parecer):
    from pat.contracts.chat import RefusalKind, TurnRefusal

    return TurnRefusal(
        kind=RefusalKind.WRITER_FAILED,
        summary="o relatorio foi bloqueado pelo critic mecanico",
        detail="; ".join(f"[{f.code}] {f.message}" for f in parecer.hard),
        codes=tuple(str(f.code) for f in parecer.hard),
    )

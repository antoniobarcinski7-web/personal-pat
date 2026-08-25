"""Orquestracao de um turno (T-1..T-8 da proposta).

Nenhum teste aqui chama a API: o cliente e o duplo de `conftest.py`. O que se
prova e a fiacao - duas chamadas e nunca uma terceira, a traducao de cada falha
da Fase 3 na recusa nomeada correspondente, e o rastro em `llm_call` e
`research_run` que faz a conversa gravar nas mesmas tabelas que o CLI.
"""

from __future__ import annotations

from datetime import date

from pat.contracts.chat import ChatRequest, RefusalKind
from pat.store.db import connect
from pat.store.llm_calls import count as count_calls
from pat.store.llm_calls import orphan_calls, read_calls
from tests.chat.conftest import AS_OF, PLANO_COM_DUVIDA, PLANO_SEM_DADO, FakeLLMClient

PERGUNTA = "Qual foi a margem EBITDA do GPA em 2024?"


def _send(service, texto=PERGUNTA, **campos):
    state = service.create_session()
    return service.send_message(ChatRequest(session_id=state.session_id, text=texto, **campos))


def _rows(paths):
    conn = connect(paths.warehouse, read_only=True)
    try:
        return conn.execute(
            "SELECT kind, manifest_id FROM llm_call ORDER BY recorded_at, kind"
        ).fetchall()
    finally:
        conn.close()


def test_t1_o_turno_feliz_responde_com_duas_chamadas(service, llm, paths):
    turn = _send(service)

    assert llm.roles == ["planner", "writer"], "duas chamadas, uma por papel - nunca uma terceira"
    assert turn.refusal is None
    assert turn.answer is not None
    assert turn.manifest is not None
    assert turn.context_sha256 is None, "o primeiro turno de uma sessao nao tem contexto"

    conn = connect(paths.warehouse, read_only=True)
    try:
        (gravados,) = conn.execute(
            "SELECT COUNT(*) FROM research_run WHERE manifest_id = ?",
            [turn.manifest.manifest_id],
        ).fetchone()
    finally:
        conn.close()
    assert gravados == 1, "o manifesto do turno tem que estar em research_run"


def test_t1_o_numero_da_resposta_nao_veio_de_nenhum_dos_dois_modelos(service, llm):
    """A invariante central, no turno: o modelo escreveu token, o motor o valor."""
    turn = _send(service)

    (claim,) = [c for c in turn.answer.claims if c.claim_kind == "numeric"]
    assert claim.rendered_value not in llm.plan_text
    assert claim.rendered_value not in llm.prose_text
    assert claim.rendered_value in turn.answer.prose


def test_t2_plano_com_duvida_recusa_sem_executar(paths):
    """T-2: a duvida do planejador e uma recusa PROPRIA, com os candidatos.

    Distinta de plano malformado de proposito: devolver a duvida em vez de
    chutar e a melhor coisa que o planejador pode fazer.
    """
    from pat.chat import ChatService

    llm = FakeLLMClient(plan_text=PLANO_COM_DUVIDA)
    service = ChatService(
        paths=paths, llm=llm, model="fake-model", default_as_of=AS_OF,
        program_path=False,  # estes testes cobrem o caminho da Fase 3
    )
    turn = _send(service)

    assert llm.roles == ["planner"], "plano recusado nao chega ao escritor"
    assert turn.refusal.kind is RefusalKind.PLANNER_UNRESOLVED
    assert turn.refusal.candidates, "os candidatos viram pino do usuario e nao podem sumir"
    assert turn.manifest is None, "nada executou: manifesto nenhum"
    assert [r.kind for r in orphan_calls_of(paths)] == ["planner"]


def orphan_calls_of(paths):
    conn = connect(paths.warehouse, read_only=True)
    try:
        return orphan_calls(conn)
    finally:
        conn.close()


def test_t3_warehouse_que_nao_serve_vira_plan_unresolvable(paths):
    from pat.chat import ChatService

    llm = FakeLLMClient(plan_text=PLANO_SEM_DADO)
    service = ChatService(
        paths=paths, llm=llm, model="fake-model", default_as_of=AS_OF,
        program_path=False,  # estes testes cobrem o caminho da Fase 3
    )
    turn = _send(service)

    assert turn.refusal.kind is RefusalKind.PLAN_UNRESOLVABLE
    assert turn.refusal.codes, "o codigo da pendencia viaja como veio da Fase 3"
    assert turn.refusal.remedies, "a saida (`pat fetch`, `pat build`) chega a tela"


def test_t4_planner_error_vira_recusa_nomeada(paths):
    """T-4: `PlannerError` -> PLANNER_FAILED, com o hash da resposta no detalhe.

    O que este teste NAO afirma, e por que: a chamada que falhou nao e gravada
    em `llm_call`. `PlannerError` carrega `response_sha256`, e nao um
    `PlanProvenance` - nao ha o que gravar sem fabricar procedencia. E o mesmo
    comportamento de `cmd_plan`, que tambem retorna antes de `_record_call`.
    """
    from pat.chat import ChatService

    llm = FakeLLMClient(plan_text="isto nao e um plano")
    service = ChatService(
        paths=paths, llm=llm, model="fake-model", default_as_of=AS_OF,
        program_path=False,  # estes testes cobrem o caminho da Fase 3
    )
    turn = _send(service)

    assert turn.refusal.kind is RefusalKind.PLANNER_FAILED
    assert turn.answer is None
    assert turn.plan is None
    assert turn.refusal.detail and "resposta" in turn.refusal.detail


def test_t5_writer_error_nao_cai_para_prosa_deterministica(paths):
    """T-5: falhar e melhor que apresentar um texto que ninguem pediu.

    O escritor devolve prosa com digito solto - a regra do digito a recusa - e
    o turno tem que sair como recusa, jamais com a prosa deterministica que
    `build_answer` sabe montar sozinha.
    """
    from pat.chat import ChatService

    prosa_com_digito = '{"prose": "A margem EBITDA foi de 3.89% no exercicio.",' \
        ' "interpretations": []}'
    llm = FakeLLMClient(prose_text=prosa_com_digito)
    service = ChatService(
        paths=paths, llm=llm, model="fake-model", default_as_of=AS_OF,
        program_path=False,  # estes testes cobrem o caminho da Fase 3
    )
    turn = _send(service)

    assert turn.refusal.kind is RefusalKind.WRITER_FAILED
    assert turn.answer is None, "sem fallback: recusa nao tem prosa"
    assert turn.plan is not None, "o plano continua visivel - foi o texto que falhou"


def test_t6_traducao_de_falha_de_metrica(service):
    """T-6: `ComputationFailure` -> METRIC_UNAVAILABLE, sem reescrever o motivo."""
    from dataclasses import dataclass

    from pat.chat.turn import _from_outcome
    from pat.contracts.research import ComputationFailure, DerivationFailureReason
    from pat.contracts.semantics import MetricUnavailable, ReportingScope, UnavailableReason

    indisponivel = MetricUnavailable(
        metric="ebitda",
        metric_version="v1",
        reason=UnavailableReason.MISSING_CONCEPT,
        message="d_and_a_pnl nao tem binding para esta companhia",
        entity_id="br:cnpj:47508411000156",
        period_end=date(2024, 12, 31),
        as_of=date(2025, 6, 30),
        scope=ReportingScope.CONSOLIDATED,
        concept_id="d_and_a_pnl",
        tried=("DFC_MI 6.01.01.02",),
        remedy="escrever um binding proprio para a companhia",
    )
    falha = ComputationFailure(
        step_id="ebitda_fy2024",
        reason=DerivationFailureReason.INPUT_FAILED,
        message=indisponivel.message,
        remedy=indisponivel.remedy,
        unavailable=indisponivel,
    )

    @dataclass
    class _Execucao:
        failures: tuple
        outputs_available: bool = False

    @dataclass
    class _Outcome:
        plan: object
        violations: tuple
        issues: tuple
        execution: object

    class _Plano:
        unresolved = ()

    recusa = _from_outcome(
        _Outcome(plan=_Plano(), violations=(), issues=(), execution=_Execucao(failures=(falha,)))
    )

    assert recusa.kind is RefusalKind.METRIC_UNAVAILABLE
    assert "d_and_a_pnl" in recusa.detail
    assert "DFC_MI 6.01.01.02" in recusa.detail, "os enderecos tentados chegam a tela"
    assert recusa.remedies == (indisponivel.remedy,)
    assert recusa.candidates == (), "endereco de taxonomia nao e escolha do usuario"


def test_t7_o_turno_feliz_grava_duas_chamadas_com_papel_distinto(service, paths):
    """T-7: uma linha por chamada, e so a do escritor ligada ao manifesto."""
    turn = _send(service)

    linhas = _rows(paths)
    assert [kind for kind, _ in linhas] == ["planner", "writer"]
    por_papel = dict(linhas)
    assert por_papel["planner"] is None, "planejar nao produz manifesto"
    assert por_papel["writer"] == turn.manifest.manifest_id

    conn = connect(paths.warehouse, read_only=True)
    try:
        assert count_calls(conn) == 2
        (do_escritor,) = read_calls(conn, turn.manifest.manifest_id)
    finally:
        conn.close()
    assert do_escritor.kind == "writer"


def test_t8_os_pinos_chegam_ordenados_e_sem_repeticao(service):
    """T-8: a ordem em que alguem clicou nos chips nao vira pergunta diferente."""
    from pat.chat.turn import build_question

    state = service.create_session()
    pedido = ChatRequest(
        session_id=state.session_id,
        text=PERGUNTA,
        pinned_entities=("br:cnpj:2", "br:cnpj:1", "br:cnpj:2"),
        pinned_periods=(date(2024, 12, 31), date(2023, 12, 31), date(2024, 12, 31)),
    )

    question = build_question(pedido, state)

    assert question.pinned_entities == ("br:cnpj:1", "br:cnpj:2")
    assert question.pinned_periods == (date(2023, 12, 31), date(2024, 12, 31))


def test_o_segundo_turno_carrega_contexto(service, llm):
    """O contexto entra no prompt do planejador - e so no dele."""
    state = service.create_session()
    service.send_message(ChatRequest(session_id=state.session_id, text=PERGUNTA))
    segundo = service.send_message(ChatRequest(session_id=state.session_id, text="E a margem?"))

    assert segundo.context_sha256 is not None
    prompts = [r.user for r in llm.requests if "redator" not in r.system]
    assert "CONVERSA" in prompts[-1], "o segundo plano tem que ver a conversa ate aqui"


# -- a recusa pura, que a gramatica passou a saber expressar -----------------


PLANO_RECUSA_PURA = __import__("json").dumps(
    {
        "objective": "Esclarecer a que 'demais series' a pergunta se refere",
        "as_of": AS_OF.isoformat(),
        "scope": "consolidated",
        "steps": [],
        "outputs": [],
        "assumptions": [],
        "unresolved": [
            {
                "kind": "unsupported_question",
                "detail": "'demais series' nao diz quais: outras metricas, outras empresas ou outros anos",
                "candidates": [
                    "Outras metricas da mesma empresa",
                    "A mesma metrica para as demais empresas",
                ],
            }
        ],
    }
)


def test_pergunta_ambigua_vira_recusa_nomeada_e_nao_falha_de_modelo(paths):
    """O caso real que expos o buraco da gramatica.

    Numa conversa, "teria como voce buscar as demais series?" fez o planejador
    devolver `unresolved` com zero passos - a coisa certa. O contrato exigia
    pelo menos um passo, a recusa era rejeitada como `contract_violation`, e o
    usuario via "o modelo nao produziu uma saida valida": a culpa no modelo
    exatamente quando ele acertou, e os candidatos jogados fora.

    Agora o caminho e o projetado: PLANNER_UNRESOLVED, com os candidatos
    preservados para a UI transformar em pino do usuario.
    """
    from pat.chat import ChatService

    llm = FakeLLMClient(plan_text=PLANO_RECUSA_PURA)
    service = ChatService(
        paths=paths, llm=llm, model="fake-model", default_as_of=AS_OF,
        program_path=False,  # estes testes cobrem o caminho da Fase 3
    )
    state = service.create_session()
    turn = service.send_message(
        ChatRequest(session_id=state.session_id, text="Teria como buscar as demais series?")
    )

    assert turn.answer is None
    assert turn.refusal is not None
    assert turn.refusal.kind is RefusalKind.PLANNER_UNRESOLVED, (
        "recusa honesta do planejador nao pode sair como falha do modelo"
    )
    assert "unsupported_question" in turn.refusal.codes
    assert turn.refusal.candidates, "os candidatos sao o que permite desambiguar"
    assert turn.plan is not None and turn.plan.is_refusal

    # Uma chamada, nao duas: sem numero para citar, o escritor nao e chamado.
    assert llm.roles == ["planner"]

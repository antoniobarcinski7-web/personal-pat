"""`ChatTurn` -> JSON da UI (V-1..V-4 da proposta).

O contrato desta camada e com o frontend, e a coisa que ele nao pode fazer e
formatar numero: os valores saem como `rendered_value`, string produzida pelo
unico formatador do sistema.
"""

from __future__ import annotations

import json

from pat.chat.view import turn_view
from pat.contracts.chat import ChatRequest
from tests.chat.conftest import PLANO_COM_DUVIDA, FakeLLMClient

PERGUNTA = "Qual foi a margem EBITDA do GPA em 2024?"


def _turn(service, texto=PERGUNTA):
    state = service.create_session()
    return service.send_message(ChatRequest(session_id=state.session_id, text=texto))


def test_v1_o_turno_feliz_traz_os_campos_da_especificacao(service):
    view = service.view(_turn(service))

    for campo in (
        "turn_index",
        "session_id",
        "as_of",
        "asked_at",
        "question",
        "status",
        "answer",
        "refusal",
        "plan",
        "manifest",
        "provenance",
        "context_sha256",
        "elapsed_ms",
    ):
        assert campo in view, f"o frontend espera {campo}"

    assert view["status"] == "answered"
    assert view["refusal"] is None
    assert view["provenance"]["planner"]["model_id"] == "fake-model-20260101"
    assert view["provenance"]["writer"] is not None
    assert view["manifest"]["manifest_id"]
    assert set(view["answer"]) >= {"prose", "numeric_claims", "interpretive_claims", "warnings"}


def test_v2_nada_de_decimal_ou_data_crua_sobrevive(service):
    """V-2: `json.dumps` sem `default=`.

    Se um `Decimal` ou um `date` escapasse, ele so apareceria na fronteira HTTP
    - longe daqui, e como 500 sem explicacao.
    """
    view = service.view(_turn(service))

    payload = json.dumps(view)  # sem default: qualquer tipo cru levanta aqui
    assert "Decimal" not in payload


def test_v2_o_valor_e_string_pronta_e_ninguem_o_reformata(service):
    turn = _turn(service)
    view = service.view(turn)

    (claim,) = view["answer"]["numeric_claims"]
    (original,) = [c for c in turn.answer.claims if c.claim_kind == "numeric"]
    assert claim["rendered_value"] == original.rendered_value
    assert isinstance(claim["rendered_value"], str)


def test_v3_entity_name_vem_do_snapshot(service):
    """V-3: join por `entity_id`, para que a UI nao dependa de `step_id` legivel."""
    view = service.view(_turn(service))

    passos = [s for s in view["plan"]["steps"] if s["step_kind"] == "metric"]
    assert passos, "o plano do fixture tem um passo de metrica"
    for passo in passos:
        assert passo["entity_name"], f"{passo['entity_id']} sem nome"
        assert passo["entity_name"] == service.entity_names()[passo["entity_id"]]


def test_v4_leitura_do_modelo_sai_em_chave_separada_do_numero(service):
    """V-4: juntar os dois blocos mataria a distincao na ultima perna."""
    view = service.view(_turn(service))

    numericos = view["answer"]["numeric_claims"]
    interpretativos = view["answer"]["interpretive_claims"]

    assert numericos and interpretativos
    assert all("rendered_value" in c for c in numericos)
    assert all("rendered_value" not in c for c in interpretativos), (
        "leitura do modelo nao tem valor - estruturalmente"
    )


def test_o_turno_recusado_sai_com_refusal_e_sem_answer(paths):
    from pat.chat import ChatService
    from tests.chat.conftest import AS_OF

    service = ChatService(
        paths=paths,
        llm=FakeLLMClient(plan_text=PLANO_COM_DUVIDA),
        model="fake-model",
        default_as_of=AS_OF,
    )
    view = service.view(_turn(service))

    assert view["status"] == "refused"
    assert view["answer"] is None
    assert view["refusal"]["kind"] == "planner_unresolved"
    assert view["refusal"]["candidates"]
    json.dumps(view)


def test_turn_view_nao_precisa_do_servico(service):
    """A funcao e pura: `ChatTurn` + nomes -> dict. Sem banco, sem rede."""
    turn = _turn(service)

    assert turn_view(turn, entity_names={})["plan"]["steps"][0]["entity_name"] is None

"""Estado de conversa: janela, contexto e log (S-1..S-7 da proposta).

O teste que vale mais que os outros deste arquivo e o S-4: ele varre os bytes
canonicos do contexto atras de qualquer numero de turno anterior. E a unica
afirmacao aqui que nao da para fazer olhando o tipo - `TurnPlanSummary` nao tem
onde por um `Decimal`, mas quem monta poderia ter enfiado um valor num campo de
texto.
"""

from __future__ import annotations

from datetime import date

import pytest

from pat.chat.session import (
    CONTEXT_WINDOW,
    InvalidSessionId,
    SessionStore,
    build_context,
    new_session_id,
)
from pat.research.canonical import canonical_bytes
from tests.chat.conftest import AS_OF, make_turn


def test_s1_sessao_nova_nao_tem_contexto(store):
    """S-1: `None`, e nao contexto vazio.

    Contexto vazio mudaria o prompt do primeiro turno em relacao ao de
    `pat plan` - e as duas coisas sao a mesma pergunta.
    """
    state = store.create(as_of=AS_OF)

    assert build_context(state) is None


def test_s2_a_janela_pega_os_mais_recentes_em_ordem(store):
    state = store.create(as_of=AS_OF)
    for i in range(CONTEXT_WINDOW + 3):
        store.append(state, make_turn(state.session_id, turn_index=i, text=f"pergunta {i}"))

    context = build_context(state)

    assert len(context.turns) == CONTEXT_WINDOW
    assert [t.question_text for t in context.turns] == [
        f"pergunta {i}" for i in range(3, CONTEXT_WINDOW + 3)
    ]
    assert context.as_of == AS_OF


def test_s3_turno_recusado_entra_no_contexto_com_o_codigo(store):
    """S-3: "isso ja foi recusado por X" e a correcao de curso mais barata."""
    state = store.create(as_of=AS_OF)
    store.append(state, make_turn(state.session_id))

    (resumo,) = build_context(state).turns

    assert resumo.outcome == "refused"
    assert resumo.refusal_codes == ("unknown_entity",)


def test_s4_nenhum_valor_do_turno_anterior_sobrevive_no_contexto(service, paths):
    """S-4: varredura textual sobre os bytes que vao para dentro do prompt.

    Roda um turno de verdade - com numeros de verdade, vindos do motor - e
    procura cada `rendered_value`, `result_id` e `manifest_id` produzidos nele
    dentro do contexto do turno seguinte. Nenhum pode aparecer: os numeros de
    todo turno sao recalculados do zero, e o modelo nao pode ve-los.
    """
    from pat.contracts.chat import ChatRequest

    state = service.create_session()
    turn = service.send_message(
        ChatRequest(session_id=state.session_id, text="Qual foi a margem EBITDA do GPA em 2024?")
    )
    assert turn.answer is not None, "o teste precisa de um turno respondido para ter numeros"

    bytes_do_contexto = canonical_bytes(build_context(state)).decode("utf-8")

    proibidos = [turn.manifest.manifest_id, *turn.manifest.result_ids]
    proibidos += [c.rendered_value for c in turn.answer.claims if c.claim_kind == "numeric"]
    proibidos += [c.result_id for c in turn.answer.claims if c.claim_kind == "numeric"]
    for valor in proibidos:
        assert valor not in bytes_do_contexto, f"{valor!r} vazou para o contexto"

    assert turn.answer.prose not in bytes_do_contexto, "a prosa do turno anterior nao e contexto"


def test_s5_o_jsonl_preserva_ordem_e_conteudo(tmp_path):
    """S-5: gravar N turnos e reler de um `SessionStore` novo."""
    store = SessionStore(tmp_path / "chat")
    state = store.create(as_of=AS_OF)
    for i in range(3):
        store.append(state, make_turn(state.session_id, turn_index=i, text=f"pergunta {i}"))

    relido = SessionStore(tmp_path / "chat").get(state.session_id)

    assert [t.turn_index for t in relido.turns] == [0, 1, 2]
    assert [t.question.text for t in relido.turns] == [f"pergunta {i}" for i in range(3)]
    assert relido.as_of == AS_OF


@pytest.mark.parametrize(
    "ruim", ["../../etc/passwd", "ABCDEF0123456789", "0123", "0123456789abcdef0", ""]
)
def test_s6_session_id_fora_do_padrao_nao_vira_caminho(store, ruim):
    with pytest.raises(InvalidSessionId):
        store.get(ruim)


def test_s6_o_id_e_emitido_pelo_servidor(store):
    state = store.create(as_of=AS_OF)

    assert len(state.session_id) == 16
    assert new_session_id() != new_session_id(), "o nonce tem que variar"


def test_s7_as_of_da_sessao_atravessa_os_turnos(service):
    """S-7: nao existe API que troque o `as_of` no meio da conversa.

    A pergunta de todo turno recebe o `as_of` da sessao, e o `ChatRequest` nao
    tem campo para outro - trocar abre sessao nova.
    """
    from pat.chat.turn import build_question
    from pat.contracts.chat import ChatRequest

    state = service.create_session(as_of=date(2025, 6, 30))
    pergunta = build_question(ChatRequest(session_id=state.session_id, text="e a margem?"), state)

    assert pergunta.as_of == date(2025, 6, 30)
    assert "as_of" not in ChatRequest.model_fields

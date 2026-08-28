"""O5 - a conversa, sobre um workspace de verdade e sem modelo nenhum.

As falas testadas aqui sao as do plano, literalmente:

    "Por que a receita caiu?"           -> pergunta, responde com o motor
    "Acho que o moat e escala."         -> afirmacao, vira HIPOTESE
    "Investiga isso."                   -> agenda decomposta e executada
    "Mas e a concorrencia?"             -> critica, com agenda de falsificacao
    "Volta naquela hipotese de margem." -> retoma o fio

O teste que define o milestone e `test_afirmacao_do_usuario_nunca_vira_claim`:
e o ponto onde a conviccao de quem digita entraria no sistema com a mesma
forca de um numero calculado, se ninguem impedisse.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pat.contracts.opportunity import (
    Actor,
    ChatTurnRecorded,
    HypothesisStatus,
    Intent,
    TurnAction,
    TurnRequest,
)
from pat.opportunity import create_workspace, open_workspace, profile_for
from pat.opportunity.chat import ChatAgent, classify, respond
from pat.opportunity.reason import ShapeReasoner
from pat.opportunity.tools import PatTools
from tests.opportunity.conftest import CREATED_AT
from tests.semantics import golden_gpa as gpa

AS_OF = date(2026, 6, 30)


@pytest.fixture
def tools(br_warehouse):
    return PatTools(
        br_warehouse, company=profile_for(br_warehouse, gpa.ENTITY_ID), as_of=AS_OF
    )


@pytest.fixture
def ws(root, br_warehouse):
    return create_workspace(
        root,
        company=profile_for(br_warehouse, gpa.ENTITY_ID),
        as_of=AS_OF,
        created_at=CREATED_AT,
        mandate="entender a qualidade do resultado operacional",
    )


@pytest.fixture
def agent(ws, tools):
    return ChatAgent(ws, tools, ShapeReasoner())


def fala(agent, texto, **kw):
    return agent.respond(
        TurnRequest(text=texto, workspace_id=agent.workspace.workspace_id, **kw)
    )


# -- classificacao ----------------------------------------------------------


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Por que a receita caiu?", Intent.ASK),
        ("Quanto foi o EBITDA em 2023?", Intent.ASK),
        ("Acho que o moat e escala.", Intent.ASSERT),
        ("Investiga isso.", Intent.INVESTIGATE),
        ("Mas e a concorrencia?", Intent.CHALLENGE),
        ("Volta naquela hipotese de margem.", Intent.RESUME),
        ("Onde estamos?", Intent.STATUS),
        ("Roda o advogado do diabo", Intent.CRITIQUE),
    ],
)
def test_falas_do_plano_classificam(texto, esperado):
    assert classify(texto) is esperado


def test_frase_que_nao_casa_vira_unclear_e_nao_um_chute():
    """O conjunto e fechado, e o que sobra pergunta de volta.

    Um classificador que sempre escolhe a intencao mais provavel transforma
    uma frase truncada numa corrida de pesquisa que ninguem pediu - e o diario
    e append-only.
    """
    assert classify("banana") is Intent.UNCLEAR
    assert classify("...") is Intent.UNCLEAR


def test_hint_do_usuario_vence_a_classificacao(agent):
    resposta = fala(agent, "banana", intent_hint=Intent.STATUS)
    assert resposta.intent is Intent.STATUS
    assert TurnAction.ANSWERED_FROM_STATE in resposta.actions


def test_turno_unclear_tem_que_perguntar_de_volta(agent):
    resposta = fala(agent, "banana")
    assert resposta.intent is Intent.UNCLEAR
    assert resposta.actions == (TurnAction.ASKED_BACK,)
    assert not resposta.changed_workspace


# -- ASK --------------------------------------------------------------------


def test_pergunta_sobre_numero_e_respondida_pelo_motor_com_ancora(agent):
    """"Por que a receita caiu?" -> serie do motor, com os enderecos.

    O texto da resposta nao explica a causa - o motor nao sabe causa. Ele
    devolve a forma, e o que sustenta a forma vem em `grounded_in`.
    """
    resposta = fala(agent, "Por que a receita caiu?")

    assert resposta.intent is Intent.ASK
    assert TurnAction.QUERIED_ENGINE in resposta.actions
    assert resposta.grounded_in, "resposta com numero tem que trazer o endereco dele"
    # O identificador de um resultado de metrica e `ref|entidade|periodo|escopo|as_of`.
    assert all("|" in ident for ident in resposta.grounded_in)
    assert "receita_liquida" in resposta.text


def test_pergunta_sem_cobertura_e_recusa_nomeada_e_nao_prosa(agent):
    """Nao ha metrica registrada para isso, e nenhuma frase preenche a lacuna.

    Este e o modo de falha central de um agente conversacional sobre dados:
    responder bem uma pergunta que ele nao tem como responder.
    """
    resposta = fala(agent, "Qual e o churn de assinantes?")

    assert TurnAction.REFUSED in resposta.actions
    assert not resposta.grounded_in
    assert "nao tenho como responder" in resposta.text.lower()


def test_resposta_do_motor_nao_e_produzida_pelo_agente(agent, tools):
    """O numero que aparece na conversa e o numero que o motor calculou.

    Confere contra o motor diretamente, sem passar pelo chat: se o agente
    reescrevesse, reescalasse ou arredondasse, os dois divergiriam.
    """
    resposta = fala(agent, "Quanto foi a receita?")
    periodos = tools.coverage().period_ends[-1:]
    do_motor = tools.metric("receita_liquida@v1", period_end=periodos[0])

    assert str(do_motor.value) in resposta.text


# -- ASSERT -----------------------------------------------------------------


def test_afirmacao_do_usuario_nunca_vira_claim(agent):
    """"Acho que o moat e escala." -> hipotese ABERTA, atribuida ao usuario.

    Claim exige evidencia. Uma afirmacao do analista e o comeco de uma
    investigacao, e transforma-la em claim faria a conviccao dele entrar no
    sistema com a mesma forca de um numero do motor.
    """
    resposta = fala(agent, "Acho que o moat e escala.")

    assert TurnAction.OPENED_HYPOTHESIS in resposta.actions
    assert len(resposta.hypotheses_touched) == 1
    slug = resposta.hypotheses_touched[0]

    estado = agent.workspace.state
    hipotese = estado.hypothesis(slug)
    assert hipotese is not None
    assert hipotese.status is HypothesisStatus.OPEN
    assert hipotese.opened_by is Actor.USER
    assert estado.claims == (), "afirmacao do usuario nao pode virar claim"
    assert estado.conclusions == ()


def test_agente_cobra_o_falsificador_na_hora(agent):
    resposta = fala(agent, "Acho que o moat e escala.")
    assert "falsificador" in resposta.text.lower()
    hipotese = agent.workspace.state.hypothesis(resposta.hypotheses_touched[0])
    # O placeholder e honesto: diz que o analista ainda nao declarou.
    assert hipotese.untested_falsifiers


def test_afirmar_duas_vezes_nao_apaga_a_primeira(agent):
    primeira = fala(agent, "Acho que o moat e escala.")
    segunda = fala(agent, "Acho que o moat e escala.")

    assert TurnAction.OPENED_HYPOTHESIS not in segunda.actions
    assert len(agent.workspace.state.hypotheses) == 1
    assert segunda.hypotheses_touched == primeira.hypotheses_touched


# -- INVESTIGATE ------------------------------------------------------------


def test_investiga_isso_planeja_e_executa_sem_confirmacao(agent):
    resposta = fala(agent, "Investiga a margem e a receita.")

    assert TurnAction.PLANNED_AGENDA in resposta.actions
    assert TurnAction.RAN_TASKS in resposta.actions
    assert resposta.tasks_touched
    assert resposta.changed_workspace

    agenda = agent.workspace.state.agenda
    assert agenda.objective == "Investiga a margem e a receita."
    assert agenda.tasks


def test_o_turno_diz_o_que_ficou_travado(agent):
    """Investigar o que o motor nao cobre nao vira silencio.

    Uma tarefa que trava e informacao; some-la faria a conversa parecer ter
    respondido tudo.
    """
    resposta = fala(agent, "Investiga o churn de assinantes.")
    travadas = [
        t.slug
        for t in agent.workspace.state.agenda.tasks
        if t.blockers
    ]
    assert travadas
    assert any(slug in resposta.text for slug in travadas)


# -- CHALLENGE / CRITIQUE ---------------------------------------------------


def test_desafio_roda_o_critico_e_agenda_o_conserto(agent):
    """"Mas e a concorrencia?" -> critica E tarefas de falsificacao.

    A diferenca entre CHALLENGE e CRITIQUE e essa: um critico que so aponta
    produz um relatorio que alguem le, concorda e arquiva.
    """
    fala(agent, "Acho que o moat e escala.")
    antes = len(agent.workspace.state.agenda.tasks)

    resposta = fala(agent, "Mas e a concorrencia?")

    assert TurnAction.RAN_CRITIQUE in resposta.actions
    assert TurnAction.PLANNED_AGENDA in resposta.actions
    assert len(agent.workspace.state.agenda.tasks) > antes
    assert resposta.tasks_touched


def test_critica_pura_nao_mexe_na_agenda(agent):
    fala(agent, "Acho que o moat e escala.")
    antes = len(agent.workspace.state.agenda.tasks)

    resposta = fala(agent, "Roda o advogado do diabo")

    assert resposta.intent is Intent.CRITIQUE
    assert resposta.actions == (TurnAction.RAN_CRITIQUE,)
    assert len(agent.workspace.state.agenda.tasks) == antes


def test_agente_so_discorda_com_contra_evidencia(agent):
    """Discordar sem evidencia nao e ceticismo - e outra opiniao."""
    fala(agent, "Acho que o moat e escala.")
    resposta = fala(agent, "Roda o advogado do diabo")
    assert resposta.disagreement is None


def test_agente_discorda_do_usuario_quando_ha_contra_evidencia(agent):
    """O agente esta autorizado a dizer "eu nao seguiria com isso".

    A autorizacao vem da contra-evidencia registrada, e o texto da
    discordancia devolve a decisao ao analista - registrada como dele.
    """
    from pat.contracts.opportunity import (
        CounterEvidence,
        CounterEvidenceAdded,
        EvidenceKind,
        EvidenceLink,
    )

    resposta = fala(agent, "Acho que o moat e escala.")
    slug = resposta.hypotheses_touched[0]
    agent.workspace.apply(
        CounterEvidenceAdded(
            hypothesis=slug,
            counter=CounterEvidence(
                link=EvidenceLink(
                    kind=EvidenceKind.METRIC,
                    ref="margem_ebitda@v1|br:cnpj:47508411000156|2023-12-31|consolidated|2026-06-30",
                    note="margem cai enquanto a escala cresce",
                    linked_by=Actor.AGENT,
                    at=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
                ),
                undermines="se escala fosse moat, a margem subiria com o volume",
                decisive=True,
            ),
        ),
        actor=Actor.AGENT,
    )

    veredito = fala(agent, "Roda o advogado do diabo")

    assert veredito.disagreement is not None
    assert "decisao" in veredito.disagreement.lower()


# -- RESUME e STATUS --------------------------------------------------------


def test_volta_naquela_hipotese_retoma_o_fio(agent):
    fala(agent, "Acho que a margem operacional e estruturalmente baixa.")

    resposta = fala(agent, "Volta naquela hipotese de margem.")

    assert resposta.intent is Intent.RESUME
    assert TurnAction.ANSWERED_FROM_STATE in resposta.actions
    assert resposta.hypotheses_touched
    assert "falsificadores nao testados" in resposta.text


def test_retomar_com_ambiguidade_pergunta_em_vez_de_escolher(agent):
    """Retomar a hipotese errada faz o analista raciocinar sobre outra coisa
    sem perceber. Empate pergunta."""
    fala(agent, "Acho que a margem operacional e baixa.")
    fala(agent, "Acho que a margem bruta e alta.")

    resposta = fala(agent, "Volta naquela hipotese de margem.")

    assert resposta.actions == (TurnAction.ASKED_BACK,)
    assert len(resposta.hypotheses_touched) == 2


def test_status_le_o_estado_e_nao_muda_nada(agent):
    fala(agent, "Investiga a receita.")
    seq_antes = agent.workspace.state.workspace.seq

    resposta = fala(agent, "Onde estamos?")

    assert not resposta.changed_workspace
    assert "as_of" in resposta.text
    # O turno em si e um evento; o que ele nao pode e mudar a investigacao.
    assert agent.workspace.state.workspace.seq == seq_antes + 1


# -- o fio da conversa e o diario -------------------------------------------


def test_conversa_sobrevive_ao_processo(ws, tools, root):
    """Nao ha objeto de sessao: reabrir o workspace e continuar a conversa.

    E o mesmo invariante do resto da camada - o estado corrente e sempre a
    dobra -, aplicado a conversa. Um cache de sessao ao lado seria uma segunda
    fonte de verdade sobre o que foi dito.
    """
    primeiro = ChatAgent(ws, tools, ShapeReasoner())
    fala(primeiro, "Acho que o moat e escala.")
    fala(primeiro, "Onde estamos?")
    wid = ws.workspace_id
    del primeiro, ws

    reaberto = open_workspace(root, wid)
    segundo = ChatAgent(reaberto, tools, ShapeReasoner())
    resposta = fala(segundo, "Volta naquela hipotese de moat.")

    assert resposta.turn_index == 2, "o indice do turno vem da dobra, nao da memoria"
    assert resposta.hypotheses_touched


def test_turno_grava_o_que_disse_e_o_que_fez(agent):
    fala(agent, "Investiga a receita.")

    turnos = agent.workspace.state.turns
    assert len(turnos) == 1
    turno = turnos[0]
    assert isinstance(turno, ChatTurnRecorded)
    assert turno.user_text == "Investiga a receita."
    # O que faz do registro uma auditoria, e nao uma transcricao.
    assert TurnAction.PLANNED_AGENDA in turno.actions


def test_indice_de_turno_e_posicao_na_conversa(agent):
    """Quem grava nao escolhe o indice: dois processos falando com o mesmo
    workspace produziriam dois turnos 3."""
    from pat.opportunity.state import FoldError

    with pytest.raises(FoldError, match="posicao na conversa"):
        agent.workspace.apply(
            ChatTurnRecorded(
                turn_index=7,
                user_text="oi",
                intent=Intent.STATUS,
                response_text="oi",
            ),
            actor=Actor.AGENT,
        )


def test_agente_recusa_turno_de_outro_workspace(agent):
    with pytest.raises(ValueError, match="workspace so"):
        agent.respond(TurnRequest(text="Onde estamos?", workspace_id="0" * 16))


def test_respond_sem_agente_faz_o_mesmo(ws, tools):
    resposta = respond(
        ws,
        tools,
        TurnRequest(text="Onde estamos?", workspace_id=ws.workspace_id),
        reasoner=ShapeReasoner(),
    )
    assert resposta.intent is Intent.STATUS
    assert resposta.turn_index == 0

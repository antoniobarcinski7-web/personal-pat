"""O1 - a agenda: tarefas, dependencias, bloqueios, perguntas em aberto.

O criterio de aceitacao e `test_criar_executar_persistir_reabrir_continuar`.
Os demais testes existem para as regras que a agenda impede, e cada um nomeia
o modo de falha que ele fecha - regra sem teste vira comentario, e comentario
some.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pat.contracts.opportunity import (
    Actor,
    AgendaObjectiveSet,
    Blocker,
    BlockerCleared,
    BlockerRecorded,
    FindingRecorded,
    QuestionAnswered,
    QuestionOpened,
    ResearchAgenda,
    ResearchTask,
    TaskCreated,
    TaskPriority,
    TaskPriorityChanged,
    TaskStatus,
    TaskStatusChanged,
)
from pat.opportunity import FoldError, create_workspace, open_workspace
from tests.opportunity.conftest import AS_OF, CREATED_AT


@pytest.fixture
def ws(root, gpa_profile):
    return create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)


def _tarefa(slug: str, **kw) -> TaskCreated:
    defaults = {
        "objective": f"investigar {slug}",
        "completion_criteria": f"{slug} respondido com numero do motor ou citacao",
    }
    return TaskCreated(slug=slug, **(defaults | kw))


# -- criterio de aceitacao --------------------------------------------------


def test_criar_executar_persistir_reabrir_continuar(ws, root):
    """O ciclo inteiro da O1, com o processo no meio.

    Cria programa, cria tarefas, executa uma, persiste, reabre, continua de
    onde parou.
    """
    ws.apply(AgendaObjectiveSet(objective="entender o poder de precificacao"), actor=Actor.USER)
    ws.apply(_tarefa("receita", priority=TaskPriority.HIGH), actor=Actor.AGENT)
    ws.apply(_tarefa("margem", depends_on=("receita",)), actor=Actor.AGENT)
    ws.apply(_tarefa("concorrencia", priority=TaskPriority.LOW), actor=Actor.AGENT)

    # So `receita` e `concorrencia` estao prontas; `margem` espera `receita`.
    assert [t.slug for t in ws.state.agenda.ready()] == ["receita", "concorrencia"]
    assert [t.slug for t in ws.state.agenda.waiting_on_dependency()] == ["margem"]

    ws.apply(TaskStatusChanged(slug="receita", status=TaskStatus.RUNNING), actor=Actor.AGENT)
    ws.apply(
        FindingRecorded(
            slug="receita",
            text="a receita cresceu no periodo",
            result_ids=("a" * 64,),
        ),
        actor=Actor.ENGINE,
    )
    ws.apply(TaskStatusChanged(slug="receita", status=TaskStatus.COMPLETE), actor=Actor.AGENT)

    # Concluir `receita` destrava `margem`.
    assert [t.slug for t in ws.state.agenda.ready()] == ["margem", "concorrencia"]

    reaberto = open_workspace(root, ws.workspace_id)

    assert reaberto.state == ws.state
    assert reaberto.state.agenda.objective == "entender o poder de precificacao"
    receita = reaberto.state.agenda.task("receita")
    assert receita.status is TaskStatus.COMPLETE
    assert receita.findings[0].actor is Actor.ENGINE
    assert receita.findings[0].is_grounded
    # E continua de onde parou.
    reaberto.apply(TaskStatusChanged(slug="margem", status=TaskStatus.RUNNING), actor=Actor.AGENT)
    assert reaberto.state.agenda.task("margem").status is TaskStatus.RUNNING


# -- ordem e prontidao ------------------------------------------------------


def test_prioridade_ordena_e_a_criacao_desempata(ws):
    ws.apply(_tarefa("a"), actor=Actor.AGENT, at=CREATED_AT)
    ws.apply(_tarefa("b", priority=TaskPriority.HIGH), actor=Actor.AGENT, at=CREATED_AT)
    ws.apply(_tarefa("c"), actor=Actor.AGENT, at=CREATED_AT + timedelta(minutes=1))
    ws.apply(_tarefa("d", priority=TaskPriority.LOW), actor=Actor.AGENT, at=CREATED_AT)

    assert [t.slug for t in ws.state.agenda.ready()] == ["b", "a", "c", "d"]


def test_repriorizar_muda_a_ordem(ws):
    ws.apply(_tarefa("a"), actor=Actor.AGENT)
    ws.apply(_tarefa("b"), actor=Actor.AGENT)
    ws.apply(
        TaskPriorityChanged(slug="b", priority=TaskPriority.HIGH), actor=Actor.USER
    )
    assert [t.slug for t in ws.state.agenda.ready()] == ["b", "a"]


def test_so_complete_encerra(ws):
    """BLOCKED e NEEDS_HUMAN sao paradas, nao fins.

    Uma agenda que tratasse parada como conclusao destravaria as dependentes,
    e a tese se declararia pronta tendo pulado o que travou.
    """
    ws.apply(_tarefa("base"), actor=Actor.AGENT)
    ws.apply(_tarefa("depende", depends_on=("base",)), actor=Actor.AGENT)
    ws.apply(
        BlockerRecorded(
            slug="base",
            blocker=Blocker(reason="documento nao sincronizado", remedy="rode `pat docs sync`"),
        ),
        actor=Actor.AGENT,
    )

    assert ws.state.agenda.task("base").status is TaskStatus.BLOCKED
    assert not ws.state.agenda.task("base").is_done
    assert [t.slug for t in ws.state.agenda.ready()] == []
    assert [t.slug for t in ws.state.agenda.waiting_on_dependency()] == ["depende"]
    assert ws.state.agenda.is_exhausted


def test_exaurida_nao_e_o_mesmo_que_concluida(ws):
    ws.apply(_tarefa("a"), actor=Actor.AGENT)
    ws.apply(
        BlockerRecorded(
            slug="a",
            blocker=Blocker(reason="falta escolher a linha", remedy="rode `pat accounts`", needs_human=True),
        ),
        actor=Actor.AGENT,
    )
    agenda = ws.state.agenda
    assert agenda.is_exhausted
    assert agenda.of_status(TaskStatus.NEEDS_HUMAN)[0].slug == "a"
    assert agenda.of_status(TaskStatus.COMPLETE) == ()


def test_bloqueio_humano_e_fila_separada(ws):
    """O que o agente destrava sozinho e o que espera uma pessoa nao sao a
    mesma fila."""
    ws.apply(_tarefa("sistema"), actor=Actor.AGENT)
    ws.apply(_tarefa("humano"), actor=Actor.AGENT)
    ws.apply(
        BlockerRecorded(
            slug="sistema",
            blocker=Blocker(reason="documento faltando", remedy="sincronize os documentos"),
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        BlockerRecorded(
            slug="humano",
            blocker=Blocker(
                reason="premissa de crescimento nao e do agente",
                remedy="o analista escolhe a taxa",
                needs_human=True,
            ),
        ),
        actor=Actor.AGENT,
    )

    assert ws.state.agenda.task("sistema").status is TaskStatus.BLOCKED
    assert ws.state.agenda.task("humano").status is TaskStatus.NEEDS_HUMAN


def test_destravar_devolve_para_pending_sem_apagar_a_historia(ws):
    ws.apply(_tarefa("a"), actor=Actor.AGENT)
    ws.apply(
        BlockerRecorded(
            slug="a", blocker=Blocker(reason="sem documento", remedy="sincronize")
        ),
        actor=Actor.AGENT,
    )
    ws.apply(BlockerCleared(slug="a", note="documentos chegaram"), actor=Actor.AGENT)

    assert ws.state.agenda.task("a").status is TaskStatus.PENDING
    assert ws.state.agenda.task("a").blockers == ()
    # O bloqueio continua no diario: e parte da historia da tese.
    kinds = [e.kind for e in ws.journal.read()]
    assert "blocker_recorded" in kinds and "blocker_cleared" in kinds


# -- o que a dobra recusa ---------------------------------------------------


def test_concluir_com_bloqueio_pendente_e_recusado(ws):
    ws.apply(_tarefa("a"), actor=Actor.AGENT)
    ws.apply(
        BlockerRecorded(
            slug="a", blocker=Blocker(reason="sem documento", remedy="sincronize")
        ),
        actor=Actor.AGENT,
    )
    with pytest.raises(FoldError, match="bloqueio\\(s\\) pendente"):
        ws.apply(TaskStatusChanged(slug="a", status=TaskStatus.COMPLETE), actor=Actor.AGENT)


def test_blocked_sem_blocker_e_recusado(ws):
    ws.apply(_tarefa("a"), actor=Actor.AGENT)
    with pytest.raises(FoldError, match="BLOCKED sem blocker"):
        ws.apply(TaskStatusChanged(slug="a", status=TaskStatus.BLOCKED), actor=Actor.AGENT)


def test_tarefa_inexistente_e_recusada(ws):
    with pytest.raises(FoldError, match="nao existe"):
        ws.apply(TaskStatusChanged(slug="fantasma", status=TaskStatus.RUNNING), actor=Actor.AGENT)


def test_slug_repetido_e_recusado(ws):
    ws.apply(_tarefa("a"), actor=Actor.AGENT)
    with pytest.raises(FoldError, match="criada duas vezes"):
        ws.apply(_tarefa("a", objective="outro objetivo"), actor=Actor.AGENT)


def test_dependencia_inexistente_e_recusada_na_criacao(ws):
    with pytest.raises(FoldError, match="ainda nao existe"):
        ws.apply(_tarefa("a", depends_on=("nunca-criada",)), actor=Actor.AGENT)


def test_ciclo_de_dependencia_e_impossivel_por_construcao():
    """A ordem de criacao ja impede o ciclo - uma dependencia tem que existir
    antes -, mas o contrato tambem recusa, para o caso de um estado montado
    fora do diario."""
    agora = datetime(2025, 7, 1, tzinfo=UTC)
    comum = {
        "objective": "o",
        "completion_criteria": "c",
        "created_at": agora,
        "updated_at": agora,
    }
    with pytest.raises(ValueError, match="ciclo de dependencia"):
        ResearchAgenda(
            tasks=(
                ResearchTask(slug="a", depends_on=("b",), **comum),
                ResearchTask(slug="b", depends_on=("a",), **comum),
            )
        )


def test_tarefa_nao_depende_de_si_mesma():
    agora = datetime(2025, 7, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="depende de si mesma"):
        ResearchTask(
            slug="a",
            objective="o",
            completion_criteria="c",
            depends_on=("a",),
            created_at=agora,
            updated_at=agora,
        )


def test_criterio_de_conclusao_e_obrigatorio():
    """Tarefa sem criterio e concluida por sensacao - que e como um analista
    para de pesquisar logo antes do que refutaria a tese."""
    with pytest.raises(ValueError):
        TaskCreated(slug="a", objective="investigar", completion_criteria="")


# -- perguntas em aberto ----------------------------------------------------


def test_perguntas_abertas_e_respondidas(ws):
    ws.apply(_tarefa("moat"), actor=Actor.AGENT)
    ws.apply(
        QuestionOpened(task="moat", slug="escala", text="a escala e mesmo barreira?"),
        actor=Actor.USER,
    )
    ws.apply(
        QuestionOpened(task="moat", slug="marca", text="a marca sustenta preco?"),
        actor=Actor.AGENT,
    )

    assert len(ws.state.agenda.task("moat").open_questions) == 2
    assert [(s, q.slug) for s, q in ws.state.agenda.all_open_questions] == [
        ("moat", "escala"),
        ("moat", "marca"),
    ]

    ws.apply(
        QuestionAnswered(task="moat", slug="escala", answer="nao no varejo alimentar regional"),
        actor=Actor.AGENT,
    )
    abertas = ws.state.agenda.task("moat").open_questions
    assert [q.slug for q in abertas] == ["marca"]
    respondida = ws.state.agenda.task("moat").questions[0]
    assert respondida.answered and respondida.answer.startswith("nao no varejo")
    # Quem perguntou continua registrado.
    assert respondida.asked_by is Actor.USER


def test_responder_pergunta_nunca_aberta_e_recusado(ws):
    ws.apply(_tarefa("moat"), actor=Actor.AGENT)
    with pytest.raises(FoldError, match="sem ter sido aberta"):
        ws.apply(QuestionAnswered(task="moat", slug="x", answer="y"), actor=Actor.AGENT)


def test_pergunta_repetida_na_mesma_tarefa_e_recusada(ws):
    ws.apply(_tarefa("moat"), actor=Actor.AGENT)
    ws.apply(QuestionOpened(task="moat", slug="x", text="a"), actor=Actor.AGENT)
    with pytest.raises(FoldError, match="aberta duas vezes"):
        ws.apply(QuestionOpened(task="moat", slug="x", text="b"), actor=Actor.AGENT)


# -- achados ----------------------------------------------------------------


def test_achado_sem_ancora_e_registravel_mas_se_distingue(ws):
    """Observacao de leitura e legitima; ela so nao sustenta conclusao
    sozinha, e a diferenca fica no tipo."""
    ws.apply(_tarefa("negocio"), actor=Actor.AGENT)
    ws.apply(
        FindingRecorded(slug="negocio", text="impressao de leitura, sem ancora"),
        actor=Actor.AGENT,
    )
    ws.apply(
        FindingRecorded(slug="negocio", text="numero do motor", result_ids=("b" * 64,)),
        actor=Actor.ENGINE,
    )
    tarefa = ws.state.agenda.task("negocio")
    assert len(tarefa.findings) == 2
    assert len(tarefa.grounded_findings) == 1
    assert tarefa.grounded_findings[0].text == "numero do motor"


def test_achado_nao_carrega_numero():
    """Garantia de tipo, nao de disciplina: `Finding` nao tem onde por um
    valor. E a mesma razao pela qual `QuoteClaim` nao tem `value`."""
    from pat.contracts.opportunity import Finding

    assert not {"value", "unit", "currency", "amount"} & set(Finding.model_fields)


def test_slug_invalido_nao_passa():
    for ruim in ["Com Maiuscula", "com/barra", "com espaco", "", "-comeca-com-traco"]:
        with pytest.raises(ValueError):
            TaskCreated(slug=ruim, objective="o", completion_criteria="c")

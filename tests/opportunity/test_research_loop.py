"""O6 - o ciclo de pesquisa, rodando de ponta a ponta sem modelo nenhum.

O criterio de aceitacao e `test_investiga_isso_vira_agenda_executada`: o
usuario diz o que quer investigar, o agente decompoe, executa varias tarefas
sem confirmacao, grava tudo e volta com o que achou.

O outro teste que define o milestone e
`test_citacao_inventada_e_barrada_e_registrada`. Sem ele, o ciclo aceitaria
uma frase bem escrita que cita um identificador que nunca existiu - que e a
alucinacao de procedencia, pior que a de conteudo porque passa por verificada.
"""

from __future__ import annotations

from datetime import date

import pytest

from pat.contracts.opportunity import Actor, TaskStatus
from pat.contracts.opportunity.loop import (
    FindingProposal,
    Phase,
    RejectionReason,
    StepKind,
    StepRequest,
    TaskProposal,
    TaskVerdict,
)
from pat.opportunity import create_workspace, open_workspace, profile_for
from pat.opportunity.reason import (
    Reasoner,
    ScriptedReasoner,
    ShapeReasoner,
    series_shape,
)
from pat.opportunity.research import (
    build_context,
    phases,
    plan_agenda,
    run_agenda,
    run_task,
)
from pat.opportunity.tools import PatTools
from tests.opportunity.conftest import CREATED_AT
from tests.semantics import golden_gpa as gpa

AS_OF = date(2026, 6, 30)
FY2023 = date(2023, 12, 31)
FY2024 = date(2024, 12, 31)


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


# -- criterio de aceitacao --------------------------------------------------


def test_investiga_isso_vira_agenda_executada(ws, tools, root):
    """"Investiga a margem e a receita" -> agenda, execucao, achados, diario.

    Nenhuma confirmacao no meio, e nenhum modelo em lugar nenhum.
    """
    reasoner = ShapeReasoner()

    criados = plan_agenda(
        ws, tools, reasoner, objective="investigar receita e margem da companhia"
    )
    # Desde a N3 a decomposicao e por TEMA, e nao por metrica: "receita e
    # margem" vira as duas perguntas que um analista faria, cada uma com o seu
    # criterio de conclusao. Uma tarefa por metrica nao tinha criterio que
    # valesse alguma coisa - "levantei a metrica" e sempre verdade.
    assert set(criados) == {"crescimento", "rentabilidade"}
    assert ws.state.agenda.objective.startswith("investigar receita")

    corrida = run_agenda(ws, tools, reasoner)

    assert len(corrida.reports) == 2
    assert set(corrida.completed) == {"crescimento", "rentabilidade"}
    assert corrida.blocked == ()
    assert corrida.stopped_because == "agenda exaurida"

    # Os achados citam numero do motor, e estao no diario.
    tarefa = ws.state.agenda.task("crescimento")
    assert tarefa.status is TaskStatus.COMPLETE
    assert tarefa.grounded_findings, "o achado cita o resultado que o sustenta"
    assert all(f.actor is Actor.AGENT for f in tarefa.findings)

    # E sobrevive ao processo.
    reaberto = open_workspace(root, ws.workspace_id)
    assert reaberto.state == ws.state
    assert reaberto.state.agenda.task("rentabilidade").status is TaskStatus.COMPLETE


def test_citacao_inventada_e_barrada_e_registrada(ws, tools):
    """A frase parece ancorada e nao esta. Barrar sem registrar esconderia
    que o interpretador cita o que nao existe."""
    reasoner = ScriptedReasoner(
        tasks=(
            TaskProposal(
                slug="receita",
                objective="levantar receita_liquida@v1",
                completion_criteria="dois periodos calculados",
            ),
        ),
        steps={
            "receita": (
                StepRequest(
                    step_id="m0",
                    kind=StepKind.METRIC,
                    ref="receita_liquida@v1",
                    period_ends=(FY2023, FY2024),
                    rationale="a metrica do objetivo",
                ),
            )
        },
        findings={
            "receita": (
                FindingProposal(text="a receita cresceu", cites=("id-que-nunca-existiu",)),
                FindingProposal(text="observacao sem citacao nenhuma"),
            )
        },
        verdicts={
            "receita": TaskVerdict(status=TaskStatus.COMPLETE, rationale="ok")
        },
    )

    plan_agenda(ws, tools, reasoner, objective="receita")
    relatorio = run_task(ws, tools, reasoner, slug="receita")

    assert len(relatorio.rejected) == 1
    barrado = relatorio.rejected[0]
    assert barrado.reason is RejectionReason.UNKNOWN_CITATION
    assert "id-que-nunca-existiu" in barrado.detail
    # A frase sem ancora nenhuma passa - ela nao afirma procedencia falsa.
    assert [f.text for f in relatorio.findings] == ["observacao sem citacao nenhuma"]

    # E a rejeicao esta no diario, legivel.
    textos = [f.text for f in ws.state.agenda.task("receita").findings]
    assert any("interpretacao barrada" in t for t in textos)
    assert any("a receita cresceu" in t for t in textos)


def test_citacao_valida_passa(ws, tools):
    """O contraponto do teste anterior: citar o que o passo produziu funciona."""
    passo = StepRequest(
        step_id="m0",
        kind=StepKind.METRIC,
        ref="receita_liquida@v1",
        period_ends=(FY2023, FY2024),
        rationale="a metrica do objetivo",
    )
    reasoner = ScriptedReasoner(
        tasks=(
            TaskProposal(
                slug="receita", objective="receita", completion_criteria="dois periodos"
            ),
        ),
        steps={"receita": (passo,)},
        verdicts={"receita": TaskVerdict(status=TaskStatus.COMPLETE, rationale="ok")},
    )
    plan_agenda(ws, tools, reasoner, objective="receita")

    # Primeiro descobre que IDs existem, depois cita um deles.
    seco = run_task(ws, tools, reasoner, slug="receita")
    disponivel = seco.outcomes[0].result_ids[0]
    assert "|" in disponivel, "o identificador de metrica e a chave reproduzivel"

    reasoner.findings = {
        "receita": (FindingProposal(text="dois periodos calculados", cites=(disponivel,)),)
    }
    relatorio = run_task(ws, tools, reasoner, slug="receita")
    assert relatorio.rejected == ()
    assert relatorio.findings[0].cites == (disponivel,)


# -- as fases ---------------------------------------------------------------


def test_as_oito_fases_existem():
    assert [p.value for p in phases()] == [
        "understand",
        "plan",
        "research",
        "analyze",
        "test",
        "criticize",
        "update",
        "next",
    ]
    assert len(set(Phase)) == 8


def test_o_contexto_nao_carrega_numero(ws, tools):
    """Um contexto com valores faria o planejamento depender do que ja se sabe,
    e o passo seguinte seria "planejar" um numero ja visto."""
    from decimal import Decimal

    contexto = build_context(ws, tools)
    for campo in vars(contexto).values():
        assert not isinstance(campo, Decimal)
    assert contexto.periods == (FY2023, FY2024)
    assert "receita_liquida@v1" in contexto.metrics_available
    assert contexto.mandate.startswith("entender a qualidade")


# -- o que o laco recusa a fazer --------------------------------------------


def test_tarefa_sem_passo_vira_needs_human(ws, tools):
    """O objetivo nao casa com metrica registrada nenhuma.

    A tabela de termos e declarada; nao casar e a resposta certa, e a tarefa
    para pedindo uma pessoa em vez de inventar uma consulta.
    """
    reasoner = ShapeReasoner()
    plan_agenda(ws, tools, reasoner, objective="entender a cultura organizacional")
    corrida = run_agenda(ws, tools, reasoner)

    assert corrida.blocked == ("escopo",)
    tarefa = ws.state.agenda.task("escopo")
    assert tarefa.status is TaskStatus.NEEDS_HUMAN
    assert tarefa.blockers[0].needs_human
    assert tarefa.blockers[0].remedy


def test_veredito_bloqueado_exige_remedio():
    with pytest.raises(ValueError, match="motivo E remedio"):
        TaskVerdict(status=TaskStatus.BLOCKED, rationale="parou")


def test_veredito_nao_bloqueado_nao_carrega_bloqueio():
    """Um bloqueio que nao bloqueia vira ressalva decorativa."""
    with pytest.raises(ValueError, match="traz bloqueio"):
        TaskVerdict(
            status=TaskStatus.COMPLETE,
            rationale="ok",
            blocker_reason="mas tem isso",
            blocker_remedy="faca aquilo",
        )


def test_passo_sem_periodo_nao_pergunta_nada():
    with pytest.raises(ValueError, match="metrica sem periodo"):
        StepRequest(
            step_id="m0", kind=StepKind.METRIC, ref="ebitda@v1", rationale="porque sim"
        )


def test_passo_exige_justificativa():
    """Dez passos sem justificativa sao indistinguiveis de dez consultas por
    desencargo."""
    with pytest.raises(ValueError):
        StepRequest(
            step_id="m0",
            kind=StepKind.METRIC,
            ref="ebitda@v1",
            period_ends=(FY2024,),
            rationale="",
        )


def test_o_teto_por_corrida_existe(ws, tools):
    """Uma agenda que se realimentasse rodaria para sempre sem ninguem olhar."""
    reasoner = ShapeReasoner()
    plan_agenda(ws, tools, reasoner, objective="receita e margem e ebitda e lucro")
    corrida = run_agenda(ws, tools, reasoner, max_tasks=1)
    assert len(corrida.reports) == 1
    assert "teto de 1" in corrida.stopped_because
    # As outras continuam prontas para a proxima corrida.
    assert ws.state.agenda.ready()


def test_dependencia_nao_concluida_segura_a_dependente(ws, tools):
    reasoner = ScriptedReasoner(
        tasks=(
            TaskProposal(
                slug="base", objective="receita", completion_criteria="c"
            ),
            TaskProposal(
                slug="depende",
                objective="margem",
                completion_criteria="c",
                depends_on=("base",),
            ),
        ),
        verdicts={
            "base": TaskVerdict(
                status=TaskStatus.BLOCKED,
                rationale="sem dado",
                blocker_reason="nao ha fato",
                blocker_remedy="rode `pat build`",
            )
        },
    )
    plan_agenda(ws, tools, reasoner, objective="x")
    corrida = run_agenda(ws, tools, reasoner)

    assert corrida.blocked == ("base",)
    assert "esperando dependencia" in corrida.stopped_because
    assert ws.state.agenda.task("depende").status is TaskStatus.PENDING


def test_planejar_duas_vezes_nao_duplica_a_agenda(ws, tools):
    """"Investiga isso" repetido nao vira agenda em dobro."""
    reasoner = ShapeReasoner()
    primeiro = plan_agenda(ws, tools, reasoner, objective="receita")
    segundo = plan_agenda(ws, tools, reasoner, objective="receita")
    assert primeiro and segundo == ()
    assert len(ws.state.agenda.tasks) == len(primeiro)


# -- forma de serie ---------------------------------------------------------


def test_forma_de_serie_classifica_direcao_e_faixa():
    from decimal import Decimal

    # Os degraus vem de `research/shape.py` e nao sao redefinidos aqui: se
    # "grande" quisesse dizer coisas diferentes em dois lugares do sistema, o
    # relatorio ficaria incoerente consigo mesmo.
    assert series_shape(
        [(FY2023, Decimal("100")), (FY2024, Decimal("110"))]
    ).magnitude.value == "moderate"
    assert series_shape(
        [(FY2023, Decimal("100")), (FY2024, Decimal("130"))]
    ).magnitude.value == "large"

    forma = series_shape([(FY2023, Decimal("100")), (FY2024, Decimal("150"))])
    assert forma.direction.value == "up"
    assert forma.magnitude.value == "extreme", "+50% passa do teto de `large`"
    assert forma.relative == Decimal("0.5")

    queda = series_shape([(FY2023, Decimal("100")), (FY2024, Decimal("99.9"))])
    assert queda.direction.value == "flat", "0,1% e ruido, nao tendencia"


def test_forma_de_serie_com_base_zero_nao_inventa_magnitude():
    """Inventar "infinito" ou "100%" daria uma magnitude que nao existe."""
    from decimal import Decimal

    forma = series_shape([(FY2023, Decimal("0")), (FY2024, Decimal("10"))])
    assert forma.direction is None and forma.magnitude is None


def test_forma_de_um_ponto_so_nao_tem_direcao():
    from decimal import Decimal

    assert series_shape([(FY2024, Decimal("10"))]).direction is None


def test_os_dois_raciocinadores_satisfazem_o_protocolo():
    assert isinstance(ShapeReasoner(), Reasoner)
    assert isinstance(ScriptedReasoner(), Reasoner)


def test_o_relatorio_diz_quem_raciocinou(ws, tools):
    """Uma conclusao de regra e uma conclusao de modelo tem forcas
    diferentes."""
    reasoner = ShapeReasoner()
    plan_agenda(ws, tools, reasoner, objective="receita")
    relatorio = run_task(ws, tools, reasoner, slug="crescimento")
    assert relatorio.reasoner_id == "shape/v1"


def test_o_raciocinador_deterministico_nao_explica_causa(ws, tools):
    """As afirmacoes dele sao de FORMA. Um deterministico que "explicasse"
    seria um gerador de causa plausivel."""
    reasoner = ShapeReasoner()
    plan_agenda(ws, tools, reasoner, objective="receita")
    relatorio = run_task(ws, tools, reasoner, slug="crescimento")
    for finding in relatorio.findings:
        baixo = finding.text.lower()
        for causal in (" porque ", " devido ", " por conta ", " gracas a "):
            assert causal not in baixo, f"o achado {finding.text!r} afirma causa"


# -- a serie que vai e volta ------------------------------------------------


def test_serie_nao_monotonica_nao_e_descrita_pelas_pontas():
    """O EBIT real do GPA: -565 MM, +660 MM, -436 MM.

    Comparar so as pontas dava "up / large" - aritmeticamente certo e errado
    sobre o que aconteceu. O ano do meio e o unico positivo dos tres, e uma
    forma que o esconde e o analogo textual do numero aproximado que se
    apresenta como exato.
    """
    from decimal import Decimal

    forma = series_shape(
        [
            (date(2022, 12, 31), Decimal("-565000000")),
            (date(2023, 12, 31), Decimal("660000000")),
            (date(2024, 12, 31), Decimal("-436000000")),
        ]
    )

    assert forma.reversals == 1
    assert not forma.is_monotonic
    assert forma.crosses_zero
    # Direcao sobrevive - "menos negativo" e afirmavel. Magnitude nao: 22% de
    # melhora sobre um EBIT negativo nao e 22% de nada.
    assert forma.direction is not None
    assert forma.magnitude is None


def test_serie_monotonica_continua_simples():
    """A checagem nova nao pode fazer toda serie parecer acidentada."""
    from decimal import Decimal

    forma = series_shape(
        [
            (date(2022, 12, 31), Decimal("100")),
            (date(2023, 12, 31), Decimal("110")),
            (date(2024, 12, 31), Decimal("130")),
        ]
    )

    assert forma.is_monotonic
    assert not forma.crosses_zero
    assert forma.magnitude is not None


def test_um_ano_de_lado_no_meio_nao_e_reversao():
    """Diferenca nula nao muda o sentido; conta-la faria toda serie longa
    parecer acidentada."""
    from decimal import Decimal

    forma = series_shape(
        [
            (date(2022, 12, 31), Decimal("100")),
            (date(2023, 12, 31), Decimal("100")),
            (date(2024, 12, 31), Decimal("130")),
        ]
    )

    assert forma.is_monotonic


def test_a_prosa_avisa_que_as_pontas_nao_contam_a_serie(ws, tools):
    """O aviso tem que chegar ao relatorio, e nao so ao objeto.

    Uma forma que sabe da reversao e uma frase que nao a menciona deixa o
    problema exatamente onde ele estava.
    """
    from pat.opportunity.research import _shape_em_prosa

    from decimal import Decimal

    forma = series_shape(
        [
            (date(2022, 12, 31), Decimal("-565000000")),
            (date(2023, 12, 31), Decimal("660000000")),
            (date(2024, 12, 31), Decimal("-436000000")),
        ]
    )
    prosa = _shape_em_prosa(forma)

    assert "NAO MONOTONICA" in prosa
    assert "troca de sinal" in prosa

"""O7 - o advogado do diabo.

O achado que da nome ao milestone e `SUPPORTED_WITHOUT_FALSIFICATION`: uma
hipotese sustentada sem registro de que alguem procurou o contrario. Sem ele,
"procuramos e nao achamos" e "nao procuramos" produzem exatamente o mesmo
estado - zero contra-evidencias - e pedem leituras opostas.

O outro e `USER_ASSERTION_CONTRADICTED`, que e o que autoriza o agente a
discordar do analista. Ele so aparece com contra-evidencia registrada junto:
discordar sem evidencia nao e ceticismo, e outra opiniao.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pat.contracts.claims import Severity
from pat.contracts.opportunity import (
    Actor,
    AlternativeConsidered,
    ClaimAsserted,
    CounterEvidence,
    CounterEvidenceAdded,
    EvidenceKind,
    EvidenceLink,
    EvidenceLinked,
    FalsificationAttempted,
    HypothesisOpened,
    HypothesisStatus,
    HypothesisStatusChanged,
    HypothesisStrength,
    TaskCreated,
    WorkspaceFinding,
)
from pat.opportunity import create_workspace
from pat.opportunity.critic import critique, falsification_agenda
from tests.opportunity.conftest import AS_OF, CREATED_AT

AT = datetime(2025, 7, 2, tzinfo=UTC)


@pytest.fixture
def ws(root, gpa_profile):
    return create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)


def _metric(periodo: str = "2024-12-31", ref: str | None = None) -> EvidenceLink:
    return EvidenceLink(
        kind=EvidenceKind.METRIC,
        ref=ref or f"ebitda@v1|br:cnpj:1|{periodo}|consolidated|2026-06-30",
        note="numero do motor",
        linked_by=Actor.ENGINE,
        at=AT,
    )


def _quote() -> EvidenceLink:
    return EvidenceLink(
        kind=EvidenceKind.QUOTE, ref="q" * 64, note="release", linked_by=Actor.AGENT, at=AT
    )


def _abre(ws, slug="moat", actor=Actor.AGENT, falsifiers=("a participacao cai tres anos",)):
    ws.apply(
        HypothesisOpened(
            slug=slug, statement=f"{slug} existe e e durável", falsifiers=falsifiers
        ),
        actor=actor,
    )


def _sustenta(ws, slug="moat", links=None):
    for link in links or (_metric(),):
        ws.apply(EvidenceLinked(hypothesis=slug, link=link), actor=Actor.ENGINE)
    ws.apply(
        HypothesisStatusChanged(
            slug=slug,
            status=HypothesisStatus.SUPPORTED,
            strength=HypothesisStrength.MODERATE,
            rationale="evidencia a favor",
        ),
        actor=Actor.AGENT,
    )


def _codigos(critica) -> set[WorkspaceFinding]:
    return {f.code for f in critica.findings}


# -- os dois achados que definem a O7 ---------------------------------------


def test_sustentada_sem_ter_procurado_o_contrario_e_achado_duro(ws):
    _abre(ws)
    _sustenta(ws)

    critica = critique(ws.state)

    assert WorkspaceFinding.SUPPORTED_WITHOUT_FALSIFICATION in _codigos(critica)
    achado = next(
        f
        for f in critica.findings
        if f.code is WorkspaceFinding.SUPPORTED_WITHOUT_FALSIFICATION
    )
    assert achado.severity is Severity.HARD
    assert critica.blocks, "a tese nao pode ser apresentada como concluida"
    assert achado.remedy and achado.hypothesis == "moat"


def test_registrar_a_tentativa_desarma_o_achado(ws):
    """E o `how` e o que faz o registro valer alguma coisa."""
    _abre(ws)
    _sustenta(ws)
    ws.apply(
        FalsificationAttempted(
            hypothesis="moat",
            how="busca no corpus por 'participacao de mercado' em 14 documentos de 2023-2025",
            falsifier="a participacao cai tres anos",
            found=False,
        ),
        actor=Actor.AGENT,
    )

    critica = critique(ws.state)
    assert WorkspaceFinding.SUPPORTED_WITHOUT_FALSIFICATION not in _codigos(critica)
    assert WorkspaceFinding.FALSIFIER_UNTESTED not in _codigos(critica)
    assert ws.state.hypothesis("moat").was_falsification_attempted


def test_a_tentativa_sem_como_nao_existe():
    """Sem `how`, o registro viraria um carimbo que qualquer um aplica para
    destravar a critica."""
    with pytest.raises(ValueError):
        FalsificationAttempted(hypothesis="moat", how="")


def test_o_agente_discorda_do_usuario_com_evidencia_na_mao(ws):
    """`USER_ASSERTION_CONTRADICTED` e o que autoriza "eu nao aceitaria essa
    conclusao ainda" - e ele carrega o endereco da evidencia."""
    _abre(ws, slug="escala", actor=Actor.USER)
    _sustenta(ws, slug="escala")
    ws.apply(
        CounterEvidenceAdded(
            hypothesis="escala",
            counter=CounterEvidence(
                link=_metric(periodo="2023-12-31", ref="margem_ebitda@v1|br:cnpj:1|2023-12-31|consolidated|2026-06-30"),
                undermines="a margem caiu enquanto a escala crescia",
                decisive=True,
            ),
        ),
        actor=Actor.ENGINE,
    )

    critica = critique(ws.state)
    achado = next(
        f for f in critica.findings if f.code is WorkspaceFinding.USER_ASSERTION_CONTRADICTED
    )
    assert achado.severity is Severity.HARD
    assert achado.hypothesis == "escala"
    assert achado.evidence_refs, "a discordancia vem com o endereco da evidencia"
    assert "analista" in achado.message


def test_hipotese_do_agente_contradita_nao_gera_o_achado_do_usuario(ws):
    """O achado e sobre discordar de uma PESSOA. Contra-evidencia numa hipotese
    do proprio agente ja e coberta por `DECISIVE_COUNTER_IGNORED`."""
    _abre(ws, slug="minha", actor=Actor.AGENT)
    _sustenta(ws, slug="minha")
    ws.apply(
        CounterEvidenceAdded(
            hypothesis="minha",
            counter=CounterEvidence(
                link=_quote(), undermines="corta contra", decisive=True
            ),
        ),
        actor=Actor.AGENT,
    )
    codigos = _codigos(critique(ws.state))
    assert WorkspaceFinding.USER_ASSERTION_CONTRADICTED not in codigos
    assert WorkspaceFinding.DECISIVE_COUNTER_IGNORED in codigos


# -- os demais achados ------------------------------------------------------


def test_falsificador_ja_ocorrido_com_hipotese_de_pe(ws):
    _abre(ws)
    _sustenta(ws)
    ws.apply(
        CounterEvidenceAdded(
            hypothesis="moat",
            counter=CounterEvidence(
                link=_metric(), undermines="o falsificador ocorreu", decisive=True
            ),
        ),
        actor=Actor.ENGINE,
    )
    achado = next(
        f
        for f in critique(ws.state).findings
        if f.code is WorkspaceFinding.DECISIVE_COUNTER_IGNORED
    )
    assert achado.severity is Severity.HARD
    assert achado.evidence_refs


def test_sustentada_com_tarefa_travada(ws):
    """A tese se declarando pronta tendo pulado o que nao conseguiu fazer."""
    from pat.contracts.opportunity import Blocker, BlockerRecorded

    _abre(ws)
    ws.apply(
        TaskCreated(
            slug="testar-moat",
            objective="testar o moat",
            completion_criteria="serie de participacao em tres anos",
            hypothesis="moat",
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        BlockerRecorded(
            slug="testar-moat",
            blocker=Blocker(reason="sem dado de participacao", remedy="ingerir a fonte"),
        ),
        actor=Actor.AGENT,
    )
    _sustenta(ws)

    achado = next(
        f
        for f in critique(ws.state).findings
        if f.code is WorkspaceFinding.SUPPORTED_WITH_BLOCKED_TASK
    )
    assert achado.severity is Severity.HARD
    assert achado.task == "testar-moat"


def test_sem_alternativa_considerada_e_leve(ws):
    _abre(ws)
    _sustenta(ws)
    achado = next(
        f
        for f in critique(ws.state).findings
        if f.code is WorkspaceFinding.NO_ALTERNATIVE_CONSIDERED
    )
    assert achado.severity is Severity.SOFT
    assert not any(
        f.code is WorkspaceFinding.NO_ALTERNATIVE_CONSIDERED
        for f in _apos_alternativa(ws).findings
    )


def _apos_alternativa(ws):
    ws.apply(
        AlternativeConsidered(
            hypothesis="moat",
            explanation="a margem pode vir de mix, e nao de barreira",
            ruled_out=True,
            basis="o mix por bandeira e estavel nos tres periodos",
        ),
        actor=Actor.AGENT,
    )
    return critique(ws.state)


def test_alternativa_descartada_exige_base():
    """"Nao me pareceu provavel" e a forma mais educada de nao ter
    considerado."""
    with pytest.raises(ValueError, match="sem base"):
        AlternativeConsidered(
            hypothesis="moat", explanation="podia ser mix", ruled_out=True
        )
    # Deixar aberta e legitimo e nao exige base.
    assert AlternativeConsidered(
        hypothesis="moat", explanation="podia ser mix"
    ).ruled_out is False


def test_um_periodo_so_e_achado(ws):
    """Um ano pode ser qualquer coisa."""
    _abre(ws)
    _sustenta(ws, links=(_metric(periodo="2024-12-31"),))
    ws.apply(
        FalsificationAttempted(hypothesis="moat", how="busquei"), actor=Actor.AGENT
    )
    assert WorkspaceFinding.SINGLE_PERIOD_SUPPORT in _codigos(critique(ws.state))


def test_dois_periodos_nao_geram_o_achado(ws):
    _abre(ws)
    _sustenta(
        ws,
        links=(_metric(periodo="2023-12-31"), _metric(periodo="2024-12-31")),
    )
    assert WorkspaceFinding.SINGLE_PERIOD_SUPPORT not in _codigos(critique(ws.state))


def test_so_citacao_do_emissor_e_achado(ws):
    """Repetir o que a companhia disse e diferente de ter verificado."""
    _abre(ws)
    _sustenta(ws, links=(_quote(),))
    assert WorkspaceFinding.ISSUER_ONLY_SUPPORT in _codigos(critique(ws.state))


def test_premissa_nao_sustenta_afirmacao_sobre_o_passado(ws):
    """Grandeza ancorada so numa premissa e projecao escrita no passado."""
    ws.apply(
        ClaimAsserted(
            slug="cresceu-12",
            text="a receita cresceu 12% no periodo",
            evidence=(
                EvidenceLink(
                    kind=EvidenceKind.ASSUMPTION,
                    ref="premissa-crescimento",
                    note="premissa do analista",
                    linked_by=Actor.USER,
                    at=AT,
                ),
            ),
        ),
        actor=Actor.USER,
    )
    achado = next(
        f
        for f in critique(ws.state).findings
        if f.code is WorkspaceFinding.ASSUMPTION_AS_OBSERVATION
    )
    assert achado.claim == "cresceu-12"


def test_claim_sem_numero_ancorado_em_premissa_nao_e_achado(ws):
    """A checagem e sobre grandeza. Uma afirmacao qualitativa que declara
    premissa nao esta escrevendo projecao no passado."""
    ws.apply(
        ClaimAsserted(
            slug="qualitativa",
            text="assumimos que o setor continua consolidando",
            evidence=(
                EvidenceLink(
                    kind=EvidenceKind.ASSUMPTION,
                    ref="premissa-setor",
                    linked_by=Actor.USER,
                    at=AT,
                ),
            ),
        ),
        actor=Actor.USER,
    )
    assert WorkspaceFinding.ASSUMPTION_AS_OBSERVATION not in _codigos(critique(ws.state))


def test_hipotese_orfa(ws):
    _abre(ws)
    assert WorkspaceFinding.ORPHAN_HYPOTHESIS in _codigos(critique(ws.state))


def test_falsificador_nao_testado(ws):
    _abre(ws, falsifiers=("a participacao cai", "a margem cai dois anos"))
    ws.apply(
        FalsificationAttempted(
            hypothesis="moat", how="busquei no corpus", falsifier="a participacao cai"
        ),
        actor=Actor.AGENT,
    )
    achados = [
        f for f in critique(ws.state).findings if f.code is WorkspaceFinding.FALSIFIER_UNTESTED
    ]
    assert len(achados) == 1
    assert "a margem cai dois anos" in achados[0].message
    assert ws.state.hypothesis("moat").untested_falsifiers == ("a margem cai dois anos",)


# -- a agenda de falsificacao -----------------------------------------------


def test_a_critica_devolve_tarefas_e_nao_so_reclamacoes(ws):
    """Um critico que so aponta produz um relatorio que alguem le, concorda e
    arquiva."""
    _abre(ws, falsifiers=("a participacao cai", "a margem cai dois anos"))
    _sustenta(ws)

    propostas = falsification_agenda(ws.state)
    slugs = {p.slug for p in propostas}
    assert "falsificar-moat-0" in slugs and "falsificar-moat-1" in slugs
    assert "alternativa-moat" in slugs
    # Testar o que derruba a tese e mais urgente que mais uma evidencia a favor.
    assert all(p.priority.value == "high" for p in propostas)
    assert all(p.hypothesis == "moat" for p in propostas)
    assert all(p.completion_criteria for p in propostas)


def test_hipotese_ja_rejeitada_nao_gera_agenda(ws):
    _abre(ws)
    ws.apply(
        CounterEvidenceAdded(
            hypothesis="moat",
            counter=CounterEvidence(link=_metric(), undermines="derruba", decisive=True),
        ),
        actor=Actor.ENGINE,
    )
    ws.apply(
        HypothesisStatusChanged(
            slug="moat",
            status=HypothesisStatus.REJECTED,
            strength=HypothesisStrength.STRONG,
            rationale="o falsificador ocorreu",
        ),
        actor=Actor.AGENT,
    )
    assert falsification_agenda(ws.state) == ()


def test_a_critica_e_pura(ws):
    """Nao muda estado. Um laco critic -> autor -> critic seria um amostrador
    que uma hora aprova algo errado."""
    _abre(ws)
    _sustenta(ws)
    antes = ws.state
    critique(antes)
    assert ws.state == antes


def test_severidade_vem_da_taxonomia_nao_de_quem_escreve():
    """O mesmo codigo duro num lugar e leve no outro faria "esta tese pode ser
    apresentada?" depender de qual caminho gerou o achado."""
    from pat.contracts.opportunity.critic import HARD_FINDINGS

    assert HARD_FINDINGS < set(WorkspaceFinding)
    assert WorkspaceFinding.SINGLE_PERIOD_SUPPORT not in HARD_FINDINGS


def test_workspace_limpo_nao_gera_achado(ws):
    """O criterio negativo: um estado bem cuidado passa sem objecao."""
    _abre(ws)
    ws.apply(
        TaskCreated(
            slug="testar-moat",
            objective="testar",
            completion_criteria="serie",
            hypothesis="moat",
        ),
        actor=Actor.AGENT,
    )
    _sustenta(
        ws, links=(_metric(periodo="2023-12-31"), _metric(periodo="2024-12-31"))
    )
    ws.apply(
        FalsificationAttempted(
            hypothesis="moat",
            how="serie de participacao em tres anos, mais busca no corpus",
            falsifier="a participacao cai tres anos",
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        AlternativeConsidered(
            hypothesis="moat",
            explanation="podia ser mix",
            ruled_out=True,
            basis="mix estavel nos tres periodos",
        ),
        actor=Actor.AGENT,
    )
    critica = critique(ws.state)
    assert critica.findings == (), [f.code.value for f in critica.findings]
    assert not critica.blocks
    assert critica.hypotheses_checked == 1

"""O9 - a tese, e a auditoria que a leva ate a evidencia original.

Dois testes definem o milestone.

`test_a_tese_resolve_ate_a_evidencia` prova o caminho feliz: cada afirmacao
chega a um numero do motor ou a um trecho verbatim.

`test_hipotese_que_cai_depois_derruba_a_tese_na_auditoria` prova o caso que
ninguem revisa sozinho: a pesquisa continuou, a hipotese caiu, e a tese ficou
no arquivo dizendo o que dizia. Nada avisa, porque a tese nao mudou - o que
mudou esta em outro lugar do estado.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pat.contracts.opportunity import (
    Actor,
    Catalyst,
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
    InvestmentThesis,
    QuestionOpened,
    Risk,
    RiskSeverity,
    TaskCreated,
    ThesisDirection,
    ThesisDrafted,
    ThesisIssue,
    ValuationDeclared,
)
from pat.contracts.opportunity.critic import AlternativeConsidered
from pat.opportunity import create_workspace, open_workspace
from pat.opportunity.thesis import audit_thesis
from tests.opportunity.conftest import AS_OF, CREATED_AT

AT = datetime(2025, 7, 2, tzinfo=UTC)
RESULT_ID = "ebitda@v1|br:cnpj:1|2024-12-31|consolidated|2026-06-30"


@pytest.fixture
def ws(root, gpa_profile):
    return create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)


def _metric(ref: str = RESULT_ID) -> EvidenceLink:
    return EvidenceLink(
        kind=EvidenceKind.METRIC, ref=ref, note="numero do motor", linked_by=Actor.ENGINE, at=AT
    )


def _tese(**kw) -> InvestmentThesis:
    base = {
        "slug": "gpa-long",
        "statement": "o mercado subestima a recuperacao de margem do varejo alimentar",
        "direction": ThesisDirection.LONG,
        "confidence": HypothesisStrength.MODERATE,
        "key_assumptions": ("a recuperacao de margem persiste por dois anos",),
        "risks": (
            Risk(
                slug="concorrencia",
                text="entrada de atacarejo nas pracas principais",
                severity=RiskSeverity.MATERIAL,
                leading_indicator="aberturas anunciadas por trimestre",
            ),
        ),
        "falsifiers": ("a margem bruta cai dois anos seguidos",),
        "counter_thesis": (
            "o ganho de margem veio de mix e nao de eficiencia, e reverte quando a "
            "bandeira de menor ticket voltar a crescer"
        ),
        "as_of": AS_OF,
        "author": Actor.USER,
        "created_at": AT,
    }
    return InvestmentThesis(**(base | kw))


def _hipotese_sustentada(ws, slug="margem"):
    ws.apply(
        HypothesisOpened(
            slug=slug,
            statement="a margem se recupera de forma durável",
            falsifiers=("a margem bruta cai dois anos seguidos",),
        ),
        actor=Actor.AGENT,
    )
    ws.apply(EvidenceLinked(hypothesis=slug, link=_metric()), actor=Actor.ENGINE)
    ws.apply(
        EvidenceLinked(
            hypothesis=slug,
            link=_metric(ref="ebitda@v1|br:cnpj:1|2023-12-31|consolidated|2026-06-30"),
        ),
        actor=Actor.ENGINE,
    )
    ws.apply(
        FalsificationAttempted(
            hypothesis=slug,
            how="serie de margem bruta em quatro periodos, mais busca no corpus",
            falsifier="a margem bruta cai dois anos seguidos",
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        AlternativeConsidered(
            hypothesis=slug,
            explanation="podia ser mix de bandeiras",
            ruled_out=True,
            basis="o mix e estavel nos quatro periodos",
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        HypothesisStatusChanged(
            slug=slug,
            status=HypothesisStatus.SUPPORTED,
            strength=HypothesisStrength.MODERATE,
            rationale="dois periodos de margem em recuperacao",
        ),
        actor=Actor.AGENT,
    )


# -- os dois criterios de aceitacao -----------------------------------------


def test_a_tese_resolve_ate_a_evidencia(ws, root):
    _hipotese_sustentada(ws)
    ws.apply(
        ClaimAsserted(
            slug="margem-subiu",
            text="a margem subiu em dois periodos consecutivos",
            evidence=(_metric(),),
            hypothesis="margem",
        ),
        actor=Actor.AGENT,
    )
    tese = _tese(supporting_claims=("margem-subiu",), supporting_hypotheses=("margem",))
    ws.apply(ThesisDrafted(thesis=tese), actor=Actor.USER)

    auditoria = audit_thesis(ws.state, tese)

    assert auditoria.resolves, [i.message for i in auditoria.issues]
    assert auditoria.claims_checked == 1
    assert RESULT_ID in auditoria.evidence_refs
    assert len(auditoria.evidence_refs) == 2, "sem repeticao"

    # E a tese sobrevive ao processo.
    reaberto = open_workspace(root, ws.workspace_id)
    assert reaberto.state.thesis("gpa-long").statement == tese.statement
    assert reaberto.state == ws.state


def test_hipotese_que_cai_depois_derruba_a_tese_na_auditoria(ws):
    """O caso que ninguem revisa sozinho: a tese nao mudou, o que mudou esta
    em outro lugar do estado."""
    _hipotese_sustentada(ws)
    tese = _tese(supporting_hypotheses=("margem",))
    ws.apply(ThesisDrafted(thesis=tese), actor=Actor.USER)
    assert audit_thesis(ws.state, tese).resolves

    # A pesquisa continua, e a evidencia vira.
    ws.apply(
        CounterEvidenceAdded(
            hypothesis="margem",
            counter=CounterEvidence(
                link=_metric(ref="margem@v1|br:cnpj:1|2025-12-31|consolidated|2026-06-30"),
                undermines="a margem caiu no periodo seguinte",
                decisive=True,
            ),
        ),
        actor=Actor.ENGINE,
    )
    ws.apply(
        HypothesisStatusChanged(
            slug="margem",
            status=HypothesisStatus.REJECTED,
            strength=HypothesisStrength.STRONG,
            rationale="o falsificador ocorreu",
        ),
        actor=Actor.AGENT,
    )

    auditoria = audit_thesis(ws.state, tese)
    assert not auditoria.resolves
    problema = auditoria.of(ThesisIssue.HYPOTHESIS_REJECTED)[0]
    assert problema.ref == "margem"
    assert "nada avisou" in problema.message
    assert problema.remedy


# -- o que a tese e obrigada a dizer ----------------------------------------


@pytest.mark.parametrize(
    ("campo", "vazio"),
    [("key_assumptions", ()), ("risks", ()), ("falsifiers", ()), ("counter_thesis", "")],
)
def test_a_tese_nao_pode_omitir_o_que_a_enfraquece(campo, vazio):
    """Cada ausencia e uma forma conhecida de a tese parecer mais forte do que
    e: sem premissa, esconde de que depende; sem risco, afirma que nao ha
    nenhum; sem falsificador, nao esta sendo afirmada."""
    with pytest.raises(ValueError):
        _tese(**{campo: vazio})


def test_unresolved_pode_ser_vazio_mas_o_campo_nunca_some():
    """Ha teses sem pergunta em aberto. O que nao ha e tese que nao precisa
    responder a pergunta."""
    assert _tese().unresolved == ()
    assert "unresolved" in InvestmentThesis.model_fields


def test_risco_que_quebra_a_tese_tambem_e_falsificador():
    """Listar so num dos dois lugares deixa a tese afirmando, na secao de
    falsificadores, que nada a derruba - depois de ter listado o que a mata na
    secao de riscos."""
    with pytest.raises(ValueError, match="THESIS_BREAKING e nao aparece"):
        _tese(
            risks=(
                Risk(
                    slug="perda-de-escala",
                    text="a companhia perde a escala regional",
                    severity=RiskSeverity.THESIS_BREAKING,
                ),
            )
        )
    ok = _tese(
        risks=(
            Risk(
                slug="perda-de-escala",
                text="a companhia perde a escala regional",
                severity=RiskSeverity.THESIS_BREAKING,
            ),
        ),
        falsifiers=("a companhia perde a escala regional",),
    )
    assert ok.thesis_breaking_risks


def test_watch_sem_gatilho_e_no_position():
    with pytest.raises(ValueError, match="WATCH quer dizer"):
        _tese(direction=ThesisDirection.WATCH)
    ok = _tese(
        direction=ThesisDirection.WATCH,
        catalysts=(
            Catalyst(
                slug="3t26",
                text="o resultado do 3T26 mostra se a margem persiste",
                expected_by=date(2026, 11, 15),
            ),
        ),
    )
    assert not ok.is_actionable


def test_nao_concluir_e_uma_conclusao():
    """Um sistema que so permitisse LONG ou SHORT pressionaria toda tese para
    um extremo, e a pressao apareceria como conviccao."""
    tese = _tese(direction=ThesisDirection.NO_POSITION)
    assert not tese.is_actionable
    assert ThesisDirection.NO_POSITION in set(ThesisDirection)


def test_catalisador_sem_prazo_e_diferente_de_em_breve():
    """`None` quer dizer "sem prazo", nao "em breve" - e a diferenca precisa
    aparecer no papel."""
    sem_prazo = Catalyst(slug="c", text="alguma hora a margem aparece")
    assert sem_prazo.expected_by is None


# -- a auditoria -------------------------------------------------------------


def test_claim_inexistente_e_pego(ws):
    tese = _tese(supporting_claims=("nunca-afirmado",))
    problema = audit_thesis(ws.state, tese).of(ThesisIssue.CLAIM_MISSING)[0]
    assert problema.ref == "nunca-afirmado"


def test_hipotese_nunca_testada_e_pega(ws):
    ws.apply(
        HypothesisOpened(slug="aberta", statement="talvez", falsifiers=("x",)),
        actor=Actor.AGENT,
    )
    tese = _tese(supporting_hypotheses=("aberta",))
    assert audit_thesis(ws.state, tese).of(ThesisIssue.HYPOTHESIS_OPEN)


def test_objecao_dura_do_advogado_do_diabo_e_herdada_pela_tese(ws):
    """Sem essa ligacao, um workspace produziria uma critica bloqueante e uma
    tese tranquila lado a lado, e o leitor escolheria qual acreditar."""
    ws.apply(
        HypothesisOpened(slug="frouxa", statement="tem moat", falsifiers=("cai",)),
        actor=Actor.AGENT,
    )
    ws.apply(EvidenceLinked(hypothesis="frouxa", link=_metric()), actor=Actor.ENGINE)
    ws.apply(
        HypothesisStatusChanged(
            slug="frouxa",
            status=HypothesisStatus.SUPPORTED,
            strength=HypothesisStrength.STRONG,
            rationale="sem ter procurado o contrario",
        ),
        actor=Actor.AGENT,
    )

    tese = _tese(supporting_hypotheses=("frouxa",))
    problemas = audit_thesis(ws.state, tese).of(ThesisIssue.BLOCKING_CRITIQUE)
    assert problemas
    assert "frouxa" in problemas[0].message


def test_pergunta_em_aberto_nao_listada_e_pega(ws):
    """O silencio sobre elas se le como completude."""
    _hipotese_sustentada(ws)
    ws.apply(
        TaskCreated(slug="t", objective="o", completion_criteria="c"), actor=Actor.AGENT
    )
    ws.apply(
        QuestionOpened(task="t", slug="mix", text="o mix explica a margem?"),
        actor=Actor.USER,
    )

    tese = _tese(supporting_hypotheses=("margem",))
    problema = audit_thesis(ws.state, tese).of(ThesisIssue.OPEN_QUESTIONS_UNLISTED)[0]
    assert "mix" in problema.message

    # Listar a pergunta resolve.
    listada = _tese(
        supporting_hypotheses=("margem",),
        unresolved=("o mix explica a margem?",),
    )
    assert not audit_thesis(ws.state, listada).of(ThesisIssue.OPEN_QUESTIONS_UNLISTED)


def test_valuation_citada_e_inexistente(ws):
    tese = _tese(valuation="dcf-que-nao-existe")
    assert audit_thesis(ws.state, tese).of(ThesisIssue.VALUATION_MISSING)


def test_valuation_declarada_passa(ws, gpa_profile):
    from tests.opportunity.test_valuation import _modelo

    ws.apply(ValuationDeclared(model=_modelo()), actor=Actor.USER)
    tese = _tese(valuation="dcf-base")
    assert not audit_thesis(ws.state, tese).of(ThesisIssue.VALUATION_MISSING)


def test_a_auditoria_e_pura(ws):
    _hipotese_sustentada(ws)
    tese = _tese(supporting_hypotheses=("margem",))
    antes = ws.state
    audit_thesis(antes, tese)
    assert ws.state == antes


# -- versoes -----------------------------------------------------------------


def test_reescrever_a_tese_mantem_as_versoes_no_diario(ws):
    """Uma tese que muda de LONG para NO_POSITION e a informacao mais valiosa
    que o diario guarda."""
    ws.apply(ThesisDrafted(thesis=_tese()), actor=Actor.USER)
    ws.apply(
        ThesisDrafted(
            thesis=_tese(
                direction=ThesisDirection.NO_POSITION,
                confidence=HypothesisStrength.WEAK,
            )
        ),
        actor=Actor.USER,
    )

    corrente = ws.state.thesis("gpa-long")
    assert corrente.direction is ThesisDirection.NO_POSITION
    assert len(ws.state.theses) == 1, "o estado tem UMA tese corrente"

    versoes = [e.body.thesis.direction for e in ws.journal.read() if e.kind == "thesis_drafted"]
    assert versoes == [ThesisDirection.LONG, ThesisDirection.NO_POSITION]

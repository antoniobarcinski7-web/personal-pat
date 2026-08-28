"""O9 - a auditoria da tese: ela resolve ate a evidencia original?

Uma funcao, pura, e a pergunta que ela responde e a unica que decide se uma
tese pode ser defendida numa mesa: **toda afirmacao chega a um numero do motor
ou a um trecho verbatim?**

O que a auditoria pega, e por que cada caso importa
--------------------------------------------------
`HYPOTHESIS_REJECTED` e o achado mais valioso do modulo. Ele aparece quando a
hipotese cai DEPOIS de a tese ter sido escrita - a pesquisa continuou, a
evidencia virou, e a tese ficou no arquivo dizendo o que dizia. E o caso que
ninguem revisa sozinho, porque nada avisa: a tese nao mudou, e o que mudou
esta em outro lugar do estado.

`OPEN_QUESTIONS_UNLISTED` pega a versao mais educada do mesmo problema. A
agenda tem perguntas em aberto, a tese nao as menciona, e o leitor conclui que
nao ha nenhuma. Nao e mentira - e o silencio se lendo como completude.

`BLOCKING_CRITIQUE` liga esta camada a O7: se o advogado do diabo tem objecao
dura sobre uma hipotese que a tese usa, a tese herda a objecao. Sem essa
ligacao, um workspace poderia produzir uma critica bloqueante e uma tese
tranquila lado a lado, e o leitor escolheria qual acreditar.
"""

from __future__ import annotations

from pat.contracts.claims import Severity
from pat.contracts.opportunity.hypothesis import HypothesisStatus
from pat.contracts.opportunity.thesis import (
    InvestmentThesis,
    ThesisAudit,
    ThesisAuditIssue,
    ThesisIssue,
)

__all__ = ["audit_thesis"]


def _issue(issue: ThesisIssue, message: str, remedy: str, ref: str | None = None):
    return ThesisAuditIssue(issue=issue, message=message, remedy=remedy, ref=ref)


def audit_thesis(state, thesis: InvestmentThesis) -> ThesisAudit:
    """Percorre a cadeia da tese ate a evidencia e diz onde ela se rompe.

    Pura: nao abre banco e nao chama modelo. Conferir se um `result_id` de
    fato resolve ate os bytes e outro trabalho, e ele ja existe -
    `pat provenance` e `pat provenance-unit`. O que esta funcao confere e a
    integridade REFERENCIAL: a tese cita claims que existem, que apontam para
    enderecos, e que se apoiam em hipoteses que continuam de pe.
    """
    from pat.opportunity.critic import critique

    problemas: list[ThesisAuditIssue] = []
    enderecos: list[str] = []
    claims_conferidos = 0

    for slug in thesis.supporting_claims:
        claim = state.claim(slug)
        if claim is None:
            problemas.append(
                _issue(
                    ThesisIssue.CLAIM_MISSING,
                    f"a tese cita o claim {slug!r}, que nao existe no workspace",
                    "Afirme o claim antes de cita-lo, ou remova a citacao da tese.",
                    slug,
                )
            )
            continue
        claims_conferidos += 1
        if not claim.evidence:
            problemas.append(
                _issue(
                    ThesisIssue.CLAIM_WITHOUT_EVIDENCE,
                    f"o claim {slug!r} nao aponta para evidencia nenhuma",
                    "Ligue o claim a um resultado do motor ou a um trecho verbatim.",
                    slug,
                )
            )
            continue
        enderecos.extend(e.ref for e in claim.evidence)

    critica = critique(state)

    for slug in thesis.supporting_hypotheses:
        hipotese = state.hypothesis(slug)
        if hipotese is None:
            problemas.append(
                _issue(
                    ThesisIssue.HYPOTHESIS_MISSING,
                    f"a tese cita a hipotese {slug!r}, que nao existe",
                    "Abra a hipotese, ou remova a citacao.",
                    slug,
                )
            )
            continue
        if hipotese.status is HypothesisStatus.REJECTED:
            problemas.append(
                _issue(
                    ThesisIssue.HYPOTHESIS_REJECTED,
                    (
                        f"a tese se apoia em {slug!r}, que foi REJEITADA. A hipotese "
                        "caiu depois de a tese ter sido escrita, e nada avisou"
                    ),
                    "Reescreva a tese sem esta hipotese, ou reabra a hipotese com razao.",
                    slug,
                )
            )
        elif hipotese.status is HypothesisStatus.OPEN:
            problemas.append(
                _issue(
                    ThesisIssue.HYPOTHESIS_OPEN,
                    f"a tese se apoia em {slug!r}, que nunca foi testada",
                    f"Teste {slug!r} antes de sustentar a tese nela, ou marque-a "
                    "INCONCLUSIVE e diga isso em `unresolved`.",
                    slug,
                )
            )
        enderecos.extend(e.ref for e in hipotese.supporting)

        duras = [f for f in critica.for_hypothesis(slug) if f.severity is Severity.HARD]
        for achado in duras:
            problemas.append(
                _issue(
                    ThesisIssue.BLOCKING_CRITIQUE,
                    (
                        f"objecao dura em aberto sobre {slug!r}, que a tese usa: "
                        f"{achado.message}"
                    ),
                    achado.remedy,
                    slug,
                )
            )

    if thesis.valuation is not None and state.valuation(thesis.valuation) is None:
        problemas.append(
            _issue(
                ThesisIssue.VALUATION_MISSING,
                f"a tese aponta para a valuation {thesis.valuation!r}, que nao existe",
                "Declare o modelo antes de cita-lo na tese.",
                thesis.valuation,
            )
        )

    abertas = tuple(q.text for _, q in state.agenda.all_open_questions)
    nao_listadas = tuple(
        q for q in abertas if not any(q in u or u in q for u in thesis.unresolved)
    )
    if nao_listadas:
        problemas.append(
            _issue(
                ThesisIssue.OPEN_QUESTIONS_UNLISTED,
                (
                    f"{len(nao_listadas)} pergunta(s) em aberto na agenda nao aparecem "
                    f"em `unresolved`: {list(nao_listadas[:3])}"
                ),
                (
                    "Liste as perguntas em aberto na tese, ou responda-as. O silencio "
                    "sobre elas se le como completude."
                ),
            )
        )

    return ThesisAudit(
        thesis=thesis.slug,
        issues=tuple(problemas),
        claims_checked=claims_conferidos,
        evidence_refs=tuple(dict.fromkeys(enderecos)),
    )

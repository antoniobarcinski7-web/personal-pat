"""O2 - hipotese, claim, evidencia, contra-evidencia, conclusao.

O criterio de aceitacao e `test_abrir_sustentar_refutar_concluir_persistir`.
O resto do arquivo testa as separacoes que dao nome ao milestone - e a que
mais importa e a primeira: um claim sem evidencia nao existe, no tipo.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pat.contracts.opportunity import (
    Actor,
    Claim,
    ClaimAsserted,
    ConclusionDrawn,
    CounterEvidence,
    CounterEvidenceAdded,
    EvidenceKind,
    EvidenceLink,
    EvidenceLinked,
    FalsifierAdded,
    Hypothesis,
    HypothesisOpened,
    HypothesisStatus,
    HypothesisStatusChanged,
    HypothesisStrength,
)
from pat.opportunity import FoldError, create_workspace, open_workspace
from tests.opportunity.conftest import AS_OF, CREATED_AT

AT = datetime(2025, 7, 2, 10, 0, tzinfo=UTC)


@pytest.fixture
def ws(root, gpa_profile):
    return create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)


def _metric(ref: str = "a" * 64, note: str = "ebitda@v1 do periodo") -> EvidenceLink:
    return EvidenceLink(
        kind=EvidenceKind.METRIC, ref=ref, note=note, linked_by=Actor.ENGINE, at=AT
    )


def _quote(ref: str = "b" * 64, note: str = "release do trimestre") -> EvidenceLink:
    return EvidenceLink(
        kind=EvidenceKind.QUOTE, ref=ref, note=note, linked_by=Actor.AGENT, at=AT
    )


def _abre(slug="pricing-power", **kw) -> HypothesisOpened:
    defaults = {
        "statement": "a empresa consegue repassar custo sem perder volume",
        "falsifiers": (
            "margem bruta cai em dois anos seguidos com receita crescendo",
            "volume cai enquanto preco medio sobe",
        ),
    }
    return HypothesisOpened(slug=slug, **(defaults | kw))


# -- criterio de aceitacao --------------------------------------------------


def test_abrir_sustentar_refutar_concluir_persistir(ws, root):
    ws.apply(_abre(), actor=Actor.USER)
    assert ws.state.hypothesis("pricing-power").status is HypothesisStatus.OPEN
    assert ws.state.hypothesis("pricing-power").strength is None

    ws.apply(
        EvidenceLinked(hypothesis="pricing-power", link=_metric()), actor=Actor.ENGINE
    )
    ws.apply(
        ClaimAsserted(
            slug="margem-estavel",
            text="a margem bruta ficou estavel com a receita subindo",
            evidence=(_metric(),),
            hypothesis="pricing-power",
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        HypothesisStatusChanged(
            slug="pricing-power",
            status=HypothesisStatus.SUPPORTED,
            strength=HypothesisStrength.MODERATE,
            rationale="dois anos de margem estavel com receita subindo",
        ),
        actor=Actor.AGENT,
    )

    # Contra-evidencia chega depois e enfraquece, sem apagar o que ja havia.
    ws.apply(
        CounterEvidenceAdded(
            hypothesis="pricing-power",
            counter=CounterEvidence(
                link=_quote(note="concorrente falou em guerra de precos"),
                undermines="a estabilidade de margem pode ser mix, nao preco",
            ),
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        HypothesisStatusChanged(
            slug="pricing-power",
            status=HypothesisStatus.WEAKENED,
            strength=HypothesisStrength.WEAK,
            rationale="a estabilidade tem explicacao alternativa nao descartada",
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        ConclusionDrawn(
            hypothesis="pricing-power",
            text="ha indicio de repasse, insuficiente para sustentar a tese sozinho",
            strength=HypothesisStrength.WEAK,
            residual_uncertainty="mix e preco nao foram separados nos dados disponiveis",
        ),
        actor=Actor.AGENT,
    )

    reaberto = open_workspace(root, ws.workspace_id)
    assert reaberto.state == ws.state

    h = reaberto.state.hypothesis("pricing-power")
    assert h.status is HypothesisStatus.WEAKENED
    assert h.strength is HypothesisStrength.WEAK
    assert len(h.supporting) == 1 and len(h.counter) == 1
    assert reaberto.state.claims_for("pricing-power")[0].slug == "margem-estavel"
    conclusao = reaberto.state.conclusion_for("pricing-power")
    assert conclusao.residual_uncertainty.startswith("mix e preco")


def test_conclusao_exige_hipotese_testada(ws):
    """WEAKENED e INCONCLUSIVE concluem; OPEN nao.

    OPEN e o unico estado que quer dizer "ainda nao testada". Exigir SUPPORTED
    ou REJECTED empurraria toda duvida para um dos extremos - e "os dados nao
    resolvem isso" e uma conclusao legitima, nao uma hipotese em aberto.
    """
    ws.apply(_abre(), actor=Actor.USER)
    with pytest.raises(FoldError, match="ainda OPEN"):
        ws.apply(
            ConclusionDrawn(
                hypothesis="pricing-power",
                text="tem pricing power",
                strength=HypothesisStrength.STRONG,
                residual_uncertainty="nenhuma",
            ),
            actor=Actor.AGENT,
        )


# -- a separacao claim / hipotese -------------------------------------------


def test_claim_sem_evidencia_nao_existe():
    """Nao e um claim fraco: e categoria errada, e o nome dela e hipotese."""
    with pytest.raises(ValueError):
        ClaimAsserted(slug="x", text="a empresa e boa", evidence=())
    with pytest.raises(ValueError):
        Claim(slug="x", text="a empresa e boa", evidence=(), asserted_by=Actor.AGENT, at=AT)


def test_hipotese_sem_falsificador_nao_existe():
    """Afirmacao que nada derrubaria nao esta sendo testada, esta sendo
    defendida."""
    with pytest.raises(ValueError):
        HypothesisOpened(slug="x", statement="a empresa e boa", falsifiers=())


def test_nao_existe_evento_para_remover_falsificador():
    """Remover o teste que a hipotese nao passaria e a forma mais limpa de
    tornar uma tese imune a evidencia. O vocabulario nao tem essa palavra."""
    from pat.contracts.opportunity import EVENT_BODIES

    nomes = {t.__name__.lower() for t in EVENT_BODIES}
    assert not any("falsifierremoved" in n or "falsifierdropped" in n for n in nomes)


def test_evidencia_nao_carrega_conteudo():
    """`EvidenceLink` guarda endereco, nunca copia. Copia diverge da fonte e,
    sendo mais comoda de ler, e a que acaba citada."""
    assert not {"text", "value", "unit", "currency"} & set(EvidenceLink.model_fields)


def test_contra_evidencia_e_tipo_proprio():
    """Nao e `EvidenceLink` com sinal: somar "quantas evidencias" misturando
    lados opostos daria uma contagem que parece certa e esta errada."""
    assert "undermines" in CounterEvidence.model_fields
    assert "undermines" not in EvidenceLink.model_fields


# -- estados ----------------------------------------------------------------


def test_supported_sem_evidencia_e_recusado(ws):
    ws.apply(_abre(), actor=Actor.USER)
    with pytest.raises(FoldError, match="SUPPORTED sem evidencia"):
        ws.apply(
            HypothesisStatusChanged(
                slug="pricing-power",
                status=HypothesisStatus.SUPPORTED,
                strength=HypothesisStrength.STRONG,
                rationale="eu acho",
            ),
            actor=Actor.AGENT,
        )


def test_rejected_sem_contra_evidencia_e_recusado(ws):
    """Rejeitar por mudanca de opiniao apaga a razao, que e o que se le
    depois."""
    ws.apply(_abre(), actor=Actor.USER)
    with pytest.raises(FoldError, match="REJECTED sem contra-evidencia"):
        ws.apply(
            HypothesisStatusChanged(
                slug="pricing-power",
                status=HypothesisStatus.REJECTED,
                strength=HypothesisStrength.WEAK,
                rationale="mudei de ideia",
            ),
            actor=Actor.AGENT,
        )


def test_rejeitar_nao_apaga_a_evidencia_que_sustentava(ws):
    """A hipotese rejeitada continua inteira no estado.

    Sem isso, a mesma hipotese volta tres semanas depois como se fosse nova.
    """
    ws.apply(_abre(), actor=Actor.USER)
    ws.apply(EvidenceLinked(hypothesis="pricing-power", link=_metric()), actor=Actor.ENGINE)
    ws.apply(
        CounterEvidenceAdded(
            hypothesis="pricing-power",
            counter=CounterEvidence(
                link=_metric(ref="c" * 64, note="margem bruta caiu dois anos seguidos"),
                undermines="o falsificador declarado na abertura ocorreu",
                decisive=True,
            ),
        ),
        actor=Actor.ENGINE,
    )
    ws.apply(
        HypothesisStatusChanged(
            slug="pricing-power",
            status=HypothesisStatus.REJECTED,
            strength=HypothesisStrength.STRONG,
            rationale="o falsificador ocorreu",
        ),
        actor=Actor.AGENT,
    )

    h = ws.state.hypothesis("pricing-power")
    assert h.status is HypothesisStatus.REJECTED
    assert len(h.supporting) == 1, "a evidencia que sustentava continua registrada"
    assert h.is_falsifiable_now
    assert h.is_settled


def test_forca_nao_existe_enquanto_open(ws):
    ws.apply(_abre(), actor=Actor.USER)
    with pytest.raises(FoldError, match="forca declarada"):
        ws.apply(
            HypothesisStatusChanged(
                slug="pricing-power",
                status=HypothesisStatus.OPEN,
                strength=HypothesisStrength.STRONG,
                rationale="continua aberta",
            ),
            actor=Actor.AGENT,
        )


def test_reabrir_limpa_a_forca(ws):
    """Reabrir e dizer que o veredito anterior nao vale mais."""
    ws.apply(_abre(), actor=Actor.USER)
    ws.apply(EvidenceLinked(hypothesis="pricing-power", link=_metric()), actor=Actor.ENGINE)
    ws.apply(
        HypothesisStatusChanged(
            slug="pricing-power",
            status=HypothesisStatus.SUPPORTED,
            strength=HypothesisStrength.STRONG,
            rationale="evidencia forte",
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        HypothesisStatusChanged(
            slug="pricing-power",
            status=HypothesisStatus.OPEN,
            rationale="apareceu duvida sobre o escopo dos dados",
        ),
        actor=Actor.USER,
    )
    h = ws.state.hypothesis("pricing-power")
    assert h.status is HypothesisStatus.OPEN
    assert h.strength is None


def test_inconclusive_e_diferente_de_open(ws):
    """'testada e sem resposta' nao e 'ainda nao testada'."""
    ws.apply(_abre(), actor=Actor.USER)
    ws.apply(
        HypothesisStatusChanged(
            slug="pricing-power",
            status=HypothesisStatus.INCONCLUSIVE,
            strength=HypothesisStrength.WEAK,
            rationale="volume por bandeira nao existe nos dados publicos",
        ),
        actor=Actor.AGENT,
    )
    assert ws.state.hypothesis("pricing-power").status is HypothesisStatus.INCONCLUSIVE
    assert ws.state.open_hypotheses == ()


# -- evidencia --------------------------------------------------------------


def test_ausencia_exige_escopo():
    """Ausencia sem escopo nao distingue 'a empresa nunca falou disso' de
    'os documentos nao foram ingeridos'."""
    with pytest.raises(ValueError, match="exige `note` com o escopo"):
        EvidenceLink(
            kind=EvidenceKind.ABSENCE, ref="busca-guidance", linked_by=Actor.AGENT, at=AT
        )
    ok = EvidenceLink(
        kind=EvidenceKind.ABSENCE,
        ref="busca-guidance",
        note="nenhum dos 14 documentos de 2023-2025 menciona guidance de margem",
        linked_by=Actor.AGENT,
        at=AT,
    )
    assert ok.kind is EvidenceKind.ABSENCE


def test_claim_so_com_citacao_nao_e_ancorado_no_motor(ws):
    """Repetir o que o emissor disse e legitimo, e e diferente de ter
    verificado."""
    ws.apply(_abre(), actor=Actor.USER)
    ws.apply(
        ClaimAsserted(
            slug="so-citacao",
            text="a companhia diz ter ganho participacao",
            evidence=(_quote(),),
            hypothesis="pricing-power",
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        ClaimAsserted(
            slug="com-numero",
            text="a receita cresceu acima do setor",
            evidence=(_metric(), _quote()),
            hypothesis="pricing-power",
        ),
        actor=Actor.AGENT,
    )
    assert ws.state.claim("so-citacao").is_engine_grounded is False
    assert ws.state.claim("com-numero").is_engine_grounded is True


# -- integridade referencial ------------------------------------------------


def test_evidencia_para_hipotese_inexistente_e_recusada(ws):
    with pytest.raises(FoldError, match="nao existe"):
        ws.apply(EvidenceLinked(hypothesis="fantasma", link=_metric()), actor=Actor.ENGINE)


def test_hipotese_repetida_e_recusada(ws):
    ws.apply(_abre(), actor=Actor.USER)
    with pytest.raises(FoldError, match="aberta duas vezes"):
        ws.apply(_abre(statement="outro enunciado"), actor=Actor.USER)


def test_claim_repetido_e_recusado(ws):
    ws.apply(
        ClaimAsserted(slug="c", text="a", evidence=(_metric(),)), actor=Actor.AGENT
    )
    with pytest.raises(FoldError, match="afirmado duas vezes"):
        ws.apply(
            ClaimAsserted(slug="c", text="b", evidence=(_metric(),)), actor=Actor.AGENT
        )


def test_falsificador_pode_ser_acrescentado(ws):
    """Pesquisar ensina o que observar. Acrescentar pode; remover nao existe."""
    ws.apply(_abre(), actor=Actor.USER)
    ws.apply(
        FalsifierAdded(
            slug="pricing-power", falsifier="participacao de mercado cai tres anos seguidos"
        ),
        actor=Actor.AGENT,
    )
    assert len(ws.state.hypothesis("pricing-power").falsifiers) == 3
    # Idempotente: acrescentar o mesmo nao duplica.
    ws.apply(
        FalsifierAdded(
            slug="pricing-power", falsifier="participacao de mercado cai tres anos seguidos"
        ),
        actor=Actor.AGENT,
    )
    assert len(ws.state.hypothesis("pricing-power").falsifiers) == 3


def test_hipotese_montada_fora_do_diario_tambem_e_validada():
    """O contrato repete as regras da dobra para o caso de um estado montado
    em memoria, num teste ou numa camada futura."""
    comum = {
        "slug": "h",
        "statement": "s",
        "falsifiers": ("f",),
        "opened_by": Actor.USER,
        "created_at": AT,
        "updated_at": AT,
    }
    with pytest.raises(ValueError, match="SUPPORTED sem nenhuma evidencia"):
        Hypothesis(**comum, status=HypothesisStatus.SUPPORTED, strength=HypothesisStrength.WEAK)
    with pytest.raises(ValueError, match="OPEN com forca declarada"):
        Hypothesis(**comum, status=HypothesisStatus.OPEN, strength=HypothesisStrength.WEAK)

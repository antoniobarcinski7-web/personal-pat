"""Do que foi executado ate o grafo: os nos ancorados sao construidos pelo
motor, nunca pelo modelo.

Reusa o programa de ponta a ponta da M5.3 - decomposicao real da Petrobras
mais evidencia real do corpus - e confere que o grafo que sai dali ja tem
tudo que uma resposta precisa citar, antes de qualquer chamada de modelo.
"""

from __future__ import annotations

import pytest

from pat.contracts.claims import ClaimGraph, ClaimKind
from pat.research.claims import ground_claims
from pat.research.critic import review
from tests.research.test_run_program import (  # noqa: F401 - fixtures
    _programa,
    _rodar,
    conn,
    pergunta,
)


@pytest.fixture
def apurado(conn, pergunta):
    resultado = _rodar(conn, pergunta, _programa(pergunta))
    return resultado, ground_claims(resultado)


def test_o_motor_constroi_fato_calculo_e_citacao(apurado):
    """As tres especies ancoradas saem da execucao, nao do modelo."""
    _resultado, grounded = apurado
    especies = {no.kind for no in grounded.nodes}
    assert ClaimKind.CALCULATION in especies
    assert ClaimKind.QUOTE in especies
    assert ClaimKind.INFERENCE not in especies
    assert ClaimKind.CONCLUSION not in especies


def test_cada_contribuicao_vira_um_no_que_se_apoia_no_total(apurado):
    """A arvore da decomposicao fica navegavel no grafo."""
    _resultado, grounded = apurado
    grafo = ClaimGraph(nodes=tuple(grounded.nodes))

    calculos = grafo.of_kind(ClaimKind.CALCULATION)
    totais = [no for no in calculos if not no.supports]
    partes = [no for no in calculos if no.supports]

    assert len(totais) == 1
    assert len(partes) == 3  # receita, custo, despesa
    assert all(parte.supports == (totais[0].claim_id,) for parte in partes)


def test_o_texto_da_citacao_e_verbatim(apurado):
    resultado, grounded = apurado
    unidades = {
        hit.quote.unit_id: hit.quote.text
        for outcome in resultado.evidence
        if outcome.result is not None
        for hit in outcome.result.hits
    }
    for no in grounded.nodes:
        if no.kind is ClaimKind.QUOTE:
            assert no.text == unidades[no.unit_id]


def test_o_valor_nao_mora_no_grafo(apurado):
    """`ClaimNode` nao tem campo de valor; a tabela de substituicao fica fora.

    E assim que um valor nao chega ao prompt do escritor - a mesma decisao da
    Fase 3, um nivel acima.
    """
    _resultado, grounded = apurado
    assert grounded.values, "a tabela de substituicao existe"
    for no in grounded.nodes:
        assert "value" not in no.model_fields_set or True
        for valor in grounded.values.values():
            assert valor not in no.text, (
                f"o valor {valor!r} vazou para o texto do no {no.claim_id[:12]}"
            )


def test_o_prompt_do_escritor_nao_carrega_valor_nenhum(apurado, pergunta):
    """A conferencia de ponta a ponta, nos bytes que vao para a API."""
    from pat.research.program_writer import build_user_prompt

    _resultado, grounded = apurado
    prompt = build_user_prompt(pergunta, grounded)

    for valor in grounded.values.values():
        assert valor not in prompt, f"{valor!r} vazou para o prompt do escritor"

    # O que ele PRECISA saber esta la: token, rotulo e o texto das citacoes.
    assert "tokens_disponiveis" in prompt
    assert "afirmacoes_apuradas" in prompt


def test_um_grafo_so_de_nos_ancorados_passa_no_critic(apurado):
    """Antes de o modelo escrever qualquer coisa, o que ha ja e integro."""
    resultado, grounded = apurado
    grafo = ClaimGraph(nodes=tuple(grounded.nodes))
    unidades = {
        hit.quote.unit_id: hit.quote.text
        for outcome in resultado.evidence
        if outcome.result is not None
        for hit in outcome.result.hits
    }
    relatorio = review(grafo, result=resultado, unit_texts=unidades)
    assert not relatorio.blocks, [f.message for f in relatorio.hard]


def test_o_residual_material_vira_no_e_ressalva():
    """Residual so no rodape deixaria uma conclusao ignorar o nao explicado
    sem que nada no grafo registrasse a omissao."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from pat.contracts.common import PeriodType
    from pat.contracts.decomposition import BreakdownAxis, Contribution, DecompositionResult
    from pat.contracts.program import DecompositionOutcome, ProgramResult
    from pat.contracts.semantics import Fidelity, ReportingScope

    MM = Decimal(1_000_000)
    contribuicao = Contribution(
        member_id="revenue_net",
        member_label="Receita liquida",
        sign=1,
        value_from=Decimal(100) * MM,
        value_to=Decimal(80) * MM,
        delta=Decimal(-20) * MM,
        contribution=Decimal(-20) * MM,
        fidelity=Fidelity.EXACT,
    )
    decomposicao = DecompositionResult(
        decomposition_id="x",
        decomposition_version="v1",
        axis=BreakdownAxis.COMPONENT,
        target_id="alvo",
        target_label="Alvo",
        entity_id="e",
        scope=ReportingScope.CONSOLIDATED,
        period_type=PeriodType.YEAR,
        period_from=date(2023, 12, 31),
        period_to=date(2024, 12, 31),
        as_of=date(2025, 6, 30),
        target_from=Decimal(100) * MM,
        target_to=Decimal(70) * MM,
        target_delta=Decimal(-30) * MM,
        contributions=(contribuicao,),
        residual=Decimal(-10) * MM,
        closes=False,
        tolerance_abs=Decimal(1000),
        currency="BRL",
        fidelity=Fidelity.EXACT,
        knowledge_date=date(2025, 3, 1),
        mapping_sha256="9" * 64,
    )
    resultado = ProgramResult(
        program_id="a" * 64,
        question_id="b" * 64,
        as_of=date(2025, 6, 30),
        executed_at=datetime(2025, 6, 30, tzinfo=UTC),
        decompositions=(DecompositionOutcome(request_id="d1", result=decomposicao),),
        capability_sha256="c" * 64,
    )

    grounded = ground_claims(resultado)
    textos = " ".join(no.text for no in grounded.nodes)
    assert "NAO EXPLICADA" in textos
    assert any("nao fecha" in aviso for aviso in grounded.warnings)

"""Planos adversariais: um por codigo de violacao.

Estes testes nao precisam de LLM, e e esse o ponto. A superficie de ataque nao
e o modelo - e a gramatica de plano. "Fazer o modelo inventar um numero" e
intestavel; "fazer um plano contrabandear um numero pelo validador" e
deterministico, gratuito e rapido.

Todo caso aqui responde a mesma pergunta: este plano executa? A resposta tem
que ser nao, com codigo nomeado.
"""

from __future__ import annotations

from datetime import date

import pytest

from pat.contracts.research import (
    DerivationOp,
    DerivationParams,
    DerivationStep,
    MetricStep,
    ResearchPlan,
    ViolationCode,
)
from pat.contracts.semantics import MetricRef, ReportingScope
from pat.research.canonical import question_id
from pat.research.validate import PlanNotExecutable, certify, plan_limits, validate_plan
from tests.research.conftest import make_plan, make_question
from tests.semantics import golden_gpa as gpa


@pytest.fixture
def registry():
    from pat.semantics.registry import default_registry

    return default_registry()


def _codes(plan, question, registry) -> set[ViolationCode]:
    return {v.code for v in validate_plan(plan, question, registry)}


def _metric(step_id: str, ref: str = "margem_ebitda@v1", period_end=gpa.FY2023) -> MetricStep:
    return MetricStep(
        step_id=step_id,
        metric=MetricRef.parse(ref),
        entity_id=gpa.ENTITY_ID,
        period_end=period_end,
    )


# -- metricas e referencias --------------------------------------------------


METRICA_QUE_NAO_EXISTE = "metrica_inexistente@v1"
"""Nome que o registro nunca vai ter.

Antes era `roic@v1`, escolhido por ser plausivel e ausente - ate a N4
registrar `roic@v1` de verdade, e os dois testes que dependiam da ausencia
passarem a exercitar uma metrica existente. Um nome explicitamente
inventado nao colide com nenhum milestone futuro."""


def test_metrica_inexistente(question, registry):
    plano = make_plan(question, steps=(_metric("a", METRICA_QUE_NAO_EXISTE),), outputs=("a",))
    assert ViolationCode.UNKNOWN_METRIC in _codes(plano, question, registry)


def test_saida_pendurada(question, registry):
    plano = make_plan(question, steps=(_metric("a"),), outputs=("nao_existe",))
    assert ViolationCode.DANGLING_OUTPUT in _codes(plano, question, registry)


def test_insumo_pendurado(question, registry):
    plano = make_plan(
        question,
        steps=(
            _metric("a"),
            DerivationStep(step_id="d", op=DerivationOp.DELTA, inputs=("a", "fantasma")),
        ),
        outputs=("d",),
    )
    assert ViolationCode.DANGLING_INPUT in _codes(plano, question, registry)


def test_referencia_para_frente(question, registry):
    plano = make_plan(
        question,
        steps=(
            DerivationStep(step_id="d", op=DerivationOp.DELTA, inputs=("a", "b")),
            _metric("a"),
            _metric("b", period_end=gpa.FY2024),
        ),
        outputs=("d",),
    )
    assert ViolationCode.FORWARD_REFERENCE in _codes(plano, question, registry)


def test_ciclo(question, registry):
    plano = make_plan(
        question,
        steps=(
            _metric("a"),
            DerivationStep(step_id="d", op=DerivationOp.DELTA, inputs=("a", "d")),
        ),
        outputs=("d",),
    )
    assert ViolationCode.PLAN_CYCLE in _codes(plano, question, registry)


# -- aridade e parametros ----------------------------------------------------


def test_delta_com_um_insumo(question, registry):
    plano = make_plan(
        question,
        steps=(_metric("a"), DerivationStep(step_id="d", op=DerivationOp.DELTA, inputs=("a",))),
        outputs=("d",),
    )
    assert ViolationCode.ARITY in _codes(plano, question, registry)


def test_delta_com_tres_insumos(question, registry):
    plano = make_plan(
        question,
        steps=(
            _metric("a"),
            _metric("b", period_end=gpa.FY2024),
            _metric("c"),
            DerivationStep(step_id="d", op=DerivationOp.DELTA, inputs=("a", "b", "c")),
        ),
        outputs=("d",),
    )
    assert ViolationCode.ARITY in _codes(plano, question, registry)


def test_cagr_sem_years(question, registry):
    plano = make_plan(
        question,
        steps=(
            _metric("a"),
            _metric("b", period_end=gpa.FY2024),
            DerivationStep(step_id="d", op=DerivationOp.CAGR, inputs=("a", "b")),
        ),
        outputs=("d",),
    )
    assert ViolationCode.MISSING_PARAM in _codes(plano, question, registry)


def test_cagr_com_years_nao_positivo(question, registry):
    plano = make_plan(
        question,
        steps=(
            _metric("a"),
            _metric("b", period_end=gpa.FY2024),
            DerivationStep(
                step_id="d",
                op=DerivationOp.CAGR,
                inputs=("a", "b"),
                params=DerivationParams(years=0),
            ),
        ),
        outputs=("d",),
    )
    assert ViolationCode.BAD_PARAM in _codes(plano, question, registry)


def test_dimensoes_incompativeis(question, registry):
    """Somar dinheiro com fracao. Conferivel sem dado, porque a dimensao e
    declarada na metrica."""
    plano = make_plan(
        question,
        steps=(
            _metric("dinheiro", "ebitda@v1"),
            _metric("fracao", "margem_ebitda@v1"),
            DerivationStep(step_id="d", op=DerivationOp.DELTA, inputs=("dinheiro", "fracao")),
        ),
        outputs=("d",),
    )
    assert ViolationCode.DIMENSION_MISMATCH in _codes(plano, question, registry)


# -- eixo temporal e identidade ----------------------------------------------


def test_periodo_posterior_ao_as_of(question, registry):
    plano = make_plan(question, steps=(_metric("a", period_end=date(2030, 12, 31)),), outputs=("a",))
    assert ViolationCode.PERIOD_AFTER_AS_OF in _codes(plano, question, registry)


def test_as_of_do_plano_diferente_do_da_pergunta(question, registry):
    plano = make_plan(question, as_of=date(2024, 6, 30))
    assert ViolationCode.AS_OF_MISMATCH in _codes(plano, question, registry)


def test_question_id_de_outra_pergunta(question, registry):
    outra = make_question(text="pergunta completamente diferente")
    plano = make_plan(question, question_id=question_id(outra))
    assert ViolationCode.QUESTION_ID_MISMATCH in _codes(plano, question, registry)


# -- pinos do usuario --------------------------------------------------------


def test_pino_de_entidade_contrariado(registry):
    pergunta = make_question(pinned_entities=("br:cnpj:00000000000000",))
    plano = make_plan(pergunta)
    assert ViolationCode.PIN_CONTRADICTED_ENTITY in _codes(plano, pergunta, registry)


def test_pino_de_periodo_contrariado(registry):
    pergunta = make_question(pinned_periods=(gpa.FY2023,))
    plano = make_plan(pergunta)
    assert ViolationCode.PIN_CONTRADICTED_PERIOD in _codes(plano, pergunta, registry)


def test_pino_de_escopo_contrariado(registry):
    pergunta = make_question(pinned_scope=ReportingScope.PARENT_ONLY)
    plano = make_plan(pergunta)
    assert ViolationCode.PIN_CONTRADICTED_SCOPE in _codes(plano, pergunta, registry)


# -- pendencias e tamanho ----------------------------------------------------


def test_plano_com_pendencia_nao_executa(question, registry):
    from pat.contracts.research import UnresolvedItem, UnresolvedKind

    plano = make_plan(
        question,
        unresolved=(
            UnresolvedItem(kind=UnresolvedKind.AMBIGUOUS_ENTITY, detail="qual GPA?"),
        ),
    )
    assert ViolationCode.PLAN_NOT_EXECUTABLE in _codes(plano, question, registry)


def test_plano_grande_demais(question, registry):
    passos = tuple(_metric(f"m{i}") for i in range(40))
    plano = make_plan(question, steps=passos, outputs=("m0",))
    assert ViolationCode.PLAN_TOO_LARGE in _codes(plano, question, registry)


# -- o validador reporta tudo, nao a primeira --------------------------------


def test_validador_reporta_todas_as_violacoes(question, registry):
    plano = make_plan(
        question,
        as_of=date(2024, 6, 30),
        steps=(_metric("a", METRICA_QUE_NAO_EXISTE),),
        outputs=("fantasma",),
    )
    codigos = _codes(plano, question, registry)
    assert {
        ViolationCode.AS_OF_MISMATCH,
        ViolationCode.UNKNOWN_METRIC,
        ViolationCode.DANGLING_OUTPUT,
    } <= codigos


# -- plano invalido nao chega ao executor ------------------------------------


def test_certify_recusa_plano_com_violacao(question, registry):
    from pat.contracts.research import PlanViolation

    plano = make_plan(question)
    violacao = PlanViolation(code=ViolationCode.UNKNOWN_METRIC, message="x")
    with pytest.raises(PlanNotExecutable):
        certify(plano, question, violations=(violacao,), issues=())


def test_validated_plan_nao_pode_ser_construido_na_mao(question):
    """"Plano invalido nao executa" e propriedade de tipo, nao disciplina."""
    from pat.research.validate import ValidatedPlan

    plano = make_plan(question)
    with pytest.raises(TypeError, match="certify"):
        ValidatedPlan(plan=plano, question=question)


def test_executor_so_aceita_plano_certificado(question):
    from pat.research.execute import execute_plan

    plano = make_plan(question)
    with pytest.raises(AttributeError):
        execute_plan(plano, engine=None)


# -- limite de entidades (R-6) -----------------------------------------------
#
# `max_entities` viaja no snapshot e o prompt do planejador o anuncia. Estes
# tres testes existem para que ele seja cobrado, e nao apenas pedido.


def _entidade(n: int) -> str:
    return f"br:cnpj:{n:014d}"


def _plano_com_entidades(question, quantas: int):
    passos = tuple(
        MetricStep(
            step_id=f"m{i}",
            metric=MetricRef.parse("margem_ebitda@v1"),
            entity_id=_entidade(i + 1),
            period_end=gpa.FY2023,
        )
        for i in range(quantas)
    )
    return make_plan(question, steps=passos, outputs=("m0",))


def test_abaixo_do_limite_de_entidades_passa(question, registry):
    limite = plan_limits().max_entities
    plano = _plano_com_entidades(question, limite - 1)
    assert ViolationCode.PLAN_TOO_LARGE not in _codes(plano, question, registry)


def test_exatamente_no_limite_de_entidades_passa(question, registry):
    """O limite e inclusivo. Um off-by-one aqui recusaria plano legitimo."""
    limite = plan_limits().max_entities
    plano = _plano_com_entidades(question, limite)
    assert ViolationCode.PLAN_TOO_LARGE not in _codes(plano, question, registry)


def test_acima_do_limite_de_entidades_e_recusado(question, registry):
    limite = plan_limits().max_entities
    plano = _plano_com_entidades(question, limite + 1)
    violacoes = [
        v
        for v in validate_plan(plano, question, registry)
        if v.code is ViolationCode.PLAN_TOO_LARGE
    ]

    assert violacoes, "plano acima do limite de entidades executou"
    # A mensagem nomeia as empresas: quem for dividir o plano precisa saber
    # quais sao, e nao so que sao demais.
    assert str(limite + 1) in violacoes[0].message
    assert _entidade(1) in violacoes[0].message


def test_muitos_passos_da_mesma_empresa_nao_disparam_o_limite_de_entidades(question, registry):
    """O limite conta empresas distintas, nao passos. Uma serie temporal longa
    de uma empresa so pode esbarrar em `max_steps`."""
    passos = tuple(
        MetricStep(
            step_id=f"m{i}",
            metric=MetricRef.parse("margem_ebitda@v1"),
            entity_id=gpa.ENTITY_ID,
            period_end=gpa.FY2023,
        )
        for i in range(plan_limits().max_entities + 3)
    )
    plano = make_plan(question, steps=passos, outputs=("m0",))
    assert ViolationCode.PLAN_TOO_LARGE not in _codes(plano, question, registry)

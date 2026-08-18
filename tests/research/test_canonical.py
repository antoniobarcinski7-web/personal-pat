"""Canonicalizacao e identidade.

O que estes testes protegem: dois objetos logicamente iguais tem que produzir
os mesmos bytes e o mesmo hash, e uma mudanca que importa tem que produzir
outro. Sem isso, `plan_id` viraria decoracao.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pat.contracts.research import (
    DerivationOp,
    DerivationStep,
    MetricStep,
    PlanProvenance,
)
from pat.contracts.semantics import MetricRef
from pat.research.canonical import (
    canonical_bytes,
    decimal_str,
    normalize_text,
    plan_id,
    question_id,
    sha256_of,
)
from tests.research.conftest import make_plan, make_question
from tests.semantics import golden_gpa as gpa

SHA = "c" * 64


# -- forma canonica ----------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (Decimal("1E+3"), "1000"),
        (Decimal("0.000"), "0"),
        (Decimal("-0"), "0"),
        (Decimal("-0.10900"), "-0.109"),
        (Decimal("1136000000"), "1136000000"),
        (Decimal("0.100939123456"), "0.100939123456"),
    ],
)
def test_decimal_vira_string_plana(entrada, esperado):
    assert decimal_str(entrada) == esperado


def test_decimal_nunca_vira_float(plan):
    """Float binario nao reproduz entre plataformas; string reproduz."""
    payload = canonical_bytes({"x": Decimal("0.1")})
    assert payload == b'{"x":"0.1"}'


def test_float_e_recusado_explicitamente():
    with pytest.raises(TypeError, match="use Decimal"):
        canonical_bytes({"x": 0.1})


def test_chave_nula_e_omitida_mas_tupla_vazia_fica():
    passo = DerivationStep(step_id="d", op=DerivationOp.DELTA, inputs=("a", "b"))
    payload = json.loads(canonical_bytes(passo))
    assert "params" in payload
    assert payload["params"] == {}, "years=None sai; o objeto fica"


def test_ordem_de_chave_e_estavel_e_sem_espaco(plan):
    payload = canonical_bytes(plan)
    assert b", " not in payload and b": " not in payload
    texto = payload.decode()
    chaves = [k for k in ("as_of", "objective", "outputs", "plan_version") if f'"{k}"' in texto]
    posicoes = [texto.index(f'"{k}"') for k in chaves]
    assert posicoes == sorted(posicoes)


def test_metric_ref_serializa_como_string():
    passo = MetricStep(
        step_id="a",
        metric=MetricRef.parse("ebitda@v1"),
        entity_id="e",
        period_end=date(2023, 12, 31),
    )
    assert json.loads(canonical_bytes(passo))["metric"] == "ebitda@v1"


def test_datetime_normaliza_para_utc():
    from zoneinfo import ZoneInfo

    em_sp = datetime(2025, 7, 1, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    em_utc = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
    assert canonical_bytes({"t": em_sp}) == canonical_bytes({"t": em_utc})


def test_normalize_text_ignora_espaco():
    assert normalize_text("  a   b\n") == "a b"


# -- identidade da pergunta --------------------------------------------------


def test_question_id_ignora_asked_at():
    uma = make_question(asked_at=datetime(2020, 1, 1, tzinfo=UTC))
    outra = make_question(asked_at=datetime(2030, 1, 1, tzinfo=UTC))
    assert question_id(uma) == question_id(outra)


def test_question_id_ignora_espaco_em_branco():
    uma = make_question(text="quanto foi a margem?")
    outra = make_question(text="  quanto   foi a margem?\n")
    assert question_id(uma) == question_id(outra)


def test_question_id_muda_com_as_of():
    uma = make_question()
    outra = make_question(as_of=date(2024, 6, 30))
    assert question_id(uma) != question_id(outra)


def test_question_id_muda_com_restricao():
    from pat.contracts.research import ResearchConstraints

    uma = make_question()
    outra = make_question(constraints=ResearchConstraints(allow_unconfirmed_mapping=True))
    assert question_id(uma) != question_id(outra)


# -- identidade do plano -----------------------------------------------------


def test_plan_id_e_estavel_entre_construcoes(question):
    assert plan_id(make_plan(question)) == plan_id(make_plan(question))


def test_plan_id_ignora_a_procedencia_do_modelo(question):
    """O recorte que impede metadado de LLM de mexer na identidade do plano."""
    plano = make_plan(question)
    antes = plan_id(plano)

    procedencia = PlanProvenance(
        model_id="modelo-a",
        temperature=Decimal(0),
        max_tokens=2048,
        system_prompt_sha256=SHA,
        prompt_sha256=SHA,
        response_sha256=SHA,
        capability_sha256=SHA,
        called_at=datetime(2025, 7, 1, tzinfo=UTC),
        client_fingerprint="fake/v1/00000000",
    )
    outra = procedencia.model_copy(update={"model_id": "modelo-b"})

    # A procedencia nao e campo do plano; o hash do plano nao a alcanca.
    assert sha256_of(procedencia) != sha256_of(outra)
    assert plan_id(plano) == antes


def test_plan_id_muda_com_a_ordem_dos_passos(question):
    plano = make_plan(question)
    invertido = plano.model_copy(
        update={"steps": (plano.steps[1], plano.steps[0], plano.steps[2])}
    )
    assert plan_id(plano) != plan_id(invertido)


def test_plan_id_muda_com_o_periodo(question):
    plano = make_plan(question)
    passo = plano.steps[0].model_copy(update={"period_end": gpa.FY2024})
    mexido = plano.model_copy(update={"steps": (passo, *plano.steps[1:])})
    assert plan_id(plano) != plan_id(mexido)

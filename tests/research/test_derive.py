"""As sete operacoes, e sobretudo as recusas.

O que importa aqui nao e que a soma esteja certa - e que nenhuma borda produza
NaN, infinito, zero silencioso ou parcial. Toda recusa tem codigo nomeado.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.research import (
    ComputationFailure,
    ComputationResult,
    DerivationFailureReason,
    DerivationOp,
    DerivationParams,
    DerivedValue,
    ResultKind,
)
from pat.contracts.semantics import Dimension, Fidelity
from pat.research.derive import derive

KD = date(2025, 2, 18)


def _result(
    step_id: str,
    value: str,
    *,
    dimension: Dimension = Dimension.MONEY,
    currency: str | None = "BRL",
    fidelity: Fidelity = Fidelity.EXACT,
    knowledge_date: date = KD,
) -> ComputationResult:
    """Resultado derivado sintetico: exercita a derivacao sem tocar no motor."""
    return ComputationResult(
        result_id=f"{ord(step_id[-1]):02x}" * 32,
        step_id=step_id,
        kind=ResultKind.DERIVED,
        derived=DerivedValue(
            op=DerivationOp.MIN,
            value=Decimal(value),
            dimension=dimension,
            currency=currency,
            derived_from=("f" * 64,),
            fidelity=fidelity,
            knowledge_date=knowledge_date,
        ),
    )


def _derive(op, inputs, **params):
    return derive("d", op, inputs, DerivationParams(**params))


# -- caminhos felizes --------------------------------------------------------


def test_delta_subtrai_na_ordem_declarada():
    out = _derive(DerivationOp.DELTA, [_result("a", "10"), _result("b", "25")])
    assert out.value == Decimal(15)
    assert out.dimension is Dimension.MONEY
    assert out.currency == "BRL"


def test_delta_invertido_da_negativo_e_nao_reordena():
    """Ordem de declaracao e ordem da conta. Reordenar por data seria consertar
    o plano em silencio."""
    out = _derive(DerivationOp.DELTA, [_result("a", "25"), _result("b", "10")])
    assert out.value == Decimal(-15)


def test_delta_pct_e_adimensional():
    out = _derive(DerivationOp.DELTA_PCT, [_result("a", "200"), _result("b", "250")])
    assert out.value == Decimal("0.25")
    assert out.dimension is Dimension.RATIO
    assert out.currency is None


def test_ratio_divide_na_ordem():
    out = _derive(DerivationOp.RATIO, [_result("a", "1"), _result("b", "4")])
    assert out.value == Decimal("0.25")


def test_cagr():
    out = _derive(
        DerivationOp.CAGR, [_result("a", "100"), _result("b", "121")], years=2
    )
    assert out.value.quantize(Decimal("0.0001")) == Decimal("0.1000")
    assert out.dimension is Dimension.RATIO


@pytest.mark.parametrize(
    ("op", "esperado"),
    [(DerivationOp.MIN, "10"), (DerivationOp.MAX, "30"), (DerivationOp.MEAN, "20")],
)
def test_variadicas(op, esperado):
    out = _derive(op, [_result("a", "10"), _result("b", "20"), _result("c", "30")])
    assert out.value == Decimal(esperado)


# -- recusas -----------------------------------------------------------------


def test_denominador_zero_em_ratio():
    out = _derive(DerivationOp.RATIO, [_result("a", "1"), _result("b", "0")])
    assert isinstance(out, ComputationFailure)
    assert out.reason is DerivationFailureReason.ZERO_DENOMINATOR


def test_base_zero_em_delta_pct():
    out = _derive(DerivationOp.DELTA_PCT, [_result("a", "0"), _result("b", "5")])
    assert isinstance(out, ComputationFailure)
    assert out.reason is DerivationFailureReason.ZERO_DENOMINATOR


@pytest.mark.parametrize(("base", "final"), [("-100", "121"), ("0", "121"), ("100", "-121")])
def test_cagr_com_base_ou_final_nao_positivo(base, final):
    """Taxa composta sobre valor nao positivo nao e numero pequeno, e erro de
    categoria. Recusa antes de o Decimal levantar."""
    out = _derive(DerivationOp.CAGR, [_result("a", base), _result("b", final)], years=2)
    assert isinstance(out, ComputationFailure)
    assert out.reason is DerivationFailureReason.NON_POSITIVE_CAGR_BASE


def test_moedas_diferentes_param_a_conta():
    out = _derive(
        DerivationOp.DELTA,
        [_result("a", "10", currency="BRL"), _result("b", "20", currency="USD")],
    )
    assert isinstance(out, ComputationFailure)
    assert out.reason is DerivationFailureReason.CURRENCY_MISMATCH


def test_dimensoes_diferentes_param_a_conta():
    out = _derive(
        DerivationOp.DELTA,
        [
            _result("a", "10"),
            _result("b", "0.2", dimension=Dimension.RATIO, currency=None),
        ],
    )
    assert isinstance(out, ComputationFailure)
    assert out.reason is DerivationFailureReason.DIMENSION_MISMATCH


def test_insumo_que_falhou_nao_vira_zero():
    falha = ComputationFailure(
        step_id="a", reason=DerivationFailureReason.ZERO_DENOMINATOR, message="original"
    )
    out = _derive(DerivationOp.DELTA, [falha, _result("b", "20")])
    assert isinstance(out, ComputationFailure)
    assert out.reason is DerivationFailureReason.INPUT_FAILED
    assert "original" in out.message


def test_aridade_errada():
    out = _derive(DerivationOp.DELTA, [_result("a", "1")])
    assert isinstance(out, ComputationFailure)
    assert out.reason is DerivationFailureReason.ARITY


# -- propagacao --------------------------------------------------------------


def test_fidelidade_da_saida_e_a_mais_fraca():
    out = _derive(
        DerivationOp.DELTA,
        [
            _result("a", "10", fidelity=Fidelity.EXACT),
            _result("b", "20", fidelity=Fidelity.APPROXIMATE),
        ],
    )
    assert out.fidelity is Fidelity.APPROXIMATE


def test_knowledge_date_da_saida_e_a_maior():
    out = _derive(
        DerivationOp.DELTA,
        [
            _result("a", "10", knowledge_date=date(2024, 2, 21)),
            _result("b", "20", knowledge_date=date(2025, 2, 18)),
        ],
    )
    assert out.knowledge_date == date(2025, 2, 18)


def test_derived_from_aponta_para_os_insumos():
    a, b = _result("a", "10"), _result("b", "20")
    out = _derive(DerivationOp.DELTA, [a, b])
    assert out.derived_from == (a.result_id, b.result_id)


def test_nenhuma_operacao_produz_valor_nao_finito():
    """Varredura: toda operacao, sobre valores de borda que ainda sao validos."""
    valores = ["1", "-1", "0.0000001", "1000000000000", "-1000000000000"]
    for op in DerivationOp:
        for esquerda in valores:
            for direita in valores:
                out = _derive(
                    op,
                    [_result("a", esquerda), _result("b", direita)],
                    years=3 if op is DerivationOp.CAGR else None,
                )
                if isinstance(out, ComputationFailure):
                    continue
                assert out.value.is_finite(), f"{op} produziu {out.value}"

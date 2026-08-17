"""Renderizacao, citacoes, regra do digito e avisos.

O caso D-2 vive aqui: o fixture offline do GPA tem os dois checks PASSANDO
(FY2023: D&A 1.136 contra DVA 1.133, dentro da tolerancia de 5%), entao a
falha de check e exercitada com um `MetricResult` sintetico. A divergencia
real de FY2022 - 46% - continua sendo provada onde sempre foi, em
`tests/network/test_semantics_live.py`, contra o que a CVM publica hoje.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pat.contracts.research import ComputationResult, ResultKind, WarningKind
from pat.contracts.semantics import Fidelity
from pat.research.answer import (
    ProseRejected,
    check_prose,
    collect_warnings,
    substitution_table,
)
from pat.research.render import format_money, format_ratio, render_results
from tests.research.conftest import make_metric_result


def _result(metric_result, step_id="passo") -> ComputationResult:
    return ComputationResult(
        result_id="d" * 64,
        step_id=step_id,
        kind=ResultKind.METRIC,
        metric_result=metric_result,
    )


# -- formatacao --------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (Decimal("1813000000"), "R$ 1.81 bn"),
        (Decimal("731000000"), "R$ 731.0 MM"),
        (Decimal("1234.5"), "R$ 1234.50"),
        (Decimal("-436000000"), "R$ -436.0 MM"),
    ],
)
def test_faixas_de_dinheiro(valor, esperado):
    assert format_money(valor, "BRL") == esperado


def test_fracao_vira_percentual():
    assert format_ratio(Decimal("0.100939")) == "10.09%"
    assert format_ratio(Decimal("0.038904")) == "3.89%"


def test_arredondamento_e_half_even_explicito():
    """Sem isto o resultado dependeria do contexto decimal do processo."""
    assert format_ratio(Decimal("0.10125")) == "10.12%"
    assert format_ratio(Decimal("0.10135")) == "10.14%"


def test_renderizacao_nao_muda_o_valor_autoritativo():
    metric = make_metric_result(value=Decimal("1136000000.123456789"))
    resultado = _result(metric)
    (claim,) = render_results((resultado,))

    assert claim.rendered_value == "R$ 1.14 bn"
    assert resultado.value == Decimal("1136000000.123456789")


def test_a_citacao_carrega_result_id_e_o_significado_sem_o_valor():
    (claim,) = render_results((_result(make_metric_result()),))
    assert claim.result_id == "d" * 64
    assert "d_and_a@v1" in claim.means
    assert "1136" not in claim.means, "o rotulo nao pode conter o numero"


# -- regra do digito ---------------------------------------------------------


def test_prosa_com_digito_solto_e_rejeitada():
    tabela = {"{{s:a}}": "10.09%"}
    with pytest.raises(ProseRejected) as exc:
        check_prose("a margem foi de 9,4% em {{s:a}}", tabela)
    assert exc.value.code == "FORBIDDEN_LITERAL"


def test_token_desconhecido_e_rejeitado():
    with pytest.raises(ProseRejected) as exc:
        check_prose("valor {{s:fantasma}}", {"{{s:a}}": "1%"})
    assert exc.value.code == "UNKNOWN_TOKEN"


def test_prosa_sem_citacao_e_rejeitada():
    with pytest.raises(ProseRejected) as exc:
        check_prose("a margem melhorou por alavancagem {{p:fy2023}}", {"{{p:fy2023}}": "FY2023"})
    assert exc.value.code == "NO_CITATION"


def test_prosa_valida_passa():
    tabela = {"{{s:a}}": "10.09%", "{{p:fy2023}}": "FY2023"}
    check_prose("em {{p:fy2023}} a margem foi {{s:a}}", tabela)


def test_rotulo_de_periodo_e_token_e_por_isso_a_regra_nao_tem_excecao():
    """Escrever FY2023 literal e rejeitado; o token equivalente passa."""
    tabela = {"{{s:a}}": "10.09%", "{{p:fy2023}}": "FY2023"}
    with pytest.raises(ProseRejected):
        check_prose("em FY2023 a margem foi {{s:a}}", tabela)
    check_prose("em {{p:fy2023}} a margem foi {{s:a}}", tabela)


def test_tabela_de_substituicao_e_fechada():
    tabela = substitution_table((_result(make_metric_result()),))
    assert set(tabela) == {"{{s:passo}}", "{{p:fy2023}}", "{{p:end_2023}}"}


# -- avisos ------------------------------------------------------------------


def test_check_que_falha_vira_aviso(check_falho):
    """D-2: a falha de check nao pode sumir - o numero sai marcado."""
    avisos = collect_warnings((_result(check_falho),))
    tipos = {a.kind for a in avisos}
    assert WarningKind.CHECK_FAILED in tipos

    (aviso,) = [a for a in avisos if a.kind is WarningKind.CHECK_FAILED]
    assert "proxima_da_retida" in aviso.message
    assert aviso.result_id == "d" * 64


def test_fidelidade_aproximada_vira_aviso():
    metric = make_metric_result(fidelity=Fidelity.APPROXIMATE)
    avisos = collect_warnings((_result(metric),))
    assert WarningKind.APPROXIMATE_FIDELITY in {a.kind for a in avisos}


def test_mapeamento_nao_conferido_vira_aviso():
    metric = make_metric_result(mapping_confirmed=False)
    avisos = collect_warnings((_result(metric),))
    assert WarningKind.UNCONFIRMED_MAPPING in {a.kind for a in avisos}


def test_resultado_limpo_nao_produz_aviso():
    assert collect_warnings((_result(make_metric_result()),)) == ()

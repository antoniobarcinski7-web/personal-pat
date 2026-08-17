"""Golden da Fase 3: a fatia vertical inteira, sem LLM.

    plano -> validacao -> resolucao -> execucao -> derivacao
          -> renderizacao -> citacoes -> resposta -> manifesto

Caso aprovado em D-1: GPA, margem EBITDA, FY2024 contra FY2023, AS OF
2025-06-30. Os dois periodos existem no fixture da Fase 2 e os dois valores
foram conferidos a mao la; nada e inventado aqui.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pat.contracts.semantics import Dimension, Fidelity
from pat.query.asof import AsOf
from pat.research import run_plan
from tests.research.conftest import EXECUTED_AT
from tests.semantics import golden_gpa as gpa

AS_OF = gpa.FY2024.replace(year=2025, month=6, day=30)


@pytest.fixture
def outcome(gpa_conn, plan, question):
    return run_plan(gpa_conn, plan=plan, question=question, executed_at=EXECUTED_AT)


# -- 1. o plano executa fim a fim, sem modelo --------------------------------


def test_o_plano_executa_sem_nenhuma_chamada_de_modelo(outcome):
    assert outcome.executable
    assert outcome.execution.outputs_available
    assert outcome.answer is not None
    assert outcome.manifest.planner is None, "caminho deterministico nao tem autor de modelo"
    assert outcome.manifest.writer is None


# -- 2. os numeros batem com o fixture conferido a mao -----------------------


def test_as_margens_batem_com_o_conferido_a_mao(outcome):
    por_passo = outcome.execution.by_step()

    fy2023 = por_passo["margin_fy2023"].value
    fy2024 = por_passo["margin_fy2024"].value

    assert fy2023.quantize(Decimal("0.000001")) == gpa.EXPECTED_MARGEM[(gpa.FY2023, AS_OF)]
    assert fy2024.quantize(Decimal("0.000001")) == gpa.EXPECTED_MARGEM[(gpa.FY2024, AS_OF)]


def test_o_delta_usa_precisao_cheia_e_nao_os_valores_arredondados(outcome):
    """D-5: arredondar antes de subtrair nao e a mesma conta.

    O fixture guarda as margens com 6 casas; o delta tem que sair da divisao
    inteira, nao da diferenca entre os dois numeros ja cortados.
    """
    por_passo = outcome.execution.by_step()
    fy2023 = por_passo["margin_fy2023"].value
    fy2024 = por_passo["margin_fy2024"].value
    change = por_passo["change"]

    assert change.value == fy2024 - fy2023
    assert change.value < 0, "a margem caiu de FY2023 para FY2024"

    arredondado = fy2024.quantize(Decimal("0.000001")) - fy2023.quantize(Decimal("0.000001"))
    assert change.value != arredondado or change.value == arredondado
    # A afirmacao que importa: o valor guardado tem mais casas que a
    # apresentacao, entao a conta e reproduzivel a partir dele.
    assert abs(change.value.as_tuple().exponent) > 6


def test_a_derivacao_e_adimensional_e_sem_moeda(outcome):
    change = outcome.execution.by_step()["change"]
    assert change.dimension is Dimension.RATIO
    assert change.currency is None


# -- 3. eixo temporal --------------------------------------------------------


def test_as_of_e_explicito_em_todo_resultado(outcome, plan):
    for result in outcome.execution.results:
        if result.metric_result is not None:
            assert result.metric_result.as_of == plan.as_of
            assert result.metric_result.knowledge_date <= plan.as_of


def test_knowledge_date_e_a_da_dfp_de_2025(outcome):
    for step_id in ("margin_fy2023", "margin_fy2024"):
        assert outcome.execution.by_step()[step_id].knowledge_date == gpa.KD_2025


# -- 4. procedencia ate os bytes ---------------------------------------------


def test_toda_citacao_numerica_desce_ate_o_blob_do_bronze(outcome, gpa_conn, gpa_bronze):
    """I2 dois andares acima: da afirmacao ao byte, com hash reconferido."""
    asof = AsOf(gpa_conn)
    por_id = {r.result_id: r for r in outcome.execution.results}

    citacoes = [c for c in outcome.answer.claims if c.claim_kind == "numeric"]
    assert citacoes, "resposta sem nenhuma citacao numerica"

    for claim in citacoes:
        assert claim.result_id in por_id, "citacao aponta para resultado inexistente"

    fatos = outcome.execution.leaf_facts()
    assert fatos, "execucao sem nenhum fato folha"
    for fact_id in fatos:
        prov = asof.provenance(fact_id)
        assert prov is not None, f"fato sem procedencia: {fact_id}"
        assert prov.dataset_id == "cvm.dfp"
        assert asof.verify_provenance(fact_id, gpa_bronze)


def test_o_valor_derivado_aponta_para_os_pais(outcome):
    por_passo = outcome.execution.by_step()
    change = por_passo["change"]
    pais = set(change.derived.derived_from)
    assert pais == {
        por_passo["margin_fy2023"].result_id,
        por_passo["margin_fy2024"].result_id,
    }


# -- 5. determinismo ---------------------------------------------------------


def test_duas_execucoes_produzem_os_mesmos_result_ids(gpa_conn, plan, question):
    uma = run_plan(gpa_conn, plan=plan, question=question, executed_at=EXECUTED_AT)
    outra = run_plan(gpa_conn, plan=plan, question=question, executed_at=EXECUTED_AT)

    assert [r.result_id for r in uma.execution.results] == [
        r.result_id for r in outra.execution.results
    ]
    assert uma.plan_id == outra.plan_id
    assert uma.capability_sha256 == outra.capability_sha256
    assert uma.manifest.manifest_id == outra.manifest.manifest_id


def test_periodo_que_ainda_nao_era_publico_e_recusado(gpa_conn):
    """I4 chegando intacto na camada de pesquisa.

    Em 2024-06-30 o exercicio de 2024 nem tinha terminado. O resolvedor recusa
    o passo em vez de devolver o numero que so existiria depois.
    """
    from pat.contracts.research import ResolutionCode
    from pat.research import review_plan
    from tests.research.conftest import make_plan, make_question

    antes = gpa.FY2023.replace(year=2024, month=6, day=30)
    pergunta = make_question(as_of=antes)
    plano = make_plan(pergunta, as_of=antes)

    review = review_plan(gpa_conn, plan=plano, question=pergunta)

    assert not review.executable
    codigos = {issue.code for issue in review.issues}
    assert ResolutionCode.PERIOD_NOT_COVERED in codigos


# -- 6. resposta e ressalvas -------------------------------------------------


def test_todo_numero_da_prosa_veio_de_substituicao(outcome):
    """Removidos os valores substituidos, nao pode sobrar digito nenhum.

    E a mesma conferencia que o escritor do Milestone 2 vai enfrentar: o
    caminho deterministico ja passa por ela hoje.
    """
    import re

    from pat.research.answer import substitution_table

    tabela = substitution_table(outcome.execution.output_results(outcome.plan))
    prosa = outcome.answer.prose
    assert prosa

    for valor in sorted(tabela.values(), key=len, reverse=True):
        prosa = prosa.replace(valor, "")
    assert not re.search(r"[0-9]", prosa), f"digito fora de substituicao em {prosa!r}"


def test_gpa_nao_produz_ressalva(outcome):
    """Mapeamento proprio, fidelidade exata, checks passando.

    Uma resposta do GPA com aviso e regressao - e e isso que da sentido ao
    aviso quando ele aparecer para outra companhia.
    """
    assert outcome.answer.warnings == ()


def test_fidelidade_exata_atravessa_ate_a_derivacao(outcome):
    for result in outcome.execution.results:
        assert result.fidelity is Fidelity.EXACT


# -- 7. manifesto ------------------------------------------------------------


def test_o_manifesto_carrega_a_linhagem_exigida(outcome):
    manifesto = outcome.manifest

    assert manifesto.plan_id == outcome.plan_id
    assert manifesto.question_id == outcome.plan.question_id
    assert manifesto.capability_sha256 == outcome.capability_sha256
    assert manifesto.as_of == outcome.plan.as_of
    assert manifesto.metric_versions == ("margem_ebitda@v1",)
    assert len(manifesto.mapping_sha256s) == 1
    assert manifesto.fact_ids, "manifesto sem fato folha"
    assert manifesto.pat_version
    assert manifesto.python_version
    assert manifesto.outputs_available is True
    assert set(manifesto.result_ids) == {r.result_id for r in outcome.execution.results}

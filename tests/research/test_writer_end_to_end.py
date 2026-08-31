"""O escritor sobre a fatia vertical de verdade, com o modelo falsificado.

    plano -> validacao -> resolucao -> execucao -> ESCRITOR -> manifesto -> resposta

Warehouse do GPA, bronze de verdade, parser de verdade, motor de verdade. So o
modelo e falso - e e falso de proposito: o que se prova aqui e que o texto
final carrega os numeros do EXECUTOR, e um modelo real tornaria o teste uma
afirmacao sobre o modelo em vez de sobre a montagem.

O par com `test_golden_gpa_research.py` e o ponto: aquele roda o mesmo plano
sem escritor nenhum. Os dois resultados sao comparados numero a numero aqui -
e a unica forma de o escritor mudar um valor seria essa comparacao quebrar.
"""

from __future__ import annotations

import json

import pytest

from pat.research import run_plan
from tests.research.conftest import EXECUTED_AT
from tests.research.test_writer import MODEL, ScriptedLLM

PROSA = (
    "A margem EBITDA consolidada do GPA foi de {{s:margin_fy2024}} no exercicio "
    "{{p:fy2024}}, contra {{s:margin_fy2023}} em {{p:fy2023}} - uma variacao de "
    "{{s:change}}."
)

LEITURA = "A rentabilidade operacional mudou pouco entre os dois exercicios."


@pytest.fixture
def llm():
    return ScriptedLLM(
        json.dumps(
            {
                "prose": PROSA,
                "interpretations": [
                    {"text": LEITURA, "supports": ["margin_fy2023", "margin_fy2024"]}
                ],
            }
        )
    )


@pytest.fixture
def com_escritor(gpa_conn, plan, question, llm):
    return run_plan(
        gpa_conn,
        plan=plan,
        question=question,
        executed_at=EXECUTED_AT,
        llm=llm,
        model=MODEL,
    )


@pytest.fixture
def sem_escritor(gpa_conn, plan, question):
    return run_plan(gpa_conn, plan=plan, question=question, executed_at=EXECUTED_AT)


# -- o escritor recebe o que o executor produziu -----------------------------


def test_o_escritor_e_chamado_uma_vez_com_os_passos_do_executor(com_escritor, llm):
    (chamada,) = llm.calls

    for step_id in ("margin_fy2023", "margin_fy2024", "change"):
        assert step_id in chamada.user, (
            f"{step_id} nao chegou ao escritor: ele nao esta escrevendo sobre o "
            "que o executor calculou"
        )


def test_o_prompt_da_corrida_real_nao_carrega_valor_medido(com_escritor, llm):
    """A mesma checagem do teste unitario, agora sobre numeros que sairam do
    motor de verdade - inclusive o derivado, que nao existe em fixture."""
    (chamada,) = llm.calls

    for claim in com_escritor.answer.claims:
        if claim.claim_kind == "numeric":
            assert claim.rendered_value not in chamada.user


# -- e nao muda nenhum deles -------------------------------------------------


def test_os_numeros_sao_identicos_aos_da_corrida_sem_escritor(com_escritor, sem_escritor):
    """A afirmacao inteira do M3 em um teste.

    Mesmo plano, mesmo warehouse, mesma execucao - um com modelo no caminho e
    outro sem. `result_id` e `rendered_value` iguais nos dois significa que o
    escritor nao tocou em valor nenhum: ele so escolheu as palavras em volta.
    """

    def numeros(outcome):
        return {
            claim.token: (claim.result_id, claim.rendered_value, claim.unit, claim.means)
            for claim in outcome.answer.claims
            if claim.claim_kind == "numeric"
        }

    assert numeros(com_escritor) == numeros(sem_escritor)


def test_os_resultados_da_execucao_sao_identicos(com_escritor, sem_escritor):
    com = {r.step_id: (r.result_id, r.value) for r in com_escritor.execution.results}
    sem = {r.step_id: (r.result_id, r.value) for r in sem_escritor.execution.results}

    assert com == sem


def test_os_avisos_nao_dependem_do_escritor(com_escritor, sem_escritor):
    """As ressalvas sao recolhidas dos resultados por `collect_warnings`, e nao
    escritas pelo modelo - ele nem ve a mensagem delas. Um escritor que
    pudesse suprimir um aviso seria um escritor que esconde."""
    assert com_escritor.answer.warnings == sem_escritor.answer.warnings


# -- o texto final -----------------------------------------------------------


def test_a_prosa_do_modelo_chega_substituida(com_escritor):
    prose = com_escritor.answer.prose

    assert "{{" not in prose, "sobrou token por substituir"
    assert "margem EBITDA consolidada do GPA" in prose

    por_token = {
        claim.token: claim.rendered_value
        for claim in com_escritor.answer.claims
        if claim.claim_kind == "numeric"
    }
    for rendered in por_token.values():
        assert rendered in prose


def test_a_leitura_do_modelo_vira_claim_separada_e_citada(com_escritor):
    """Afirmacao, evidencia e numero ficam distinguiveis na resposta final."""
    leituras = [c for c in com_escritor.answer.claims if c.claim_kind == "interpretive"]
    numericas = [c for c in com_escritor.answer.claims if c.claim_kind == "numeric"]

    (leitura,) = leituras
    assert leitura.text == LEITURA
    assert set(leitura.supports) <= {c.result_id for c in numericas}, (
        "toda leitura tem que apontar para um resultado que a resposta cita"
    )


# -- procedencia -------------------------------------------------------------


def test_o_manifesto_registra_o_autor_do_texto(com_escritor):
    writer = com_escritor.manifest.writer

    assert writer is not None
    assert writer.model_id == MODEL
    assert writer.client_fingerprint == "scripted/v1/00000000"
    assert com_escritor.manifest.planner is None, (
        "o plano veio de arquivo nesta corrida - dizer isso e mais honesto do "
        "que inventar uma procedencia de planejador"
    )


def test_a_procedencia_do_escritor_entra_na_identidade_da_corrida(
    com_escritor, sem_escritor
):
    """Duas corridas do mesmo plano no mesmo instante, uma com autor de texto e
    outra sem, sao corridas diferentes - e o `manifest_id` tem que dizer isso.
    Se batessem, o manifesto estaria afirmando que o texto nao faz parte do que
    foi produzido."""
    assert com_escritor.manifest.manifest_id != sem_escritor.manifest.manifest_id
    assert com_escritor.manifest.result_ids == sem_escritor.manifest.result_ids


def test_a_resposta_aponta_para_o_manifesto_que_registra_seu_autor(com_escritor):
    """A ordem que a raiz de composicao existe para manter: escrever, entao
    manifestar, entao responder."""
    assert com_escritor.answer.manifest_id == com_escritor.manifest.manifest_id


def test_o_texto_cru_do_modelo_fica_recuperavel(com_escritor):
    """Com os tokens ainda por substituir: e o que se quer ler ao auditar uma
    resposta estranha, porque separa o que o modelo redigiu do que a montagem
    preencheu."""
    assert com_escritor.writer.prose == PROSA


# -- sem modelo, nada muda ---------------------------------------------------


def test_sem_llm_o_caminho_continua_sendo_o_de_antes(sem_escritor):
    assert sem_escritor.writer is None
    assert sem_escritor.manifest.writer is None
    assert sem_escritor.answer is not None


def test_llm_sem_model_e_erro_em_vez_de_default_escondido(gpa_conn, plan, question, llm):
    with pytest.raises(ValueError, match="model"):
        run_plan(gpa_conn, plan=plan, question=question, executed_at=EXECUTED_AT, llm=llm)

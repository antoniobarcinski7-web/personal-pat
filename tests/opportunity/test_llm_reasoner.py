"""O raciocinador de modelo, testado sem modelo.

Todo teste aqui usa `FakeLLMClient`, indexado por `prompt_sha256`. Nao ha
`-m network` neste arquivo de proposito: o que se afirma e o que o PAT faz com
a resposta, e isso nao depende de a resposta ter vindo de um modelo de verdade.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from pat.contracts.opportunity import CompanyProfile, ResearchTask
from pat.contracts.opportunity.loop import (
    FindingProposal,
    StepKind,
    StepOutcome,
    TaskVerdict,
)
from pat.contracts.opportunity.agenda import TaskStatus
from pat.opportunity.llm_reasoner import (
    LlmReasoner,
    LlmReasonerError,
    LlmReasonerFailure,
    build_decompose_prompt,
    build_interpret_prompt,
    build_judge_prompt,
    build_plan_prompt,
)
from pat.opportunity.reason import Reasoner, ResearchContext
from pat.research.llm import FakeLLMClient, LLMRequest

MODELO = "claude-opus-5"
AGORA = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def context() -> ResearchContext:
    return ResearchContext(
        company=CompanyProfile(
            entity_id="us:cik:0001065280",
            display_name="NETFLIX INC",
            jurisdiction="US",
        ),
        as_of=date(2026, 8, 30),
        periods=(date(2024, 12, 31), date(2025, 12, 31)),
        metrics_available=("receita_liquida@v1", "ebitda@v1"),
        decompositions_available=(),
        documents=12,
        mandate="vale a pena investir na Netflix hoje?",
        annual_periods=(date(2024, 12, 31), date(2025, 12, 31)),
        instant_periods=(date(2025, 12, 31),),
    )


@pytest.fixture
def task() -> ResearchTask:
    return ResearchTask(
        slug="crescimento",
        objective="A receita cresce?",
        completion_criteria="serie de receita em pelo menos tres exercicios",
        created_at=AGORA,
        updated_at=AGORA,
    )


def _responde(prompt: str, texto: str) -> FakeLLMClient:
    """Cliente que responde `texto` ao prompt que o PAT de fato montaria.

    A chave e o `prompt_sha256` da requisicao real, e nao a ordem da chamada:
    um teste por ordem passaria a mentir no dia em que o raciocinador
    reordenasse as perguntas.
    """
    from pat.opportunity.llm_reasoner import (
        DEFAULT_MAX_TOKENS,
        DEFAULT_TEMPERATURE,
        DEFAULT_TIMEOUT_S,
        SYSTEM_PROMPT,
    )

    request = LLMRequest(
        system=SYSTEM_PROMPT,
        user=prompt,
        model=MODELO,
        max_tokens=DEFAULT_MAX_TOKENS,
        temperature=DEFAULT_TEMPERATURE,
        timeout_s=DEFAULT_TIMEOUT_S,
    )
    return FakeLLMClient({request.prompt_sha256: texto})


# -- o encaixe --------------------------------------------------------------


def test_satisfaz_o_mesmo_protocolo_do_shape_reasoner():
    """O ponto do desenho: trocar quem raciocina e trocar o objeto, nao o laco."""
    reasoner = LlmReasoner(client=FakeLLMClient(), model=MODELO)
    assert isinstance(reasoner, Reasoner)


def test_reasoner_id_carrega_modelo_e_configuracao():
    """Uma conclusao de regra e uma conclusao de modelo tem forcas diferentes, e
    o relatorio precisa dizer qual foi - com a configuracao junto, porque outra
    configuracao de geracao produz outra conclusao."""
    reasoner = LlmReasoner(
        client=FakeLLMClient(fingerprint="fake/v2/abcd1234"), model=MODELO
    )
    assert reasoner.reasoner_id == f"llm/v1/{MODELO}/fake/v2/abcd1234"


# -- decompose --------------------------------------------------------------


def test_decompose_propoe_tarefas_e_hipoteses(context):
    """A hipotese e exatamente o que o `ShapeReasoner` se recusa a propor - uma
    regra proporia a mesma para toda empresa."""
    resposta = json.dumps(
        {
            "tasks": [
                {
                    "slug": "crescimento",
                    "objective": "A receita cresce?",
                    "completion_criteria": "serie de tres exercicios",
                    "priority": "high",
                }
            ],
            "hypotheses": [
                {
                    "slug": "preco-sustenta",
                    "statement": "O crescimento vem de preco, nao de assinantes",
                    "falsifiers": ["assinantes crescendo mais que a receita"],
                }
            ],
        }
    )
    objetivo = "vale a pena investir na Netflix hoje?"
    cliente = _responde(
        build_decompose_prompt(objective=objetivo, context=context), resposta
    )
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    tarefas, hipoteses = reasoner.decompose(objective=objetivo, context=context)

    assert [t.slug for t in tarefas] == ["crescimento"]
    assert hipoteses[0].falsifiers == ("assinantes crescendo mais que a receita",)


def test_decompose_sem_tarefa_recusa_com_motivo(context):
    """Agenda vazia devolvida em silencio se leria como "nao ha o que
    investigar", que e uma afirmacao sobre a empresa. E uma afirmacao sobre a
    resposta do modelo."""
    objetivo = "vale a pena investir na Netflix hoje?"
    cliente = _responde(
        build_decompose_prompt(objective=objetivo, context=context),
        json.dumps({"tasks": [], "hypotheses": []}),
    )
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    with pytest.raises(LlmReasonerError) as exc:
        reasoner.decompose(objective=objetivo, context=context)
    assert exc.value.failure is LlmReasonerFailure.EMPTY_RESULT


# -- plan -------------------------------------------------------------------


def test_plan_devolve_passos_da_gramatica_fechada(context, task):
    resposta = json.dumps(
        {
            "steps": [
                {
                    "step_id": "serie-receita",
                    "kind": "metric",
                    "rationale": "a serie responde se cresce",
                    "ref": "receita_liquida@v1",
                    "period_ends": ["2024-12-31", "2025-12-31"],
                }
            ]
        }
    )
    cliente = _responde(build_plan_prompt(task=task, context=context), resposta)
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    passos = reasoner.plan(task=task, context=context)

    assert passos[0].kind is StepKind.METRIC
    assert passos[0].period_ends == (date(2024, 12, 31), date(2025, 12, 31))


def test_plan_recusa_passo_fora_da_gramatica(context, task):
    """Metrica sem periodo nao pergunta nada, e `StepRequest` ja sabe disso. O
    ponto do teste e que a violacao vira recusa NOMEADA em vez de um passo que
    o motor recebe e nao entende."""
    resposta = json.dumps(
        {
            "steps": [
                {
                    "step_id": "sem-periodo",
                    "kind": "metric",
                    "rationale": "esqueci o periodo",
                    "ref": "receita_liquida@v1",
                }
            ]
        }
    )
    cliente = _responde(build_plan_prompt(task=task, context=context), resposta)
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    with pytest.raises(LlmReasonerError) as exc:
        reasoner.plan(task=task, context=context)
    assert exc.value.failure is LlmReasonerFailure.CONTRACT_VIOLATION


def test_plan_deixa_passar_metrica_que_nao_existe(context, task):
    """Deliberado, e o oposto do que o instinto pede.

    Filtrar aqui esconderia que o raciocinador pediu o que nao existe. Deixado
    passar, o passo vira um `StepOutcome` com `unavailable` - visivel no
    relatorio, no mesmo lugar de toda outra recusa do PAT.
    """
    resposta = json.dumps(
        {
            "steps": [
                {
                    "step_id": "inventada",
                    "kind": "metric",
                    "rationale": "quero esta",
                    "ref": "metrica_inexistente@v1",
                    "period_ends": ["2025-12-31"],
                }
            ]
        }
    )
    cliente = _responde(build_plan_prompt(task=task, context=context), resposta)
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    passos = reasoner.plan(task=task, context=context)

    assert passos[0].ref == "metrica_inexistente@v1"


# -- interpret --------------------------------------------------------------


def test_interpret_devolve_prosa_que_cita_por_id(context, task):
    """O que o `ShapeReasoner` nao faz: uma frase sobre o que os numeros
    querem dizer. Ela nao tem `value` nem `unit` - a garantia e de tipo."""
    outcome = StepOutcome(
        step_id="serie-receita",
        kind=StepKind.METRIC,
        ref="receita_liquida@v1",
        result_ids=("mr_abc123",),
        rendered=("2025-12-31: 39.000.000.000 USD",),
    )
    resposta = json.dumps(
        {
            "findings": [
                {
                    "text": "A receita cresce em todos os exercicios cobertos.",
                    "cites": ["mr_abc123"],
                }
            ]
        }
    )
    cliente = _responde(
        build_interpret_prompt(task=task, outcomes=(outcome,), context=context),
        resposta,
    )
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    achados = reasoner.interpret(task=task, outcomes=(outcome,), context=context)

    assert achados[0].cites == ("mr_abc123",)
    assert not hasattr(achados[0], "value")


def test_a_citacao_inventada_e_barrada_por_quem_ja_barrava(context, task):
    """Este modulo NAO reconfere citacao, e o teste existe para provar que a
    checagem que ja existia continua pegando o que o modelo inventar.

    Duas implementacoes da mesma regra divergem, e a que diverge para o lado
    permissivo e a que ninguem nota.
    """
    from pat.opportunity.research import _verify

    outcome = StepOutcome(
        step_id="serie-receita",
        kind=StepKind.METRIC,
        ref="receita_liquida@v1",
        result_ids=("mr_abc123",),
    )
    inventado = FindingProposal(text="A receita cresce.", cites=("mr_naoexiste",))

    aceitos, rejeitados = _verify((inventado,), (outcome,))

    assert aceitos == ()
    assert rejeitados[0].reason.value == "unknown_citation"


# -- judge ------------------------------------------------------------------


def test_judge_devolve_veredito_do_conjunto_fechado(context, task):
    resposta = json.dumps(
        {
            "status": "complete",
            "rationale": "a serie cobre tres exercicios, como o criterio pedia",
            "blocker_reason": None,
            "blocker_remedy": None,
            "needs_human": False,
        }
    )
    cliente = _responde(
        build_judge_prompt(task=task, outcomes=(), findings=(), context=context),
        resposta,
    )
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    veredito = reasoner.judge(task=task, outcomes=(), findings=(), context=context)

    assert isinstance(veredito, TaskVerdict)
    assert veredito.status is TaskStatus.COMPLETE


def test_judge_com_estado_que_nao_existe_recusa(context, task):
    """`"quase_pronto"` nao e um estado. Aceita-lo seria deixar o modelo
    inventar o vocabulario da agenda."""
    cliente = _responde(
        build_judge_prompt(task=task, outcomes=(), findings=(), context=context),
        json.dumps({"status": "quase_pronto", "rationale": "acho que sim"}),
    )
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    with pytest.raises(LlmReasonerError) as exc:
        reasoner.judge(task=task, outcomes=(), findings=(), context=context)
    assert exc.value.failure is LlmReasonerFailure.CONTRACT_VIOLATION


# -- resposta ruim ----------------------------------------------------------


def test_prosa_em_vez_de_json_recusa_com_motivo(context, task):
    cliente = _responde(
        build_plan_prompt(task=task, context=context),
        "Claro! Aqui esta o plano que eu sugiro para a Netflix:",
    )
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    with pytest.raises(LlmReasonerError) as exc:
        reasoner.plan(task=task, context=context)
    assert exc.value.failure is LlmReasonerFailure.MALFORMED_JSON


def test_resposta_truncada_nao_se_confunde_com_prosa(context, task):
    """Os dois casos levantam `JSONDecodeError`, e os remedios sao opostos:
    "aumente o teto" e "conserte o prompt". Sob um nome so, o achado seria
    intermitente - dependeria do tamanho do plano do dia."""
    from pat.opportunity.llm_reasoner import (
        DEFAULT_MAX_TOKENS,
        DEFAULT_TEMPERATURE,
        DEFAULT_TIMEOUT_S,
        SYSTEM_PROMPT,
    )

    prompt = build_plan_prompt(task=task, context=context)
    request = LLMRequest(
        system=SYSTEM_PROMPT,
        user=prompt,
        model=MODELO,
        max_tokens=DEFAULT_MAX_TOKENS,
        temperature=DEFAULT_TEMPERATURE,
        timeout_s=DEFAULT_TIMEOUT_S,
    )
    cliente = FakeLLMClient(
        {request.prompt_sha256: '{"steps": [{"step_id": "corta'},
        stop_reason="max_tokens",
    )
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    with pytest.raises(LlmReasonerError) as exc:
        reasoner.plan(task=task, context=context)
    assert exc.value.failure is LlmReasonerFailure.TRUNCATED_RESPONSE


def test_lista_no_lugar_do_objeto_recusa(context, task):
    cliente = _responde(build_plan_prompt(task=task, context=context), "[1, 2, 3]")
    reasoner = LlmReasoner(client=cliente, model=MODELO)

    with pytest.raises(LlmReasonerError) as exc:
        reasoner.plan(task=task, context=context)
    assert exc.value.failure is LlmReasonerFailure.NOT_AN_OBJECT


# -- o que o modelo pode ver ------------------------------------------------


def test_o_contexto_enviado_ao_modelo_nao_tem_numero_de_empresa(context):
    """`ResearchContext` nao carrega valor, e o prompt tambem nao pode
    carregar: um contexto com numeros faria o planejamento depender do que ja
    se sabe, e o passo seguinte seria "planejar" um numero ja visto."""
    prompt = build_decompose_prompt(objective="qualquer", context=context)

    # Os unicos numeros no prompt sao datas, contagem de documentos e o gabarito
    # do JSON. Nenhum valor de metrica - eles nem estao no contexto.
    assert "39.000.000.000" not in prompt
    assert "receita_liquida@v1" in prompt
    assert "NETFLIX INC" in prompt


def test_o_prompt_e_puro_e_nao_chama_modelo(context, task):
    """Poder montar o prompt sem cliente e o que permite hasha-lo, versiona-lo
    e olhar para ele num teste - sem gastar chamada."""
    a = build_plan_prompt(task=task, context=context)
    b = build_plan_prompt(task=task, context=context)
    assert a == b

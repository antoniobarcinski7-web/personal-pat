"""O planejador: texto -> `ResearchPlan`, e nada alem disso.

Nenhum modelo real e chamado aqui. O `FakeLLMClient` responde por
`prompt_sha256`, entao todo teste e deterministico e afirma sobre o prompt
que foi de fato montado - nao sobre um prompt suposto.

As duas propriedades que estes testes defendem:

1. **O planejador nao conserta.** Resposta que nao vira plano vira erro
   nomeado, com o `response_sha256` para achar o texto cru depois. Nao ha
   segunda chamada, nao ha plano alternativo, nao ha silencio.
2. **O planejador nao valida semantica.** Metrica inexistente, empresa
   inventada e periodo impossivel passam por aqui se a *forma* estiver certa,
   porque quem recusa com codigo e remedio e o validador. Uma segunda camada
   de validacao aqui divergiria da primeira.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pat.contracts.research import (
    CapabilitySnapshot,
    DerivationCard,
    DerivationOp,
    EntityCard,
    MetricCard,
    OutputKind,
    ResearchPlan,
    ResearchQuestion,
)
from pat.contracts.semantics import Dimension, PeriodKind, ReportingScope
from pat.research.canonical import capability_sha256, plan_id, question_id
from pat.research.llm import FakeLLMClient, LLMRequest
from pat.research.planner import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    SYSTEM_PROMPT,
    PlannerError,
    PlannerFailure,
    build_request,
    plan_question,
)

MODELO = "claude-opus-5"
GPA = "br:cnpj:47508411000156"
CHAMADO_EM = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def snapshot() -> CapabilitySnapshot:
    """Snapshot minimo, montado a mao.

    Nao usa `build_snapshot`: o planejador nao conhece warehouse, e um teste
    que precisasse de banco para exercitar o planejador estaria testando o
    acoplamento que o desenho existe para nao ter.
    """
    return CapabilitySnapshot(
        built_at=datetime(2026, 1, 1, tzinfo=UTC),
        metrics=(
            MetricCard(
                ref="margem_ebitda@v1",
                kind="calculated",
                dimension=Dimension.RATIO,
                period_kind=PeriodKind.FLOW,
                definition="EBITDA sobre receita liquida",
            ),
        ),
        entities=(
            EntityCard(
                entity_id=GPA,
                cod_cvm=14826,
                denom_cia="CIA BRASILEIRA DE DISTRIBUICAO",
                period_ends=(date(2023, 12, 31), date(2024, 12, 31)),
                scopes=(ReportingScope.CONSOLIDATED,),
                has_own_mapping=True,
            ),
        ),
        scopes=(ReportingScope.CONSOLIDATED,),
        derivations=(
            DerivationCard(op=DerivationOp.DELTA, arity="2", output_dimension="igual aos insumos"),
        ),
    )


@pytest.fixture
def question() -> ResearchQuestion:
    return ResearchQuestion(
        text="Como evoluiu a margem EBITDA do GPA de 2023 para 2024?",
        as_of=date(2025, 6, 30),
        asked_at=datetime(2026, 8, 17, 11, 0, tzinfo=UTC),
        requested_output=OutputKind.COMPARISON,
    )


PLANO_VALIDO = {
    "objective": "Comparar a margem EBITDA consolidada do GPA entre FY2023 e FY2024",
    "as_of": "2025-06-30",
    "scope": "consolidated",
    "steps": [
        {
            "step_id": "margin_fy2023",
            "step_kind": "metric",
            "metric": {"name": "margem_ebitda", "version": "v1"},
            "entity_id": GPA,
            "period_end": "2023-12-31",
        },
        {
            "step_id": "margin_fy2024",
            "step_kind": "metric",
            "metric": {"name": "margem_ebitda", "version": "v1"},
            "entity_id": GPA,
            "period_end": "2024-12-31",
        },
        {
            "step_id": "change",
            "step_kind": "derivation",
            "op": "delta",
            "inputs": ["margin_fy2023", "margin_fy2024"],
        },
    ],
    "outputs": ["margin_fy2023", "margin_fy2024", "change"],
}


def _fake(question, snapshot, resposta, **kwargs) -> FakeLLMClient:
    """Duplo ja preparado para responder a requisicao que o planejador fara."""
    fake = FakeLLMClient(**kwargs)
    texto = resposta if isinstance(resposta, str) else json.dumps(resposta)
    fake.register(build_request(question, snapshot, model=MODELO), texto)
    return fake


def _planeja(question, snapshot, resposta, **kwargs):
    fake = _fake(question, snapshot, resposta, **kwargs)
    outcome = plan_question(
        question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM
    )
    return outcome, fake


# -- A. pergunta valida -> plano ---------------------------------------------


def test_pergunta_valida_vira_plano(question, snapshot):
    outcome, _ = _planeja(question, snapshot, PLANO_VALIDO)

    assert isinstance(outcome.plan, ResearchPlan)
    assert outcome.plan.scope is ReportingScope.CONSOLIDATED
    assert outcome.plan.as_of == date(2025, 6, 30)
    assert [s.step_id for s in outcome.plan.steps] == [
        "margin_fy2023",
        "margin_fy2024",
        "change",
    ]
    assert outcome.plan.outputs == ("margin_fy2023", "margin_fy2024", "change")


def test_question_id_e_injetado_e_nao_pedido_ao_modelo(question, snapshot):
    """Pedir um sha256 ao modelo seria pedir um numero que ele nao calcula."""
    outcome, _ = _planeja(question, snapshot, PLANO_VALIDO)

    assert outcome.plan.question_id == question_id(question)
    assert "question_id" not in json.dumps(PLANO_VALIDO)


def test_o_plano_tem_identidade_estavel(question, snapshot):
    """Duas corridas com a mesma resposta dao o mesmo `plan_id` - nada da
    procedencia do modelo entra nele."""
    uma, _ = _planeja(question, snapshot, PLANO_VALIDO)
    outra, _ = _planeja(question, snapshot, PLANO_VALIDO, model_id="outro-modelo")

    assert plan_id(uma.plan) == plan_id(outra.plan)
    assert uma.provenance.model_id != outra.provenance.model_id


# -- B, K. o planejador chama o cliente, uma vez ------------------------------


def test_o_planejador_chama_o_cliente(question, snapshot):
    _, fake = _planeja(question, snapshot, PLANO_VALIDO)

    assert len(fake.calls) == 1
    assert isinstance(fake.calls[0], LLMRequest)


def test_uma_pergunta_e_exatamente_uma_chamada(question, snapshot):
    """Sem laco de ferramenta, sem refinamento em varias voltas: a afirmacao
    'o modelo foi chamado uma vez' e conferivel."""
    fake = _fake(question, snapshot, PLANO_VALIDO)

    plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert len(fake.calls) == 1


# -- C, D, E. o prompt --------------------------------------------------------


def test_o_request_carrega_o_system_prompt_do_modulo(question, snapshot):
    _, fake = _planeja(question, snapshot, PLANO_VALIDO)
    request = fake.calls[0]

    assert request.system == SYSTEM_PROMPT
    assert request.model == MODELO
    assert request.max_tokens == DEFAULT_MAX_TOKENS
    assert request.temperature is DEFAULT_TEMPERATURE is None


def test_o_override_de_temperatura_e_pedido_explicito_e_continua_decimal(
    question, snapshot
):
    """Por default o PAT nao expressa preferencia de amostragem - quem escolhe a
    configuracao de geracao e o adapter. Mas quem pedir continua obrigado a
    `Decimal`, e o pedido aparece na procedencia: valor nao-nulo ali significa
    que foi pedido *e* aplicado."""
    fake = FakeLLMClient()
    pedido = build_request(question, snapshot, model=MODELO, temperature=Decimal("0.7"))
    fake.register(pedido, json.dumps(PLANO_VALIDO))

    outcome = plan_question(
        question,
        snapshot,
        llm=fake,
        model=MODELO,
        temperature=Decimal("0.7"),
        called_at=CHAMADO_EM,
    )

    assert isinstance(fake.calls[0].temperature, Decimal)
    assert outcome.provenance.temperature == Decimal("0.7")


def test_a_procedencia_registra_a_configuracao_de_geracao_do_cliente(
    question, snapshot
):
    """`temperature=None` sozinho nao diz sob que configuracao o plano saiu.
    `client_fingerprint` responde isso - e e ele, e nao um campo `thinking`,
    porque nome de mecanismo de fornecedor nao entra em contrato universal."""
    fake = _fake(question, snapshot, PLANO_VALIDO, fingerprint="fake/v9/deadbeef")
    outcome = plan_question(
        question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM
    )

    assert outcome.provenance.temperature is None
    assert outcome.provenance.client_fingerprint == "fake/v9/deadbeef"


def test_resposta_truncada_falha_com_nome_proprio(question, snapshot):
    """Um JSON cortado no teto de tokens tambem levanta `JSONDecodeError`, e
    sairia como MALFORMED_JSON - o mesmo nome de "o modelo escreveu prosa".
    Remedios opostos sob um nome so e achado intermitente."""
    cortado = json.dumps(PLANO_VALIDO)[:120]
    fake = _fake(question, snapshot, cortado, stop_reason="max_tokens")

    with pytest.raises(PlannerError) as erro:
        plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert erro.value.failure is PlannerFailure.TRUNCATED_RESPONSE
    assert "max_tokens" in str(erro.value)


def test_o_system_prompt_diz_a_separacao_e_as_proibicoes():
    """O prompt e constante de modulo justamente para poder ser conferido."""
    texto = SYSTEM_PROMPT.lower()

    assert "nunca produz numeros" in texto
    assert "nao invente metrica" in texto
    assert "nao invente empresa" in texto
    assert "nao invente periodo" in texto
    assert "nao escreva codigo" in texto
    assert "unico objeto json" in texto
    assert "unresolved" in texto


def test_o_snapshot_chega_ao_prompt(question, snapshot):
    _, fake = _planeja(question, snapshot, PLANO_VALIDO)
    user = fake.calls[0].user

    assert "margem_ebitda@v1" in user
    assert GPA in user
    assert "2024-12-31" in user
    assert question.text in user


def test_o_relogio_do_snapshot_nao_entra_no_prompt(question, snapshot):
    """`built_at` fora do prompt: com ele, duas execucoes identicas teriam
    `prompt_sha256` diferentes e o cache nunca acertaria."""
    outro_relogio = snapshot.model_copy(update={"built_at": datetime(2030, 5, 5, tzinfo=UTC)})

    a = build_request(question, snapshot, model=MODELO)
    b = build_request(question, outro_relogio, model=MODELO)

    assert "2026-01-01" not in a.user
    assert a.user == b.user
    assert a.prompt_sha256 == b.prompt_sha256


def test_o_mesmo_input_produz_o_mesmo_prompt(question, snapshot):
    a = build_request(question, snapshot, model=MODELO)
    b = build_request(question, snapshot, model=MODELO)

    assert a.user == b.user
    assert a.prompt_sha256 == b.prompt_sha256


def test_snapshot_diferente_produz_prompt_diferente(question, snapshot):
    """Se as capacidades mudam, a pergunta feita ao modelo e outra."""
    reduzido = snapshot.model_copy(update={"metrics": ()})

    assert (
        build_request(question, reduzido, model=MODELO).prompt_sha256
        != build_request(question, snapshot, model=MODELO).prompt_sha256
    )


def test_o_prompt_e_serializado_canonicamente_e_nao_por_repr(question, snapshot):
    """`repr()` nao promete estabilidade entre versoes de Python; um prompt
    que muda sozinho e um cache que nunca acerta."""
    user = build_request(question, snapshot, model=MODELO).user

    assert "EntityCard(" not in user
    assert "CapabilitySnapshot(" not in user
    assert '{"concepts"' in user or '"entities"' in user


# -- F, G, L. resposta invalida falha, e falha uma vez so ---------------------


def test_json_malformado_falha_explicitamente(question, snapshot):
    _, fake = None, _fake(question, snapshot, "isto nao e json {")

    with pytest.raises(PlannerError) as erro:
        plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert erro.value.failure is PlannerFailure.MALFORMED_JSON
    assert len(erro.value.response_sha256) == 64


def test_resposta_que_nao_e_objeto_falha(question, snapshot):
    fake = _fake(question, snapshot, json.dumps(["um", "array"]))

    with pytest.raises(PlannerError) as erro:
        plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert erro.value.failure is PlannerFailure.NOT_AN_OBJECT


def test_plano_fora_da_gramatica_falha_no_contrato(question, snapshot):
    """Passo sem `step_kind` nao e passo: o contrato recusa, e o planejador
    nao tenta adivinhar qual tipo era."""
    quebrado = {**PLANO_VALIDO, "steps": [{"step_id": "x", "metric": "margem_ebitda@v1"}]}
    fake = _fake(question, snapshot, quebrado)

    with pytest.raises(PlannerError) as erro:
        plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert erro.value.failure is PlannerFailure.CONTRACT_VIOLATION
    assert erro.value.detail


def test_uma_falha_de_parsing_nao_gera_segunda_chamada(question, snapshot):
    """Retentativa seria um segundo caminho de influencia, que teria que ser
    hasheado e manifestado por conta propria."""
    fake = _fake(question, snapshot, "{")

    with pytest.raises(PlannerError):
        plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert len(fake.calls) == 1


def test_o_planejador_nao_conserta_resposta_com_cerca_de_markdown(question, snapshot):
    """Tirar a cerca seria consertar criativamente. O prompt pede JSON cru; se
    veio outra coisa, o prompt e que precisa mudar - e isso e informacao."""
    cercado = "```json\n" + json.dumps(PLANO_VALIDO) + "\n```"
    fake = _fake(question, snapshot, cercado)

    with pytest.raises(PlannerError) as erro:
        plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert erro.value.failure is PlannerFailure.MALFORMED_JSON


def test_o_modelo_nao_pode_declarar_a_identidade_da_pergunta(question, snapshot):
    """Sobrescrever em silencio esconderia um modelo confuso sobre identidade."""
    fake = _fake(question, snapshot, {**PLANO_VALIDO, "question_id": "a" * 64})

    with pytest.raises(PlannerError) as erro:
        plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert erro.value.failure is PlannerFailure.QUESTION_ID_EMITTED


# -- 10. adversarial: o que e do validador passa por aqui ---------------------


def test_numero_literal_num_passo_nao_tem_onde_entrar(question, snapshot):
    """A gramatica nao tem campo de valor. `extra="forbid"` recusa na
    fronteira - nao por regra de prompt, mas pela forma do tipo."""
    com_valor = {
        **PLANO_VALIDO,
        "steps": [{**PLANO_VALIDO["steps"][0], "value": "19250000000"}],
        "outputs": ["margin_fy2023"],
    }
    fake = _fake(question, snapshot, com_valor)

    with pytest.raises(PlannerError) as erro:
        plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert erro.value.failure is PlannerFailure.CONTRACT_VIOLATION


def test_campo_extra_no_plano_e_recusado(question, snapshot):
    fake = _fake(question, snapshot, {**PLANO_VALIDO, "confidence": "alta"})

    with pytest.raises(PlannerError) as erro:
        plan_question(question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM)

    assert erro.value.failure is PlannerFailure.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    ("nome", "mudanca"),
    [
        ("metrica fora do snapshot", {"metric": {"name": "roic", "version": "v1"}}),
        ("empresa inexistente", {"entity_id": "br:cnpj:00000000000000"}),
        ("periodo nao coberto", {"period_end": "2019-12-31"}),
    ],
)
def test_o_planejador_deixa_a_semantica_para_o_validador(question, snapshot, nome, mudanca):
    """Estrutura certa, conteudo proibido: passa por aqui de proposito.

    Recusar aqui seria uma segunda implementacao das regras do validador - e
    duas implementacoes divergem, normalmente na direcao de a mais permissiva
    deixar passar o que a outra recusaria. Quem recusa com codigo e remedio e
    `validate.py`.
    """
    plano = {
        **PLANO_VALIDO,
        "steps": [{**PLANO_VALIDO["steps"][0], **mudanca}],
        "outputs": ["margin_fy2023"],
    }
    outcome, _ = _planeja(question, snapshot, plano)

    assert isinstance(outcome.plan, ResearchPlan), nome


def test_plano_com_pendencia_e_montado_e_nao_recusado(question, snapshot):
    """Devolver a duvida e o comportamento correto; quem decide que um plano
    com pendencia nao executa e o validador."""
    plano = {
        **PLANO_VALIDO,
        "unresolved": [
            {
                "kind": "ambiguous_entity",
                "detail": "duas companhias chamadas GPA",
                "candidates": [GPA],
            }
        ],
    }
    outcome, _ = _planeja(question, snapshot, plano)

    assert len(outcome.plan.unresolved) == 1
    assert outcome.plan.unresolved[0].detail


# -- H, I. o planejador nao executa e nao le dado -----------------------------


def test_o_planejador_nao_executa_o_plano(question, snapshot):
    """Ele devolve o plano e para: nao ha resultado, nem numero, nem resposta."""
    outcome, _ = _planeja(question, snapshot, PLANO_VALIDO)

    assert not hasattr(outcome, "results")
    assert not hasattr(outcome, "answer")
    assert set(vars(outcome)) == {"plan", "provenance", "response"}


def test_o_planejador_roda_sem_banco_sem_disco_e_sem_ambiente(question, snapshot):
    """A assinatura nao aceita conexao, e o teste inteiro roda sem uma.

    Se algum dia o planejador precisar de banco, este teste quebra - e e a
    unica maneira de a fronteira ser afirmavel em vez de prometida.
    """
    fake = _fake(question, snapshot, PLANO_VALIDO)

    outcome = plan_question(
        question, snapshot, llm=fake, model=MODELO, called_at=CHAMADO_EM
    )

    assert outcome.plan is not None


# -- J. procedencia -----------------------------------------------------------


def test_a_procedencia_corresponde_a_chamada(question, snapshot):
    outcome, fake = _planeja(question, snapshot, PLANO_VALIDO, model_id="claude-opus-5-20260101")
    request, response = fake.calls[0], outcome.response
    prov = outcome.provenance

    assert prov.model_id == "claude-opus-5-20260101"
    assert prov.model_id == response.model_id
    assert prov.temperature == request.temperature
    assert prov.max_tokens == request.max_tokens
    assert prov.system_prompt_sha256 == request.system_prompt_sha256
    assert prov.prompt_sha256 == request.prompt_sha256
    assert prov.response_sha256 == response.response_sha256
    assert prov.cached is False


def test_o_capability_sha_vem_do_snapshot_e_nao_e_inventado(question, snapshot):
    outcome, _ = _planeja(question, snapshot, PLANO_VALIDO)

    assert outcome.provenance.capability_sha256 == capability_sha256(snapshot)


def test_called_at_e_injetavel_para_o_teste_nao_depender_do_relogio(question, snapshot):
    outcome, _ = _planeja(question, snapshot, PLANO_VALIDO)

    assert outcome.provenance.called_at == CHAMADO_EM


def test_a_procedencia_nao_entra_na_identidade_do_plano(question, snapshot):
    """`plan_id` ignora modelo, temperatura e hashes: e assim que metadado de
    LLM fica impedido de mexer na identidade do plano."""
    outcome, _ = _planeja(question, snapshot, PLANO_VALIDO)

    corpo = plan_id(outcome.plan)
    assert outcome.provenance.prompt_sha256 not in corpo
    assert outcome.provenance.response_sha256 not in corpo

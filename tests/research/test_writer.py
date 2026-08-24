"""O escritor: evidencia estruturada -> prosa, sem nunca ver um numero.

O que estes testes provam, em uma frase cada:

- o modelo nao recebe valor nenhum - nem cru, nem formatado;
- o que ele escreve e token, e quem substitui e a montagem deterministica;
- portanto o valor do executor chega ao texto final bit a bit;
- e tudo que o modelo pode fazer de errado tem nome proprio.

Nenhum teste aqui chama a API. O par offline/`-m llm` e o de sempre: este
arquivo prova o comportamento, `test_llm_smoke.py` prova que ele sobrevive ao
mundo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pat.contracts.research import ComputationResult, ResultKind
from pat.contracts.semantics import Fidelity
from pat.research.answer import substitution_table
from pat.research.llm import LLMRefused, LLMRequest, LLMResponse, LLMTimeout
from pat.research.render import format_value
from pat.research.writer import (
    DEFAULT_MAX_TOKENS,
    WriterError,
    WriterFailure,
    build_request,
    build_user_prompt,
    write_prose,
)
from tests.research.conftest import make_metric_result, make_plan, make_question

MODEL = "modelo-de-teste"
CALLED_AT = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)


class ScriptedLLM:
    """Cliente falso que responde o mesmo texto a qualquer prompt.

    `FakeLLMClient` indexa por `prompt_sha256`, o que e a escolha certa para o
    planejador: quem escreve o teste tem a requisicao em maos antes da
    chamada. Para o escritor nao da - no caminho ponta a ponta o prompt so
    existe DEPOIS da execucao, porque e montado a partir dos resultados dela.
    Registrar a resposta exigiria reexecutar o plano so para descobrir a
    chave.

    Guarda as requisicoes recebidas para que "quantas vezes o modelo foi
    chamado" seja asserção e nao suposicao.
    """

    def __init__(self, text: str = "", *, stop_reason: str = "end_turn", raises=None) -> None:
        self._text = text
        self._stop_reason = stop_reason
        self._raises = raises
        self.calls: list[LLMRequest] = []

    @property
    def fingerprint(self) -> str:
        return "scripted/v1/00000000"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if self._raises is not None:
            raise self._raises
        return LLMResponse.for_text(
            self._text,
            model_id=MODEL,
            prompt_sha256=request.prompt_sha256,
            stop_reason=self._stop_reason,
            called_at=CALLED_AT,
        )


def _result(step_id: str, metric_result, *, letra: str) -> ComputationResult:
    return ComputationResult(
        result_id=letra * 64,
        step_id=step_id,
        kind=ResultKind.METRIC,
        metric_result=metric_result,
    )


@pytest.fixture
def resultados():
    """Dois resultados sinteticos. Os valores sao ilustrativos e nenhum teste
    os compara com a fonte - o que se afirma aqui e comportamento."""
    return (
        _result("margin_fy2023", make_metric_result(value=Decimal("1133000000")), letra="a"),
        _result("margin_fy2024", make_metric_result(value=Decimal("1136000000")), letra="b"),
    )


@pytest.fixture
def contexto(resultados):
    question = make_question()
    return question, make_plan(question), resultados, substitution_table(resultados)


def _resposta(prose: str, interpretations=()) -> str:
    return json.dumps({"prose": prose, "interpretations": list(interpretations)})


def _escreve(contexto, texto: str, **kwargs):
    question, plan, resultados, _table = contexto
    llm = ScriptedLLM(texto, **kwargs)
    outcome = write_prose(
        question,
        plan,
        resultados,
        llm=llm,
        model=MODEL,
        capability_sha256="c" * 64,
    )
    return llm, outcome


# ---------------------------------------------------------------------------
# O modelo nao ve numero
# ---------------------------------------------------------------------------


def test_o_prompt_nao_contem_nenhum_valor_renderizado(contexto):
    """A afirmacao central do modulo, conferida e nao prometida.

    Se um `rendered_value` aparecesse no prompt, "o escritor preserva os
    valores do executor" viraria uma promessa sobre o comportamento de um
    modelo. Nao aparecendo, e uma propriedade da montagem.

    So os `{{s:...}}` entram na checagem, e a distincao e o ponto: a tabela de
    substituicao guarda duas coisas diferentes sob a mesma forma. `{{s:...}}`
    e uma GRANDEZA MEDIDA - saiu do motor, e e ela que nao pode chegar ao
    modelo. `{{p:...}}` e um rotulo de periodo, que ja esta na pergunta e no
    objetivo do plano e nao seria removivel dali sem tornar o prompt
    ininteligivel. Periodo e token pela regra do digito, nao por ser segredo.
    """
    question, plan, resultados, table = contexto
    prompt = build_user_prompt(question, plan, resultados, table)

    medidos = [valor for token, valor in table.items() if token.startswith("{{s:")]
    assert medidos, "sem valor medido na tabela o teste nao afirma nada"

    for renderizado in medidos:
        assert renderizado not in prompt, (
            f"o valor {renderizado!r} chegou ao prompt: o escritor passou a ter "
            "um numero para copiar"
        )


def test_o_prompt_nao_contem_o_valor_cru(contexto):
    question, plan, resultados, table = contexto
    prompt = build_user_prompt(question, plan, resultados, table)

    for resultado in resultados:
        assert str(resultado.metric_result.value) not in prompt


def test_o_prompt_oferece_exatamente_os_tokens_permitidos(contexto):
    """O universo de tokens do prompt e o mesmo da tabela de substituicao.

    Conjunto EXATO nos dois sentidos: um token a menos faz o modelo escrever
    prosa sem poder citar um resultado; um a mais e um token que a substituicao
    nao conhece, e o texto seria recusado por UNKNOWN_TOKEN depois de a chamada
    ja ter sido paga.
    """
    question, plan, resultados, table = contexto
    payload = json.loads(
        build_user_prompt(question, plan, resultados, table).split("\n", 1)[1]
    )

    assert set(payload["available_tokens"]) == set(table)


def test_o_prompt_nao_traz_o_numero_do_check_que_falhou():
    """`CHECK_FAILED` carrega `observed` e `expected` na mensagem - numeros do
    motor. So o `kind` vai ao modelo; o aviso inteiro chega ao leitor por
    `build_answer`, que o recolhe direto dos resultados."""
    from pat.contracts.semantics import CheckOutcome

    metric = make_metric_result(
        checks=(
            CheckOutcome(
                check_id="proxima_da_retida",
                status="fail",
                observed=Decimal("1902000000"),
                expected=Decimal("1026000000"),
            ),
        )
    )
    resultados = (_result("margin_fy2024", metric, letra="b"),)
    question = make_question()
    prompt = build_user_prompt(
        question, make_plan(question), resultados, substitution_table(resultados)
    )

    assert "1902000000" not in prompt
    assert "1026000000" not in prompt
    assert "check_failed" in prompt, "a ressalva em si tem que chegar, so nao o numero"


def test_a_fidelidade_aproximada_chega_ao_modelo():
    """Ressalvar exige saber que ha o que ressalvar."""
    metric = make_metric_result(fidelity=Fidelity.APPROXIMATE)
    resultados = (_result("margin_fy2024", metric, letra="b"),)
    question = make_question()
    prompt = build_user_prompt(
        question, make_plan(question), resultados, substitution_table(resultados)
    )

    assert "approximate" in prompt


# ---------------------------------------------------------------------------
# O valor do executor chega intacto
# ---------------------------------------------------------------------------


def test_o_token_vira_o_valor_que_o_executor_produziu(contexto):
    """O caminho inteiro do numero: motor -> render -> substituicao. O modelo
    escreveu um token e nada mais."""
    from pat.research.answer import build_answer

    question, plan, resultados, _table = contexto
    _llm, outcome = _escreve(
        contexto,
        _resposta("A margem consolidada foi de {{s:margin_fy2024}} no exercicio findo."),
    )

    answer = build_answer(
        plan=plan,
        plan_id="e" * 64,
        results=resultados,
        manifest_id="f" * 64,
        prose=outcome.prose,
        extra_claims=outcome.interpretations,
    )

    esperado = format_value(resultados[1])
    assert esperado in answer.prose
    assert "{{s:" not in answer.prose, "todo token tem que ter sido substituido"


def test_a_prosa_sai_do_escritor_com_os_tokens_por_substituir(contexto):
    """O que se quer ler ao auditar e o que o MODELO escreveu, nao o resultado
    da substituicao - senao nao da para distinguir o que ele redigiu do que a
    montagem preencheu."""
    _llm, outcome = _escreve(
        contexto, _resposta("A margem foi de {{s:margin_fy2024}} no periodo.")
    )

    assert "{{s:margin_fy2024}}" in outcome.prose


def test_o_escritor_nao_recebe_conexao_nem_motor():
    """Conferido na assinatura: nao ha por onde passar um warehouse."""
    import inspect

    parametros = set(inspect.signature(write_prose).parameters)
    assert not parametros & {"conn", "engine", "connection", "warehouse"}


# ---------------------------------------------------------------------------
# Afirmacao, evidencia e resultado ficam distinguiveis
# ---------------------------------------------------------------------------


def test_a_leitura_vira_InterpretiveClaim_ligada_ao_result_id(contexto):
    """A ligacao que torna a resposta auditavel: a afirmacao aponta para o
    resultado que a sustenta, e o `result_id` leva ao manifesto e a linhagem."""
    _llm, outcome = _escreve(
        contexto,
        _resposta(
            "A margem foi de {{s:margin_fy2024}}.",
            [{"text": "A rentabilidade operacional ficou estavel entre os dois exercicios.",
              "supports": ["margin_fy2023", "margin_fy2024"]}],
        ),
    )

    (leitura,) = outcome.interpretations
    assert leitura.claim_kind == "interpretive"
    assert leitura.supports == ("a" * 64, "b" * 64)


def test_a_leitura_nao_tem_onde_guardar_um_numero(contexto):
    """`InterpretiveClaim` nao tem campo de valor, e nao deve ganhar um: e o
    que impede uma leitura de virar indistinguivel de um fato medido."""
    _llm, outcome = _escreve(
        contexto,
        _resposta(
            "A margem foi de {{s:margin_fy2024}}.",
            [{"text": "A rentabilidade ficou estavel.", "supports": ["margin_fy2024"]}],
        ),
    )

    (leitura,) = outcome.interpretations
    assert not hasattr(leitura, "value")
    assert not hasattr(leitura, "rendered_value")


def test_leitura_que_cita_passo_inexistente_e_recusada(contexto):
    """Ignorar o item produziria uma afirmacao que PARECE citada. Rastro que
    nao bate e pior do que rastro nenhum."""
    with pytest.raises(WriterError) as exc:
        _escreve(
            contexto,
            _resposta(
                "A margem foi de {{s:margin_fy2024}}.",
                [{"text": "Leitura sem base.", "supports": ["margin_fy2019"]}],
            ),
        )

    assert exc.value.failure is WriterFailure.UNKNOWN_STEP_REFERENCE


def test_uma_resposta_sem_leitura_nenhuma_e_valida(contexto):
    """Lista vazia e melhor do que interpretacao inventada."""
    _llm, outcome = _escreve(contexto, _resposta("A margem foi de {{s:margin_fy2024}}."))

    assert outcome.interpretations == ()


# ---------------------------------------------------------------------------
# A regra do digito, agora contra um modelo
# ---------------------------------------------------------------------------


def test_numero_escrito_pelo_modelo_e_recusado(contexto):
    """O modo de falha que a Fase 3 inteira existe para tornar impossivel."""
    with pytest.raises(WriterError) as exc:
        _escreve(
            contexto, _resposta("A margem foi de 3,89% no exercicio {{s:margin_fy2024}}.")
        )

    assert exc.value.failure is WriterFailure.PROSE_REJECTED
    assert "FORBIDDEN_LITERAL" in str(exc.value)


def test_ano_escrito_por_extenso_tambem_e_recusado(contexto):
    """Periodo e token. Sem isso a regra precisaria de uma lista de numerais
    permitidos, e lista de excecao envelhece mal."""
    with pytest.raises(WriterError) as exc:
        _escreve(contexto, _resposta("Em 2024 a margem foi de {{s:margin_fy2024}}."))

    assert exc.value.failure is WriterFailure.PROSE_REJECTED


def test_token_inventado_e_recusado(contexto):
    with pytest.raises(WriterError) as exc:
        _escreve(contexto, _resposta("A margem foi de {{s:margem_inventada}}."))

    assert exc.value.failure is WriterFailure.PROSE_REJECTED
    assert "UNKNOWN_TOKEN" in str(exc.value)


def test_prosa_sem_citacao_nenhuma_e_recusada(contexto):
    """Interpretacao sem numero citado nao e resposta de research."""
    with pytest.raises(WriterError) as exc:
        _escreve(contexto, _resposta("A companhia manteve rentabilidade estavel."))

    assert exc.value.failure is WriterFailure.PROSE_REJECTED
    assert "NO_CITATION" in str(exc.value)


def test_o_escritor_nao_tenta_de_novo(contexto):
    """Uma chamada, sempre. Retentativa seria um segundo caminho de influencia
    nao hasheado - e "tentar ate passar" e como um portao vira laco de
    amostragem que uma hora deixa passar algo errado-mas-aprovado."""
    question, plan, resultados, _table = contexto
    llm = ScriptedLLM(_resposta("A margem foi de 3,89%."))

    with pytest.raises(WriterError):
        write_prose(
            question, plan, resultados, llm=llm, model=MODEL, capability_sha256="c" * 64
        )

    assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# Tudo que pode dar errado tem nome proprio
# ---------------------------------------------------------------------------


def test_resposta_truncada_tem_nome_proprio(contexto):
    """Antes do parse, de proposito: um JSON cortado ao meio tambem levanta
    `JSONDecodeError` e sairia como MALFORMED_JSON - dois problemas com
    remedios opostos sob um nome so."""
    with pytest.raises(WriterError) as exc:
        _escreve(contexto, '{"prose": "A margem foi de {{s:margin', stop_reason="max_tokens")

    assert exc.value.failure is WriterFailure.TRUNCATED_RESPONSE


def test_json_malformado_tem_nome_proprio(contexto):
    with pytest.raises(WriterError) as exc:
        _escreve(contexto, "Claro! Aqui esta a analise que voce pediu:")

    assert exc.value.failure is WriterFailure.MALFORMED_JSON


def test_resposta_que_nao_e_objeto_tem_nome_proprio(contexto):
    with pytest.raises(WriterError) as exc:
        _escreve(contexto, '["A margem foi de {{s:margin_fy2024}}."]')

    assert exc.value.failure is WriterFailure.NOT_AN_OBJECT


def test_campo_a_mais_na_resposta_e_recusado(contexto):
    """`extra="forbid"` na fronteira: um modelo tentando devolver o valor
    junto tem que falhar aqui, e nao ser ignorado em silencio."""
    with pytest.raises(WriterError) as exc:
        _escreve(
            contexto,
            json.dumps(
                {"prose": "A margem foi de {{s:margin_fy2024}}.", "value": "3,89%"}
            ),
        )

    assert exc.value.failure is WriterFailure.CONTRACT_VIOLATION


def test_prosa_ausente_e_recusada(contexto):
    with pytest.raises(WriterError) as exc:
        _escreve(contexto, json.dumps({"interpretations": []}))

    assert exc.value.failure is WriterFailure.CONTRACT_VIOLATION


def test_o_erro_carrega_o_sha_da_resposta(contexto):
    """O texto cru fica recuperavel pelo hash, entao o diagnostico nao depende
    de a mensagem ter copiado o suficiente da resposta."""
    import hashlib

    texto = "nao e json"
    with pytest.raises(WriterError) as exc:
        _escreve(contexto, texto)

    assert exc.value.response_sha256 == hashlib.sha256(texto.encode()).hexdigest()


@pytest.mark.parametrize(
    "erro",
    [LLMTimeout("estourou o prazo"), LLMRefused("o modelo recusou")],
)
def test_erro_da_porta_sobe_sem_ser_traduzido(contexto, erro):
    """Falha de fronteira nao vira `WriterError`: as duas coisas tem remedios
    diferentes ("o texto do modelo nao serve" contra "a chamada nao
    aconteceu"), e um nome so as confundiria. Nao ha resposta parcial nem
    fallback - o escritor nao inventa prosa quando o modelo nao responde."""
    question, plan, resultados, _table = contexto
    llm = ScriptedLLM(raises=erro)

    with pytest.raises(type(erro)):
        write_prose(
            question, plan, resultados, llm=llm, model=MODEL, capability_sha256="c" * 64
        )


# ---------------------------------------------------------------------------
# Procedencia
# ---------------------------------------------------------------------------


def test_a_procedencia_registra_a_chamada_inteira(contexto):
    _llm, outcome = _escreve(contexto, _resposta("A margem foi de {{s:margin_fy2024}}."))
    provenance = outcome.provenance

    assert provenance.model_id == MODEL
    assert provenance.client_fingerprint == "scripted/v1/00000000"
    assert provenance.max_tokens == DEFAULT_MAX_TOKENS
    assert provenance.capability_sha256 == "c" * 64
    assert provenance.response_sha256 == outcome.response.response_sha256


def test_a_temperatura_nao_e_pedida(contexto):
    """`None` nao e zero implicito: e a afirmacao de que quem escolhe a
    configuracao de geracao e o adapter, identificado pelo fingerprint."""
    _llm, outcome = _escreve(contexto, _resposta("A margem foi de {{s:margin_fy2024}}."))

    assert outcome.provenance.temperature is None


def test_o_instante_vem_da_resposta_e_nao_do_relogio(contexto):
    """M-1: quem produziu a resposta e quem sabe quando ela foi feita. Uma
    resposta servida do cache carrega o instante da chamada ORIGINAL."""
    _llm, outcome = _escreve(contexto, _resposta("A margem foi de {{s:margin_fy2024}}."))

    assert outcome.provenance.called_at == CALLED_AT


def test_o_prompt_e_estavel_entre_montagens(contexto):
    """Duas montagens da mesma evidencia dao o mesmo `prompt_sha256`. Sem isso
    o cache nunca acertaria, e a afirmacao "a mesma entrada gera a mesma
    chamada" nao seria conferivel."""
    question, plan, resultados, table = contexto
    args = (question, plan, resultados, table)

    primeiro = build_request(*args, model=MODEL)
    segundo = build_request(*args, model=MODEL)

    assert primeiro.prompt_sha256 == segundo.prompt_sha256

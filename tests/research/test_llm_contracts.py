"""A fronteira do modelo: contratos e duplo de teste.

Nenhum modelo e chamado aqui, e nenhum sera chamado por estes testes nunca -
e esse o ponto. O `FakeLLMClient` existe para que o planejador (Milestone 2.2)
possa ser testado inteiro sem rede, sem chave de API e sem variancia.

O que estes testes defendem:

1. a identidade de uma requisicao e o seu conteudo, nao o seu endereco;
2. `timeout_s` nao muda a identidade - paciencia nao e pergunta;
3. um hash de procedencia que nao corresponde ao conteudo e rejeitado;
4. o duplo e deterministico e registra o que recebeu, em ordem.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from pat.research.llm import (
    FakeLLMClient,
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
)

TEXTO = "FY2023: 10.09%"
SHA_DE_TEXTO = "8dbe1b0c0e1de0e2f6d40ecc9a1a1c2f30ea1bda67a1d8b1b6c1bb0c1a6a9c0f"


def _request(**overrides) -> LLMRequest:
    base = dict(
        system="Voce planeja pesquisa financeira.",
        user="Qual foi a margem EBITDA do GPA em 2024?",
        model="claude-opus-5",
        max_tokens=1024,
        temperature=Decimal("0"),
    )
    return LLMRequest(**(base | overrides))


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# -- LLMRequest --------------------------------------------------------------


def test_request_se_constroi_com_o_minimo():
    request = _request()

    assert request.model == "claude-opus-5"
    assert request.max_tokens == 1024
    assert request.temperature == Decimal("0")
    assert request.stop_sequences == ()
    assert request.timeout_s == 60


def test_request_e_imutavel_e_recusa_campo_desconhecido():
    """O padrao de fronteira do projeto: `Frozen` com `extra="forbid"`."""
    request = _request()

    with pytest.raises(ValidationError):
        LLMRequest(**{**dict(request), "provider": "anthropic"})

    with pytest.raises(ValidationError):
        request.model = "outro"


@pytest.mark.parametrize(
    "invalido",
    [
        {"max_tokens": 0},
        {"max_tokens": -1},
        {"temperature": Decimal("1.5")},
        {"temperature": Decimal("-0.1")},
        {"timeout_s": 0},
        {"system": ""},
        {"user": ""},
        {"model": ""},
        {"max_tokens": "mil"},
    ],
)
def test_request_rejeita_valor_invalido(invalido):
    with pytest.raises(ValidationError):
        _request(**invalido)


def test_temperatura_e_decimal_e_nao_float():
    """Float nao entra em identidade canonica: o serializador levanta, de
    proposito, porque binario de ponto flutuante nao reproduz entre
    plataformas."""
    with pytest.raises(ValidationError):
        _request(temperature=0.7)

    assert isinstance(_request(temperature=Decimal("0.7")).temperature, Decimal)


# -- identidade da requisicao ------------------------------------------------


def test_a_mesma_pergunta_tem_o_mesmo_sha():
    assert _request().prompt_sha256 == _request().prompt_sha256
    assert len(_request().prompt_sha256) == 64


@pytest.mark.parametrize(
    "mudanca",
    [
        {"system": "Outra instrucao."},
        {"user": "Outra pergunta?"},
        {"model": "claude-sonnet-5"},
        {"max_tokens": 2048},
        {"temperature": Decimal("1")},
        {"stop_sequences": ("FIM",)},
    ],
)
def test_qualquer_mudanca_de_conteudo_muda_o_sha(mudanca):
    """Editar o system prompt invalida o cache: prompt novo e pergunta nova,
    mesmo que o texto do usuario nao tenha mudado."""
    assert _request(**mudanca).prompt_sha256 != _request().prompt_sha256


def test_timeout_nao_entra_na_identidade():
    """Duas chamadas identicas com paciencia diferente sao a mesma pergunta,
    e tem que acertar o mesmo cache."""
    assert _request(timeout_s=5).prompt_sha256 == _request(timeout_s=600).prompt_sha256


def test_system_prompt_tem_hash_proprio():
    """`PlanProvenance` pede os dois separados: permite responder "que
    instrucao produziu este plano" sem carregar a pergunta junto."""
    request = _request()

    assert request.system_prompt_sha256 == _sha(request.system)
    assert request.system_prompt_sha256 != request.prompt_sha256
    # O hash do system nao muda quando so a pergunta muda.
    assert _request(user="outra?").system_prompt_sha256 == request.system_prompt_sha256


# -- LLMResponse -------------------------------------------------------------


def test_response_se_constroi_pelo_construtor_que_calcula_o_hash():
    request = _request()
    response = LLMResponse.for_text(
        TEXTO, model_id="claude-opus-5-20260101", prompt_sha256=request.prompt_sha256
    )

    assert response.text == TEXTO
    assert response.response_sha256 == _sha(TEXTO)
    assert response.stop_reason == "end_turn"
    assert response.cached is False


def test_response_rejeita_hash_que_nao_bate_com_o_texto():
    """Um rastro que nao bate e pior do que rastro nenhum: parece prova."""
    with pytest.raises(ValidationError):
        LLMResponse(
            text=TEXTO,
            model_id="claude-opus-5",
            stop_reason="end_turn",
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
        )


def test_response_rejeita_sha_malformado():
    with pytest.raises(ValidationError):
        LLMResponse(
            text=TEXTO,
            model_id="claude-opus-5",
            stop_reason="end_turn",
            prompt_sha256="curto-demais",
            response_sha256=_sha(TEXTO),
        )


def test_response_registra_quem_de_fato_respondeu():
    """Alias de modelo resolve para uma versao concreta, e e a versao que
    entra no manifesto."""
    response = LLMResponse.for_text(
        TEXTO, model_id="claude-opus-5-20260101", prompt_sha256="a" * 64
    )

    assert response.model_id == "claude-opus-5-20260101"


def test_texto_vazio_e_resposta_valida():
    """Resposta vazia e um problema do planejador, nao da fronteira: quem
    decide que um plano vazio nao serve e o validador."""
    response = LLMResponse.for_text("", model_id="m", prompt_sha256="a" * 64)

    assert response.text == ""
    assert response.response_sha256 == _sha("")


# -- FakeLLMClient -----------------------------------------------------------


def test_o_duplo_satisfaz_o_protocolo():
    assert isinstance(FakeLLMClient(), LLMClient)


def test_devolve_exatamente_a_resposta_configurada():
    request = _request()
    fake = FakeLLMClient()
    fake.register(request, TEXTO)

    response = fake.complete(request)

    assert response.text == TEXTO
    assert response.prompt_sha256 == request.prompt_sha256
    assert response.response_sha256 == _sha(TEXTO)


def test_pode_ser_configurado_por_sha_direto():
    request = _request()
    fake = FakeLLMClient({request.prompt_sha256: TEXTO})

    assert fake.complete(request).text == TEXTO


def test_registra_as_requisicoes_na_ordem():
    primeira, segunda = _request(), _request(user="E em 2023?")
    fake = FakeLLMClient()
    fake.register(primeira, "a")
    fake.register(segunda, "b")

    fake.complete(segunda)
    fake.complete(primeira)

    assert [r.user for r in fake.calls] == [segunda.user, primeira.user]
    assert len(fake.calls) == 2


def test_o_registro_de_chamadas_nao_pode_ser_alterado_por_fora():
    fake = FakeLLMClient()
    assert isinstance(fake.calls, tuple)


def test_chamadas_repetidas_sao_identicas():
    """Determinismo: sem relogio, sem aleatoriedade, sem estado que evolua."""
    request = _request()
    fake = FakeLLMClient()
    fake.register(request, TEXTO)

    respostas = [fake.complete(request) for _ in range(5)]

    assert len({r.text for r in respostas}) == 1
    assert len({r.response_sha256 for r in respostas}) == 1
    assert len({r.model_id for r in respostas}) == 1
    assert len(fake.calls) == 5  # mas cada chamada continua sendo registrada


def test_duas_instancias_com_a_mesma_tabela_respondem_igual():
    request = _request()
    tabela = {request.prompt_sha256: TEXTO}

    uma = FakeLLMClient(tabela).complete(request)
    outra = FakeLLMClient(tabela).complete(request)

    assert uma == outra


def test_prompt_nao_registrado_falha_dizendo_o_que_fazer():
    """Falhar alto e melhor do que devolver um texto plausivel: um duplo que
    improvisa resposta transforma teste de planejador em teste de nada."""
    fake = FakeLLMClient()

    with pytest.raises(LLMError, match="register"):
        fake.complete(_request())

    # A chamada aconteceu e ficou registrada, mesmo tendo falhado.
    assert len(fake.calls) == 1


def test_o_duplo_ecoa_o_modelo_pedido_por_default():
    request = _request(model="claude-sonnet-5")
    fake = FakeLLMClient()
    fake.register(request, TEXTO)

    assert fake.complete(request).model_id == "claude-sonnet-5"


def test_o_duplo_pode_simular_alias_resolvido():
    """Provider real devolve versao concreta; o duplo precisa saber imitar
    isso para que o teste de manifesto seja honesto."""
    request = _request()
    fake = FakeLLMClient(model_id="claude-opus-5-20260101")
    fake.register(request, TEXTO)

    assert fake.complete(request).model_id == "claude-opus-5-20260101"


def test_o_duplo_nunca_marca_resposta_como_vinda_de_cache():
    """O cache e outro objeto, atras do mesmo Protocol (Milestone 2.2). Um
    duplo que se dissesse cache esconderia a diferenca entre os dois."""
    request = _request()
    fake = FakeLLMClient()
    fake.register(request, TEXTO)

    assert fake.complete(request).cached is False

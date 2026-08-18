"""O adapter: traduz o provider para o contrato, nunca o contrário.

Nenhum teste aqui toca a rede ou exige credencial — o SDK é injetado. O que
está sendo verificado não é a API da Anthropic (isso é `-m network`), e sim as
regras do PAT sobre como falar com ela:

1. nenhum retry implícito;
2. `temperature` não-nulo nunca é descartado em silêncio;
3. erro de provider vira exceção nomeada do PAT, sem tipo de biblioteca vazando;
4. o `fingerprint` muda quando o perfil de geração muda;
5. `called_at` é o instante em que a chamada começou.
"""

from __future__ import annotations

from decimal import Decimal

import anthropic
import pytest

from pat.research.llm import (
    LLMClient,
    LLMRefused,
    LLMRequest,
    LLMTimeout,
    LLMTransportError,
)
from pat.research.llm.anthropic import (
    ADAPTER_VERSION,
    ENV_API_KEY,
    GENERATION_PROFILE,
    PROVIDER,
    AnthropicClient,
)

TEXTO = '{"objective": "x"}'


def _request(**overrides) -> LLMRequest:
    base = dict(
        system="Voce planeja pesquisa financeira.",
        user="Qual foi a margem EBITDA do GPA em 2024?",
        model="claude-opus-5",
        max_tokens=16384,
    )
    return LLMRequest(**(base | overrides))


class _Bloco:
    def __init__(self, tipo: str, text: str = "") -> None:
        self.type = tipo
        self.text = text


class _Mensagem:
    def __init__(self, blocos, *, model="claude-opus-5-20260101", stop_reason="end_turn"):
        self.content = blocos
        self.model = model
        self.stop_reason = stop_reason


class _SDKFalso:
    """Duplo do SDK. Registra o payload recebido para que o teste afirme sobre
    o que foi de fato enviado, e não sobre o que se supõe ter sido."""

    def __init__(self, resposta=None, erro: Exception | None = None) -> None:
        self._resposta = resposta or _Mensagem([_Bloco("text", TEXTO)])
        self._erro = erro
        self.payloads: list[dict] = []
        self.messages = self

    def create(self, **payload):
        self.payloads.append(payload)
        if self._erro is not None:
            raise self._erro
        return self._resposta


def _cliente(**kwargs) -> AnthropicClient:
    return AnthropicClient(sdk=_SDKFalso(**kwargs))


# -- contrato ----------------------------------------------------------------


def test_satisfaz_o_protocolo():
    assert isinstance(_cliente(), LLMClient)


def test_devolve_o_texto_e_fecha_os_hashes():
    request = _request()
    response = _cliente().complete(request)

    assert response.text == TEXTO
    assert response.prompt_sha256 == request.prompt_sha256
    assert response.cached is False


def test_registra_quem_de_fato_respondeu():
    """Alias resolve para versão concreta, e é a versão que vai ao manifesto."""
    assert _cliente().complete(_request()).model_id == "claude-opus-5-20260101"


def test_so_o_texto_visivel_entra_na_resposta():
    """Misturar deliberação no mesmo string quebraria `json.loads` de um jeito
    difícil de diagnosticar."""
    sdk = _SDKFalso(
        resposta=_Mensagem(
            [_Bloco("thinking", "raciocinio"), _Bloco("text", TEXTO)]
        )
    )

    assert AnthropicClient(sdk=sdk).complete(_request()).text == TEXTO


def test_called_at_e_o_instante_da_chamada():
    from datetime import UTC, datetime

    antes = datetime.now(UTC)
    response = _cliente().complete(_request())

    assert antes <= response.called_at <= datetime.now(UTC)
    assert response.called_at.tzinfo is not None


# -- o que é enviado ---------------------------------------------------------


def test_o_payload_carrega_o_contrato():
    sdk = _SDKFalso()
    request = _request()
    AnthropicClient(sdk=sdk).complete(request)

    enviado = sdk.payloads[0]
    assert enviado["model"] == request.model
    assert enviado["max_tokens"] == request.max_tokens
    assert enviado["system"] == request.system
    assert enviado["messages"] == [{"role": "user", "content": request.user}]
    assert enviado["timeout"] == request.timeout_s


def test_temperatura_nao_pedida_nao_e_enviada():
    """Default do PAT é não expressar preferência de amostragem. Mandar zero
    aqui seria o adapter inventando um pedido que o contrato não fez."""
    sdk = _SDKFalso()
    AnthropicClient(sdk=sdk).complete(_request())

    assert "temperature" not in sdk.payloads[0]


def test_temperatura_pedida_e_enviada():
    sdk = _SDKFalso()
    AnthropicClient(sdk=sdk).complete(_request(temperature=Decimal("0.7")))

    assert sdk.payloads[0]["temperature"] == 0.7


def test_stop_sequences_vazio_nao_e_enviado():
    sdk = _SDKFalso()
    AnthropicClient(sdk=sdk).complete(_request())

    assert "stop_sequences" not in sdk.payloads[0]


def test_o_perfil_v1_nao_configura_raciocinio():
    """Não configurar é decisão do v1, coberta por ADAPTER_VERSION — e não a
    ausência de uma decisão."""
    sdk = _SDKFalso()
    AnthropicClient(sdk=sdk).complete(_request())

    assert "thinking" not in sdk.payloads[0]
    assert GENERATION_PROFILE["thinking"] == "provider_default"


# -- sem retry ---------------------------------------------------------------


def test_uma_chamada_e_uma_chamada():
    sdk = _SDKFalso()
    AnthropicClient(sdk=sdk).complete(_request())

    assert len(sdk.payloads) == 1


def test_erro_nao_gera_segunda_tentativa():
    """Retentativa é um segundo caminho de influência: teria que ser hasheada e
    manifestada por conta própria."""
    sdk = _SDKFalso(erro=anthropic.APIConnectionError(request=None))

    with pytest.raises(LLMTransportError):
        AnthropicClient(sdk=sdk).complete(_request())

    assert len(sdk.payloads) == 1


def test_o_adapter_desliga_o_retry_do_sdk():
    """O SDK tenta duas vezes por default. O código tem que dizer o contrário
    explicitamente — conferido no texto, porque o default some numa leitura
    distraída."""
    from pathlib import Path

    fonte = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "pat"
        / "research"
        / "llm"
        / "anthropic.py"
    ).read_text(encoding="utf-8")

    assert "max_retries=0" in fonte
    assert GENERATION_PROFILE["retries"] == "disabled"


# -- mapeamento de erro ------------------------------------------------------


def test_timeout_vira_erro_nomeado_do_pat():
    sdk = _SDKFalso(erro=anthropic.APITimeoutError(request=None))

    with pytest.raises(LLMTimeout):
        AnthropicClient(sdk=sdk).complete(_request())


def test_recusa_do_modelo_e_distinta_de_erro_de_transporte():
    """A chamada funcionou; a resposta foi uma negativa."""
    sdk = _SDKFalso(resposta=_Mensagem([_Bloco("text", "")], stop_reason="refusal"))

    with pytest.raises(LLMRefused):
        AnthropicClient(sdk=sdk).complete(_request())


def test_nenhum_tipo_do_sdk_vaza_para_cima():
    """Quem trata o erro não deveria precisar importar o SDK para saber o que
    aconteceu."""
    sdk = _SDKFalso(erro=anthropic.APIConnectionError(request=None))

    with pytest.raises(LLMTransportError) as erro:
        AnthropicClient(sdk=sdk).complete(_request())

    assert not isinstance(erro.value, anthropic.AnthropicError)


def test_temperatura_recusada_pelo_modelo_falha_dizendo_o_porque():
    """A regra dura do adapter: um override não-nulo nunca é descartado em
    silêncio. Reenviar sem o campo faria a procedência registrar um pedido que
    não foi atendido."""

    class _Status(anthropic.APIStatusError):
        def __init__(self):
            self.status_code = 400
            Exception.__init__(self, "temperature is not supported")

    sdk = _SDKFalso(erro=_Status())

    with pytest.raises(LLMTransportError, match="temperature"):
        AnthropicClient(sdk=sdk).complete(_request(temperature=Decimal("0.7")))

    assert len(sdk.payloads) == 1  # não reenviou sem o parâmetro


# -- credencial --------------------------------------------------------------


def test_sem_chave_falha_em_vez_de_degradar(monkeypatch):
    """O adapter não inventa credencial e não cai para um modo degradado."""
    monkeypatch.delenv(ENV_API_KEY, raising=False)

    with pytest.raises(LLMTransportError, match=ENV_API_KEY):
        AnthropicClient()


def test_a_variavel_de_ambiente_e_a_convencao_do_fornecedor():
    assert ENV_API_KEY == "ANTHROPIC_API_KEY"


# -- fingerprint -------------------------------------------------------------


def test_o_fingerprint_tem_provider_versao_e_hash_do_perfil():
    partes = _cliente().fingerprint.split("/")

    assert partes[0] == PROVIDER
    assert partes[1] == ADAPTER_VERSION
    assert len(partes[2]) == 8


def test_o_fingerprint_muda_quando_o_perfil_muda(monkeypatch):
    """É isto que faz uma mudança de configuração invalidar o cache em vez de
    servir silenciosamente a resposta da configuração antiga."""
    antes = _cliente().fingerprint

    monkeypatch.setitem(GENERATION_PROFILE, "thinking", "disabled")

    assert _cliente().fingerprint != antes


def test_o_fingerprint_e_estavel_entre_instancias():
    assert _cliente().fingerprint == _cliente().fingerprint

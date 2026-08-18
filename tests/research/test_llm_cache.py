"""O cache: acerto, erro, e o que a chave precisa distinguir.

A propriedade central defendida aqui nao e "o cache economiza chamada" - e que
ele **nao serve a resposta de uma configuracao para uma chamada feita sob
outra**. Esse erro nao apareceria em nenhum teste ingenuo de acerto/erro,
porque o rastro resultante e internamente consistente: o `response_sha256`
bate com o texto servido. Era exatamente o buraco que a auditoria pre-2.3
encontrou, e os testes de fingerprint aqui sao o que impede de voltar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pat.research.llm import FakeLLMClient, LLMClient, LLMRequest
from pat.research.llm.cache import (
    CALL_VERSION,
    CachedLLMClient,
    InMemoryLLMCache,
    LLMCache,
    call_sha256,
)
from pat.research.llm.store import CachedCall

TEXTO = '{"objective": "x"}'
CHAMADO_EM = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
DEPOIS = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)


def _request(**overrides) -> LLMRequest:
    base = dict(
        system="Voce planeja pesquisa financeira.",
        user="Qual foi a margem EBITDA do GPA em 2024?",
        model="claude-opus-5",
        max_tokens=16384,
    )
    return LLMRequest(**(base | overrides))


def _cliente(request, texto=TEXTO, **kwargs) -> FakeLLMClient:
    kwargs.setdefault("called_at", CHAMADO_EM)
    fake = FakeLLMClient(**kwargs)
    fake.register(request, texto)
    return fake


# -- a chave -----------------------------------------------------------------


def test_a_chave_e_estavel_e_tem_a_forma_de_um_sha():
    request = _request()

    assert call_sha256(request, "fake/v1") == call_sha256(request, "fake/v1")
    assert len(call_sha256(request, "fake/v1")) == 64


def test_a_chave_nao_e_o_prompt_sha256():
    """Se fossem iguais, o fingerprint nao estaria entrando na identidade."""
    request = _request()

    assert call_sha256(request, "fake/v1") != request.prompt_sha256


def test_mesmo_prompt_e_fingerprints_diferentes_sao_chamadas_diferentes():
    """O caso que motiva a arquitetura inteira do cache."""
    request = _request()

    assert call_sha256(request, "anthropic/v1/aaaa") != call_sha256(
        request, "anthropic/v2/bbbb"
    )


def test_a_chave_herda_o_que_o_prompt_sha256_ja_garantia():
    """Compoe e nao substitui: editar o system prompt continua invalidando, e
    `timeout_s` continua sem invalidar."""
    fp = "fake/v1"

    assert call_sha256(_request(system="Outra."), fp) != call_sha256(_request(), fp)
    assert call_sha256(_request(timeout_s=5), fp) == call_sha256(_request(timeout_s=600), fp)


def test_temperatura_pedida_ou_nao_muda_a_chave():
    fp = "fake/v1"

    assert call_sha256(_request(temperature=Decimal("0")), fp) != call_sha256(_request(), fp)


# -- acerto e erro -----------------------------------------------------------


def test_o_cache_satisfaz_os_dois_protocolos():
    assert isinstance(InMemoryLLMCache(), LLMCache)
    assert isinstance(CachedLLMClient(FakeLLMClient(), InMemoryLLMCache()), LLMClient)


def test_primeira_chamada_e_miss_e_chega_no_cliente_de_dentro():
    request = _request()
    inner = _cliente(request)
    cache = InMemoryLLMCache()

    response = CachedLLMClient(inner, cache).complete(request)

    assert len(inner.calls) == 1
    assert response.cached is False
    assert response.text == TEXTO
    assert len(cache) == 1


def test_segunda_chamada_e_hit_e_nao_toca_o_cliente_de_dentro():
    request = _request()
    inner = _cliente(request)
    cliente = CachedLLMClient(inner, InMemoryLLMCache())

    cliente.complete(request)
    response = cliente.complete(request)

    assert len(inner.calls) == 1  # a segunda nao passou
    assert response.cached is True
    assert response.text == TEXTO


def test_o_hit_preserva_o_instante_da_chamada_original():
    """M-1, que e o motivo de `called_at` ter mudado de lugar: carimbar "agora"
    numa resposta gravada faria o manifesto afirmar uma chamada que nao
    aconteceu - e afirmar com aparencia de rastro."""
    request = _request()
    cliente = CachedLLMClient(_cliente(request), InMemoryLLMCache())

    primeira = cliente.complete(request)
    segunda = cliente.complete(request)

    assert primeira.called_at == CHAMADO_EM
    assert segunda.called_at == CHAMADO_EM


def test_o_hit_preserva_os_hashes_e_o_modelo_que_respondeu():
    request = _request()
    inner = _cliente(request, model_id="claude-opus-5-20260101")
    cliente = CachedLLMClient(inner, InMemoryLLMCache())

    primeira = cliente.complete(request)
    segunda = cliente.complete(request)

    assert segunda.model_id == primeira.model_id == "claude-opus-5-20260101"
    assert segunda.response_sha256 == primeira.response_sha256
    assert segunda.prompt_sha256 == primeira.prompt_sha256


def test_prompt_diferente_e_miss():
    uma, outra = _request(), _request(user="E em 2023?")
    inner = FakeLLMClient(called_at=CHAMADO_EM)
    inner.register(uma, TEXTO)
    inner.register(outra, '{"objective": "y"}')
    cliente = CachedLLMClient(inner, InMemoryLLMCache())

    cliente.complete(uma)
    cliente.complete(outra)

    assert len(inner.calls) == 2


def test_dois_clientes_com_configuracoes_diferentes_nao_compartilham_resposta():
    """O cenario de corrupcao silenciosa, encenado.

    Sem o fingerprint na chave, a segunda chamada devolveria o texto da v1
    afirmando na procedencia que rodou sob a v2 - e o `response_sha256` bateria,
    porque bate com o que foi servido.
    """
    request = _request()
    cache = InMemoryLLMCache()

    v1 = _cliente(request, "resposta sob v1", fingerprint="anthropic/v1/aaaa")
    v2 = _cliente(request, "resposta sob v2", fingerprint="anthropic/v2/bbbb")

    primeira = CachedLLMClient(v1, cache).complete(request)
    segunda = CachedLLMClient(v2, cache).complete(request)

    assert primeira.text == "resposta sob v1"
    assert segunda.text == "resposta sob v2"
    assert len(v2.calls) == 1  # a v2 realmente chamou; nao herdou o cache da v1
    assert len(cache) == 2


def test_envolver_um_cliente_nao_muda_quem_esta_chamando():
    """Se o wrapper inventasse fingerprint proprio, a chave passaria a depender
    de estar em cache - circular - e a procedencia registraria o involucro."""
    inner = FakeLLMClient(fingerprint="anthropic/v1/aaaa")

    assert CachedLLMClient(inner, InMemoryLLMCache()).fingerprint == inner.fingerprint


def test_a_primeira_gravacao_vence():
    """Sobrescrever tornaria uma corrida antiga irreproduzivel: o cache existe
    para congelar o que foi respondido, nao para acompanhar o modelo."""
    request = _request()
    cache = InMemoryLLMCache()
    chave = call_sha256(request, "fake/v1/00000000")

    inner = _cliente(request)
    CachedLLMClient(inner, cache).complete(request)

    cache.put(
        CachedCall(
            call_version=CALL_VERSION,
            call_sha256=chave,
            prompt_sha256=request.prompt_sha256,
            client_fingerprint="fake/v1/00000000",
            text="resposta nova",
            model_id="m",
            stop_reason="end_turn",
            response_sha256="a" * 64,
            called_at=DEPOIS,
        )
    )

    assert cache.get(chave).text == TEXTO


# -- CachedCall --------------------------------------------------------------


def test_o_registro_nao_guarda_manifest_id():
    """Por razao mecanica, nao estetica: `manifest_id` embute `executed_at`,
    entao inclui-lo na identidade faria toda consulta ser MISS (M-3)."""
    assert "manifest_id" not in CachedCall.model_fields


def test_a_resposta_reconstruida_sai_marcada_como_cache():
    request = _request()
    inner = _cliente(request)
    cliente = CachedLLMClient(inner, InMemoryLLMCache())
    cliente.complete(request)

    assert cliente.complete(request).cached is True

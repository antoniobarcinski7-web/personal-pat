"""O cache em disco: mesma mecânica do bronze, outro significado.

Estes testes defendem as quatro propriedades que o `BronzeStore` também
defende, porque uma resposta gravada sustenta a reprodutibilidade de uma
corrida antiga exatamente como um blob sustenta a de um número:

1. escrita atômica — entrada parcial nunca fica visível;
2. arquivo somente-leitura;
3. hash conferido na leitura;
4. corrupção falha alto, nunca vira MISS silencioso.

A quarta é a que importa mais aqui: um cache que degrada em silêncio
transforma corrupção em custo de API, e o sintoma aparece na fatura.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime

import pytest

from pat.config import Paths
from pat.research.llm import FakeLLMClient, LLMRequest
from pat.research.llm.cache import CALL_VERSION, CachedLLMClient, LLMCache, call_sha256
from pat.research.llm.store import CALLS_DIR, CachedCall, FileLLMCache, LLMCacheError

TEXTO = '{"objective": "x"}'
CHAMADO_EM = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _request(**overrides) -> LLMRequest:
    base = dict(
        system="Voce planeja pesquisa financeira.",
        user="Qual foi a margem EBITDA do GPA em 2024?",
        model="claude-opus-5",
        max_tokens=16384,
    )
    return LLMRequest(**(base | overrides))


def _call(key: str, *, text: str = TEXTO) -> CachedCall:
    import hashlib

    return CachedCall(
        call_version=CALL_VERSION,
        call_sha256=key,
        prompt_sha256="a" * 64,
        client_fingerprint="fake/v1/00000000",
        text=text,
        model_id="claude-opus-5-20260101",
        stop_reason="end_turn",
        response_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        called_at=CHAMADO_EM,
    )


@pytest.fixture
def cache(tmp_path) -> FileLLMCache:
    return FileLLMCache(tmp_path / "llm")


# -- contrato ----------------------------------------------------------------


def test_satisfaz_o_protocolo(cache):
    assert isinstance(cache, LLMCache)


def test_ausente_e_none_e_nao_erro(cache):
    assert cache.get("f" * 64) is None


def test_grava_e_le_de_volta(cache):
    chave = "a" * 64
    cache.put(_call(chave))

    lido = cache.get(chave)

    assert lido is not None
    assert lido.text == TEXTO
    assert lido.called_at == CHAMADO_EM
    assert lido.client_fingerprint == "fake/v1/00000000"


# -- mecânica do bronze ------------------------------------------------------


def test_o_fan_out_e_de_dois_niveis(cache):
    """Diretório com centenas de milhares de entradas degrada listagem em
    qualquer sistema de arquivos."""
    chave = "ab" + "c" * 62
    cache.put(_call(chave))

    esperado = cache.root / CALLS_DIR / "ab" / "cc" / f"{chave}.json"
    assert esperado.exists()


def test_o_arquivo_gravado_e_somente_leitura(cache):
    chave = "a" * 64
    cache.put(_call(chave))

    caminho = cache.root / CALLS_DIR / "aa" / "aa" / f"{chave}.json"
    modo = stat.S_IMODE(caminho.stat().st_mode)

    assert not modo & stat.S_IWUSR
    with pytest.raises(PermissionError):
        caminho.write_text("adulterado", encoding="utf-8")


def test_nao_sobra_arquivo_temporario(cache):
    cache.put(_call("a" * 64))

    assert list(cache.root.rglob("*.tmp")) == []


def test_a_primeira_gravacao_vence(cache):
    """Sobrescrever tornaria uma corrida antiga irreproduzível."""
    chave = "a" * 64
    cache.put(_call(chave))
    cache.put(_call(chave, text="resposta nova"))

    assert cache.get(chave).text == TEXTO


# -- corrupção falha alto ----------------------------------------------------


def _sobrescreve(caminho, conteudo: str) -> None:
    """O arquivo é somente-leitura de propósito; adulterar exige forçar."""
    os.chmod(caminho, stat.S_IRUSR | stat.S_IWUSR)
    caminho.write_text(conteudo, encoding="utf-8")


def test_texto_adulterado_e_detectado(cache):
    chave = "a" * 64
    cache.put(_call(chave))
    caminho = cache.root / CALLS_DIR / "aa" / "aa" / f"{chave}.json"

    payload = json.loads(caminho.read_text(encoding="utf-8"))
    payload["text"] = "outra coisa"  # o response_sha256 continua o antigo
    _sobrescreve(caminho, json.dumps(payload))

    with pytest.raises(LLMCacheError, match="corrompido"):
        cache.get(chave)


def test_entrada_ilegivel_falha_em_vez_de_virar_miss(cache):
    chave = "a" * 64
    cache.put(_call(chave))
    caminho = cache.root / CALLS_DIR / "aa" / "aa" / f"{chave}.json"
    _sobrescreve(caminho, "{isto nao e json")

    with pytest.raises(LLMCacheError, match="ilegivel"):
        cache.get(chave)


def test_entrada_gravada_sob_a_chave_errada_e_detectada(cache):
    chave = "a" * 64
    cache.put(_call(chave))
    caminho = cache.root / CALLS_DIR / "aa" / "aa" / f"{chave}.json"

    payload = json.loads(caminho.read_text(encoding="utf-8"))
    payload["call_sha256"] = "b" * 64
    _sobrescreve(caminho, json.dumps(payload))

    with pytest.raises(LLMCacheError, match="inconsistente"):
        cache.get(chave)


def test_verify_separa_o_que_esta_intacto(cache):
    boa, ruim = "a" * 64, "b" * 64
    cache.put(_call(boa))
    cache.put(_call(ruim))

    caminho = cache.root / CALLS_DIR / "bb" / "bb" / f"{ruim}.json"
    payload = json.loads(caminho.read_text(encoding="utf-8"))
    payload["text"] = "adulterado"
    _sobrescreve(caminho, json.dumps(payload))

    ok, corrompidos = cache.verify()

    assert ok == [boa]
    assert corrompidos == [ruim]


def test_verify_em_cache_vazio_nao_falha(cache):
    assert cache.verify() == ([], [])


# -- ponta a ponta com o cliente ---------------------------------------------


def test_o_cache_sobrevive_ao_processo(tmp_path):
    """A propriedade que justifica gravar em disco: a segunda corrida não
    chama o modelo, e carrega o instante da chamada original."""
    raiz = tmp_path / "llm"
    request = _request()

    inner = FakeLLMClient(called_at=CHAMADO_EM)
    inner.register(request, TEXTO)
    CachedLLMClient(inner, FileLLMCache(raiz)).complete(request)

    # Outra instância, como se fosse outro processo.
    outro = FakeLLMClient(called_at=datetime(2030, 1, 1, tzinfo=UTC))
    outro.register(request, "resposta diferente")
    resposta = CachedLLMClient(outro, FileLLMCache(raiz)).complete(request)

    assert len(outro.calls) == 0
    assert resposta.cached is True
    assert resposta.text == TEXTO
    assert resposta.called_at == CHAMADO_EM


def test_fingerprints_diferentes_geram_entradas_diferentes_em_disco(tmp_path):
    raiz = tmp_path / "llm"
    request = _request()
    cache = FileLLMCache(raiz)

    for fp, texto in (("anthropic/v1/aaaa", "sob v1"), ("anthropic/v2/bbbb", "sob v2")):
        inner = FakeLLMClient(called_at=CHAMADO_EM, fingerprint=fp)
        inner.register(request, texto)
        CachedLLMClient(inner, cache).complete(request)

    assert len(list((raiz / CALLS_DIR).rglob("*.json"))) == 2
    assert cache.get(call_sha256(request, "anthropic/v1/aaaa")).text == "sob v1"
    assert cache.get(call_sha256(request, "anthropic/v2/bbbb")).text == "sob v2"


# -- o diretório é separado do bronze ----------------------------------------


def test_o_diretorio_de_llm_nao_e_o_bronze(tmp_path):
    """As duas cadeias de procedência são paralelas e nunca se encontram."""
    paths = Paths(tmp_path).ensure()

    assert paths.llm != paths.bronze
    assert paths.llm.exists()
    assert paths.bronze not in paths.llm.parents

"""De onde a chave vem, e em que ordem.

Nenhum teste aqui toca no ambiente do processo nem no Keychain de verdade: o
primeiro contaminaria os testes que rodam depois, e o segundo faria a suite
depender do que esta guardado na maquina de quem roda - passaria na minha e
falharia na sua, que e a pior forma de teste verde.
"""

from __future__ import annotations

import subprocess

import pytest

from pat import credentials as _credentials
from pat.research.llm.anthropic import ENV_API_KEY

from pat.credentials import (
    KEYCHAIN_SERVICE,
    CredentialSource,
    ResolvedCredential,
    keychain_store_command,
    resolve_api_key,
)

_LEITURA_REAL = _credentials._from_keychain
"""Capturada no import, ANTES de a guarda autouse da suite entrar.

`tests/conftest.py` corta `_from_keychain` para toda a suite, para que nenhum
teste dependa do que esta guardado na maquina. Este arquivo e a excecao: ele
testa a propria leitura. Devolver a funcao real aqui e o oposto de furar a
guarda - o que se exercita abaixo e o parsing da saida do comando, com
`subprocess.run` sempre dublado, nunca o conteudo do Keychain de ninguem.
"""


@pytest.fixture(autouse=True)
def _restaura_a_leitura(monkeypatch):
    monkeypatch.setattr(_credentials, "_from_keychain", _LEITURA_REAL)



def test_explicito_vence_tudo():
    """Quem passou no construtor decidiu; nada deve sobrescrever isso."""
    credencial = resolve_api_key(explicit="sk-explicita", env={ENV_API_KEY: "sk-ambiente"})
    assert credencial == ResolvedCredential("sk-explicita", CredentialSource.EXPLICIT)


def test_ambiente_vence_o_keychain(monkeypatch):
    """De proposito, e o contrario seria um achado caro.

    Uma chave guardada meses atras sobrescrevendo em silencio a que a pessoa
    acabou de exportar daria cobranca na conta errada - erro que ninguem liga
    ao lugar certo.
    """
    monkeypatch.setattr(
        "pat.credentials._from_keychain", lambda: "sk-guardada"
    )
    credencial = resolve_api_key(env={ENV_API_KEY: "sk-do-momento"})
    assert credencial.key == "sk-do-momento"
    assert credencial.source is CredentialSource.ENVIRONMENT


def test_keychain_quando_o_ambiente_esta_vazio(monkeypatch):
    monkeypatch.setattr(
        "pat.credentials._from_keychain", lambda: "sk-guardada"
    )
    credencial = resolve_api_key(env={})
    assert credencial == ResolvedCredential("sk-guardada", CredentialSource.KEYCHAIN)


def test_sem_fonte_nenhuma_devolve_none(monkeypatch):
    """`None`, e nao string vazia: uma chave vazia chegaria ao SDK e viraria um
    401 sem causa visivel."""
    monkeypatch.setattr("pat.credentials._from_keychain", lambda: None)
    assert resolve_api_key(env={}) is None


def test_variavel_vazia_nao_conta_como_chave(monkeypatch):
    """`export ANTHROPIC_API_KEY=` e o jeito comum de "desligar" a variavel, e
    trata-la como definida faria o Keychain nunca ser consultado."""
    monkeypatch.setattr(
        "pat.credentials._from_keychain", lambda: "sk-guardada"
    )
    credencial = resolve_api_key(env={ENV_API_KEY: ""})
    assert credencial.source is CredentialSource.KEYCHAIN


# -- o segredo nao vaza ------------------------------------------------------


def test_repr_nao_imprime_a_chave():
    """Traceback vai parar em log, em issue e em captura de tela."""
    texto = repr(ResolvedCredential("sk-ant-supersecreta-123", CredentialSource.KEYCHAIN))
    assert "supersecreta" not in texto
    assert "keychain" in texto


# -- o comando do keychain ---------------------------------------------------


def test_o_comando_guardado_pede_a_chave_em_vez_de_receber_na_linha():
    """`-w` no FIM faz o `security` perguntar. Com a chave na linha de comando
    ela ficaria no historico do shell, que e o oposto do que este modulo existe
    para fazer."""
    comando = keychain_store_command()
    assert comando.rstrip().splitlines()[0].endswith("-w")
    assert KEYCHAIN_SERVICE in comando
    assert "sk-" not in comando


# -- leitura do keychain -----------------------------------------------------


def test_keychain_ausente_nao_e_falha(monkeypatch):
    """Item que nao existe faz `security` sair com codigo 44. Isso e "nao ha
    chave guardada", e nao "a configuracao esta quebrada"."""
    from pat import credentials

    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    monkeypatch.setattr(
        credentials.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 44, stdout="", stderr="not found"),
    )
    assert credentials._from_keychain() is None


def test_fora_do_macos_nem_tenta(monkeypatch):
    """`security` nao existe em Linux, e deixar o `FileNotFoundError` subir
    faria a ausencia de uma conveniencia parecer falha de configuracao."""
    from pat import credentials

    monkeypatch.setattr(credentials.sys, "platform", "linux")

    def explode(*a, **k):
        raise AssertionError("nao deveria chamar `security` fora do macOS")

    monkeypatch.setattr(credentials.subprocess, "run", explode)
    assert credentials._from_keychain() is None


def test_keychain_travado_vira_ausencia_e_nao_espera_para_sempre(monkeypatch):
    """O `security` pode abrir dialogo de desbloqueio. Um teto curto transforma
    "travou" num estado que o chamador ja sabe tratar."""
    from pat import credentials

    monkeypatch.setattr(credentials.sys, "platform", "darwin")

    def estoura(*a, **k):
        raise subprocess.TimeoutExpired(cmd="security", timeout=5)

    monkeypatch.setattr(credentials.subprocess, "run", estoura)
    assert credentials._from_keychain() is None


def test_a_chave_lida_perde_so_a_quebra_de_linha_do_comando(monkeypatch):
    """`-w` imprime a senha com `\\n` no fim. O `strip` e sobre a saida do
    comando; uma chave que de fato tenha espaco foi guardada errada, e o 401
    depois disso e informacao verdadeira."""
    from pat import credentials

    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    monkeypatch.setattr(
        credentials.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="sk-ant-abc\n", stderr=""),
    )
    assert credentials._from_keychain() == "sk-ant-abc"


def test_keychain_vazio_nao_vira_chave_vazia(monkeypatch):
    from pat import credentials

    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    monkeypatch.setattr(
        credentials.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="\n", stderr=""),
    )
    assert credentials._from_keychain() is None


# -- a mensagem de erro do adapter -------------------------------------------


def test_o_adapter_sem_chave_ensina_a_guardar(monkeypatch):
    """A mensagem antiga dizia que faltava a variavel e parava ai. Dizer
    "guarde no Keychain" sem dizer como e o mesmo que nao dizer nada."""
    from pat.research.llm import LLMTransportError
    from pat.research.llm.anthropic import AnthropicClient

    monkeypatch.setattr(
        "pat.credentials.resolve_api_key", lambda **k: None
    )
    with pytest.raises(LLMTransportError) as exc:
        AnthropicClient()
    assert "security add-generic-password" in str(exc.value)
    assert KEYCHAIN_SERVICE in str(exc.value)

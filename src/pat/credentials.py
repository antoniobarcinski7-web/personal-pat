"""De onde vem a credencial. Ordem declarada, nunca busca.

O adapter continua nao inventando credencial: ele le de fontes NOMEADAS, numa
ordem fixa, e diz qual usou. A diferenca entre isso e "procurar a chave" e a
mesma que existe entre o mapeamento semantico e o casamento por rotulo - uma
lista fechada que alguem escreveu, contra uma varredura que acha algo parecido.

A ordem, e o porque dela:

1. `api_key=` explicito. Quem passa no construtor decidiu, e nada deve
   sobrescrever uma decisao dita em codigo.
2. `ANTHROPIC_API_KEY` no ambiente. E o que todo mundo espera, e continua
   sendo o jeito de rodar uma chave diferente por sessao sem tocar em nada
   guardado - `ANTHROPIC_API_KEY=sk-outra pat ...` continua funcionando.
3. Keychain do macOS, no item `pat-anthropic-api-key`. E o "salvar no
   sistema": fica cifrado no login.keychain, e nao em texto puro num
   `.zshrc` que vai junto num backup, num `git add -A` distraido ou numa
   captura de tela do terminal.

O ambiente vence o Keychain de proposito. O contrario faria uma chave guardada
meses atras sobrescrever, em silencio, a que a pessoa acabou de exportar para
testar - e o sintoma seria uma cobranca na conta errada, que e o tipo de erro
que ninguem liga ao lugar certo.

Nao ha quarta fonte. Sem arquivo `.env`, sem `~/.pat/credentials`: um arquivo
de credencial dentro do projeto e a maneira mais comum de um segredo virar
commit.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "KEYCHAIN_SERVICE",
    "CredentialSource",
    "ResolvedCredential",
    "keychain_store_command",
    "resolve_api_key",
]

ENV_API_KEY = "ANTHROPIC_API_KEY"
"""Declarado aqui, e reexportado pelo adapter.

Importa-lo do adapter faria um ciclo - ele importa este modulo - e amarraria a
resolucao de credencial ao unico provider que existe hoje."""

KEYCHAIN_SERVICE = "pat-anthropic-api-key"

_KEYCHAIN_TIMEOUT_S = 5
"""O `security` pode abrir dialogo de desbloqueio e ficar esperando. Um teto
curto transforma "travou para sempre" em "nao achou", que e um estado que o
chamador ja sabe tratar."""


class CredentialSource(StrEnum):
    """De onde a chave veio. Entra em mensagem de erro e em diagnostico.

    Existe porque "a chave errada" e um sintoma mudo: a API responde 401 e o
    usuario nao tem como saber se leu do ambiente ou do Keychain. Nomear a
    fonte transforma meia hora de confusao numa linha.
    """

    EXPLICIT = "explicito"
    ENVIRONMENT = "ambiente"
    KEYCHAIN = "keychain"


@dataclass(frozen=True)
class ResolvedCredential:
    """A chave e de onde ela veio.

    `__repr__` e sobrescrito porque um dataclass normal imprimiria o segredo
    em qualquer traceback - e traceback vai parar em log, em issue e em
    captura de tela.
    """

    key: str
    source: CredentialSource

    def __repr__(self) -> str:
        return f"ResolvedCredential(key=<{len(self.key)} caracteres>, source={self.source.value})"


def _from_keychain() -> str | None:
    """Le o item do Keychain do macOS. Silencioso quando nao ha.

    Fora do macOS devolve `None` sem tentar: `security` nao existe em Linux, e
    deixar o `FileNotFoundError` subir daqui faria a ausencia de uma
    conveniencia parecer falha de configuracao.
    """
    if sys.platform != "darwin":
        return None
    try:
        concluido = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=_KEYCHAIN_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if concluido.returncode != 0:
        return None
    # `-w` imprime so a senha, com quebra de linha no fim. `strip()` aqui e
    # sobre a saida do comando, e nao sobre o segredo: uma chave com espaco na
    # ponta foi guardada errada, e limpa-la calada esconderia isso ate o 401.
    chave = concluido.stdout.strip()
    return chave or None


def resolve_api_key(
    *, explicit: str | None = None, env: dict[str, str] | None = None
) -> ResolvedCredential | None:
    """A ordem declarada, uma vez. `None` quando nenhuma fonte tem a chave.

    `env` e injetavel para que o teste exercite a precedencia sem mexer no
    ambiente do processo - um teste que exportasse variavel de verdade
    contaminaria os que rodam depois dele.
    """
    if explicit:
        return ResolvedCredential(explicit, CredentialSource.EXPLICIT)

    ambiente = os.environ if env is None else env
    do_ambiente = ambiente.get(ENV_API_KEY)
    if do_ambiente:
        return ResolvedCredential(do_ambiente, CredentialSource.ENVIRONMENT)

    do_keychain = _from_keychain()
    if do_keychain:
        return ResolvedCredential(do_keychain, CredentialSource.KEYCHAIN)

    return None


def keychain_store_command() -> str:
    """O comando que guarda a chave, para aparecer na mensagem de erro.

    Uma frase que diz "guarde no Keychain" sem dizer como e a mesma coisa que
    nao dizer nada. E o comando fica em `security`, e nao num `pat auth
    --key sk-...`, porque o segundo deixaria o segredo no historico do shell.
    """
    return (
        f"security add-generic-password -a \"$USER\" -s {KEYCHAIN_SERVICE} -U -w\n"
        "  (o comando pergunta a chave; ela nao fica no historico do shell)"
    )

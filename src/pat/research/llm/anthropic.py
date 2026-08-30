"""O unico arquivo do projeto que conhece a Anthropic.

Tudo que e especifico do fornecedor mora aqui: nome de modelo, formato de
mensagem, mapeamento de erro, variavel de ambiente, perfil de geracao. O core
(`llm/__init__.py`, `cache.py`, `planner.py`) nao importa nada disto, e ha
teste de camada conferindo por conjunto exato.

A seta aponta numa direcao so
-----------------------------
O adapter adapta o provider ao contrato, nunca o contrario. A consequencia
pratica e uma regra dura: **um `temperature` nao-nulo nunca e descartado em
silencio**. Ou e enviado, ou a chamada falha com erro nomeado. Descartar faria
`PlanProvenance.temperature` virar declaracao de intencao sem valor probatorio
- e um rastro que nao bate e pior do que rastro nenhum, porque parece prova.

Perfil de geracao v1
--------------------
`GENERATION_PROFILE` diz por extenso o que este adapter decide. Note que
"nao configurar raciocinio" e uma *decisao*, e nao a ausencia de uma: se o
default do provider mudar, o comportamento muda. Por isso o perfil e conteudo
versionado por `ADAPTER_VERSION`, sob a mesma disciplina que o `CLAUDE.md`
impoe a `extractor_version` e `metric_version` - mudou o que se envia,
incrementa. O `fingerprint` carrega o hash do perfil para cima, e e ele que
entra na identidade da chamada no cache.

Sem retentativa
---------------
`max_retries=0` e obrigatorio, nao preferencia. O SDK tenta duas vezes por
default, e uma segunda tentativa e um segundo caminho de influencia: teria que
ser hasheada e manifestada por conta propria, ou a procedencia passa a
sub-relatar o que aconteceu.
"""

from __future__ import annotations

from datetime import UTC, datetime

import anthropic

from pat.research.canonical import sha256_of
from pat.research.llm import (
    LLMRefused,
    LLMRequest,
    LLMResponse,
    LLMTimeout,
    LLMTransportError,
)

PROVIDER = "anthropic"

ADAPTER_VERSION = "v1"
"""O quarto `*_version` do sistema. Cobre o `GENERATION_PROFILE` inteiro:
mudou o que este adapter envia - inclusive *deixar* de nao configurar algo -,
incrementa. Sem isso, o comportamento mudaria sem que a identidade da chamada
mudasse, e o cache serviria respostas de uma configuracao para outra."""

from pat.credentials import ENV_API_KEY  # noqa: E402 - reexportado

# `ENV_API_KEY` vive em `pat/credentials.py` desde que a resolucao ganhou uma
# segunda fonte. Continua exportado daqui porque e onde quem procura "a
# variavel da Anthropic" vai olhar.

GENERATION_PROFILE = {
    "thinking": "provider_default",
    "temperature": "only_when_requested",
    "retries": "disabled",
    "streaming": "disabled",
    "tools": "none",
}
"""O que a v1 decide, escrito para poder ser lido daqui a tres anos.

`thinking: provider_default` e uma escolha e nao uma omissao. Desligar
raciocinio explicitamente seria pior para esta tarefa: o planejador emite JSON
puro, e a instrucao de nao raciocinar aumenta o risco de marcacao interna vazar
no texto visivel - o que quebra `json.loads` diretamente. Nao configurar deixa
o provider no seu proprio default, e essa decisao esta coberta por
`ADAPTER_VERSION`."""

REFUSAL_STOP_REASON = "refusal"

__all__ = [
    "ADAPTER_VERSION",
    "ENV_API_KEY",
    "GENERATION_PROFILE",
    "PROVIDER",
    "AnthropicClient",
]


def _profile_sha8() -> str:
    return sha256_of(GENERATION_PROFILE)[:8]


class AnthropicClient:
    """Adapter concreto. Satisfaz o Protocol `LLMClient`.

    `sdk` e injetavel para que o teste exercite o mapeamento de erro e a
    extracao de texto sem rede e sem credencial. Em uso normal e construido
    daqui, com a chave do ambiente.
    """

    def __init__(self, *, api_key: str | None = None, sdk: object | None = None) -> None:
        if sdk is not None:
            self._sdk = sdk
            return

        # Fontes NOMEADAS, em ordem fixa - ver `credentials.py`. Continua nao
        # inventando credencial: ler de uma lista fechada que alguem escreveu
        # nao e procurar a chave por ai.
        from pat.credentials import keychain_store_command, resolve_api_key

        credencial = resolve_api_key(explicit=api_key)
        if credencial is None:
            raise LLMTransportError(
                f"{ENV_API_KEY} nao esta definida e o Keychain nao tem a chave. "
                "O adapter nao inventa credencial e nao cai para um modo "
                "degradado.\n\n"
                "Para guardar de uma vez, no Keychain do macOS:\n"
                f"  {keychain_store_command()}"
            )
        self._credential_source = credencial.source
        self._sdk = anthropic.Anthropic(
            api_key=credencial.key,
            # Obrigatorio, nao preferencia: o default do SDK e 2, e retentativa
            # e um segundo caminho de influencia nao hasheado.
            max_retries=0,
        )

    @property
    def fingerprint(self) -> str:
        """`<provider>/<adapter_version>/<config_sha8>`.

        Opaco para cima e resolvivel para baixo: quem audita le o fingerprint,
        faz checkout do commit e le o `GENERATION_PROFILE`.
        """
        return f"{PROVIDER}/{ADAPTER_VERSION}/{_profile_sha8()}"

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, object] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
        }
        if request.stop_sequences:
            payload["stop_sequences"] = list(request.stop_sequences)
        if request.temperature is not None:
            # Enviado como float porque e o que a API aceita. O `Decimal` existe
            # para a identidade canonica, que ja foi calculada antes daqui - a
            # conversao acontece na saida e nunca volta para dentro do sistema.
            payload["temperature"] = float(request.temperature)

        # Antes da chamada: e o instante em que a chamada comecou, e e ele que
        # uma resposta servida do cache vai carregar para sempre (M-1).
        called_at = datetime.now(UTC)

        try:
            message = self._sdk.messages.create(timeout=request.timeout_s, **payload)
        except anthropic.APITimeoutError as exc:
            raise LLMTimeout(
                f"o modelo nao respondeu em {request.timeout_s}s. E uma corrida "
                "falha, nao uma retentativa."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise LLMTransportError(self._explica_status(exc, request)) from exc
        except anthropic.AnthropicError as exc:
            raise LLMTransportError(f"falha ao chamar o provider: {exc}") from exc

        stop_reason = getattr(message, "stop_reason", None) or "end_turn"
        if stop_reason == REFUSAL_STOP_REASON:
            # Chamada bem-sucedida cuja resposta foi uma negativa. Distinto de
            # erro de transporte, e por isso tem excecao propria.
            raise LLMRefused(
                f"o modelo recusou responder (stop_reason={stop_reason}). "
                "Nao ha segunda tentativa: recusa e informacao, nao ruido."
            )

        return LLMResponse.for_text(
            self._texto(message),
            model_id=getattr(message, "model", None) or request.model,
            prompt_sha256=request.prompt_sha256,
            stop_reason=stop_reason,
            called_at=called_at,
        )

    @staticmethod
    def _texto(message: object) -> str:
        """So os blocos de texto visivel.

        Raciocinio bruto nao e devolvido pela API e nao seria concatenado aqui
        se fosse: o que o planejador parseia e o JSON, e misturar deliberacao
        no mesmo string quebraria `json.loads` de um jeito dificil de
        diagnosticar.
        """
        blocos = getattr(message, "content", None) or ()
        return "".join(
            bloco.text for bloco in blocos if getattr(bloco, "type", None) == "text"
        )

    @staticmethod
    def _explica_status(exc: Exception, request: LLMRequest) -> str:
        status = getattr(exc, "status_code", None)
        if status == 400 and request.temperature is not None:
            # O caso previsto pela auditoria. A alternativa - reenviar sem o
            # campo - seria o adapter descartando em silencio o que o contrato
            # pediu, e a procedencia passaria a mentir.
            return (
                f"o modelo {request.model!r} recusou o parametro temperature "
                f"({request.temperature}). O adapter nao reenvia sem ele: descartar "
                "em silencio faria a procedencia registrar um pedido que nao foi "
                f"atendido. Omita temperature para este modelo. Origem: {exc}"
            )
        return f"o provider respondeu com erro (status={status}): {exc}"

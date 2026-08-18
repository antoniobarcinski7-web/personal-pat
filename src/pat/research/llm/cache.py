"""O cache, atras do mesmo Protocol do cliente.

Quem chama nao sabe - nem deve saber - se a resposta veio da rede ou do disco.
Sabe, pelo campo `cached`, que veio de algum lugar registrado.

Por que a chave nao e `prompt_sha256`
-------------------------------------
Porque `prompt_sha256` identifica o que o PAT *pediu*, e nao o que o adapter de
fato *enviou*. Enquanto o adapter nao tinha escolha nenhuma os dois coincidiam.
No momento em que ele passa a decidir algo - omitir um parametro que o modelo
recusa, escolher um perfil de raciocinio -, deixam de coincidir.

O cenario concreto, que e o motivo desta arquitetura existir:

    seg  adapter v1  ->  MISS  ->  resposta R1 gravada sob a chave K
    ter  adapter v2  ->  HIT   ->  devolve R1

Na terca o sistema afirma na procedencia que rodou sob a v2 e serve uma
resposta produzida pela v1. Nao ha erro, nao ha aviso, e o `response_sha256`
*bate* - porque bate com R1, que e de fato o que foi servido. O rastro fica
internamente consistente e falso, que e a pior categoria de achado: a unica
testemunha confirma a versao errada.

Dai a chave compor as duas coisas. `prompt_sha256` nao muda; o cache e que
passa a hasheia-lo junto com quem esta chamando.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pat.research.canonical import sha256_of
from pat.research.llm import LLMClient, LLMRequest, LLMResponse
from pat.research.llm.store import CachedCall

CALL_VERSION = "v1"
"""Versao do *esquema de identidade* da chamada, nao do conteudo.

Existe para que uma mudanca futura na composicao da chave invalide o cache de
propria vontade, em vez de fazer entradas velhas e novas coabitarem sob chaves
que significam coisas diferentes."""

__all__ = ["CALL_VERSION", "CachedLLMClient", "InMemoryLLMCache", "LLMCache", "call_sha256"]


def call_sha256(request: LLMRequest, client_fingerprint: str) -> str:
    """A identidade da chamada efetiva.

    Compoe, e nao substitui: `request.prompt_sha256` entra inteiro, entao toda
    propriedade que ele ja garantia continua valendo - editar o system prompt
    invalida o cache, `timeout_s` nao invalida.

    Fora do hash, deliberadamente: `timeout_s` (transporte), `called_at`
    (procedencia, nao pedido) e o `model_id` resolvido (e resultado da chamada;
    inclui-lo seria circular).
    """
    return sha256_of(
        {
            "call_version": CALL_VERSION,
            "prompt_sha256": request.prompt_sha256,
            "client_fingerprint": client_fingerprint,
        }
    )


@runtime_checkable
class LLMCache(Protocol):
    """Um segundo Protocol, e nao um import de `pat.store`.

    E o que mantem `CachedLLMClient` sem nenhum caminho ate o warehouse: ele
    fala com o armazenamento pela interface, do mesmo jeito que a camada
    semantica fala com os dados por `FactResolver`.
    """

    def get(self, key: str) -> CachedCall | None: ...

    def put(self, call: CachedCall) -> None: ...


class InMemoryLLMCache:
    """Cache de processo. Util em teste e numa corrida com varias chamadas.

    Nao persiste de proposito: persistencia e `FileLLMCache`, e manter os dois
    separados e o que permite testar a logica de acerto/erro sem tocar disco.
    """

    def __init__(self) -> None:
        self._entries: dict[str, CachedCall] = {}

    def get(self, key: str) -> CachedCall | None:
        return self._entries.get(key)

    def put(self, call: CachedCall) -> None:
        # Primeira gravacao vence. Duas respostas sob a mesma chave significam
        # que o modelo variou, e o cache existe justamente para congelar a
        # primeira - sobrescrever tornaria a corrida antiga irreproduzivel.
        self._entries.setdefault(call.call_sha256, call)

    def __len__(self) -> int:
        return len(self._entries)


class CachedLLMClient:
    """Um `LLMClient` que consulta o cache antes de chamar o de dentro.

    Satisfaz o mesmo Protocol que envolve, entao o planejador nao muda uma
    linha para ganhar cache - que e a propriedade que justifica o desenho.
    """

    def __init__(self, inner: LLMClient, cache: LLMCache) -> None:
        self._inner = inner
        self._cache = cache

    @property
    def fingerprint(self) -> str:
        """O fingerprint de quem de fato responde, nunca um proprio.

        Envolver um cliente nao muda quem esta chamando o modelo. Se este
        wrapper inventasse um fingerprint seu, a chave de cache passaria a
        depender de *estar em cache*, que e circular, e a procedencia
        registraria o involucro em vez do adapter.
        """
        return self._inner.fingerprint

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = call_sha256(request, self._inner.fingerprint)

        hit = self._cache.get(key)
        if hit is not None:
            return hit.to_response()

        response = self._inner.complete(request)
        self._cache.put(
            CachedCall.of(
                request,
                response,
                call_sha256=key,
                call_version=CALL_VERSION,
                client_fingerprint=self._inner.fingerprint,
            )
        )
        return response

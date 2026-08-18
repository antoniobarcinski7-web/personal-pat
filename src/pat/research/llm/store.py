"""O registro de uma chamada gravada, e o cache em disco que o guarda.

`data/bronze/` prova de onde veio um *numero*; `data/llm/` prova de onde veio
uma *frase*. As duas cadeias de procedencia sao paralelas e nunca se encontram:
nada aqui vira `RawDocument`, ganha `Retrieval` ou e alcancavel por
`AsOf.provenance()`.

Mesma mecanica do `BronzeStore` - fan-out em dois niveis, escrita atomica,
arquivo somente-leitura, hash conferido na leitura - reimplementada aqui em vez
de herdada. Reusar a classe traria de volta exatamente a confusao semantica que
a separacao de diretorios existe para impedir.

Uma diferenca deliberada em relacao ao bronze: la o nome do arquivo e o hash do
*conteudo*; aqui e o `call_sha256`, o hash da *chamada*. Duas chamadas sob
configuracoes diferentes que por acaso devolvessem o mesmo texto sao entradas
distintas, porque "o que esta configuracao respondeu?" tem que continuar
respondivel.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from pat.contracts.common import AwareDatetime, Frozen, Sha256
from pat.research.llm import LLMRequest, LLMResponse

CALLS_DIR = "calls"

__all__ = ["CALLS_DIR", "CachedCall", "FileLLMCache", "LLMCacheError"]


class LLMCacheError(RuntimeError):
    """Cache corrompido ou ilegivel.

    Existe para que uma leitura defeituosa falhe alto em vez de virar MISS: um
    cache que degrada em silencio transforma corrupcao em custo de API, e o
    sintoma aparece na fatura em vez de no teste.
    """


class CachedCall(Frozen):
    """Tudo que e preciso gravar para reconstruir uma resposta - e nada mais.

    Note o que NAO esta aqui: `manifest_id`. O cache nao pode depender dele por
    uma razao mecanica e nao estetica - `manifest_id` embute `executed_at`,
    entao inclui-lo na identidade faria toda consulta ser MISS e o cache jamais
    acertaria. A ligacao entre chamada e manifesto mora na tabela `llm_call`,
    que e indice e nao identidade (M-3).
    """

    call_version: str
    call_sha256: Sha256
    prompt_sha256: Sha256
    client_fingerprint: str
    text: str
    model_id: str
    stop_reason: str
    response_sha256: Sha256
    called_at: AwareDatetime
    """Instante da chamada ORIGINAL. E o campo que faz o M-1 valer: e ele que
    a resposta servida do disco carrega, em vez de "agora"."""

    @classmethod
    def of(
        cls,
        request: LLMRequest,
        response: LLMResponse,
        *,
        call_sha256: str,
        call_version: str,
        client_fingerprint: str,
    ) -> "CachedCall":
        return cls(
            call_version=call_version,
            call_sha256=call_sha256,
            prompt_sha256=request.prompt_sha256,
            client_fingerprint=client_fingerprint,
            text=response.text,
            model_id=response.model_id,
            stop_reason=response.stop_reason,
            response_sha256=response.response_sha256,
            called_at=response.called_at,
        )

    def to_response(self) -> LLMResponse:
        """Reconstroi a resposta, marcada como vinda do cache.

        `cached=True` e `called_at` original andam juntos de proposito: quem le
        o manifesto precisa saber as duas coisas - que a resposta nao custou
        uma chamada agora, e quando ela custou uma.
        """
        return LLMResponse(
            text=self.text,
            model_id=self.model_id,
            stop_reason=self.stop_reason,
            prompt_sha256=self.prompt_sha256,
            response_sha256=self.response_sha256,
            called_at=self.called_at,
            cached=True,
        )


class FileLLMCache:
    """Cache persistente em `data/llm/`. Satisfaz o Protocol `LLMCache`.

    Nao importa `pat.store` e nao conhece DuckDB: gravar bytes e uma coisa,
    indexar chamadas numa tabela e outra (`llm_call`), e o cache tem que
    funcionar sem warehouse nenhum - inclusive para um plano que foi recusado e
    nunca virou manifesto.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        # Fan-out em dois niveis, como no bronze: diretorio com centenas de
        # milhares de entradas degrada listagem em qualquer sistema de arquivos.
        return self.root / CALLS_DIR / key[:2] / key[2:4] / f"{key}.json"

    def get(self, key: str) -> CachedCall | None:
        path = self._path(key)
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            call = CachedCall.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            # Falha alto em vez de devolver None: um cache que degrada em
            # silencio transforma corrupcao em custo de API, e o sintoma
            # aparece na fatura em vez de no teste.
            raise LLMCacheError(f"entrada ilegivel em {path}: {exc}") from exc

        if call.call_sha256 != key:
            raise LLMCacheError(
                f"entrada gravada sob {key} declara call_sha256={call.call_sha256}: "
                "cache inconsistente"
            )

        esperado = hashlib.sha256(call.text.encode("utf-8")).hexdigest()
        if call.response_sha256 != esperado:
            raise LLMCacheError(
                f"texto corrompido em {key}: declarado {call.response_sha256}, "
                f"calculado {esperado}"
            )

        return call

    def put(self, call: CachedCall) -> None:
        path = self._path(call.call_sha256)
        if path.exists():
            # Primeira gravacao vence, como no `InMemoryLLMCache`: sobrescrever
            # tornaria uma corrida antiga irreproduzivel.
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            call.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")

        # Escrita atomica: uma entrada parcial nunca fica visivel sob seu nome
        # final, mesmo se o processo morrer no meio.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def verify(self) -> tuple[list[str], list[str]]:
        """Recalcula o hash do texto de cada entrada. Retorna (ok, corrompidos).

        Espelha `BronzeStore.verify()` e existe pela mesma razao: adulteracao
        silenciosa de uma resposta gravada mudaria o que uma corrida antiga
        reproduz, sem que nada mais no sistema percebesse.
        """
        ok: list[str] = []
        bad: list[str] = []
        raiz = self.root / CALLS_DIR
        if not raiz.exists():
            return ok, bad
        for path in sorted(raiz.rglob("*.json")):
            key = path.stem
            try:
                self.get(key)
            except LLMCacheError:
                bad.append(key)
            else:
                ok.append(key)
        return ok, bad

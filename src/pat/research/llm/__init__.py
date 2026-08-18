"""A porta do modelo de linguagem. Contrato, nunca provider.

Espelha `FactResolver` da Fase 2: a camada de pesquisa fala com o mundo por um
Protocol, e trocar de fornecedor e escrever um adapter, nao reformar o nucleo.
Nada aqui conhece Anthropic, HTTP ou chave de API.

Por que os contratos sao modelos Pydantic e nao dataclasses
------------------------------------------------------------
A proposta original desenhou `LLMRequest`/`LLMResponse` como dataclasses
congeladas. Dois motivos para divergir, ambos verificaveis:

1. `canonical._plain` sabe serializar `BaseModel` e nao sabe serializar
   dataclass. Como `prompt_sha256` e o sha256 da forma canonica da requisicao,
   um dataclass exigiria uma segunda implementacao de serializacao - e duas
   implementacoes de identidade divergem em silencio.
2. `extra="forbid"` e a validacao de tipo sao o padrao de fronteira do projeto:
   campo inesperado falha na entrada, e nao tres camadas adiante.

Nenhum campo aqui e especulativo. Todos alimentam `PlanProvenance`
(`contracts/research.py`), que ja existe desde o Milestone 1 e ja pede
`model_id`, `temperature`, `max_tokens`, `system_prompt_sha256`,
`prompt_sha256`, `response_sha256` e `cached`.

O que este modulo deliberadamente NAO tem
------------------------------------------
Cache, persistencia, retry e adapter concreto. O cache mora atras do mesmo
Protocol (Milestone 2.2) justamente porque quem chama nao precisa saber se a
resposta veio da rede ou do disco - so precisa saber, pelo campo `cached`, que
veio de algum lugar registrado.

Retry nao existe e nao deve passar a existir aqui: uma segunda tentativa e um
segundo caminho de influencia, e teria que ser hasheada e manifestada por
conta propria, ou a procedencia passa a sub-relatar o que aconteceu.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import Field, field_validator

from pat.contracts.common import Frozen, Sha256
from pat.research.canonical import sha256_of

__all__ = [
    "FakeLLMClient",
    "LLMClient",
    "LLMError",
    "LLMRefused",
    "LLMRequest",
    "LLMResponse",
    "LLMTimeout",
    "LLMTransportError",
]


# -- erros da fronteira ------------------------------------------------------
#
# Tres nomes proprios para que nenhum tipo de provider vaze para cima. Quem
# trata o erro nao deveria precisar importar httpx para saber que deu timeout.


class LLMError(RuntimeError):
    """Base de tudo que pode dar errado na fronteira do modelo."""


class LLMTimeout(LLMError):
    """O modelo nao respondeu no prazo. E uma corrida falha, nao uma retentativa."""


class LLMTransportError(LLMError):
    """Falha de rede, autenticacao ou resposta ilegivel do provider."""


class LLMRefused(LLMError):
    """O modelo se recusou a responder. Distinto de erro de transporte: aqui a
    chamada funcionou e a resposta foi uma negativa."""


# -- contratos ---------------------------------------------------------------


class LLMRequest(Frozen):
    """Tudo que define uma chamada, e nada mais.

    `timeout_s` e o unico campo que nao entra em `prompt_sha256`: e decisao de
    transporte, nao conteudo. Duas chamadas identicas com paciencia diferente
    sao a mesma pergunta, e tem que acertar o mesmo cache.
    """

    system: str = Field(min_length=1)
    user: str = Field(min_length=1)
    model: str = Field(min_length=1, description="O que foi pedido; a resposta diz o que atendeu")
    max_tokens: int = Field(gt=0)
    temperature: Decimal = Field(
        ge=0,
        le=1,
        description="Decimal, nunca float: entra na identidade canonica, e "
        "binario de ponto flutuante nao reproduz entre plataformas",
    )
    stop_sequences: tuple[str, ...] = ()
    timeout_s: int = Field(default=60, gt=0)

    @field_validator("temperature", mode="before")
    @classmethod
    def _sem_float(cls, value: object) -> object:
        """Recusa float na entrada, em vez de deixar o Pydantic coagir.

        A coercao seria exata para um literal (`0.7` vira `Decimal("0.7")`),
        mas nao para uma expressao: `0.1 + 0.2` vira
        `Decimal("0.30000000000000004")`, e dois chamadores com a mesma
        intencao passariam a ter identidades - e caches - diferentes.
        `canonical._plain` ja levanta em float pela mesma razao; aqui o erro
        aparece na fronteira, que e onde da para corrigir.
        """
        if isinstance(value, float):
            raise ValueError(
                f"temperature recebeu float ({value!r}). Use Decimal: float entra "
                "na identidade canonica e nao reproduz entre plataformas."
            )
        return value

    @property
    def prompt_sha256(self) -> str:
        """Identidade do conteudo da chamada.

        Cobre a requisicao inteira menos o timeout, entao editar o system
        prompt invalida o cache - que e exatamente o efeito desejado: prompt
        novo e pergunta nova, mesmo que o texto do usuario nao tenha mudado.
        """
        return sha256_of(
            {
                "system": self.system,
                "user": self.user,
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "stop_sequences": self.stop_sequences,
            }
        )

    @property
    def system_prompt_sha256(self) -> str:
        """Hash so do system prompt, sobre os bytes crus.

        Existe separado porque `PlanProvenance` o pede separado: permite
        responder "que instrucao produziu este plano" sem precisar do texto do
        usuario junto.
        """
        return hashlib.sha256(self.system.encode("utf-8")).hexdigest()


class LLMResponse(Frozen):
    """O que voltou, com as duas pontas do rastro ja fechadas.

    `response_sha256` e conferido contra o proprio texto na construcao. Sem
    isso, um hash de procedencia poderia nao corresponder ao conteudo que ele
    diz identificar - e um rastro que nao bate e pior do que rastro nenhum,
    porque parece prova.
    """

    text: str
    model_id: str = Field(
        min_length=1,
        description="Quem de fato respondeu. Pode diferir do pedido: alias de "
        "modelo resolve para uma versao concreta, e o manifesto registra a versao",
    )
    stop_reason: str = Field(min_length=1)
    prompt_sha256: Sha256
    response_sha256: Sha256
    cached: bool = False

    def model_post_init(self, __context: object) -> None:
        esperado = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.response_sha256 != esperado:
            raise ValueError(
                f"response_sha256 nao corresponde ao texto: {self.response_sha256} "
                f"declarado, {esperado} calculado"
            )

    @classmethod
    def for_text(
        cls,
        text: str,
        *,
        model_id: str,
        prompt_sha256: str,
        stop_reason: str = "end_turn",
        cached: bool = False,
    ) -> "LLMResponse":
        """Construtor que calcula o hash do texto. Caminho normal de quem
        implementa um cliente - deixa o hash errado ser impossivel, e nao
        apenas detectavel."""
        return cls(
            text=text,
            model_id=model_id,
            stop_reason=stop_reason,
            prompt_sha256=prompt_sha256,
            response_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            cached=cached,
        )


@runtime_checkable
class LLMClient(Protocol):
    """A porta. Uma chamada, uma resposta, sem laco.

    Nao ha `complete_many`, nao ha streaming e nao ha ferramenta: o desenho da
    Fase 3 chama o modelo no maximo uma vez por planejador e uma por escritor,
    e uma interface que permitisse mais tornaria essa afirmacao impossivel de
    verificar.
    """

    def complete(self, request: LLMRequest) -> LLMResponse: ...


# -- duplo de teste ----------------------------------------------------------


class FakeLLMClient:
    """Cliente de mentira, deterministico, sem rede.

    Responde a partir de uma tabela indexada por `prompt_sha256`, e nao por
    ordem de chamada: um teste que dependesse da ordem passaria a mentir no dia
    em que o planejador passasse a montar o prompt de outro jeito.

    Guarda as requisicoes recebidas em ordem, para que "quantas vezes o modelo
    foi chamado" seja uma asserção e nao uma suposicao.
    """

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        *,
        model_id: str | None = None,
        stop_reason: str = "end_turn",
    ) -> None:
        self._responses: dict[str, str] = dict(responses or {})
        self._model_id = model_id
        self._stop_reason = stop_reason
        self._calls: list[LLMRequest] = []

    @property
    def calls(self) -> tuple[LLMRequest, ...]:
        """As requisicoes recebidas, na ordem. Tupla: quem inspeciona nao
        consegue alterar o registro."""
        return tuple(self._calls)

    def register(self, request: LLMRequest, text: str) -> str:
        """Associa uma resposta a uma requisicao concreta.

        Existe porque quem escreve o teste tem a requisicao em maos e nao o
        sha dela; calcular o hash na mao no teste seria reimplementar a
        identidade que o teste deveria estar conferindo.
        """
        self._responses[request.prompt_sha256] = text
        return request.prompt_sha256

    def complete(self, request: LLMRequest) -> LLMResponse:
        self._calls.append(request)  # registra antes: a chamada aconteceu
        try:
            text = self._responses[request.prompt_sha256]
        except KeyError:
            raise LLMError(
                f"FakeLLMClient sem resposta para prompt_sha256={request.prompt_sha256}. "
                "Use register(request, texto) ou passe o sha no dicionario."
            ) from None

        return LLMResponse.for_text(
            text,
            model_id=self._model_id or request.model,
            prompt_sha256=request.prompt_sha256,
            stop_reason=self._stop_reason,
        )

"""O5 - a conversa: o que o usuario disse, e o que o agente fez com isso.

A conversa NAO e o lugar onde as coisas acontecem. Ela e a porta: cada turno
vira uma ou mais acoes das camadas de baixo - pesquisar, criticar, abrir
hipotese, calcular valuation - e o que a torna auditavel e que a acao fica
registrada separada da fala.

`Intent` e um conjunto fechado
------------------------------
Nao ha intencao "outra que o modelo decidir". Um conjunto aberto significaria
que uma frase nova pode produzir um comportamento que ninguem previu, e num
sistema que grava tudo num diario append-only isso e como se escreve estado
que nao da para dobrar depois.

Quando a frase nao casa com nenhuma intencao, a resposta e `UNCLEAR` - que
pergunta de volta. Perguntar e melhor que adivinhar: adivinhar errado gasta
uma corrida de pesquisa e enche a agenda de tarefa que ninguem pediu.

O que um turno nunca faz
------------------------
Nao inventa numero, nao inventa citacao e nao preenche lacuna com prosa.
`TurnResponse.grounded_in` carrega os enderecos do que sustenta a resposta, e
uma resposta sem eles e visivelmente uma resposta sem ancora - o que e
legitimo para "o que voce quer investigar?" e nao e para "quanto foi o
EBITDA?".
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen
from pat.contracts.opportunity.base import Actor, Slug

__all__ = [
    "ChatTurnRecorded",
    "Intent",
    "TurnAction",
    "TurnRequest",
    "TurnResponse",
]


class Intent(StrEnum):
    """O que o usuario quer deste turno. Conjunto FECHADO.

    Um conjunto aberto - "o modelo decide o que fazer" - permitiria que uma
    frase nova produzisse comportamento que ninguem previu, e num sistema que
    grava tudo num diario isso e estado que nao da para dobrar depois.
    """

    ASK = "ask"
    """Pergunta sobre a empresa. Pode virar consulta ao motor, busca no corpus,
    ou uma resposta a partir do que ja esta no workspace."""

    INVESTIGATE = "investigate"
    """"Investiga isso." Decompoe em agenda e executa."""

    ASSERT = "assert"
    """O usuario afirma algo - "acho que o moat e escala". Vira hipotese, com
    o falsificador que o proprio usuario ou o agente declara. NUNCA vira
    claim: claim exige evidencia, e uma afirmacao do usuario e o comeco de uma
    investigacao, nao o fim."""

    CHALLENGE = "challenge"
    """"Mas e a concorrencia?" Pede contra-evidencia sobre algo ja afirmado."""

    RESUME = "resume"
    """"Volta naquela hipotese de margem." Retoma um fio."""

    STATUS = "status"
    """"Onde estamos?" """

    CRITIQUE = "critique"
    """Roda o advogado do diabo."""

    UNCLEAR = "unclear"
    """Nao casou com nada. O agente pergunta de volta em vez de adivinhar:
    adivinhar errado gasta uma corrida de pesquisa e enche a agenda de tarefa
    que ninguem pediu."""


class TurnAction(StrEnum):
    """O que o turno DE FATO fez. Registrado separado do que ele disse.

    Separar existe porque uma resposta bem escrita e indistinguivel, na
    leitura, de uma resposta bem escrita que nao fez nada. A lista de acoes
    responde "o que mudou no workspace?", e ela e vazia quando nada mudou.
    """

    ANSWERED_FROM_STATE = "answered_from_state"
    QUERIED_ENGINE = "queried_engine"
    SEARCHED_CORPUS = "searched_corpus"
    PLANNED_AGENDA = "planned_agenda"
    RAN_TASKS = "ran_tasks"
    OPENED_HYPOTHESIS = "opened_hypothesis"
    ADDED_COUNTER_EVIDENCE = "added_counter_evidence"
    RAN_CRITIQUE = "ran_critique"
    ASKED_BACK = "asked_back"
    REFUSED = "refused"


class TurnRequest(Frozen):
    """O que o usuario digitou, e o workspace onde isso acontece."""

    text: str = Field(min_length=1, max_length=4000)
    workspace_id: str = Field(min_length=16, max_length=16)
    intent_hint: Intent | None = Field(
        default=None,
        description=(
            "Intencao forcada pelo usuario, via comando explicito. Vence a "
            "classificacao: quem digitou sabe melhor do que o classificador"
        ),
    )


class TurnResponse(Frozen):
    """O que o agente respondeu, e o que ele fez para poder responder."""

    turn_index: int = Field(ge=0)
    intent: Intent
    text: str = Field(min_length=1)
    actions: tuple[TurnAction, ...] = ()
    grounded_in: tuple[str, ...] = Field(
        default=(),
        description="`result_id` e `unit_id` que sustentam a resposta",
    )
    tasks_touched: tuple[Slug, ...] = ()
    hypotheses_touched: tuple[Slug, ...] = ()
    follow_up: tuple[str, ...] = Field(
        default=(), description="Linhas de investigacao que o agente sugere"
    )
    disagreement: str | None = Field(
        default=None,
        description=(
            "O agente discorda do usuario, com evidencia. Preenchido so quando ha "
            "contra-evidencia registrada apontando para o contrario"
        ),
    )
    as_of: date
    at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> "TurnResponse":
        if self.intent is Intent.UNCLEAR and TurnAction.ASKED_BACK not in self.actions:
            raise ValueError(
                "turno UNCLEAR tem que perguntar de volta. Um turno que nao entendeu e "
                "nao pergunta produziu uma resposta a uma pergunta que ninguem fez."
            )
        return self

    @property
    def changed_workspace(self) -> bool:
        somente_leitura = {
            TurnAction.ANSWERED_FROM_STATE,
            TurnAction.ASKED_BACK,
            TurnAction.REFUSED,
        }
        return bool(set(self.actions) - somente_leitura)


class ChatTurnRecorded(Frozen):
    """O turno, no diario.

    A fala fica junto do que ela causou. Uma conversa cujo registro so guarda
    o texto vira transcricao; o que faz dela auditoria e `actions` do lado.
    """

    kind: Literal["chat_turn_recorded"] = "chat_turn_recorded"
    turn_index: int = Field(ge=0)
    user_text: str = Field(min_length=1)
    intent: Intent
    response_text: str = Field(min_length=1)
    actions: tuple[TurnAction, ...] = ()
    grounded_in: tuple[str, ...] = ()
    actor_of_response: Actor = Actor.AGENT

"""L4 - camada conversacional: sessoes, turnos e apresentacao.

O chat e uma casca sobre a Fase 3. Ele nao constroi motor, nao executa passo,
nao valida, nao resolve e nao formata numero: monta a pergunta, guarda a
conversa e chama `plan_for_question` e `run_plan`, que sao as duas funcoes que
compoem em processo. Toda a aritmetica continua onde sempre esteve - os nomes
das funcoes internas da Fase 3 nem aparecem aqui, e ha guard de camada exigindo
isso.

O cliente de LLM e INJETADO, nunca construido aqui - a mesma regra que
`research/__init__.py` documenta por extenso. Se este arquivo importasse o
adapter, o pacote ganharia saida de rede na importacao e o guard de camada que
exige que a rede caiba em um arquivo so deixaria de dizer o que diz. Quem monta
o cliente e `cli.py:cmd_serve`, pelo mesmo `_llm_client` que `pat plan` e
`pat ask --writer` usam.
"""

from __future__ import annotations

import threading
from datetime import date

from pat.chat.session import ConversationState, SessionStore
from pat.chat.turn import run_turn
from pat.chat.view import turn_view
from pat.config import Paths
from pat.contracts.chat import ChatRequest, ChatTurn
from pat.research import DEFAULT_SOURCE

__all__ = ["ChatService", "UnknownSession"]


class UnknownSession(LookupError):
    """`session_id` valido na forma e inexistente no servidor."""


class ChatService:
    """A raiz de composicao do chat. `http.py` so fala com ela."""

    def __init__(
        self,
        *,
        paths: Paths,
        llm,
        model: str,
        source: str = DEFAULT_SOURCE,
        git_sha: str | None = None,
        default_as_of: date | None = None,
    ) -> None:
        self.paths = paths
        self.model = model
        self.source = source
        self.git_sha = git_sha
        self.default_as_of = default_as_of
        """`as_of` das sessoes que nao pedirem um. Existe para que `pat serve
        --as-of` signifique alguma coisa sem que a UI tenha que repeti-lo em
        toda sessao nova; continua sendo escolha por sessao, nunca por
        mensagem."""
        self._llm = llm
        self._sessions = SessionStore(paths.chat)
        self._snapshot = None
        self._capability_sha256: str | None = None

        # Um turno por vez, no processo inteiro. O DuckDB e single-writer, e um
        # turno abre leitura e escrita em sequencia sobre o mesmo arquivo: duas
        # abas do mesmo browser disparando duas mensagens ao mesmo tempo
        # colidiriam no meio da segunda fase. O lock tambem torna o custo
        # previsivel - duas mensagens simultaneas sao duas chamadas de modelo em
        # paralelo, e este e um chat pessoal, nao um servico.
        self._lock = threading.Lock()

    # -- sessoes ------------------------------------------------------------

    def create_session(self, *, as_of: date | None = None) -> ConversationState:
        """`as_of` default e hoje, e depois disso e imutavel: trocar abre sessao
        nova. Ver `ConversationState`."""
        return self._sessions.create(as_of=as_of or self.default_as_of or date.today())

    def get_session(self, session_id: str) -> ConversationState | None:
        return self._sessions.get(session_id)

    # -- turno --------------------------------------------------------------

    def send_message(self, request: ChatRequest) -> ChatTurn:
        with self._lock:
            state = self._sessions.get(request.session_id)
            if state is None:
                raise UnknownSession(request.session_id)
            turn = run_turn(
                request,
                state,
                paths=self.paths,
                llm=self._llm,
                model=self.model,
                source=self.source,
                git_sha=self.git_sha,
            )
            self._sessions.append(state, turn)
            return turn

    # -- apresentacao -------------------------------------------------------

    def view(self, turn: ChatTurn) -> dict:
        return turn_view(turn, entity_names=self.entity_names())

    def entity_names(self) -> dict[str, str]:
        cards = self.snapshot().entities
        return {card.entity_id: card.denom_cia for card in cards if card.denom_cia}

    def capability(self) -> dict:
        """O que o sistema sabe executar, para a UI mostrar antes de perguntar."""
        snapshot = self.snapshot()
        return {
            "capability_sha256": self._capability_sha256,
            "built_at": snapshot.built_at.isoformat(),
            "entities": [
                {
                    "entity_id": card.entity_id,
                    "denom_cia": card.denom_cia,
                    "cod_cvm": card.cod_cvm,
                    "period_ends": [p.isoformat() for p in card.period_ends],
                    "scopes": [str(s) for s in card.scopes],
                    "has_own_mapping": card.has_own_mapping,
                }
                for card in snapshot.entities
            ],
            "metrics": [
                {"ref": card.ref, "kind": card.kind, "definition": card.definition}
                for card in snapshot.metrics
            ],
            "derivations": [
                {"op": str(card.op), "arity": card.arity, "refusals": list(card.refusals)}
                for card in snapshot.derivations
            ],
            "scopes": [str(s) for s in snapshot.scopes],
            "limits": {
                "max_steps": snapshot.limits.max_steps,
                "max_entities": snapshot.limits.max_entities,
                "max_periods_per_entity": snapshot.limits.max_periods_per_entity,
            },
            "model": self.model,
        }

    def snapshot(self):
        """O snapshot de capacidade, montado uma vez por processo.

        Memoizado de proposito: `GET /api/capability` nao pega o lock (para que
        a UI carregue enquanto um turno roda), e abrir uma conexao de leitura
        no meio da fase de escrita de um turno colidiria no DuckDB. Quem acabou
        de rodar `pat build` reinicia o servidor - que e o preco honesto de nao
        ter duas visoes do warehouse vivas ao mesmo tempo.
        """
        if self._snapshot is None:
            from pat.research.capability import build_snapshot, snapshot_sha256
            from pat.store.db import connect

            conn = connect(self.paths.warehouse, read_only=True)
            try:
                snapshot = build_snapshot(conn, source=self.source)
            finally:
                conn.close()
            self._snapshot = snapshot
            self._capability_sha256 = snapshot_sha256(snapshot)
        return self._snapshot

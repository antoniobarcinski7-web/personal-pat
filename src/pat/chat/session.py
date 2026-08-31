"""Estado de conversa: turnos em memoria, log append-only em disco.

O estado da sessao nao e fato e nao tem invariante bitemporal, entao ele nao
entra no warehouse. Uma linha JSON por turno em `data/chat/<session_id>.jsonl`
basta para a UI saber qual turno veio antes de qual; a auditoria do turno vive
em `research_run` e `llm_call`, gravada por `chat/record.py`.

Este modulo tambem e onde a conversa vira prompt - `build_context` - e por isso
ele carrega as quatro restricoes que impedem a camada de chat de furar a regra
central do projeto. Elas estao escritas em `build_context`, uma a uma.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from pat.contracts.chat import ChatTurn, ConversationContext, RefusalKind, TurnPlanSummary

SESSION_ID_RE = re.compile(r"^[0-9a-f]{16}$")

CONTEXT_WINDOW = 4
"""Quatro turnos, e nao "todos": `max_entities` ja limita o plano a 4 empresas,
o snapshot ja ocupa a maior parte do prompt, e contexto longo e onde um modelo
comeca a arrastar coisa de turno antigo para a pergunta atual. O teto tambem
esta no contrato (`ConversationContext.turns` tem `max_length=8`), de proposito:
janela e politica de quem monta, teto e propriedade da forma."""

_MODEL_FAILURES = (RefusalKind.PLANNER_FAILED, RefusalKind.WRITER_FAILED)


class InvalidSessionId(ValueError):
    """`session_id` que nao casa com `^[0-9a-f]{16}$`.

    Levantado antes de o valor virar caminho de arquivo - e nao depois, com um
    `..` ja resolvido pelo sistema operacional.
    """


def new_session_id(*, created_at: datetime | None = None, nonce: bytes | None = None) -> str:
    """Identificador emitido pelo SERVIDOR, nunca escolhido pelo cliente.

    Sha256 de (instante, nonce) truncado em 16 hex. Vir do servidor e o que
    torna path traversal por `session_id` impossivel na origem, em vez de
    depender de uma validacao que alguem pode remover por parecer redundante.
    """
    momento = (created_at or datetime.now(UTC)).isoformat()
    material = momento.encode("utf-8") + (nonce if nonce is not None else os.urandom(16))
    return hashlib.sha256(material).hexdigest()[:16]


def check_session_id(session_id: str) -> str:
    if not SESSION_ID_RE.match(session_id):
        raise InvalidSessionId(f"session_id invalido: {session_id!r}")
    return session_id


@dataclass
class ConversationState:
    """Uma conversa aberta.

    `as_of` e da sessao inteira e nao da mensagem: numa conversa que atravessa
    a meia-noite, `as_of` variavel faria "isso mudou?" comparar duas visoes do
    mundo diferentes sem avisar ninguem. Trocar `as_of` abre sessao nova.
    """

    session_id: str
    as_of: date
    created_at: datetime
    turns: list[ChatTurn] = field(default_factory=list)


class SessionStore:
    """Sessoes em memoria, com log append-only por sessao.

    Reler do disco existe para que reiniciar o servidor nao apague a conversa
    da aba aberta. O que se recupera e exatamente o que foi gravado - os
    `ChatTurn` -, e `as_of` e `created_at` sao lidos do primeiro turno, nunca
    recalculados a partir do relogio de hoje: recalcular trocaria a visao do
    mundo da sessao em silencio, que e o que `as_of` fixo existe para impedir.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._sessions: dict[str, ConversationState] = {}

    def _path(self, session_id: str) -> Path:
        return self._root / f"{check_session_id(session_id)}.jsonl"

    def create(self, *, as_of: date) -> ConversationState:
        created_at = datetime.now(UTC)
        state = ConversationState(
            session_id=new_session_id(created_at=created_at),
            as_of=as_of,
            created_at=created_at,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        self._sessions[state.session_id] = state
        return state

    def get(self, session_id: str) -> ConversationState | None:
        check_session_id(session_id)
        if (state := self._sessions.get(session_id)) is not None:
            return state
        return self._load(session_id)

    def append(self, state: ConversationState, turn: ChatTurn) -> None:
        state.turns.append(turn)
        self._sessions[state.session_id] = state
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(state.session_id)
        # Append-only, uma linha por turno: reescrever o arquivo inteiro a cada
        # turno permitiria perder os anteriores num erro de escrita no meio.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(turn.model_dump_json() + "\n")

    def _load(self, session_id: str) -> ConversationState | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        turns = [
            ChatTurn.model_validate_json(linha)
            for linha in path.read_text(encoding="utf-8").splitlines()
            if linha.strip()
        ]
        if not turns:
            return None
        state = ConversationState(
            session_id=session_id,
            as_of=turns[0].question.as_of,
            created_at=turns[0].asked_at,
            turns=turns,
        )
        self._sessions[session_id] = state
        return state


def summarize(turn: ChatTurn) -> TurnPlanSummary:
    """Um turno anterior na unica forma que o planejador ve: estrutura.

    Nenhum valor entra aqui - nem `Decimal`, nem `rendered_value`, nem
    `result_id`, nem `manifest_id`, nem mensagem de `ResearchWarning` (as de
    CHECK_FAILED carregam `observed`/`expected`, que sao numeros do motor). E o
    mesmo recorte que `research/writer.py:evidence_payload` ja faz, pela mesma
    razao: o que atravessa a fronteira do modelo nao pode carregar numero.
    """
    entity_ids: list[str] = []
    metric_refs: list[str] = []
    period_ends: list[date] = []
    ops: list = []

    plan = turn.plan
    if plan is not None:
        for step in plan.steps:
            if step.step_kind == "metric":
                if step.entity_id not in entity_ids:
                    entity_ids.append(step.entity_id)
                if (ref := str(step.metric)) not in metric_refs:
                    metric_refs.append(ref)
                if step.period_end not in period_ends:
                    period_ends.append(step.period_end)
            elif step.op not in ops:
                ops.append(step.op)

    if turn.answer is not None:
        outcome = "answered"
        codes: tuple[str, ...] = ()
    else:
        recusa = turn.refusal
        outcome = "failed" if recusa.kind in _MODEL_FAILURES else "refused"
        # A recusa entra no contexto porque "isso ja foi recusado por X" e a
        # correcao de curso mais barata que existe: ela nao custa chamada
        # nenhuma e evita o modelo replanejar identico o que ja foi negado.
        codes = recusa.codes or (str(recusa.kind),)

    return TurnPlanSummary(
        question_text=turn.question.text,
        objective=plan.objective if plan is not None else None,
        entity_ids=tuple(entity_ids),
        metric_refs=tuple(metric_refs),
        period_ends=tuple(period_ends),
        scope=plan.scope if plan is not None else None,
        derivation_ops=tuple(ops),
        outcome=outcome,
        refusal_codes=codes,
    )


def build_context(
    state: ConversationState, *, window: int = CONTEXT_WINDOW
) -> ConversationContext | None:
    """Turnos anteriores -> contexto estrutural. `None` quando nao ha nenhum.

    Quatro restricoes moram aqui, e nenhuma delas e excesso de zelo:

    1. Sessao sem turno devolve `None`, e nao contexto vazio. Contexto vazio
       mudaria o prompt do primeiro turno de uma conversa em relacao ao de
       `pat plan`, e as duas coisas sao a mesma pergunta. O prompt sem contexto
       tem que sair byte a byte igual ao de hoje - e o que
       `tests/research/test_planner.py::test_sem_contexto_o_prompt_e_o_de_sempre_byte_a_byte`
       prova, e a unica evidencia de que a M4.1 nao mexeu no planejador.

    2. Nenhum valor entra. So os campos de `TurnPlanSummary`, montados por
       `summarize` - ver o comentario de la.

    3. Contexto NAO vira pino. `pinned_periods` e whitelist soberana em
       `validate.py`: herdar `pinned_periods=(2024,)` do turno anterior faria
       "E como isso mudou desde 2023?" ser recusado com PIN_CONTRADICTED_PERIOD.
       Pino e o que o USUARIO fixou, vem do `ChatRequest` e de mais lugar
       nenhum. E a armadilha mais provavel de alguem reintroduzir depois, por
       parecer conveniencia.

    4. `as_of` e o da sessao, nunca o de hoje: ver `ConversationState`.
    """
    if not state.turns:
        return None
    recentes = state.turns[-window:] if window > 0 else []
    if not recentes:
        return None
    return ConversationContext(
        as_of=state.as_of,
        turns=tuple(summarize(turn) for turn in recentes),
    )

"""Fixtures da camada conversacional.

Warehouse de verdade - o golden do GPA, pelo caminho de producao - e modelo
falso. E a mesma combinacao da Fase 3: o duplo prova que a fiacao esta certa
sem gastar token, e os numeros que aparecem nos turnos continuam vindo do
motor, sobre fatos que passaram pelo parser.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from pat.config import resolve_paths
from pat.research.llm import LLMResponse
from tests.semantics import golden_gpa as gpa
from tests.semantics.conftest import load_zips

AS_OF = gpa.FY2024.replace(year=2025, month=6, day=30)
CHAMADO_EM = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

PLANO_BOM = json.dumps(
    {
        "objective": "margem EBITDA consolidada do GPA em FY2024",
        "as_of": AS_OF.isoformat(),
        "scope": "consolidated",
        "steps": [
            {
                "step_id": "margem_fy2024",
                "step_kind": "metric",
                "metric": {"name": "margem_ebitda", "version": "v1"},
                "entity_id": gpa.ENTITY_ID,
                "period_end": "2024-12-31",
            }
        ],
        "outputs": ["margem_fy2024"],
        "assumptions": [],
        "unresolved": [],
    }
)

PLANO_COM_DUVIDA = json.dumps(
    {
        "objective": "comparar a margem EBITDA de tres empresas",
        "as_of": AS_OF.isoformat(),
        "scope": "consolidated",
        "steps": [
            {
                "step_id": "margem_fy2024",
                "step_kind": "metric",
                "metric": {"name": "margem_ebitda", "version": "v1"},
                "entity_id": gpa.ENTITY_ID,
                "period_end": "2024-12-31",
            }
        ],
        "outputs": ["margem_fy2024"],
        "assumptions": [],
        "unresolved": [
            {
                "kind": "ambiguous_entity",
                "detail": "qual empresa? a pergunta cita 'a varejista'",
                "candidates": [gpa.ENTITY_ID, "br:cnpj:00000000000191"],
            }
        ],
    }
)

PLANO_SEM_DADO = json.dumps(
    {
        "objective": "margem EBITDA de uma empresa que o warehouse nao cobre",
        "as_of": AS_OF.isoformat(),
        "scope": "consolidated",
        "steps": [
            {
                "step_id": "margem_fy2024",
                "step_kind": "metric",
                "metric": {"name": "margem_ebitda", "version": "v1"},
                "entity_id": "br:cnpj:00000000000191",
                "period_end": "2024-12-31",
            }
        ],
        "outputs": ["margem_fy2024"],
        "assumptions": [],
        "unresolved": [],
    }
)

PROSA = json.dumps(
    {
        "prose": "A margem EBITDA consolidada do GPA foi de {{s:margem_fy2024}} no exercicio.",
        "interpretations": [
            {
                "text": "A rentabilidade operacional do periodo foi modesta.",
                "supports": ["margem_fy2024"],
            }
        ],
    }
)


class FakeLLMClient:
    """Um cliente, dois papeis, escolhidos pelo system prompt.

    Despacha por conteudo do system prompt e nao por ordem de chamada: um duplo
    que dependesse da ordem passaria a mentir no dia em que o turno deixasse de
    chamar o escritor por ultimo.
    """

    def __init__(
        self,
        *,
        plan_text: str = PLANO_BOM,
        prose_text: str = PROSA,
        plan_stop: str = "end_turn",
        prose_stop: str = "end_turn",
        raises=None,
    ) -> None:
        self.plan_text = plan_text
        self.prose_text = prose_text
        self.plan_stop = plan_stop
        self.prose_stop = prose_stop
        self.raises = raises
        self.roles: list[str] = []
        self.requests: list = []

    @property
    def fingerprint(self) -> str:
        return "fake/v1/00000000"

    def complete(self, request) -> LLMResponse:
        if self.raises is not None:
            raise self.raises
        redator = "redator" in request.system
        self.roles.append("writer" if redator else "planner")
        self.requests.append(request)
        return LLMResponse.for_text(
            self.prose_text if redator else self.plan_text,
            model_id="fake-model-20260101",
            prompt_sha256=request.prompt_sha256,
            stop_reason=self.prose_stop if redator else self.plan_stop,
            called_at=CHAMADO_EM,
        )


@pytest.fixture
def paths(tmp_path):
    """PAT_HOME com o warehouse do GPA materializado pelo caminho de producao."""
    from pat.store.bronze import BronzeStore
    from pat.store.db import connect, migrate

    paths = resolve_paths(tmp_path / "home").ensure()
    conn = connect(paths.warehouse)
    migrate(conn)
    load_zips(conn, BronzeStore(paths.bronze), gpa.zips())
    conn.close()
    return paths


@pytest.fixture
def llm():
    return FakeLLMClient()


@pytest.fixture
def service(paths, llm):
    from pat.chat import ChatService

    # `program_path=False` de proposito: estes testes cobrem o caminho da
    # Fase 3 - plano + escritor, uma chamada cada -, e o `FakeLLMClient` acima
    # devolve JSON daquela gramatica. O caminho da Fase 5 tem gramatica propria
    # e cobertura propria em `tests/chat/test_program_turn.py`.
    #
    # Fixar aqui e mais honesto do que mudar o default do servico: o que o
    # `pat serve` faz por padrao e uma decisao de produto, e nao algo que a
    # conveniencia de um fixture deva escolher.
    return ChatService(
        paths=paths,
        llm=llm,
        model="fake-model",
        default_as_of=AS_OF,
        program_path=False,
    )


@pytest.fixture
def store(tmp_path):
    from pat.chat.session import SessionStore

    return SessionStore(tmp_path / "chat")


def make_turn(session_id: str = "0" * 16, **overrides):
    """`ChatTurn` sintetico, para exercitar contexto e view sem rodar um turno."""
    from pat.contracts.chat import ChatTurn
    from pat.contracts.research import OutputKind, ResearchQuestion
    from pat.contracts.chat import RefusalKind, TurnRefusal

    question = ResearchQuestion(
        text=overrides.pop("text", "Qual foi a margem EBITDA do GPA em 2024?"),
        as_of=AS_OF,
        asked_at=CHAMADO_EM,
        requested_output=OutputKind.NARRATIVE,
    )
    base = {
        "turn_index": 0,
        "session_id": session_id,
        "asked_at": CHAMADO_EM,
        "question": question,
        "elapsed_ms": 1,
        "refusal": TurnRefusal(
            kind=RefusalKind.PLAN_UNRESOLVABLE,
            summary="nao tenho esse dado",
            codes=("unknown_entity",),
        ),
    }
    base.update(overrides)
    return ChatTurn(**base)

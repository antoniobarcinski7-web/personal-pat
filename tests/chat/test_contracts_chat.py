"""Os contratos da camada conversacional, conferidos na forma do tipo.

Nenhum destes testes constroi servidor, sessao ou cliente de modelo: o que se
afirma aqui e sobre a FORMA dos contratos, e forma se confere sem nada rodando.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from pat.contracts.chat import (
    ChatRequest,
    ChatTurn,
    ConversationContext,
    RefusalKind,
    TurnPlanSummary,
    TurnRefusal,
)
from pat.contracts.research import ResearchAnswer
from pat.contracts.semantics import ReportingScope
from pat.research.canonical import canonical_bytes, sha256_of
from tests.research.conftest import make_question

CHAT_CONTRACTS = (
    Path(__file__).resolve().parents[2] / "src" / "pat" / "contracts" / "chat.py"
)


def _fonte() -> str:
    return CHAT_CONTRACTS.read_text(encoding="utf-8")


def _classe(nome: str) -> ast.ClassDef:
    for node in ast.walk(ast.parse(_fonte())):
        if isinstance(node, ast.ClassDef) and node.name == nome:
            return node
    raise AssertionError(f"{nome} sumiu de contracts/chat.py")


# -- C-1 ---------------------------------------------------------------------


@pytest.mark.parametrize("nome", ["ConversationContext", "TurnPlanSummary"])
def test_o_contexto_conversacional_nao_tem_onde_por_um_numero(nome: str):
    """A propriedade estrutural da M4.1, conferida no texto do contrato.

    O contexto entra dentro do prompt do planejador. Se um campo aqui aceitasse
    `Decimal` - ou qualquer coisa derivada de um resultado do motor - o numero
    de um turno anterior chegaria ao modelo, e "todo numero vem do motor,
    recalculado neste turno" deixaria de ser propriedade do tipo e viraria
    promessa de prompt. O diff que fizer isso tem que quebrar aqui.
    """
    proibidos = ("Decimal", "MetricResult", "ComputationResult", "NumericClaim", "DerivedValue")
    classe = _classe(nome)

    for campo in classe.body:
        if not isinstance(campo, ast.AnnAssign):
            continue
        anotacao = ast.unparse(campo.annotation)
        for proibido in proibidos:
            assert proibido not in anotacao, (
                f"{nome}.{ast.unparse(campo.target)} aceita {proibido}: o contexto "
                "conversacional passou a poder carregar um valor ate o modelo"
            )


# -- C-2 ---------------------------------------------------------------------


def test_contratos_de_chat_nao_conhecem_implementacao():
    """Mesma regra de `contracts/research.py`, pela mesma razao: contrato
    depende so de contrato. `pat.contracts.research` e contrato e continua
    permitido - `pat.research.*` e implementacao e nao."""
    proibidos = (
        "pat.store",
        "pat.query",
        "pat.semantics",
        "pat.research",
        "pat.chat",
        "pat.parse",
        "pat.sources",
        "httpx",
        "duckdb",
        "os",
        "pathlib",
    )
    arvore = ast.parse(_fonte(), filename=str(CHAT_CONTRACTS))

    importados: set[str] = set()
    for node in ast.walk(arvore):
        if isinstance(node, ast.Import):
            importados.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            importados.add(node.module)

    for imported in sorted(importados):
        assert not imported.startswith(proibidos), (
            f"contracts/chat.py importa {imported}. Contrato depende so de contrato - "
            "e o que mantem a forma de um turno conferivel sem servidor e sem banco."
        )


# -- C-3 ---------------------------------------------------------------------


def _answer() -> ResearchAnswer:
    return ResearchAnswer(
        question_id="a" * 64,
        plan_id="b" * 64,
        prose="A margem EBITDA consolidada.",
        manifest_id="c" * 64,
    )


def _refusal() -> TurnRefusal:
    return TurnRefusal(
        kind=RefusalKind.PLAN_UNRESOLVABLE,
        summary="nao tenho esse dado",
        codes=("unknown_entity",),
    )


def _turno(**overrides) -> ChatTurn:
    base = {
        "turn_index": 0,
        "session_id": "0123456789abcdef",
        "asked_at": datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        "question": make_question(),
        "elapsed_ms": 1234,
    }
    base.update(overrides)
    return ChatTurn(**base)


def test_um_turno_e_resposta_ou_recusa_nunca_os_dois():
    """Turno com os dois campos - ou com nenhum - e o estado que a UI nao sabe
    mostrar, e que um dia seria mostrado como se fosse resposta."""
    with pytest.raises(ValidationError):
        _turno(answer=_answer(), refusal=_refusal())

    with pytest.raises(ValidationError):
        _turno()


def test_um_turno_com_exatamente_um_dos_dois_constroi():
    respondido = _turno(answer=_answer(), plan_id="b" * 64)
    assert respondido.refusal is None

    recusado = _turno(refusal=_refusal())
    assert recusado.answer is None
    assert recusado.refusal.codes == ("unknown_entity",)


# -- C-4 ---------------------------------------------------------------------


def test_o_pedido_de_chat_falha_na_fronteira():
    """`extra="forbid"` via `Frozen`: campo desconhecido no corpo do POST falha
    aqui, e nao tres camadas adiante ja como pino que ninguem pediu."""
    with pytest.raises(ValidationError):
        ChatRequest(session_id="0123456789abcdef", text="oi", as_of=date(2026, 8, 20))


@pytest.mark.parametrize(
    "session_id",
    [
        "",
        "0123456789ABCDEF",  # maiuscula nao e hex minusculo
        "0123456789abcde",  # curto demais
        "0123456789abcdef0",  # longo demais
        "../../etc/passwd",  # o motivo do padrao existir
    ],
)
def test_session_id_fora_do_padrao_e_rejeitado(session_id: str):
    """O padrao e conferido na fronteira porque `session_id` vira nome de
    arquivo em `data/chat/`. Validar depois seria validar tarde."""
    with pytest.raises(ValidationError):
        ChatRequest(session_id=session_id, text="oi")


def test_mensagem_vazia_e_rejeitada():
    with pytest.raises(ValidationError):
        ChatRequest(session_id="0123456789abcdef", text="")


# -- C-5 ---------------------------------------------------------------------


def _contexto() -> ConversationContext:
    return ConversationContext(
        as_of=date(2026, 8, 20),
        turns=(
            TurnPlanSummary(
                question_text="Compare Petrobras, Vale e WEG em EBITDA em 2024.",
                objective="comparar o EBITDA consolidado das tres em FY2024",
                entity_ids=("br:cnpj:33000167000101", "br:cnpj:33592510000154"),
                metric_refs=("ebitda@v1",),
                period_ends=(date(2024, 12, 31),),
                scope=ReportingScope.CONSOLIDATED,
                outcome="answered",
            ),
            TurnPlanSummary(
                question_text="E a margem EBITDA?",
                outcome="refused",
                refusal_codes=("unknown_entity",),
            ),
        ),
    )


def test_o_contexto_tem_forma_canonica_estavel():
    """O contexto entra no prompt via `canonical_bytes`. Se dois objetos iguais
    produzissem bytes diferentes, o `context_sha256` gravado no turno nao
    responderia "sob que contexto esta pergunta foi interpretada" - e o cache
    de LLM se fragmentaria por acidente de serializacao."""
    primeiro, segundo = _contexto(), _contexto()

    assert canonical_bytes(primeiro) == canonical_bytes(segundo)
    assert sha256_of(primeiro) == sha256_of(segundo)


def test_o_contexto_serializado_nao_carrega_valor():
    """A mesma afirmacao de C-1, do outro lado: o que sai nos bytes e o que o
    modelo le."""
    bytes_ = canonical_bytes(_contexto())

    for proibido in (b"rendered_value", b"result_id", b"manifest_id", b"fact_id"):
        assert proibido not in bytes_


def test_o_contexto_tem_teto_de_turnos():
    """Prompt que cresce sem limite muda de comportamento devagar, e o sintoma
    aparece longe da causa."""
    demais = tuple(
        TurnPlanSummary(question_text=f"pergunta {i}", outcome="answered") for i in range(9)
    )
    with pytest.raises(ValidationError):
        ConversationContext(as_of=date(2026, 8, 20), turns=demais)

"""A tabela `llm_call`: índice de chamadas, não identidade delas.

A propriedade central defendida aqui é o M-3: **`manifest_id` é nulável**. Um
plano recusado pelo validador produz uma chamada real — que custou dinheiro,
gerou uma resposta e tem rastro — e nenhum manifesto, porque nada executou.
Forçar a coluna a ser obrigatória obrigaria a inventar um `manifest_id` (o que
fabrica procedência) ou a descartar o registro (o que perde o rastro de uma
recusa, justamente o que o D-11 manda guardar).

A segunda propriedade: o cache **não** conhece `manifest_id`. Não é preferência
— `manifest_id` embute `executed_at`, então incluí-lo na identidade da chamada
faria toda consulta ser MISS e o cache jamais acertaria.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pat.contracts.research import PlanProvenance
from pat.research.llm import FakeLLMClient, LLMRequest
from pat.research.llm.cache import CachedLLMClient, InMemoryLLMCache, call_sha256
from pat.store.db import connect, migrate
from pat.store.llm_calls import count, orphan_calls, read_calls, write_call

CHAMADO_EM = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
GRAVADO_EM = datetime(2026, 8, 17, 12, 0, 5, tzinfo=UTC)
SHA = "a" * 64


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "w.duckdb")
    migrate(c)
    yield c
    c.close()


def _provenance(**overrides) -> PlanProvenance:
    base = dict(
        model_id="claude-opus-5-20260101",
        temperature=None,
        max_tokens=16384,
        system_prompt_sha256=SHA,
        prompt_sha256="b" * 64,
        response_sha256="c" * 64,
        capability_sha256="d" * 64,
        called_at=CHAMADO_EM,
        cached=False,
        client_fingerprint="anthropic/v1/deadbeef",
    )
    return PlanProvenance(**(base | overrides))


# -- o essencial: manifest_id é nulável (M-3) --------------------------------


def test_uma_chamada_pode_existir_sem_manifesto(conn):
    """O caso do plano recusado: a chamada aconteceu, nada executou."""
    assert write_call(conn, _provenance(), call_sha256=SHA, kind="planner") is True

    orfas = orphan_calls(conn)

    assert len(orfas) == 1
    assert orfas[0].manifest_id is None
    assert orfas[0].call_sha256 == SHA


def test_a_chamada_orfa_preserva_a_procedencia_inteira(conn):
    """Sem manifesto não significa sem rastro — é o ponto do M-3."""
    write_call(conn, _provenance(), call_sha256=SHA, kind="planner")

    linha = orphan_calls(conn)[0]

    assert linha.model_id == "claude-opus-5-20260101"
    assert linha.client_fingerprint == "anthropic/v1/deadbeef"
    assert linha.prompt_sha256 == "b" * 64
    assert linha.response_sha256 == "c" * 64
    assert linha.capability_sha256 == "d" * 64
    assert linha.called_at == CHAMADO_EM
    assert linha.max_tokens == 16384


def test_chamada_ligada_a_manifesto_nao_aparece_como_orfa(conn):
    write_call(conn, _provenance(), call_sha256=SHA, kind="planner", manifest_id="m1")

    assert orphan_calls(conn) == []
    assert len(read_calls(conn, "m1")) == 1


def test_as_chamadas_de_uma_corrida_saem_na_ordem_de_gravacao(conn):
    write_call(
        conn,
        _provenance(),
        call_sha256="a" * 64,
        kind="planner",
        manifest_id="m1",
        recorded_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
    )
    write_call(
        conn,
        _provenance(),
        call_sha256="b" * 64,
        kind="writer",
        manifest_id="m1",
        recorded_at=datetime(2026, 8, 17, 12, 5, tzinfo=UTC),
    )

    assert [linha.kind for linha in read_calls(conn, "m1")] == ["planner", "writer"]


# -- temperatura nula é o caso normal ----------------------------------------


def test_temperatura_nula_e_gravada_como_nula(conn):
    """Nulo registra que nenhum override foi pedido — não que ele foi zero."""
    write_call(conn, _provenance(), call_sha256=SHA, kind="planner")

    assert orphan_calls(conn)[0].temperature is None


def test_temperatura_pedida_sobrevive_a_ida_e_volta(conn):
    write_call(
        conn, _provenance(temperature=Decimal("0.7")), call_sha256=SHA, kind="planner"
    )

    assert orphan_calls(conn)[0].temperature == Decimal("0.7")


# -- append-only -------------------------------------------------------------


def test_a_mesma_chamada_no_mesmo_instante_nao_duplica(conn):
    args = dict(call_sha256=SHA, kind="planner", recorded_at=GRAVADO_EM)

    assert write_call(conn, _provenance(), **args) is True
    assert write_call(conn, _provenance(), **args) is False
    assert count(conn) == 1


def test_a_mesma_resposta_servida_duas_vezes_sao_dois_fatos(conn):
    """Um acerto de cache é uma corrida que aconteceu. `called_at` continua o
    da chamada original nas duas linhas; o que difere é `recorded_at`."""
    write_call(
        conn,
        _provenance(),
        call_sha256=SHA,
        kind="planner",
        recorded_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
    )
    write_call(
        conn,
        _provenance(cached=True),
        call_sha256=SHA,
        kind="planner",
        recorded_at=datetime(2026, 8, 18, 9, 0, tzinfo=UTC),
    )

    linhas = orphan_calls(conn)

    assert count(conn) == 2
    assert [linha.cached for linha in linhas] == [True, False]  # mais recente primeiro
    assert {linha.called_at for linha in linhas} == {CHAMADO_EM}


# -- a fronteira: o cache não conhece o manifesto ----------------------------


def test_o_cache_acerta_sem_saber_de_manifesto_nenhum(conn):
    """Se `manifest_id` entrasse na identidade da chamada, ele carregaria
    `executed_at` junto e toda consulta seria MISS."""
    request = LLMRequest(system="s", user="u", model="m", max_tokens=16384)
    inner = FakeLLMClient(called_at=CHAMADO_EM)
    inner.register(request, '{"objective": "x"}')
    cliente = CachedLLMClient(inner, InMemoryLLMCache())

    cliente.complete(request)
    segunda = cliente.complete(request)

    # A chamada é gravada com manifesto; a chave do cache não muda por isso.
    write_call(
        conn,
        _provenance(),
        call_sha256=call_sha256(request, inner.fingerprint),
        kind="planner",
        manifest_id="m1",
    )

    assert segunda.cached is True
    assert len(inner.calls) == 1


def test_o_schema_e_aditivo(tmp_path):
    """`CREATE TABLE IF NOT EXISTS`: warehouse anterior migra sozinho, e migrar
    duas vezes não é erro."""
    caminho = tmp_path / "w.duckdb"
    primeira = connect(caminho)
    migrate(primeira)
    primeira.close()

    segunda = connect(caminho)
    migrate(segunda)

    assert count(segunda) == 0
    segunda.close()

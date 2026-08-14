"""Camada semantica contra a CVM real. Marcado como `network`.

Os golden tests offline provam que o calculo esta certo. Este prova que o
*mapeamento* ainda aponta para as linhas certas no que a CVM publica hoje.

E a defesa contra a falha mais silenciosa da Fase 2: a CVM renumerar uma
subconta, o binding continuar resolvendo para alguma coisa, e o EBITDA passar
a somar a linha errada sem nada acusar. Aqui isso vira teste vermelho.

    pytest -m network

Nao roda em CI por depender de terceiro.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.semantics import Fidelity, MetricUnavailable, ReportingScope
from pat.parse.cvm_dfp import parse_dfp_zip
from pat.semantics import build_engine
from pat.sources.public.cvm import CVMProvider
from pat.store.db import connect, migrate
from pat.store.gold import build_facts, write_facts
from pat.store.silver import write_lines

pytestmark = pytest.mark.network

GPA = 14826
ENTITY = "br:cnpj:47508411000156"
FY2023 = date(2023, 12, 31)
CONSOLIDADO = ReportingScope.CONSOLIDATED


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    provider = CVMProvider()
    try:
        conn = connect(tmp_path_factory.mktemp("live") / "live.duckdb")
        migrate(conn)
        for year in (2023, 2024):
            (ref,) = provider.resolve("cvm.dfp", year=str(year))
            content = provider.fetch(ref).content
            lines, _ = parse_dfp_zip(
                content,
                year=year,
                content_sha256=hashlib.sha256(content).hexdigest(),
                retrieval_id=f"live-{year}",
                extraction_run_id="live",
                only_cod_cvm=frozenset({GPA}),
            )
            write_lines(conn, lines)
            facts, stats = build_facts(lines)
            assert stats.skipped_total == 0, stats.as_dict()
            write_facts(conn, facts)
        yield build_engine(conn)
        conn.close()
    finally:
        provider.close()


def _compute(engine, metric, as_of, period_end=FY2023):
    result = engine.compute(
        metric, entity_id=ENTITY, period_end=period_end, scope=CONSOLIDADO, as_of=as_of
    )
    assert not isinstance(result, MetricUnavailable), getattr(result, "message", "")
    return result


def test_o_mapeamento_do_gpa_ainda_resolve_contra_a_cvm_de_hoje(engine):
    """Se qualquer conta do mapeamento sumir ou for renumerada, quebra aqui."""
    for metric in ("receita_liquida@v1", "ebit@v1", "d_and_a@v1", "ebitda@v1", "margem_ebitda@v1"):
        result = _compute(engine, metric, date(2025, 6, 30))
        assert result.mapping_confirmed is True
        assert result.fidelity is Fidelity.EXACT


def test_rotulos_das_contas_mapeadas_nao_mudaram_na_origem(engine):
    """`label_as_reported` do mapeamento conferido contra o que a CVM publica.

    Nao e usado para busca - e justamente por isso precisa ser conferido: uma
    conta renomeada continuaria resolvendo em silencio."""
    from pat.semantics.loader import load_dir

    resolver = engine._resolvers[list(engine._resolvers)[0]]
    chain = load_dir().resolve(ENTITY, source="cvm.dfp")

    conferidos = 0
    for concept_id in ("revenue_net", "ebit_reported", "d_and_a_pnl", "d_and_a_retained"):
        binding, _ = chain.binding_for(concept_id)
        for line in binding.lines:
            if not line.address.label_as_reported:
                continue
            from pat.semantics import concepts

            fact = resolver.resolve(
                entity_id=ENTITY,
                address=line.address,
                period_end=FY2023,
                period_kind=concepts.get(concept_id).period_kind,
                scope=CONSOLIDADO,
                as_of=date(2025, 6, 30),
            )
            assert not hasattr(fact, "reason"), f"{concept_id}: {line.address.as_str()} sumiu"
            assert fact.label_as_reported == line.address.label_as_reported, (
                f"{concept_id}: a CVM renomeou {line.address.as_str()} para "
                f"{fact.label_as_reported!r}"
            )
            conferidos += 1
    assert conferidos >= 4


def test_ebitda_real_do_gpa_bate_com_o_conferido_a_mao(engine):
    """Os mesmos numeros do golden offline, agora vindos da fonte viva."""
    assert _compute(engine, "receita_liquida@v1", date(2024, 6, 30)).value == Decimal("19250000000")
    assert _compute(engine, "ebit@v1", date(2024, 6, 30)).value == Decimal("677000000")
    assert _compute(engine, "d_and_a@v1", date(2024, 6, 30)).value == Decimal("1136000000")
    assert _compute(engine, "ebitda@v1", date(2024, 6, 30)).value == Decimal("1813000000")


def test_reapresentacao_real_atravessa_a_camada_semantica(engine):
    """A prova de que a Fase 2 nao achatou a Fase 1."""
    antes = _compute(engine, "ebitda@v1", date(2024, 6, 30))
    depois = _compute(engine, "ebitda@v1", date(2025, 6, 30))

    assert antes.value == Decimal("1813000000")
    assert depois.value == Decimal("1796000000")
    assert antes.knowledge_date == date(2024, 2, 21)
    assert depois.knowledge_date == date(2025, 2, 18)

    margem_antes = _compute(engine, "margem_ebitda@v1", date(2024, 6, 30))
    margem_depois = _compute(engine, "margem_ebitda@v1", date(2025, 6, 30))
    assert margem_antes.value.quantize(Decimal("0.0001")) == Decimal("0.0942")
    assert margem_depois.value.quantize(Decimal("0.0001")) == Decimal("0.1009")


def test_divergencia_real_entre_d_and_a_do_dfc_e_da_dva(engine):
    """O motivo de `d_and_a_pnl` e `d_and_a_retained` serem conceitos distintos.

    Em FY2023 as duas quase coincidem; em FY2022 divergem 46%, por conta das
    operacoes descontinuadas. Um sistema que as tratasse como sinonimos daria
    um EBITDA errado em quase 900 milhoes num dos anos e certo no outro - o
    tipo de erro que passa despercebido justamente por ser intermitente.
    """
    result = _compute(engine, "d_and_a@v1", date(2025, 6, 30))
    (check,) = [c for c in result.checks if c.check_id == "proxima_da_retida"]

    assert check.status == "pass"
    assert check.observed == Decimal("1136000000")   # DFC, o que o GPA mapeia
    assert check.expected == Decimal("1122000000")   # DVA, reapresentada

    fy2022 = engine.compute(
        "d_and_a@v1",
        entity_id=ENTITY,
        period_end=date(2022, 12, 31),
        scope=CONSOLIDADO,
        as_of=date(2025, 6, 30),
    )
    (divergente,) = [c for c in fy2022.checks if c.check_id == "proxima_da_retida"]
    assert divergente.status == "fail", "a divergencia de FY2022 precisa aparecer"
    assert divergente.observed == Decimal("1902000000")
    assert divergente.expected == Decimal("1026000000")

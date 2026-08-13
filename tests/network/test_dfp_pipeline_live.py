"""Pipeline da Fase 1 contra a CVM real. Marcado como `network`.

Os testes sinteticos provam que a logica esta correta; estes provam que ela
esta correta *sobre o layout que a CVM publica hoje*. Sao a defesa contra
deriva silenciosa na origem: coluna renomeada, encoding trocado, arquivo
indice reorganizado.

    pytest -m network

Nao rodam em CI por dependerem de terceiro.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

import pytest

from pat.parse.cvm_dfp import parse_dfp_zip
from pat.query.asof import AsOf
from pat.sources.public.cvm import CVMProvider
from pat.store.db import connect, migrate
from pat.store.gold import build_facts, write_facts
from pat.store.silver import write_lines

pytestmark = pytest.mark.network

GPA = 14826
"""CIA BRASILEIRA DE DISTRIBUICAO (GPA). Escolhida por ter uma reapresentacao
real e verificavel de FY2023 - o caso que a Fase 1 existe para tratar."""

RECEITA = "3.01"
FY2023 = date(2023, 12, 31)


@pytest.fixture(scope="module")
def cvm():
    provider = CVMProvider()
    yield provider
    provider.close()


@pytest.fixture(scope="module")
def zips(cvm) -> dict[int, bytes]:
    """Baixa uma unica vez os dois anos usados pelos testes do modulo."""
    out = {}
    for year in (2023, 2024):
        (ref,) = cvm.resolve("cvm.dfp", year=str(year))
        out[year] = cvm.fetch(ref).content
    return out


def _parse(content: bytes, year: int):
    return parse_dfp_zip(
        content,
        year=year,
        content_sha256=hashlib.sha256(content).hexdigest(),
        retrieval_id=f"live-{year}",
        extraction_run_id="live",
        only_cod_cvm=frozenset({GPA}),
    )


def test_layout_real_e_parseavel_sem_perda(zips):
    """Se a CVM mudar nomes de coluna ou encoding, a perda aparece aqui como
    contador diferente de zero, em vez de virar buraco silencioso no gold."""
    lines, stats = _parse(zips[2024], 2024)

    assert lines, "nenhuma linha extraida: layout da DFP provavelmente mudou"
    assert stats.skipped_total == 0, f"perdas na extracao: {stats.as_dict()}"

    # Encoding latin-1 preservado e indice unido corretamente.
    assert all(line.denom_cia == "CIA BRASILEIRA DE DISTRIBUICAO" for line in lines)
    assert all(line.dt_receb is not None for line in lines)
    assert {line.ordem_exerc for line in lines} == {"ULTIMO", "PENULTIMO"}
    assert {line.escala_moeda for line in lines} == {"MIL"}

    # As demonstracoes que a Fase 2 vai precisar continuam todas presentes.
    assert {"DRE", "BPA", "BPP", "DFC_MI"} <= {line.statement for line in lines}


def test_contas_patrimoniais_reais_nao_tem_data_inicial(zips):
    lines, _ = _parse(zips[2024], 2024)
    bpa = [line for line in lines if line.statement == "BPA"]

    assert bpa
    assert all(line.dt_ini_exerc is None for line in bpa)


def test_reapresentacao_real_do_gpa_ponta_a_ponta(zips, tmp_path):
    """O caso que motiva o desenho bitemporal, com numeros publicos.

    A receita de FY2023 do GPA foi divulgada em fev/2024 como R$ 19,250 bi e
    reapresentada na DFP de 2024, em fev/2025, como R$ 17,793 bi - uma
    diferenca de R$ 1,457 bi. Uma consulta que ignorasse o eixo de
    conhecimento atribuiria hoje, retroativamente, um numero que ninguem
    poderia ter usado em 2024.
    """
    conn = connect(tmp_path / "live.duckdb")
    migrate(conn)
    try:
        for year in (2023, 2024):
            lines, _ = _parse(zips[year], year)
            write_lines(conn, lines)
            facts, stats = build_facts(lines)
            assert stats.skipped_total == 0, f"fatos descartados em {year}: {stats.as_dict()}"
            write_facts(conn, facts)

        asof = AsOf(conn)
        chave = dict(cod_cvm=GPA, cd_conta=RECEITA, period_end=FY2023)

        original = asof.value(**chave, as_of=date(2024, 6, 30))
        revisado = asof.value(**chave, as_of=date(2025, 6, 30))

        assert original.value == Decimal("19250000000")
        assert original.knowledge_date == date(2024, 2, 21)
        assert original.ordem_exerc == "ULTIMO"

        assert revisado.value == Decimal("17793000000")
        assert revisado.knowledge_date == date(2025, 2, 18)
        assert revisado.ordem_exerc == "PENULTIMO"

        # Antes da primeira divulgacao, nada era conhecido - e o sistema diz
        # isso, em vez de devolver o numero que so existiria depois.
        assert asof.value(**chave, as_of=date(2024, 1, 1)) is None

        # O GPA reapresentou individual e consolidado; a consulta devolve os
        # dois, porque o escopo faz parte da chave logica.
        items = asof.restatements(cod_cvm=GPA, cd_conta=RECEITA, statement="DRE")
        assert len(items) == 2

        (item,) = [i for i in items if i.consolidated]
        assert item.delta == Decimal("-1457000000")
        assert item.original.fact_id != item.revised.fact_id
    finally:
        conn.close()

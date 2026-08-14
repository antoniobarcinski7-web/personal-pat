"""Construcao da camada gold: linha silver -> fato bitemporal.

E aqui que a interpretacao acontece (escala, tipo de periodo) e aqui que o
contrato `Fact` tem a ultima palavra. Um fato que o contrato rejeita e
contado e descartado, nunca corrigido na marra.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.conftest import account_line

from pat.contracts.common import PeriodType
from pat.store import gold
from pat.store.gold import build_facts, count, write_facts


def _one(**kwargs):
    """Constroi um unico fato e devolve (GoldFact | None, BuildStats)."""
    facts, stats = build_facts([account_line(**kwargs)])
    return (facts[0] if facts else None), stats


# -- escala: a unica multiplicacao do pipeline -----------------------------


def test_escala_mil_vira_unidade_de_moeda():
    """A DFP e publicada em milhares. A mesma companhia pode declarar MIL em
    um exercicio e UNIDADE em outro; comparar valores crus daria uma variacao
    de 1000x inexistente."""
    fact, _ = _one(vl_conta=Decimal("19250.5"), escala_moeda="MIL")

    assert fact.fact.value == Decimal("19250500")
    assert fact.fact.unit == "BRL"
    assert fact.fact.currency == "BRL"


def test_escala_unidade_nao_multiplica():
    fact, _ = _one(vl_conta=Decimal("19250.5"), escala_moeda="UNIDADE")

    assert fact.fact.value == Decimal("19250.5")


def test_escala_desconhecida_e_descartada_e_contada():
    fact, stats = _one(escala_moeda="MILHAO")

    assert fact is None
    assert stats.skipped_unknown_scale == 1
    assert stats.skipped_total == 1


def test_moeda_desconhecida_e_descartada_e_contada():
    fact, stats = _one(moeda="DOLAR")

    assert fact is None
    assert stats.skipped_unknown_currency == 1


# -- tipo de periodo --------------------------------------------------------


def test_exercicio_de_doze_meses_vira_periodo_anual():
    fact, _ = _one(dt_ini_exerc=date(2023, 1, 1), dt_fim_exerc=date(2023, 12, 31))

    assert fact.fact.period_type is PeriodType.YEAR
    assert fact.fact.period_start == date(2023, 1, 1)
    assert fact.fact.period_end == date(2023, 12, 31)


def test_conta_sem_data_inicial_vira_saldo_pontual():
    fact, _ = _one(dt_ini_exerc=None, dt_fim_exerc=date(2023, 12, 31), statement="BPA")

    assert fact.fact.period_type is PeriodType.INSTANT
    assert fact.fact.period_start is None


def test_intervalo_irregular_e_descartado():
    """A DFP e anual por construcao: um intervalo de trimestre ali e sinal de
    anomalia na fonte, nao de dado util."""
    fact, stats = _one(dt_ini_exerc=date(2023, 10, 1), dt_fim_exerc=date(2023, 12, 31))

    assert fact is None
    assert stats.skipped_irregular_period == 1


# -- o contrato tem a ultima palavra ---------------------------------------


def test_fato_conhecido_antes_do_fim_do_periodo_e_rejeitado():
    """Um numero nao pode ser conhecido antes de existir. O contrato barra, o
    build conta, e nada disso passa em silencio."""
    fact, stats = _one(dt_fim_exerc=date(2023, 12, 31), dt_receb=date(2023, 6, 30))

    assert fact is None
    assert stats.skipped_contract_violation == 1
    assert stats.facts_built == 0


# -- identidade e linhagem --------------------------------------------------


def test_metrica_e_o_codigo_de_conta_com_namespace():
    """Fase 1 nao faz mapeamento semantico: metricas canonicas sao a Fase 2."""
    fact, _ = _one(statement="DRE", cd_conta="3.01")

    assert fact.fact.metric == "cvm:dre:3.01"
    assert fact.fact.metric_version == "raw"


def test_coluna_df_entra_na_metrica_da_dmpl():
    """Sem isso, componentes distintos do PL colapsam na mesma metrica."""
    fact, _ = _one(statement="DMPL", cd_conta="5.01", coluna_df="Capital Social")

    assert fact.fact.metric == "cvm:dmpl:5.01:Capital Social"


def test_fato_herda_identidade_e_linhagem_da_linha_silver():
    line = account_line()
    (fact,), _ = build_facts([line])

    assert fact.fact.fact_id == line.silver_id
    assert fact.fact.entity_id == "br:cnpj:12345678000199"
    assert fact.fact.knowledge_date == line.dt_receb  # DT_RECEB da CVM
    assert fact.fact.lineage.content_sha256 == line.content_sha256
    assert fact.fact.lineage.retrieval_id == line.retrieval_id
    assert fact.fact.lineage.locator == line.locator
    assert fact.fact.lineage.extractor_version == line.extractor_version


# -- persistencia: append-only e idempotente --------------------------------


def test_gravar_duas_vezes_nao_duplica(warehouse):
    facts, _ = build_facts([account_line()])

    assert write_facts(warehouse, facts) == 1
    assert write_facts(warehouse, facts) == 0  # ids deterministicos => no-op
    assert count(warehouse) == 1


def test_duas_versoes_do_mesmo_periodo_coexistem(warehouse):
    """Reapresentacao nao sobrescreve: e uma linha nova com knowledge_date
    posterior. A diferenca entre as duas e informacao."""
    original = account_line(
        silver_id="f" * 32, vl_conta=Decimal("1000"), dt_receb=date(2024, 2, 20)
    )
    revisado = account_line(
        silver_id="e" * 32, vl_conta=Decimal("900"), dt_receb=date(2025, 2, 20)
    )
    facts, _ = build_facts([original, revisado])
    write_facts(warehouse, facts)

    valores = warehouse.execute(
        "SELECT knowledge_date, value FROM gold_fact ORDER BY knowledge_date"
    ).fetchall()
    assert valores == [
        (date(2024, 2, 20), Decimal("1000000.0000000000")),
        (date(2025, 2, 20), Decimal("900000.0000000000")),
    ]


def test_period_type_e_gravado_como_texto_legivel(warehouse):
    """Se o StrEnum vazasse como 'PeriodType.YEAR', todo filtro por periodo
    no SQL quebraria em silencio."""
    facts, _ = build_facts([account_line()])
    write_facts(warehouse, facts)

    assert warehouse.execute("SELECT DISTINCT period_type FROM gold_fact").fetchone()[0] == "year"


def test_escrita_vazia_e_no_op(warehouse):
    assert write_facts(warehouse, []) == 0
    assert gold.count(warehouse) == 0

"""Warehouses de teste do Opportunity: uma empresa em cada jurisdicao.

Duas empresas de verdade, pelo caminho de producao - o ZIP da fonte brasileira
passa pelo parser, e as linhas americanas passam por `write_sec_facts`. Nao ha
fato sintetico inserido direto no gold: um teste do Opportunity que inserisse
fato a mao provaria que a mesa de ferramentas le uma tabela, e nao que ela le o
que o PAT produz.

As duas juntas sao o que torna a regra de camada testavel. Uma jurisdicao so
nao distingue "generico" de "brasileiro por acidente" - o mesmo argumento que
`tests/semantics/test_second_framework.py` faz para o motor.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pat.contracts.silver import XbrlFactLine

INTEL_CIK = "0000050863"
INTEL_ENTITY = f"us:cik:{INTEL_CIK}"
INTEL_NAME = "INTEL CORP"
INTEL_FY2023 = date(2023, 12, 30)
INTEL_FY2024 = date(2024, 12, 28)
INTEL_FILED = date(2025, 1, 31)

# Transcricao a mao do 10-K da Intel, em USD. Os mesmos numeros de
# `tests/semantics/test_golden_intel.py`, aqui passando pelo warehouse de
# verdade em vez de por um resolver de mentira - e por isso os dois testes nao
# sao redundantes: la se prova o calculo, aqui se prova o caminho.
INTEL_PUBLICADO: dict[tuple[str, date], str] = {
    ("RevenueFromContractWithCustomerExcludingAssessedTax", INTEL_FY2023): "54228000000",
    ("RevenueFromContractWithCustomerExcludingAssessedTax", INTEL_FY2024): "53101000000",
    ("CostOfGoodsAndServicesSold", INTEL_FY2023): "32517000000",
    ("CostOfGoodsAndServicesSold", INTEL_FY2024): "35756000000",
    ("GrossProfit", INTEL_FY2023): "21711000000",
    ("GrossProfit", INTEL_FY2024): "17345000000",
    ("OperatingExpenses", INTEL_FY2023): "21618000000",
    ("OperatingExpenses", INTEL_FY2024): "29023000000",
    ("OperatingIncomeLoss", INTEL_FY2023): "93000000",
    ("OperatingIncomeLoss", INTEL_FY2024): "-11678000000",
    ("Depreciation", INTEL_FY2023): "7847000000",
    ("Depreciation", INTEL_FY2024): "9951000000",
    ("AmortizationOfIntangibleAssets", INTEL_FY2023): "1755000000",
    ("AmortizationOfIntangibleAssets", INTEL_FY2024): "1428000000",
}


def intel_lines() -> list[XbrlFactLine]:
    """As linhas silver da Intel, com procedencia coerente.

    `filed` e o `knowledge_date` do lado americano; sem ele nao existe consulta
    AS OF, e o teste de `as_of` da mesa nao provaria nada.
    """
    linhas = []
    for i, ((element, period_end), valor) in enumerate(sorted(INTEL_PUBLICADO.items())):
        inicio = date(period_end.year, 1, 1)
        linhas.append(
            XbrlFactLine(
                silver_id=f"intel-{i:04d}",
                content_sha256="e" * 64,
                retrieval_id="ret-intel-1",
                source_member="companyfacts.json",
                source_line_no=i,
                cik=INTEL_CIK,
                entity_name=INTEL_NAME,
                taxonomy="us-gaap",
                element=element,
                period_start=inicio,
                period_end=period_end,
                fiscal_year=period_end.year,
                fiscal_period="FY",
                value=Decimal(valor),
                unit="USD",
                accession="0000050863-25-000006",
                form="10-K",
                filed=INTEL_FILED,
                extractor="tests.opportunity.warehouses",
                extractor_version="1.0.0",
                extraction_run_id="run-intel-1",
            )
        )
    return linhas


def load_intel(conn) -> None:
    """Grava a Intel no warehouse, pelo caminho de producao."""
    from pat.build_sec import write_sec_facts

    write_sec_facts(conn, intel_lines())

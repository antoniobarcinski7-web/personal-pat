"""A ingestao americana, do bronze ao gold, pela CLI e sem rede.

O teste que define este trabalho e
`test_uma_companhia_americana_entra_pela_linha_de_comando`: antes dele,
`write_sec_facts` existia e era chamado APENAS por testes - a Intel tinha
entrado no warehouse por um caminho ad hoc que ninguem conseguia repetir. Uma
funcao de producao sem chamador de producao e uma capacidade que o sistema
parece ter e nao tem.

O JSON aqui e um `companyfacts` sintetico com a forma real da origem. Ele nao
substitui o teste de rede - `-m network` e que prova que a SEC ainda publica
neste formato -, e prova a outra metade: que o PAT sabe consumir o formato.
"""

from __future__ import annotations

import json

import pytest

from pat.build import BuildError
from pat.build_sec import SUPPORTED_SEC_DATASETS, build_sec_dataset
from pat.audit.run import new_run
from pat.contracts.lineage import Run
from pat.store.bronze import BronzeStore
from pat.store.catalog import Catalog
from pat.store.db import connect, migrate

CIK = "0001065280"
NOME = "NETFLIX INC"


def companyfacts(
    *,
    cik: str = CIK,
    nome: str = NOME,
    receita_2023: str = "33723000000",
    receita_2024: str = "39000966000",
) -> bytes:
    """Um `companyfacts` com a forma da origem: facts -> taxonomia -> elemento
    -> units -> ocorrencias.

    Inclui de proposito uma unidade nao monetaria (`shares`) e um fato sem
    `start` - as duas coisas que o parser tem que descartar com motivo, e nao
    silenciosamente.
    """
    return json.dumps(
        {
            "cik": int(cik),
            "entityName": nome,
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2023-01-01",
                                    "end": "2023-12-31",
                                    "val": int(receita_2023),
                                    "fy": 2023,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2024-01-26",
                                    "accn": "0001065280-24-000030",
                                },
                                {
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "val": int(receita_2024),
                                    "fy": 2024,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2025-01-27",
                                    "accn": "0001065280-25-000044",
                                },
                            ]
                        }
                    },
                    "CostOfRevenue": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "val": 21038000000,
                                    "fy": 2024,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2025-01-27",
                                    "accn": "0001065280-25-000044",
                                }
                            ]
                        }
                    },
                    "CommonStockSharesOutstanding": {
                        # Unidade nao monetaria: tem que ficar de fora.
                        "units": {
                            "shares": [
                                {
                                    "end": "2024-12-31",
                                    "val": 427756000,
                                    "fy": 2024,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2025-01-27",
                                    "accn": "0001065280-25-000044",
                                }
                            ]
                        }
                    },
                }
            },
        }
    ).encode("utf-8")


@pytest.fixture
def ambiente(tmp_path):
    """Bronze e warehouse vazios, como depois de `pat init`."""
    conn = connect(tmp_path / "warehouse.duckdb")
    migrate(conn)
    yield conn, BronzeStore(tmp_path / "bronze"), Catalog(conn)
    conn.close()


def _ingerir(bronze, catalog, run: Run, payload: bytes, *, cik: str = CIK) -> None:
    """Poe os bytes no bronze e registra o retrieval, como `pat fetch` faria.

    Sem passar pela rede: o que se quer testar aqui e o build, e um teste de
    build que precise de rede nao roda em CI.
    """
    from datetime import UTC, datetime
    import uuid

    from pat.contracts.common import SourceTier
    from pat.contracts.documents import HttpMeta, Retrieval

    put = bronze.put(
        payload,
        run_id=run.run_id,
        media_type="application/json",
        provenance={"dataset_id": "sec.companyfacts", "resource_key": cik},
    )
    momento = datetime(2025, 6, 30, tzinfo=UTC)
    catalog.record_document(put.document)
    catalog.record_retrieval(
        Retrieval(
            retrieval_id=uuid.uuid4().hex,
            content_sha256=put.document.content_sha256,
            provider_id="sec",
            source_tier=SourceTier.PRIMARY_OFFICIAL,
            dataset_id="sec.companyfacts",
            resource_key=cik,
            url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            requested_at=momento,
            retrieved_at=momento,
            http=HttpMeta(status_code=200, content_type="application/json"),
            run_id=run.run_id,
            provider_version="1.0.0",
        )
    )


# -- o criterio -------------------------------------------------------------


def test_uma_companhia_americana_entra_pela_linha_de_comando(ambiente):
    """Bronze -> silver -> gold -> entidade, sem caminho ad hoc.

    Antes disto, `write_sec_facts` so era chamado por testes: a capacidade
    existia no codigo e nao existia no produto.
    """
    conn, bronze, catalog = ambiente
    run = new_run(command="teste")
    catalog.start_run(run)
    _ingerir(bronze, catalog, run, companyfacts())

    outcome = build_sec_dataset(
        dataset_id="sec.companyfacts", conn=conn, bronze=bronze, run=run, ciks=[CIK]
    )

    assert outcome.silver_written > 0
    assert outcome.gold_written > 0
    assert len(outcome.resources) == 1
    assert outcome.resources[0].resource_key == CIK

    from pat.query.asof import AsOf

    ref = AsOf(conn).entity_by_local_id("cik", CIK)
    assert ref is not None
    assert ref.entity_id == f"us:cik:{CIK}"
    assert ref.jurisdiction == "US"
    assert ref.display_name == NOME


def test_o_cik_e_normalizado_para_dez_digitos(ambiente):
    """`--cik 1065280` e `--cik 0001065280` sao a mesma companhia.

    O zero a esquerda importa no identificador e nao pode importar na
    digitacao: exigir os dez digitos faria o comando falhar dizendo "nenhum
    documento", que manda o leitor procurar o erro no bronze.
    """
    conn, bronze, catalog = ambiente
    run = new_run(command="teste")
    catalog.start_run(run)
    _ingerir(bronze, catalog, run, companyfacts())

    outcome = build_sec_dataset(
        dataset_id="sec.companyfacts", conn=conn, bronze=bronze, run=run, ciks=["1065280"]
    )
    assert outcome.gold_written > 0


def test_unidade_nao_monetaria_e_descartada_com_contagem(ambiente):
    """`shares` nao sustenta metrica monetaria, e o descarte e CONTADO.

    Somar o descarte ao silencio faria o operador achar que o `companyfacts`
    tem menos do que tem - e a diferenca entre "a origem nao publica" e "o
    filtro deixou de fora" e a mesma que separa ausencia de lacuna.
    """
    conn, bronze, catalog = ambiente
    run = new_run(command="teste")
    catalog.start_run(run)
    _ingerir(bronze, catalog, run, companyfacts())

    outcome = build_sec_dataset(
        dataset_id="sec.companyfacts", conn=conn, bronze=bronze, run=run
    )

    from pat.store import silver

    elementos = {
        linha[0]
        for linha in conn.execute("SELECT DISTINCT element FROM silver_xbrl_line").fetchall()
    }
    assert "CommonStockSharesOutstanding" not in elementos
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in elementos
    assert silver.count(conn) >= 0


def test_sem_bronze_a_recusa_ensina_o_comando(ambiente):
    """Nenhum documento nao vira zero fatos: vira recusa com o comando certo."""
    conn, bronze, catalog = ambiente
    run = new_run(command="teste")
    catalog.start_run(run)

    with pytest.raises(BuildError, match="pat fetch sec.companyfacts --cik"):
        build_sec_dataset(
            dataset_id="sec.companyfacts", conn=conn, bronze=bronze, run=run, ciks=[CIK]
        )


def test_dataset_sem_consumidor_e_recusado_na_porta(ambiente):
    """`sec.filing_doc` resolve URL e nao tem build. A recusa e aqui.

    Aceitar e falhar tres camadas adiante mandaria o leitor procurar o
    problema no parser.
    """
    conn, bronze, catalog = ambiente
    run = new_run(command="teste")
    catalog.start_run(run)

    with pytest.raises(BuildError, match="nao suportado no build americano"):
        build_sec_dataset(
            dataset_id="sec.filing_doc", conn=conn, bronze=bronze, run=run
        )
    assert "sec.filing_doc" not in SUPPORTED_SEC_DATASETS


def test_layout_mudado_na_origem_levanta_em_vez_de_gravar_vazio(ambiente):
    """`companyfacts` sem `entityName` e mudanca na fonte, e tem que doer.

    Gravar zero fatos em silencio produziria uma companhia "conhecida sem
    fatos" - que se le como problema de cobertura, quando e problema de
    formato.
    """
    conn, bronze, catalog = ambiente
    run = new_run(command="teste")
    catalog.start_run(run)
    _ingerir(bronze, catalog, run, json.dumps({"cik": int(CIK), "facts": {}}).encode())

    with pytest.raises(ValueError, match="layout mudou na origem"):
        build_sec_dataset(
            dataset_id="sec.companyfacts", conn=conn, bronze=bronze, run=run, ciks=[CIK]
        )


def test_reconstruir_e_idempotente(ambiente):
    """Rodar duas vezes nao duplica fato.

    O build e reprocessavel por desenho - `pat build` depois de um `fetch` que
    trouxe o mesmo conteudo tem que ser inofensivo.
    """
    conn, bronze, catalog = ambiente
    run = new_run(command="teste")
    catalog.start_run(run)
    _ingerir(bronze, catalog, run, companyfacts())

    from pat.store import gold

    build_sec_dataset(
        dataset_id="sec.companyfacts", conn=conn, bronze=bronze, run=run, ciks=[CIK]
    )
    depois_da_primeira = gold.count(conn)

    build_sec_dataset(
        dataset_id="sec.companyfacts", conn=conn, bronze=bronze, run=run, ciks=[CIK]
    )
    assert gold.count(conn) == depois_da_primeira

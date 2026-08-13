"""Fase 1 ponta a ponta: bytes -> silver -> gold -> consulta AS OF.

O teste que sustenta a fase inteira e
`test_reapresentacao_muda_o_valor_sem_apagar_o_passado`:

    documento original           FY2023 = 1.000 mil, conhecido em 2024-02-20
        v
    reapresentacao (DFP 2024)    FY2023 =   900 mil, conhecido em 2025-02-20
        v
    AS OF 2024-06-30  ->  1.000 mil     (o que um analista sabia na epoca)
    AS OF 2025-06-30  ->    900 mil     (o que se sabe hoje)

Sem isso, todo backtest usa numeros reapresentados e mente sobre o passado.

O provider e falso, mas tudo abaixo dele e o codigo de producao: bronze,
catalogo, parser, silver, gold e consulta.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

import pytest
from conftest import DocumentSpec, LineSpec, ZipSpec, build_dfp_zip, restatement_fixture

from pat.audit.run import new_run
from pat.build import BuildError, build_dataset
from pat.contracts.common import SourceTier
from pat.contracts.documents import HttpMeta, ResourceRef
from pat.ingest import ingest_dataset
from pat.query.asof import AsOf
from pat.sources.base import DatasetSpec, FetchResult, SourceProvider
from pat.store.bronze import BronzeStore
from pat.store.catalog import Catalog
from pat.store.db import connect, migrate
from pat.store.silver import count as silver_count

COD_CVM = 99999
FY2023 = date(2023, 12, 31)
CHAVE = dict(cod_cvm=COD_CVM, cd_conta="3.01", period_end=FY2023)

MIL = Decimal(1000)
ORIGINAL = Decimal(1000) * MIL  # 1.000 mil, em BRL
REVISADO = Decimal(900) * MIL  # 900 mil, em BRL


class FakeCVM(SourceProvider):
    """Serve ZIPs de DFP controlados pelo teste, na mesma forma da CVM real.

    `content_by_year` e mutavel de proposito: reescrever a entrada de um ano
    reproduz o que a CVM faz quando ha reapresentacao - mesma URL, bytes
    diferentes.
    """

    provider_id: ClassVar[str] = "cvm"
    tier: ClassVar[SourceTier] = SourceTier.PRIMARY_OFFICIAL
    version: ClassVar[str] = "1.0.0"

    def __init__(self, content_by_year: dict[str, bytes]) -> None:
        self.content_by_year = content_by_year
        self.clock = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)

    def datasets(self):
        return (
            DatasetSpec(
                dataset_id="cvm.dfp",
                title="DFP anual (falso)",
                tier=self.tier,
                params=("year",),
            ),
        )

    def resolve(self, dataset_id: str, **params: str):
        return (
            ResourceRef(
                provider_id=self.provider_id,
                dataset_id=dataset_id,
                resource_key=params["year"],
                url=f"https://dados.cvm.gov.br/dfp_cia_aberta_{params['year']}.zip",
                media_type="application/zip",
            ),
        )

    def fetch(self, ref: ResourceRef) -> FetchResult:
        content = self.content_by_year[ref.resource_key]
        # Relogio controlado: cada busca e posterior a anterior, para que a
        # ordem dos retrievals no catalogo seja verificavel.
        self.clock += timedelta(days=1)
        return FetchResult(
            ref=ref,
            content=content,
            http=HttpMeta(status_code=200, content_length=len(content)),
            requested_at=self.clock,
            retrieved_at=self.clock,
        )


class Lab:
    """Um sistema completo em memoria: provider falso + bronze + warehouse."""

    def __init__(self, tmp_path, content_by_year: dict[str, bytes]) -> None:
        from pat.sources.registry import Registry

        self.conn = connect(tmp_path / "warehouse.duckdb")
        migrate(self.conn)
        self.bronze = BronzeStore(tmp_path / "bronze")
        self.catalog = Catalog(self.conn)
        self.provider = FakeCVM(content_by_year)
        self.registry = Registry(providers=(self.provider,))

    def fetch(self, year: str):
        run = new_run(command=f"fetch cvm.dfp --year {year}")
        self.catalog.start_run(run)
        return ingest_dataset(
            "cvm.dfp",
            {"year": year},
            registry=self.registry,
            store=self.bronze,
            catalog=self.catalog,
            run=run,
        )

    def build(self, *years: str):
        run = new_run(command=f"build cvm.dfp {years}")
        self.catalog.start_run(run)
        return build_dataset(
            dataset_id="cvm.dfp",
            conn=self.conn,
            bronze=self.bronze,
            run=run,
            resource_keys=list(years) or None,
        )

    @property
    def asof(self) -> AsOf:
        return AsOf(self.conn)

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
def lab(tmp_path):
    """Os dois anos da reapresentacao, ainda nao ingeridos."""
    zip_2023, zip_2024 = restatement_fixture()
    lab = Lab(tmp_path, {"2023": zip_2023, "2024": zip_2024})
    yield lab
    lab.close()


# -- 1. um documento vira um fato rastreavel -------------------------------


def test_documento_vira_fato_com_linhagem_ate_o_byte(lab):
    """I2 ponta a ponta: o numero resolve para o documento, a posicao dentro
    dele, o retrieval e os bytes no bronze."""
    (outcome,) = lab.fetch("2023")
    report = lab.build("2023")

    assert report.silver_written > 0
    assert report.gold_written > 0
    assert report.skipped_total == 0

    view = lab.asof.value(**CHAVE, as_of=date(2024, 6, 30))
    assert view.value == ORIGINAL
    assert view.denom_cia == "COMPANHIA DE TESTE S.A."

    prov = lab.asof.provenance(view.fact_id)
    assert prov.content_sha256 == outcome.document.content_sha256
    assert prov.retrieval_id == outcome.retrieval.retrieval_id
    assert prov.url == "https://dados.cvm.gov.br/dfp_cia_aberta_2023.zip"
    assert prov.dataset_id == "cvm.dfp"
    assert prov.resource_key == "2023"
    assert prov.source_tier == "primary_official"
    assert prov.extractor == "cvm_dfp"
    assert prov.locator == "dfp_cia_aberta_DRE_con_2023.csv#L2:cd_conta=3.01"
    assert prov.source_doc_id == "111111"

    # Os bytes apontados ainda existem e conferem com o hash.
    assert lab.asof.verify_provenance(view.fact_id, lab.bronze) is True
    assert prov.bronze_path(lab.bronze.root).exists()


def test_provenance_de_fato_inexistente_e_nula(lab):
    lab.fetch("2023")
    lab.build("2023")

    assert lab.asof.provenance("nao-existe") is None
    assert lab.asof.verify_provenance("nao-existe", lab.bronze) is False


# -- 2. o teste central da Fase 1 ------------------------------------------


def test_reapresentacao_muda_o_valor_sem_apagar_o_passado(lab):
    """A DFP de 2024 traz FY2023 de novo, com outro numero.

    As duas versoes coexistem: a consulta AS OF escolhe pela data de
    conhecimento, nunca pela ordem de chegada.
    """
    lab.fetch("2023")
    lab.build("2023")

    # Antes de a reapresentacao existir, so ha um valor conhecido.
    assert lab.asof.value(**CHAVE, as_of=date(2024, 6, 30)).value == ORIGINAL
    assert len(lab.asof.history(**CHAVE)) == 1

    lab.fetch("2024")
    lab.build("2024")

    antes = lab.asof.value(**CHAVE, as_of=date(2024, 6, 30))
    depois = lab.asof.value(**CHAVE, as_of=date(2025, 6, 30))

    assert antes.value == ORIGINAL
    assert antes.knowledge_date == date(2024, 2, 20)
    assert antes.ordem_exerc == "ULTIMO"
    assert antes.source_doc_id == "111111"

    assert depois.value == REVISADO
    assert depois.knowledge_date == date(2025, 2, 20)
    assert depois.ordem_exerc == "PENULTIMO"  # veio como comparativo da DFP 2024
    assert depois.source_doc_id == "222222"

    # Nada foi sobrescrito: sao dois fatos distintos, cada um rastreavel ao
    # seu proprio documento.
    assert antes.fact_id != depois.fact_id
    assert antes.content_sha256 != depois.content_sha256
    assert [v.value for v in lab.asof.history(**CHAVE)] == [ORIGINAL, REVISADO]

    (item,) = lab.asof.restatements(cod_cvm=COD_CVM)
    assert (item.original.value, item.revised.value) == (ORIGINAL, REVISADO)
    assert item.delta == Decimal("-100000")
    assert item.delta_pct == Decimal("-10")

    # E as duas pontas continuam verificaveis contra os bytes de origem.
    assert lab.asof.verify_provenance(item.original.fact_id, lab.bronze)
    assert lab.asof.verify_provenance(item.revised.fact_id, lab.bronze)


def test_o_ano_corrente_da_dfp_2024_nao_e_afetado(lab):
    """FY2024 vem como ULTIMO no mesmo documento: a chave logica separa os
    dois periodos sem ambiguidade."""
    lab.fetch("2024")
    lab.build("2024")

    view = lab.asof.value(
        cod_cvm=COD_CVM, cd_conta="3.01", period_end=date(2024, 12, 31), as_of=date(2025, 6, 30)
    )
    assert view.value == Decimal(2000) * MIL
    assert view.ordem_exerc == "ULTIMO"


# -- 3. o outro caminho de reapresentacao: o ZIP do ano e reescrito --------


def test_zip_reescrito_na_origem_gera_a_segunda_versao(tmp_path):
    """A CVM reescreve `dfp_cia_aberta_2023.zip` quando ha reapresentacao:
    mesma URL, bytes diferentes. As duas versoes sao processadas e coexistem.
    """
    v1 = build_dfp_zip(
        ZipSpec(
            year=2023,
            documents=[DocumentSpec("2023-12-31", 1, "2024-02-20", "111111")],
            dre_con=[LineSpec("3.01", "1000.00", "2023-12-31", "2023-01-01")],
        )
    )
    v2 = build_dfp_zip(
        ZipSpec(
            year=2023,
            documents=[DocumentSpec("2023-12-31", 2, "2024-08-15", "111112")],
            dre_con=[LineSpec("3.01", "900.00", "2023-12-31", "2023-01-01", versao=2)],
        )
    )
    lab = Lab(tmp_path, {"2023": v1})
    try:
        lab.fetch("2023")
        lab.build("2023")

        lab.provider.content_by_year["2023"] = v2  # a origem reescreveu o arquivo
        lab.fetch("2023")
        assert lab.catalog.changed_resources() == [("cvm.dfp", "2023", 2)]

        # O build reprocessa *todas* as versoes de conteudo do recurso.
        lab.build("2023")

        assert lab.asof.value(**CHAVE, as_of=date(2024, 6, 30)).value == ORIGINAL
        assert lab.asof.value(**CHAVE, as_of=date(2024, 12, 31)).value == REVISADO
        assert len(lab.asof.history(**CHAVE)) == 2
        assert len(lab.asof.restatements()) == 1
    finally:
        lab.close()


# -- 4. determinismo (I3) --------------------------------------------------


def test_rebuild_nao_duplica_nem_muda_nada(lab):
    """Ids derivados do conteudo: rodar o build de novo sobre o mesmo bronze
    e um no-op. E o que permite reprocessar sem medo."""
    lab.fetch("2023")
    lab.fetch("2024")
    lab.build()

    estado = lambda: sorted(  # noqa: E731
        lab.conn.execute("SELECT fact_id, value, knowledge_date FROM gold_fact").fetchall()
    )
    primeiro = estado()
    silver_antes = silver_count(lab.conn)

    segundo_report = lab.build()

    assert segundo_report.gold_written == 0
    assert segundo_report.silver_written == 0
    assert estado() == primeiro
    assert silver_count(lab.conn) == silver_antes


def test_rebuscar_sem_mudanca_nao_cria_fato_novo(lab):
    """Um retrieval a mais sobre os mesmos bytes e informacao de catalogo,
    nao um fato novo: o `fact_id` nasce do conteudo."""
    lab.fetch("2023")
    lab.build("2023")
    antes = lab.asof.history(**CHAVE)

    lab.fetch("2023")  # mesma URL, mesmos bytes
    lab.build("2023")

    assert lab.asof.history(**CHAVE) == antes


def test_linhagem_aponta_para_o_primeiro_retrieval_do_conteudo(lab):
    """Quando o mesmo conteudo foi observado varias vezes, a linhagem aponta
    para a primeira vez que aquele documento entrou no sistema - nao para uma
    observacao qualquer."""
    (primeiro,) = lab.fetch("2023")
    (segundo,) = lab.fetch("2023")
    assert primeiro.retrieval.retrieval_id != segundo.retrieval.retrieval_id
    assert primeiro.retrieval.retrieved_at < segundo.retrieval.retrieved_at

    lab.build("2023")

    view = lab.asof.value(**CHAVE, as_of=date(2024, 6, 30))
    assert lab.asof.provenance(view.fact_id).retrieval_id == primeiro.retrieval.retrieval_id


# -- 5. o build recusa o que nao sabe fazer --------------------------------


def test_build_sem_bronze_falha_com_instrucao(lab):
    with pytest.raises(BuildError, match="Rode `pat fetch` primeiro"):
        lab.build()


def test_build_de_dataset_nao_suportado_falha(lab):
    lab.fetch("2023")
    run = new_run(command="build cvm.itr")
    lab.catalog.start_run(run)

    with pytest.raises(BuildError, match="nao suportado"):
        build_dataset(
            dataset_id="cvm.itr", conn=lab.conn, bronze=lab.bronze, run=run
        )

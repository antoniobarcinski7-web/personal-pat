"""Orquestracao SEC: bronze -> silver XBRL -> gold. Sem rede.

Espelha `build.py`, e a simetria e o ponto: o caminho americano e o brasileiro
produzem o MESMO `Fact`, com a mesma linhagem, na mesma tabela. O que muda e o
parser e o adapter de taxonomia - nada acima disso sabe de qual jurisdicao o
numero veio.

Como o endereco XBRL entra no gold
-----------------------------------
`gold_fact` guarda o endereco de uma linha em tres colunas genericas, e cada
regime as preenche com o seu vocabulario:

    coluna       CVM                    us-gaap
    statement    DRE, BPA, DFC_MI       a taxonomia ('us-gaap')
    cd_conta     3.01                   o elemento (GrossProfit)
    coluna_df    componente da DMPL     o membro dimensional (segmento)

Nao e reaproveitamento oportunista: `LineAddress` ja e opaco fora do adapter, e
essas colunas sao o armazenamento fisico dele. Quem decodifica e o adapter do
regime, e so ele.

`coluna_df` carregar o segmento e o que faz um fato dimensional NAO colidir com
o consolidado na chave logica - exatamente o papel que ele ja tinha na DMPL,
onde separa componentes do patrimonio liquido.

Escopo
------
`consolidated=True` sempre. O 10-K americano e consolidado por definicao, e nao
existe o par individual/consolidado que a CVM publica. Gravar `False` em algum
lugar seria inventar uma distincao que o regime nao faz.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from pat.contracts.common import PeriodType
from pat.contracts.entities import Company, EntityIdentity
from pat.contracts.facts import Fact
from pat.contracts.lineage import Lineage, Run
from pat.contracts.silver import XbrlFactLine
from pat.parse.sec_xbrl import EXTRACTOR, EXTRACTOR_VERSION
from pat.store import entity as entity_store
from pat.store import silver
from pat.store.gold import GoldFact

__all__ = ["SecBuildReport", "build_facts_from_xbrl", "write_sec_facts"]

TAXONOMY_STATEMENT = "us-gaap"


@dataclass(frozen=True)
class SecBuildReport:
    silver_written: int = 0
    gold_written: int = 0
    entities_written: int = 0
    skipped_no_period: int = 0
    skipped_non_monetary: int = 0
    skipped_forward_looking: int = 0

    @property
    def skipped_total(self) -> int:
        return (
            self.skipped_no_period
            + self.skipped_non_monetary
            + self.skipped_forward_looking
        )


def _period_type(line: XbrlFactLine) -> PeriodType | None:
    """Tipo de periodo, DERIVADO do que a fonte publica - nunca suposto.

    Sem `period_start` o fato e um saldo pontual (balanco). Com ele, a duracao
    decide: perto de um ano e exercicio, perto de um trimestre e trimestre. A
    tolerancia existe porque o exercicio da Intel tem 52 ou 53 semanas e fecha
    no ultimo sabado de dezembro - 2024-12-28, 2023-12-30. Um sistema que
    exigisse 365 dias exatos classificaria errado todo ano fiscal americano.
    """
    if line.period_start is None:
        return PeriodType.INSTANT
    dias = (line.period_end - line.period_start).days
    if 330 <= dias <= 400:
        return PeriodType.YEAR
    if 60 <= dias <= 120:
        return PeriodType.QUARTER
    if 150 <= dias <= 220:
        return PeriodType.SEMESTER
    return None


def build_facts_from_xbrl(
    lines: list[XbrlFactLine], *, currency: str = "USD"
) -> tuple[list[GoldFact], SecBuildReport]:
    """Linhas silver -> fatos gold, com linhagem ate o byte."""
    facts: list[GoldFact] = []
    sem_periodo = 0
    nao_monetario = 0
    prospectivos = 0

    for line in lines:
        if line.unit != currency:
            nao_monetario += 1
            continue
        tipo = _period_type(line)
        if tipo is None:
            # Duracao que nao e ano, trimestre nem semestre: tipicamente um
            # acumulado de nove meses. Ele existe e e verdadeiro, mas nao tem
            # tipo de periodo declarado no sistema - e inventar um faria uma
            # comparacao anual varrer nove meses sem avisar.
            sem_periodo += 1
            continue

        if line.filed < line.period_end:
            # DIVULGACAO PROSPECTIVA, e nao dado corrompido.
            #
            # O XBRL da SEC carrega alguns elementos que sao projecao por
            # construcao: `CashFlowHedgeGainLossToBeReclassifiedWithinTwelveMonths`
            # tem `end` doze meses a frente do `filed`, porque e exatamente o que
            # ele afirma - o que a companhia ESPERA reclassificar no ano
            # seguinte. Idem a amortizacao de plano de pensao esperada.
            #
            # O contrato `Fact` recusa isso ("um numero nao pode ser conhecido
            # antes de existir"), e o contrato esta certo: estes nao sao fatos
            # historicos. Carimbar um `knowledge_date` posterior para faze-los
            # caber transformaria projecao em fato realizado - a pior troca
            # possivel numa base de research.
            #
            # Na Intel sao 6 de 25.354 linhas. Descartados e CONTADOS.
            prospectivos += 1
            continue

        entity_id = Company.entity_id_from_cik(line.cik)
        fact = Fact(
            fact_id=line.silver_id,
            entity_id=entity_id,
            metric=f"{line.taxonomy}:{line.element}",
            metric_version=EXTRACTOR_VERSION,
            value=line.value,
            unit=currency,
            currency=currency,
            period_type=tipo,
            period_start=line.period_start,
            period_end=line.period_end,
            # `filed` E o knowledge_date. A SEC publica a data em que cada fato
            # se tornou publico, e e ela que sustenta o AS OF deste lado.
            knowledge_date=line.filed,
            consolidated=True,
            lineage=Lineage(
                content_sha256=line.content_sha256,
                retrieval_id=line.retrieval_id,
                locator=line.locator,
                extractor=EXTRACTOR,
                extractor_version=line.extractor_version,
                extraction_run_id=line.extraction_run_id,
            ),
        )
        facts.append(
            GoldFact(
                fact=fact,
                cod_cvm=0,
                denom_cia=line.entity_name,
                statement=TAXONOMY_STATEMENT,
                consolidated=True,
                coluna_df=line.segments,
                cd_conta=line.element,
                ds_conta=line.element,
                ordem_exerc="ULTIMO",
                source_doc_id=line.accession,
                source_doc_version=1,
                silver_id=line.silver_id,
            )
        )

    return facts, SecBuildReport(
        skipped_no_period=sem_periodo,
        skipped_non_monetary=nao_monetario,
        skipped_forward_looking=prospectivos,
    )


def write_sec_facts(
    conn: duckdb.DuckDBPyConnection,
    lines: list[XbrlFactLine],
    *,
    run: Run | None = None,
) -> SecBuildReport:
    """Grava silver, entidade e gold. A entidade PRIMEIRO, como no lado da CVM."""
    from pat.store.gold import write_facts

    silver_written = silver.write_xbrl_lines(conn, lines)
    facts, report = build_facts_from_xbrl(lines)

    identidades = _identities_from(lines)
    entities_written = entity_store.upsert_identities(conn, identidades)

    # `write_facts` do lado da CVM tambem grava entidade, mas a partir de
    # `cod_cvm` - que nao existe aqui. Por isso a identidade americana e
    # gravada antes, explicitamente, e a do gold vira no-op por conflito.
    gold_written = write_facts(conn, facts) if facts else 0

    return SecBuildReport(
        silver_written=silver_written,
        gold_written=gold_written,
        entities_written=entities_written,
        skipped_no_period=report.skipped_no_period,
        skipped_non_monetary=report.skipped_non_monetary,
        skipped_forward_looking=report.skipped_forward_looking,
    )


def _identities_from(lines: list[XbrlFactLine]) -> list[EntityIdentity]:
    """Uma identidade por CIK. O CIK e o canonico da jurisdicao americana."""
    vistos: dict[str, EntityIdentity] = {}
    for line in lines:
        entity_id = Company.entity_id_from_cik(line.cik)
        if entity_id in vistos:
            continue
        vistos[entity_id] = EntityIdentity(
            entity_id=entity_id,
            jurisdiction="US",
            scheme="cik",
            local_id=line.cik,
            display_name=line.entity_name,
            is_primary=True,
        )
    return list(vistos.values())

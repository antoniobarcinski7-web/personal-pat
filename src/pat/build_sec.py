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

from dataclasses import dataclass, field
from datetime import date

import duckdb

from pat.contracts.common import PeriodType
from pat.contracts.entities import Company, EntityIdentity
from pat.contracts.facts import Fact
from pat.contracts.lineage import Lineage, Run
from pat.contracts.silver import XbrlFactLine
from pat.parse.sec_xbrl import EXTRACTOR, EXTRACTOR_VERSION, parse_companyfacts
from pat.store import entity as entity_store
from pat.store import silver
from pat.store.gold import GoldFact

__all__ = [
    "SUPPORTED_SEC_DATASETS",
    "SecBuildOutcome",
    "SecBuildReport",
    "SecResourceReport",
    "build_facts_from_xbrl",
    "build_sec_dataset",
    "write_sec_facts",
]

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


NON_MONETARY_UNITS = frozenset({"shares", "pure"})
"""Unidades que NAO sao dinheiro e ainda assim sao grandeza economica.

`shares` entrou porque valor de mercado precisa de contagem de acoes, e ela nao
tem moeda: o `Fact` grava `currency=None`, que o contrato ja previa. Chamar
acoes de USD para caber no filtro antigo faria uma contagem entrar numa soma de
dinheiro sem ninguem notar."""


def build_facts_from_xbrl(
    lines: list[XbrlFactLine], *, currency: str = "USD"
) -> tuple[list[GoldFact], SecBuildReport]:
    """Linhas silver -> fatos gold, com linhagem ate o byte."""
    facts: list[GoldFact] = []
    sem_periodo = 0
    nao_monetario = 0
    prospectivos = 0

    for line in lines:
        if line.unit != currency and line.unit not in NON_MONETARY_UNITS:
            nao_monetario += 1
            continue
        # Contagem nao tem moeda. Gravar `currency="USD"` numa quantidade de
        # acoes faria ela caber numa soma de dinheiro, e o contrato existe para
        # que isso doa em vez de passar.
        moeda = currency if line.unit == currency else None
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
            unit=line.unit,
            currency=moeda,
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


# ---------------------------------------------------------------------------
# A orquestracao: bronze -> silver -> gold, por companhia
# ---------------------------------------------------------------------------

SUPPORTED_SEC_DATASETS = frozenset({"sec.companyfacts", "sec.filing_doc"})
"""Datasets que o build americano consome hoje.

`sec.filing_doc` entrou porque o arquivamento e DUAS coisas ao mesmo tempo:
corpus - texto citavel - e fato, no inline XBRL. Os dois caminhos leem o mesmo
blob do bronze e nao se atrapalham: `pat docs --sync` extrai unidades,
`pat build sec.filing_doc` extrai fatos.

E onde vivem os elementos do EMISSOR. O endpoint `companyfacts` publica so
taxonomias padrao, e a amortizacao de conteudo da Netflix - a maior despesa
nao-caixa dela - nao esta la; esta no 10-K, como `nflx:`.

`sec.financial_statements` (DERA) tem parser proprio - `parse_dera_num` - e
entra por outro caminho: um ZIP trimestral traz milhares de companhias, e nao
uma. Listar aqui so o que de fato funciona e o que impede a CLI de aceitar um
dataset e falhar tres camadas adiante.
"""


@dataclass
class SecResourceReport:
    """Uma companhia construida: o que entrou e o que foi descartado."""

    resource_key: str
    content_sha256: str
    result: SecBuildReport


@dataclass
class SecBuildOutcome:
    dataset_id: str
    resources: list[SecResourceReport] = field(default_factory=list)

    @property
    def silver_written(self) -> int:
        return sum(r.result.silver_written for r in self.resources)

    @property
    def gold_written(self) -> int:
        return sum(r.result.gold_written for r in self.resources)

    @property
    def skipped_total(self) -> int:
        return sum(
            r.result.skipped_no_period
            + r.result.skipped_non_monetary
            + r.result.skipped_forward_looking
            for r in self.resources
        )


def build_sec_dataset(
    *,
    dataset_id: str,
    conn: duckdb.DuckDBPyConnection,
    bronze,
    run: Run,
    ciks: list[str] | None = None,
) -> SecBuildOutcome:
    """Bronze -> gold para companhias americanas. Deterministico, sem rede.

    Espelha `build.build_dataset` de proposito, ate no formato do relatorio: o
    operador que ja sabe ler a saida do build brasileiro le esta sem aprender
    nada novo, e a simetria e o que lembra que os dois caminhos produzem o
    MESMO `Fact`, na mesma tabela.

    `ciks` sao `resource_key` do bronze - CIK com dez digitos, e o zero a
    esquerda importa. Sem eles, constroi tudo que ja foi baixado, que e o
    comportamento util depois de varios `fetch`.
    """
    from pat.build import BuildError, documents_for

    if dataset_id not in SUPPORTED_SEC_DATASETS:
        raise BuildError(
            f"dataset {dataset_id!r} nao suportado no build americano. "
            f"Suportados: {', '.join(sorted(SUPPORTED_SEC_DATASETS))}"
        )

    if dataset_id == "sec.filing_doc":
        return _build_inline(conn=conn, bronze=bronze, run=run, ciks=ciks)

    chaves = [str(c).zfill(10) for c in ciks] if ciks else None
    documentos = documents_for(conn, dataset_id, chaves)
    if not documentos:
        raise BuildError(
            f"nenhum documento de {dataset_id} no bronze"
            + (f" para {chaves}" if chaves else "")
            + ". Rode `pat fetch sec.companyfacts --cik N` primeiro."
        )

    outcome = SecBuildOutcome(dataset_id=dataset_id)
    for resource_key, content_sha256, retrieval_id, _storage_path in documentos:
        content = bronze.read(content_sha256)  # verifica o hash ao ler

        linhas = parse_companyfacts(
            content,
            content_sha256=content_sha256,
            retrieval_id=retrieval_id,
            extraction_run_id=run.run_id,
        )
        outcome.resources.append(
            SecResourceReport(
                resource_key=resource_key,
                content_sha256=content_sha256,
                result=write_sec_facts(conn, linhas, run=run),
            )
        )
    return outcome


def _build_inline(*, conn, bronze, run: Run, ciks: list[str] | None) -> SecBuildOutcome:
    """Arquivamentos ja no corpus -> fatos do inline XBRL.

    Le de `source_document`, e nao do catalogo do bronze: e la que estao a
    forma, o accession e a data de entrega do documento, ja tipados. Reconstrui-
    los a partir do `resource_key` seria uma segunda copia que pode divergir da
    primeira.
    """
    from pat.build import BuildError
    from pat.parse.sec_inline import parse_inline_xbrl

    predicado = ""
    params: list[object] = []
    if ciks:
        entidades = [f"us:cik:{str(c).zfill(10)}" for c in ciks]
        predicado = f"AND d.entity_id IN ({', '.join('?' * len(entidades))})"
        params = list(entidades)

    documentos = conn.execute(
        f"""
        SELECT d.document_id, d.entity_id, d.origin_category, d.resource_key,
               d.published_at, d.retrieval_id, d.title
        FROM source_document d
        WHERE d.provider_id = 'sec' {predicado}
        ORDER BY d.published_at
        """,
        params,
    ).fetchall()
    if not documentos:
        raise BuildError(
            "nenhum arquivamento da SEC no corpus"
            + (f" para {ciks}" if ciks else "")
            + ". Rode `pat docs --cik N --sync` primeiro."
        )

    outcome = SecBuildOutcome(dataset_id="sec.filing_doc")
    for doc_id, entity_id, forma, resource_key, publicado, retrieval_id, _titulo in documentos:
        conteudo = bronze.read(doc_id)  # verifica o hash ao ler
        accession = str(resource_key).split("/")[0]
        linhas, _stats = parse_inline_xbrl(
            conteudo,
            content_sha256=doc_id,
            retrieval_id=retrieval_id,
            extraction_run_id=run.run_id,
            cik=entity_id.rpartition(":")[2],
            entity_name=_nome_da_entidade(conn, entity_id),
            accession=accession,
            form=str(forma),
            filed=publicado,
            source_member=str(resource_key),
            # Dinheiro E contagem. A contagem de acoes e o insumo de valor de
            # mercado, e sem ela o preco sozinho nao responde nada.
            units=("USD", "shares"),
        )
        outcome.resources.append(
            SecResourceReport(
                resource_key=str(resource_key),
                content_sha256=doc_id,
                result=write_sec_facts(conn, linhas, run=run),
            )
        )
    return outcome


def _nome_da_entidade(conn, entity_id: str) -> str:
    """O nome ja gravado da entidade. Nao inventa um a partir do identificador.

    O parser de inline XBRL nao le o nome da companhia - `dei:EntityRegistrant
    Name` e um `ix:nonNumeric`, e este parser so le fatos numericos. Buscar o
    nome ja conhecido e melhor que derivar `us:cik:0001065280` em algum rotulo:
    o segundo apareceria no relatorio como se fosse a razao social.
    """
    from pat.query.asof import AsOf

    ref = AsOf(conn).entity(entity_id)
    return ref.display_name if ref is not None else entity_id

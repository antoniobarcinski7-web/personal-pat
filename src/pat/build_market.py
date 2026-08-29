"""Orquestracao de mercado: bronze -> gold. Sem rede.

Espelha `build_sec.py`, e a simetria continua sendo o ponto: uma cotacao vira o
MESMO `Fact` que uma linha de balanco, com a mesma linhagem, na mesma tabela. O
que muda e a procedencia - `PUBLIC_WEB` em vez de `PRIMARY_OFFICIAL` - e ela
viaja no `Retrieval`, nao no fato.

O vinculo simbolo -> companhia e DECLARADO
------------------------------------------
`NFLX` e `us:cik:0001065280` sao a mesma companhia, e nada no JSON do agregador
diz isso. Casar por nome - "Netflix, Inc." contra "NETFLIX INC" - seria
casamento por rotulo, que este projeto recusa em todo lugar; casar por ticker
de uma tabela embutida seria a mesma coisa com mais passos.

Entao quem declara e o operador, uma vez, na linha de comando:

    pat build yahoo.chart --symbol NFLX --cik 1065280

e o vinculo fica gravado como `EntityIdentity` de esquema `ticker`. Da segunda
vez em diante o `--cik` e opcional: a identidade ja esta no catalogo.

`knowledge_date` e a data da BUSCA, e nao a do pregao
-----------------------------------------------------
A serie ajustada MUDA retroativamente. O preco de 2023 lido hoje nao e o preco
de 2023 lido no ano passado, porque um desdobramento entrou no meio - a Netflix
desdobrou 10 para 1 em novembro de 2025, e toda a serie anterior foi
recalculada.

Carimbar o `knowledge_date` na data do pregao afirmaria que aquele numero era
conhecido naquele dia, e ele nao era: ele passou a ser esse quando esta serie
foi baixada. E a mesma disciplina de `filed` no lado da SEC, aplicada a uma
fonte que reescreve o passado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import duckdb

from pat.contracts.common import PeriodType
from pat.contracts.entities import Company, EntityIdentity
from pat.contracts.facts import Fact
from pat.contracts.lineage import Lineage, Run
from pat.parse.yahoo_chart import (
    CLOSE_SERIES,
    EXTRACTOR,
    EXTRACTOR_VERSION,
    QuoteLine,
    parse_chart,
)
from pat.store import entity as entity_store
from pat.store.gold import GoldFact

__all__ = [
    "SUPPORTED_MARKET_DATASETS",
    "MarketBuildReport",
    "build_market_dataset",
    "write_quotes",
]

SUPPORTED_MARKET_DATASETS = frozenset({"yahoo.chart"})

TAXONOMY_STATEMENT = "market.quote"
"""O que vai para a coluna `statement` do gold. O mesmo papel que `us-gaap`
cumpre no lado americano: e o armazenamento fisico do `LineAddress`, e so o
adapter daquela taxonomia sabe le-lo."""


@dataclass
class MarketBuildReport:
    symbol: str = ""
    entity_id: str = ""
    quotes_read: int = 0
    gold_written: int = 0
    entities_written: int = 0
    skipped_null: int = 0
    resources: list = field(default_factory=list)


def write_quotes(
    conn: duckdb.DuckDBPyConnection,
    quotes: list[QuoteLine],
    *,
    entity_id: str,
    content_sha256: str,
    retrieval_id: str,
    run: Run,
    retrieved_at: date,
) -> int:
    """Cotacoes -> gold. Idempotente pela chave logica, como todo o resto."""
    from pat.store.gold import write_facts

    facts: list[GoldFact] = []
    for quote in quotes:
        fact = Fact(
            fact_id=f"{content_sha256[:16]}-q-{quote.ordinal}",
            entity_id=entity_id,
            metric=f"{TAXONOMY_STATEMENT}:{CLOSE_SERIES}",
            metric_version=EXTRACTOR_VERSION,
            value=quote.close,
            unit=quote.currency,
            currency=quote.currency,
            # Um preco e um SALDO: ele vale naquele instante e nao se acumula.
            period_type=PeriodType.INSTANT,
            period_start=None,
            period_end=quote.trade_date,
            knowledge_date=retrieved_at,
            consolidated=True,
            lineage=Lineage(
                content_sha256=content_sha256,
                retrieval_id=retrieval_id,
                locator=f"{quote.symbol}#{quote.ordinal}:{CLOSE_SERIES}",
                extractor=EXTRACTOR,
                extractor_version=EXTRACTOR_VERSION,
                extraction_run_id=run.run_id,
            ),
        )
        facts.append(
            GoldFact(
                fact=fact,
                cod_cvm=0,
                denom_cia=quote.symbol,
                statement=TAXONOMY_STATEMENT,
                consolidated=True,
                coluna_df="",
                cd_conta=CLOSE_SERIES,
                ds_conta=f"{quote.symbol} fechamento ajustado",
                ordem_exerc="ULTIMO",
                source_doc_id=quote.symbol,
                source_doc_version=1,
                silver_id=fact.fact_id,
            )
        )
    return write_facts(conn, facts) if facts else 0


def build_market_dataset(
    *,
    dataset_id: str,
    conn: duckdb.DuckDBPyConnection,
    bronze,
    run: Run,
    symbol: str,
    cik: str | None = None,
) -> MarketBuildReport:
    """Bronze -> gold para um papel. Deterministico, sem rede."""
    from pat.build import BuildError, documents_for

    if dataset_id not in SUPPORTED_MARKET_DATASETS:
        raise BuildError(
            f"dataset {dataset_id!r} nao suportado no build de mercado. "
            f"Suportados: {', '.join(sorted(SUPPORTED_MARKET_DATASETS))}"
        )

    papel = symbol.strip().upper()
    documentos = documents_for(conn, dataset_id, [papel])
    if not documentos:
        raise BuildError(
            f"nenhuma serie de {papel} no bronze. "
            f"Rode `pat fetch {dataset_id} --symbol {papel}` primeiro."
        )

    entity_id = _entidade_do_papel(conn, papel, cik)
    identidades = [
        EntityIdentity(
            entity_id=entity_id,
            jurisdiction=_jurisdicao(entity_id),
            scheme="ticker",
            local_id=papel,
            display_name=papel,
            # O ticker e APELIDO, nunca canonico: papel muda de nome, migra de
            # bolsa e e reciclado depois de uma falencia. O canonico continua
            # sendo o identificador do regulador.
            is_primary=False,
        )
    ]
    report = MarketBuildReport(
        symbol=papel,
        entity_id=entity_id,
        entities_written=entity_store.upsert_identities(conn, identidades),
    )

    # A versao MAIS RECENTE do recurso, e nao todas: a serie e um snapshot
    # corrente que muda a cada pregao, e reprocessar as antigas gravaria o
    # mesmo pregao com precos ajustados de epocas diferentes.
    resource_key, content_sha256, retrieval_id, _path = documentos[-1]
    conteudo = bronze.read(content_sha256)
    cotacoes, stats = parse_chart(conteudo)

    buscado = conn.execute(
        "SELECT MAX(retrieved_at) FROM retrieval WHERE content_sha256 = ?",
        [content_sha256],
    ).fetchone()[0]

    report.quotes_read = stats.points_read
    report.skipped_null = stats.skipped_null
    report.gold_written = write_quotes(
        conn,
        cotacoes,
        entity_id=entity_id,
        content_sha256=content_sha256,
        retrieval_id=retrieval_id,
        run=run,
        retrieved_at=buscado.date() if hasattr(buscado, "date") else buscado,
    )
    report.resources.append((resource_key, content_sha256))
    return report


def _entidade_do_papel(conn, papel: str, cik: str | None) -> str:
    """Papel -> `entity_id`, pela identidade JA declarada ou pela que se declara.

    Sem `--cik` e sem identidade gravada, recusa. Inventar um `entity_id` a
    partir do ticker criaria uma companhia paralela cujos precos nunca
    encontrariam os fatos dela.
    """
    from pat.build import BuildError
    from pat.query.asof import AsOf

    if cik:
        return Company.entity_id_from_cik(str(cik).zfill(10))

    ref = AsOf(conn).entity_by_local_id("ticker", papel)
    if ref is None:
        raise BuildError(
            f"o papel {papel} nao esta ligado a nenhuma companhia. O vinculo e "
            "DECLARADO, e nao deduzido do nome: rode uma vez com o identificador "
            f"do regulador, por exemplo `--cik N`, e ele fica gravado."
        )
    return ref.entity_id


def _jurisdicao(entity_id: str) -> str:
    """A jurisdicao ja embutida no `entity_id` da companhia.

    Ler o prefixo aqui e aceitavel porque este modulo E a raiz de composicao do
    lado de mercado - o mesmo lugar onde `--cik` e traduzido. Nenhuma camada
    acima volta a ver isso.
    """
    return entity_id.split(":", 1)[0].upper() if ":" in entity_id else "US"

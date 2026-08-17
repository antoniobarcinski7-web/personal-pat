"""API de consulta point-in-time.

Toda leitura da camada gold passa por aqui, e toda leitura exige uma data de
conhecimento. Nao ha atalho que devolva "o valor" sem dizer *segundo quando*.

    asof.value(...)   o melhor conhecimento disponivel em uma data
    asof.latest(...)  atalho para o melhor conhecimento de hoje
    asof.history(...) todas as versoes ja conhecidas de um mesmo periodo
    asof.restatements(...) chaves cujo valor mudou entre versoes
    asof.provenance(fact_id) cadeia completa ate os bytes de origem

A regra do `value`:

    knowledge_date <= as_of
    ORDER BY knowledge_date DESC, source_doc_version DESC, fact_id ASC
    LIMIT 1

Ou seja: entre tudo que era conhecido naquela data, o mais recente vence. A
ordenacao e totalmente deterministica - `fact_id` como ultimo criterio elimina
qualquer empate, para que a mesma consulta devolva sempre a mesma linha.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from pat.store.bronze import BronzeStore

DEFAULT_STATEMENT = "DRE"


@dataclass(frozen=True)
class FactView:
    fact_id: str
    entity_id: str
    cod_cvm: int
    denom_cia: str
    statement: str
    consolidated: bool
    coluna_df: str
    cd_conta: str
    ds_conta: str
    period_type: str
    period_start: date | None
    period_end: date
    knowledge_date: date
    value: Decimal
    unit: str
    currency: str | None
    ordem_exerc: str
    source_doc_id: str
    source_doc_version: int
    content_sha256: str
    retrieval_id: str
    locator: str
    silver_id: str

    @property
    def value_thousands(self) -> Decimal:
        """Conveniencia de leitura: DFP e publicada em milhares."""
        return self.value / Decimal(1000)


@dataclass(frozen=True)
class EntityRef:
    """Identificadores de uma companhia presente no gold."""

    entity_id: str
    cod_cvm: int
    denom_cia: str


@dataclass(frozen=True)
class EntityCoverage:
    """O que existe no gold para uma companhia, sem dizer quanto vale.

    Serve a camada de pesquisa: ela precisa saber que periodos e escopos
    poderiam ser consultados antes de montar um plano, e nao pode descobrir
    isso emitindo SQL por fora.
    """

    entity_id: str
    cod_cvm: int
    denom_cia: str
    period_ends: tuple[date, ...]
    consolidated_scopes: tuple[bool, ...]
    earliest_knowledge_date: date
    latest_knowledge_date: date


@dataclass(frozen=True)
class Restatement:
    """Duas versoes conhecidas do mesmo periodo, com valores diferentes."""

    cod_cvm: int
    denom_cia: str
    statement: str
    consolidated: bool
    coluna_df: str
    cd_conta: str
    ds_conta: str
    period_end: date
    original: FactView
    revised: FactView

    @property
    def delta(self) -> Decimal:
        return self.revised.value - self.original.value

    @property
    def delta_pct(self) -> Decimal | None:
        if self.original.value == 0:
            return None
        return (self.delta / abs(self.original.value)) * Decimal(100)


@dataclass(frozen=True)
class DocumentProvenance:
    """Cadeia completa: fato -> extracao -> retrieval -> documento -> bytes."""

    fact_id: str
    locator: str
    extractor: str
    extractor_version: str
    extraction_run_id: str

    retrieval_id: str
    provider_id: str
    source_tier: str
    dataset_id: str
    resource_key: str
    url: str
    retrieved_at: object

    content_sha256: str
    size_bytes: int
    storage_path: str

    source_doc_id: str
    source_doc_version: int
    knowledge_date: date

    def bronze_path(self, bronze_root: Path) -> Path:
        return bronze_root / self.storage_path


_FACT_COLUMNS = """fact_id, entity_id, cod_cvm, denom_cia, statement, consolidated,
           coluna_df, cd_conta, ds_conta, period_type, period_start, period_end,
           knowledge_date, value, unit, currency, ordem_exerc,
           source_doc_id, source_doc_version, content_sha256, retrieval_id,
           locator, silver_id"""
"""Na ordem exata dos campos de `FactView` - `_to_view` desempacota por posicao."""

_SELECT = f"""
    SELECT {_FACT_COLUMNS}
    FROM gold_fact
"""

_KEY_PREDICATE = """
    WHERE cod_cvm = ?
      AND statement = ?
      AND consolidated = ?
      AND cd_conta = ?
      AND coluna_df = ?
      AND period_end = ?
"""

_ORDER = "ORDER BY knowledge_date DESC, source_doc_version DESC, fact_id ASC"


def _to_view(row: tuple) -> FactView:
    return FactView(*row)


class AsOf:
    """Consultas point-in-time sobre a camada gold."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def value(
        self,
        *,
        cod_cvm: int,
        cd_conta: str,
        period_end: date,
        as_of: date,
        statement: str = DEFAULT_STATEMENT,
        consolidated: bool = True,
        coluna_df: str = "",
    ) -> FactView | None:
        """O melhor conhecimento disponivel em `as_of`. None se nada era
        conhecido naquela data."""
        row = self.conn.execute(
            f"{_SELECT} {_KEY_PREDICATE} AND knowledge_date <= ? {_ORDER} LIMIT 1",
            [cod_cvm, statement, consolidated, cd_conta, coluna_df, period_end, as_of],
        ).fetchone()
        return _to_view(row) if row else None

    def latest(
        self,
        *,
        cod_cvm: int,
        cd_conta: str,
        period_end: date,
        statement: str = DEFAULT_STATEMENT,
        consolidated: bool = True,
        coluna_df: str = "",
    ) -> FactView | None:
        """Valor mais recente conhecido hoje. Atalho explicito para
        `value(as_of=hoje)` - nunca um caminho que ignora o eixo temporal."""
        return self.value(
            cod_cvm=cod_cvm,
            cd_conta=cd_conta,
            period_end=period_end,
            as_of=date.today(),
            statement=statement,
            consolidated=consolidated,
            coluna_df=coluna_df,
        )

    def history(
        self,
        *,
        cod_cvm: int,
        cd_conta: str,
        period_end: date,
        statement: str = DEFAULT_STATEMENT,
        consolidated: bool = True,
        coluna_df: str = "",
    ) -> list[FactView]:
        """Todas as versoes conhecidas, da mais antiga para a mais recente."""
        rows = self.conn.execute(
            f"{_SELECT} {_KEY_PREDICATE} "
            "ORDER BY knowledge_date ASC, source_doc_version ASC, fact_id ASC",
            [cod_cvm, statement, consolidated, cd_conta, coluna_df, period_end],
        ).fetchall()
        return [_to_view(row) for row in rows]

    def restatements(
        self,
        *,
        cod_cvm: int | None = None,
        cd_conta: str | None = None,
        statement: str | None = None,
        consolidated: bool | None = None,
        min_abs_pct: Decimal | float | None = None,
    ) -> list[Restatement]:
        """Chaves cujo valor mudou entre a primeira e a ultima versao conhecida.

        A comparacao e feita entre a versao mais antiga e a mais nova de cada
        chave logica; empates de data sao desempatados como no `value`.

        Diferente de `value` e `history`, `consolidated` aqui e opcional e o
        default e nao filtrar: esta e uma consulta de descoberta, e uma
        companhia costuma reapresentar individual e consolidado no mesmo
        documento. Esconder um dos dois por default omitiria metade do fato.
        """
        filters: list[str] = []
        params: list[object] = []
        if cod_cvm is not None:
            filters.append("cod_cvm = ?")
            params.append(cod_cvm)
        if cd_conta is not None:
            filters.append("cd_conta = ?")
            params.append(cd_conta)
        if statement is not None:
            filters.append("statement = ?")
            params.append(statement)
        if consolidated is not None:
            filters.append("consolidated = ?")
            params.append(consolidated)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""

        keys = self.conn.execute(
            f"""
            SELECT cod_cvm, statement, consolidated, cd_conta, coluna_df, period_end
            FROM gold_fact
            {where}
            GROUP BY cod_cvm, statement, consolidated, cd_conta, coluna_df, period_end
            HAVING COUNT(DISTINCT value) > 1
            ORDER BY cod_cvm, statement, consolidated, cd_conta, coluna_df, period_end
            """,
            params,
        ).fetchall()

        out: list[Restatement] = []
        for cod, stmt, con, conta, coluna, period_end in keys:
            versions = self.history(
                cod_cvm=cod,
                cd_conta=conta,
                period_end=period_end,
                statement=stmt,
                consolidated=con,
                coluna_df=coluna,
            )
            original, revised = versions[0], versions[-1]
            if original.value == revised.value:
                continue
            restatement = Restatement(
                cod_cvm=cod,
                denom_cia=revised.denom_cia,
                statement=stmt,
                consolidated=con,
                coluna_df=coluna,
                cd_conta=conta,
                ds_conta=revised.ds_conta,
                period_end=period_end,
                original=original,
                revised=revised,
            )
            if min_abs_pct is not None:
                pct = restatement.delta_pct
                if pct is None or abs(pct) < Decimal(str(min_abs_pct)):
                    continue
            out.append(restatement)
        return out

    def entity(self, entity_id: str) -> EntityRef | None:
        """Traduz a chave canonica interna para os identificadores da CVM.

        Existe para que a camada semantica possa falar so `entity_id` e ainda
        assim chegar ao gold, sem que ela mesma emita SQL: toda leitura do gold
        continua passando por esta classe.
        """
        row = self.conn.execute(
            """
            SELECT entity_id, cod_cvm, denom_cia
            FROM gold_fact
            WHERE entity_id = ?
            ORDER BY knowledge_date DESC, fact_id ASC
            LIMIT 1
            """,
            [entity_id],
        ).fetchone()
        return EntityRef(*row) if row else None

    def entity_by_cod_cvm(self, cod_cvm: int) -> EntityRef | None:
        """Caminho inverso de `entity`, para a linha de comando: o usuario
        pensa em codigo CVM, o sistema pensa em `entity_id`."""
        row = self.conn.execute(
            """
            SELECT entity_id, cod_cvm, denom_cia
            FROM gold_fact
            WHERE cod_cvm = ?
            ORDER BY knowledge_date DESC, fact_id ASC
            LIMIT 1
            """,
            [cod_cvm],
        ).fetchone()
        return EntityRef(*row) if row else None

    def entities(self, *, as_of: date | None = None) -> list[EntityRef]:
        """Companhias presentes no gold, em ordem estavel.

        `as_of` nao e enfeite: a cobertura em 2024-06-30 e genuinamente
        diferente da de hoje, e um catalogo que ignorasse isso deixaria alguem
        planejar sobre um periodo que ainda nao era publico naquela data.
        """
        predicate = "WHERE knowledge_date <= ?" if as_of is not None else ""
        params = [as_of] if as_of is not None else []
        rows = self.conn.execute(
            f"""
            SELECT entity_id, max(cod_cvm), max(denom_cia)
            FROM gold_fact
            {predicate}
            GROUP BY entity_id
            ORDER BY entity_id
            """,
            params,
        ).fetchall()
        return [EntityRef(*row) for row in rows]

    def coverage(self, entity_id: str, *, as_of: date | None = None) -> EntityCoverage | None:
        """Periodos e escopos disponiveis para uma companhia. None se nao ha nada.

        Diz que o periodo *existe*, nao que toda conta necessaria existe: um
        periodo coberto ainda pode dar `MISSING_FACT_AS_OF` na resolucao de um
        endereco. Conferir cada endereco aqui duplicaria o motor.
        """
        predicate = "AND knowledge_date <= ?" if as_of is not None else ""
        params: list[object] = [entity_id]
        if as_of is not None:
            params.append(as_of)
        row = self.conn.execute(
            f"""
            SELECT max(cod_cvm), max(denom_cia),
                   min(knowledge_date), max(knowledge_date)
            FROM gold_fact
            WHERE entity_id = ? {predicate}
            """,
            params,
        ).fetchone()
        if row is None or row[0] is None:
            return None

        periods = self.conn.execute(
            f"""
            SELECT DISTINCT period_end FROM gold_fact
            WHERE entity_id = ? {predicate}
            ORDER BY period_end
            """,
            params,
        ).fetchall()
        scopes = self.conn.execute(
            f"""
            SELECT DISTINCT consolidated FROM gold_fact
            WHERE entity_id = ? {predicate}
            ORDER BY consolidated
            """,
            params,
        ).fetchall()

        return EntityCoverage(
            entity_id=entity_id,
            cod_cvm=row[0],
            denom_cia=row[1],
            period_ends=tuple(p[0] for p in periods),
            consolidated_scopes=tuple(s[0] for s in scopes),
            earliest_knowledge_date=row[2],
            latest_knowledge_date=row[3],
        )

    def accounts(
        self,
        *,
        cod_cvm: int,
        statement: str,
        period_end: date,
        as_of: date,
        consolidated: bool = True,
    ) -> list[FactView]:
        """Plano de contas efetivo de uma companhia num periodo.

        Ferramenta de quem escreve mapeamento: mostra as contas que a
        companhia de fato publicou, com rotulo e valor, para um humano
        escolher a linha. Nao existe para o sistema adivinhar nada - o
        casamento continua sendo escrito a mao e justificado.
        """
        rows = self.conn.execute(
            f"""
            SELECT * FROM (
                SELECT {_FACT_COLUMNS},
                       row_number() OVER (
                           PARTITION BY cd_conta, coluna_df
                           ORDER BY knowledge_date DESC, source_doc_version DESC, fact_id ASC
                       ) AS rn
                FROM gold_fact
                WHERE cod_cvm = ? AND statement = ? AND consolidated = ?
                  AND period_end = ? AND knowledge_date <= ?
            ) WHERE rn = 1
            ORDER BY cd_conta, coluna_df
            """,
            [cod_cvm, statement, consolidated, period_end, as_of],
        ).fetchall()
        return [_to_view(row[:-1]) for row in rows]

    def provenance(self, fact_id: str) -> DocumentProvenance | None:
        """Cadeia completa de um fato ate os bytes de origem."""
        row = self.conn.execute(
            """
            SELECT g.fact_id, g.locator, g.extractor, g.extractor_version,
                   g.extraction_run_id,
                   r.retrieval_id, r.provider_id, r.source_tier, r.dataset_id,
                   r.resource_key, r.url, r.retrieved_at,
                   d.content_sha256, d.size_bytes, d.storage_path,
                   g.source_doc_id, g.source_doc_version, g.knowledge_date
            FROM gold_fact g
            JOIN retrieval r    ON r.retrieval_id = g.retrieval_id
            JOIN raw_document d ON d.content_sha256 = g.content_sha256
            WHERE g.fact_id = ?
            """,
            [fact_id],
        ).fetchone()
        return DocumentProvenance(*row) if row else None

    def verify_provenance(self, fact_id: str, bronze: BronzeStore) -> bool:
        """Reconfere que os bytes de origem ainda existem e batem com o hash.

        E a checagem que fecha I2: a linhagem so vale se o documento apontado
        ainda estiver la, integro.
        """
        prov = self.provenance(fact_id)
        if prov is None:
            return False
        bronze.read(prov.content_sha256)  # levanta se ausente ou corrompido
        return True

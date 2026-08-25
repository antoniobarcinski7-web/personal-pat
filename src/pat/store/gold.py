"""Construcao da camada gold: linhas silver -> fatos bitemporais.

Regras aplicadas aqui, e so aqui:

- **Escala.** `ESCALA_MOEDA = MIL` vira multiplicacao por 1000, e o fato passa
  a estar em unidades de BRL. Isso e interpretacao, por isso nao acontece em
  silver. Tambem elimina uma armadilha real: a mesma companhia pode declarar
  MIL em um exercicio e UNIDADE em outro, e comparar os valores crus daria
  uma variacao de 1000x inexistente.

- **Tipo de periodo.** Sem `dt_ini_exerc` e saldo pontual (INSTANT); com
  intervalo de ~12 meses e exercicio anual (YEAR). Qualquer outro intervalo e
  descartado e contado: a DFP e anual por construcao, entao um intervalo
  estranho e sinal de anomalia, nao de dado util.

- **Validacao pelo contrato.** Todo fato passa por `Fact` antes de ser
  persistido, o que aplica as invariantes universais - inclusive a de que
  `knowledge_date` nunca e anterior a `period_end`.

A tabela e append-only. Reapresentacao nao sobrescreve: entra como linha nova
com `knowledge_date` posterior.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import duckdb

from pat.contracts.common import PeriodType
from pat.contracts.entities import Company
from pat.contracts.facts import Fact
from pat.contracts.lineage import Lineage
from pat.contracts.silver import AccountLine

SCALE_FACTORS: dict[str, Decimal] = {
    "MIL": Decimal(1000),
    "UNIDADE": Decimal(1),
}

CURRENCY_BY_MOEDA = {"REAL": "BRL"}

_YEAR_DAYS = range(330, 401)


@dataclass
class BuildStats:
    facts_built: int = 0
    facts_written: int = 0
    skipped_unknown_scale: int = 0
    skipped_unknown_currency: int = 0
    skipped_irregular_period: int = 0
    skipped_contract_violation: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "facts_built": self.facts_built,
            "facts_written": self.facts_written,
            "skipped_unknown_scale": self.skipped_unknown_scale,
            "skipped_unknown_currency": self.skipped_unknown_currency,
            "skipped_irregular_period": self.skipped_irregular_period,
            "skipped_contract_violation": self.skipped_contract_violation,
        }

    @property
    def skipped_total(self) -> int:
        return (
            self.skipped_unknown_scale
            + self.skipped_unknown_currency
            + self.skipped_irregular_period
            + self.skipped_contract_violation
        )


@dataclass(frozen=True)
class GoldFact:
    """Fato validado mais os campos especificos da CVM que o contrato
    universal nao carrega (documento de origem, eixo ULTIMO/PENULTIMO).

    `cod_cvm` e `denom_cia` continuam AQUI, e nao na tabela de fatos: eles vem
    da linha silver e sao o insumo com que o builder popula `entity`. O que
    mudou na M5.6 e o destino deles, nao a origem - identificador de regime
    descreve a entidade, e a entidade tem tabela propria."""

    fact: Fact
    cod_cvm: int
    denom_cia: str
    statement: str
    consolidated: bool
    coluna_df: str
    cd_conta: str
    ds_conta: str
    ordem_exerc: str
    source_doc_id: str
    source_doc_version: int
    silver_id: str


def period_type_for(line: AccountLine) -> PeriodType | None:
    """None quando o intervalo nao corresponde a nenhum periodo esperado."""
    if line.dt_ini_exerc is None:
        return PeriodType.INSTANT
    span = (line.dt_fim_exerc - line.dt_ini_exerc).days + 1
    return PeriodType.YEAR if span in _YEAR_DAYS else None


def to_fact(line: AccountLine, stats: BuildStats) -> GoldFact | None:
    scale = SCALE_FACTORS.get(line.escala_moeda.upper())
    if scale is None:
        stats.skipped_unknown_scale += 1
        return None

    currency = CURRENCY_BY_MOEDA.get(line.moeda.upper())
    if currency is None:
        stats.skipped_unknown_currency += 1
        return None

    period_type = period_type_for(line)
    if period_type is None:
        stats.skipped_irregular_period += 1
        return None

    fact = Fact(
        fact_id=line.silver_id,
        entity_id=Company.entity_id_from_cnpj(line.cnpj),
        # Fase 1 nao faz mapeamento semantico: a metrica e o proprio codigo de
        # conta da CVM, com namespace. Metricas canonicas sao a Fase 2.
        metric=f"cvm:{line.statement.lower()}:{line.cd_conta}"
        + (f":{line.coluna_df}" if line.coluna_df else ""),
        metric_version="raw",
        value=line.vl_conta * scale,
        unit=currency,
        currency=currency,
        period_type=period_type,
        period_start=line.dt_ini_exerc,
        period_end=line.dt_fim_exerc,
        knowledge_date=line.dt_receb,
        lineage=Lineage(
            content_sha256=line.content_sha256,
            retrieval_id=line.retrieval_id,
            locator=line.locator,
            extractor=line.extractor,
            extractor_version=line.extractor_version,
            extraction_run_id=line.extraction_run_id,
        ),
    )

    return GoldFact(
        fact=fact,
        cod_cvm=line.cod_cvm,
        denom_cia=line.denom_cia,
        statement=line.statement,
        consolidated=line.consolidated,
        coluna_df=line.coluna_df,
        cd_conta=line.cd_conta,
        ds_conta=line.ds_conta,
        ordem_exerc=line.ordem_exerc,
        source_doc_id=line.doc_id,
        source_doc_version=line.versao,
        silver_id=line.silver_id,
    )


def build_facts(lines: Sequence[AccountLine]) -> tuple[list[GoldFact], BuildStats]:
    from pydantic import ValidationError

    stats = BuildStats()
    facts: list[GoldFact] = []
    for line in lines:
        try:
            gold = to_fact(line, stats)
        except ValidationError:
            # O contrato rejeitou o fato (ex. knowledge_date anterior ao fim do
            # periodo). Contado, nunca aceito em silencio.
            stats.skipped_contract_violation += 1
            continue
        if gold is not None:
            facts.append(gold)
            stats.facts_built += 1
    return facts, stats


# `cod_cvm` e `denom_cia` sairam daqui na M5.6: eles descrevem a ENTIDADE, e
# nao o fato. Vao para a tabela `entity`, uma linha por regime. O que resta
# nesta tupla ou e universal (periodo, valor, linhagem) ou e endereco dentro do
# documento de origem, que e do fato mesmo.
_COLUMNS = (
    "fact_id",
    "entity_id",
    "statement",
    "consolidated",
    "coluna_df",
    "cd_conta",
    "ds_conta",
    "period_type",
    "period_start",
    "period_end",
    "knowledge_date",
    "value",
    "unit",
    "currency",
    "ordem_exerc",
    "source_doc_id",
    "source_doc_version",
    "silver_id",
    "content_sha256",
    "retrieval_id",
    "locator",
    "extractor",
    "extractor_version",
    "extraction_run_id",
)

_INSERT = (
    f"INSERT INTO gold_fact ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))}) "
    "ON CONFLICT (fact_id) DO NOTHING"
)


def identities_from(facts: Sequence[GoldFact]) -> list["EntityIdentity"]:
    """Fatos da CVM -> identidades de entidade, deduplicadas.

    Duas por companhia: o CNPJ, que e o identificador canonico brasileiro e
    de onde `entity_id` deriva, e o `cod_cvm`, que e o apelido com que a CVM
    publica e com que um humano digita na linha de comando.

    O `entity_id` NAO e recalculado aqui - vem do fato, que ja o carrega. Uma
    segunda derivacao seria uma segunda fonte de verdade para a identidade da
    empresa, e as duas divergiriam no dia em que uma delas mudasse de regra.
    """
    from pat.contracts.entities import EntityIdentity

    vistos: dict[tuple[str, str], EntityIdentity] = {}
    for g in facts:
        # So o caminho da CVM. Um fato de outra jurisdicao chega aqui pelo
        # builder dela, que sabe qual e o identificador canonico de la - e
        # derivar CNPJ de um `us:cik:` produziria um identificador inventado.
        if not g.fact.entity_id.startswith("br:cnpj:"):
            continue
        cnpj = g.fact.entity_id.removeprefix("br:cnpj:")
        for scheme, local_id, primario in (
            ("cnpj", cnpj, True),
            ("cod_cvm", str(g.cod_cvm), False),
        ):
            chave = (g.fact.entity_id, scheme)
            if chave in vistos:
                continue
            vistos[chave] = EntityIdentity(
                entity_id=g.fact.entity_id,
                jurisdiction="BR",
                scheme=scheme,
                local_id=local_id,
                display_name=g.denom_cia,
                is_primary=primario,
            )
    return list(vistos.values())


def write_facts(conn: duckdb.DuckDBPyConnection, facts: Sequence[GoldFact]) -> int:
    """Grava os fatos E as entidades que eles descrevem, nesta ordem.

    As duas juntas de proposito: um fato cujo `entity_id` nao resolve para
    nenhuma entidade e o desaparecimento silencioso que a M5.6 existe para
    impedir, e separar as chamadas deixaria esse estado alcancavel por
    esquecimento. Aqui ele nao e alcancavel.
    """
    if not facts:
        return 0

    from pat.store.entity import upsert_identities

    upsert_identities(conn, identities_from(facts))

    before = conn.execute("SELECT COUNT(*) FROM gold_fact").fetchone()[0]
    conn.executemany(
        _INSERT,
        [
            [
                g.fact.fact_id,
                g.fact.entity_id,
                g.statement,
                g.consolidated,
                g.coluna_df,
                g.cd_conta,
                g.ds_conta,
                str(g.fact.period_type),
                g.fact.period_start,
                g.fact.period_end,
                g.fact.knowledge_date,
                g.fact.value,
                g.fact.unit,
                g.fact.currency,
                g.ordem_exerc,
                g.source_doc_id,
                g.source_doc_version,
                g.silver_id,
                g.fact.lineage.content_sha256,
                g.fact.lineage.retrieval_id,
                g.fact.lineage.locator,
                g.fact.lineage.extractor,
                g.fact.lineage.extractor_version,
                g.fact.lineage.extraction_run_id,
            ]
            for g in facts
        ],
    )
    after = conn.execute("SELECT COUNT(*) FROM gold_fact").fetchone()[0]
    return after - before


def count(conn: duckdb.DuckDBPyConnection) -> int:
    return conn.execute("SELECT COUNT(*) FROM gold_fact").fetchone()[0]

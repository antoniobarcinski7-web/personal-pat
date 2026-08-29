"""`FactResolver` de cotacao. O terceiro regime, e o primeiro nao contabil.

A pergunta que este arquivo tem que responder honestamente
-----------------------------------------------------------
"Qual era o preco em 31 de dezembro de 2024?" tem resposta. "Qual era o preco
em 28 de dezembro de 2024?" - o fim do exercicio da Intel - nao tem: era
sabado, e a bolsa nao abriu.

As duas saidas ruins sao simetricas. Recusar torna o valor de mercado
inutilizavel em metade dos exercicios fiscais americanos, que fecham em datas
sem pregao. Devolver o pregao mais proximo em SILENCIO afirma que houve
negociacao num dia em que nao houve.

A saida usada aqui e a terceira: o resolver olha para tras ate
`MAX_LOOKBACK_DAYS` e devolve o ULTIMO fechamento anterior ou igual a data
pedida - e o `ResolvedFact` carrega o `period_end` do pregao que de fato
aconteceu, e nao a data pedida. O deslocamento fica visivel no resultado, na
linhagem e na citacao. Quem ler ve "fechamento de 27/12" onde pediu 28/12.

Olhar para tras, e nunca para frente
------------------------------------
Um pregao POSTERIOR a data pedida nao era conhecido naquela data. Usa-lo seria
vazamento de futuro - o mesmo erro que `as_of` existe para impedir, cometido
num eixo diferente. A janela e para tras, sempre.

O limite de dias e declarado
----------------------------
Dez dias corridos cobrem qualquer feriado prolongado e nao cobrem um papel que
parou de negociar. Um papel suspenso ha meses devolveria uma cotacao velha com
cara de corrente, e a recusa nesse caso e a resposta certa.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import ClassVar

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    LineAddress,
    PeriodKind,
    ReportingScope,
    TaxonomyId,
    UnavailableReason,
)
from pat.query.asof import AsOf
from pat.semantics.frameworks.market.adapter import series_of
from pat.semantics.resolver import ResolutionFailure, ResolvedFact

MAX_LOOKBACK_DAYS = 10
"""Quantos dias corridos olhar para tras ate desistir.

Dez cobrem qualquer feriado prolongado - Natal e Ano Novo emendados, um
feriado nacional colado num fim de semana. Nao cobrem um papel que parou de
negociar, e essa e a intencao: uma cotacao de tres meses atras devolvida como
se fosse a do fechamento seria um numero velho com cara de corrente."""

TAXONOMY_STATEMENT = "market.quote"


class MarketQuoteResolver:
    taxonomy: ClassVar[TaxonomyId] = TaxonomyId.MARKET_QUOTE

    def __init__(self, asof: AsOf) -> None:
        self._asof = asof

    def entity_display(self, entity_id: str) -> tuple[str | None, tuple[tuple[str, str], ...]]:
        ref = self._asof.entity(entity_id)
        if ref is None:
            return None, ()
        locais = tuple(
            (linha[0], linha[1])
            for linha in self._asof.conn.execute(
                "SELECT scheme, local_id FROM entity WHERE entity_id = ? ORDER BY scheme",
                [entity_id],
            ).fetchall()
        )
        return ref.display_name, locais

    def resolve(
        self,
        *,
        entity_id: str,
        address: LineAddress,
        period_end: date,
        period_kind: PeriodKind,
        scope: ReportingScope,
        as_of: date,
        member: str | None = None,
    ) -> ResolvedFact | ResolutionFailure:
        if period_kind is not PeriodKind.STOCK:
            return ResolutionFailure(
                UnavailableReason.WRONG_PERIOD_TYPE,
                "uma cotacao e saldo pontual, e o conceito pede fluxo. Preco nao "
                "se acumula num intervalo; o que se acumula e retorno, que e "
                "outra grandeza.",
            )
        if member is not None:
            return ResolutionFailure(
                UnavailableReason.SCOPE_NOT_AVAILABLE,
                "cotacao nao tem dimensao: um papel nao se decompoe em segmentos.",
            )

        serie = series_of(address)
        # A janela olha para TRAS, e o corte de `as_of` continua valendo por
        # cima: um pregao posterior ao `as_of` nao era conhecido, e um pregao
        # posterior a data pedida nao existia ainda.
        limite = period_end - timedelta(days=MAX_LOOKBACK_DAYS)
        row = self._asof.conn.execute(
            """
            SELECT value, currency, period_end, knowledge_date, fact_id, locator
            FROM gold_fact
            WHERE entity_id = ?
              AND statement = ?
              AND cd_conta = ?
              AND period_end <= ?
              AND period_end >= ?
              AND knowledge_date <= ?
            ORDER BY period_end DESC, knowledge_date DESC
            LIMIT 1
            """,
            [entity_id, TAXONOMY_STATEMENT, serie, period_end, limite, as_of],
        ).fetchone()

        if row is None:
            return ResolutionFailure(
                UnavailableReason.MISSING_FACT_AS_OF,
                f"nenhum fechamento de {serie!r} para {entity_id} entre "
                f"{limite} e {period_end}, conhecido em {as_of}. A janela olha "
                f"{MAX_LOOKBACK_DAYS} dias para tras e nunca para frente: um "
                "pregao posterior nao era conhecido na data pedida.",
            )

        valor, moeda, pregao, conhecido, fact_id, locator = row
        return ResolvedFact(
            value=valor,
            currency=moeda or "USD",
            period_type=PeriodType.INSTANT,
            period_start=None,
            # O pregao que de fato aconteceu, e nao a data pedida. E o que faz
            # o deslocamento aparecer no resultado em vez de sumir nele.
            period_end=pregao,
            knowledge_date=conhecido,
            fact_id=fact_id,
            locator=locator,
            label_as_reported=address.label_as_reported,
        )

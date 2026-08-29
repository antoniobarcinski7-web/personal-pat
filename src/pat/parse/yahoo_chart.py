"""Cotacao diaria -> fatos de mercado. O analogo de `sec_xbrl.py` para preco.

Duas decisoes de leitura que mudam o numero, e por isso estao aqui e nao
implicitas:

1. **Fechamento AJUSTADO, e nao o bruto.** Yahoo publica os dois: `close` e o
   preco negociado no dia, `adjclose` o mesmo preco recalculado para ser
   comparavel com hoje depois de desdobramentos e proventos. Para uma serie
   historica, o bruto e uma armadilha - a Netflix desdobrou 10 para 1 em
   novembro de 2025, e uma serie de `close` mostra uma queda de 90% num dia em
   que ninguem perdeu nada.

   O custo do ajustado tambem e declarado: ele MUDA retroativamente. O preco de
   2023 lido hoje nao e o preco de 2023 lido no ano passado, porque o
   desdobramento de 2025 entrou no meio. Isso e legitimo e e por isso que o
   `knowledge_date` do fato e a data da BUSCA, e nao a do pregao - o numero
   passou a ser esse quando esta serie foi baixada.

2. **Fuso.** O timestamp e epoch UTC do momento de abertura do pregao na bolsa
   de origem. A data do pregao e a data LOCAL da bolsa, e converter em UTC
   jogaria um pregao asiatico para o dia anterior. O deslocamento vem do
   proprio JSON (`gmtoffset`), e nao de uma tabela de fusos deste modulo.

O que este modulo NAO faz
-------------------------
Nao preenche dia sem pregao, nao interpola e nao decide qual e "o preco de
fechamento do exercicio". Fim de semana simplesmente nao tem fato, e quem
precisa do preco numa data em que a bolsa nao abriu resolve isso no resolver,
declaradamente.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

__all__ = [
    "EXTRACTOR",
    "EXTRACTOR_VERSION",
    "ChartParseStats",
    "QuoteLine",
    "parse_chart",
]

EXTRACTOR = "yahoo.chart"
EXTRACTOR_VERSION = "1.0.0"

CLOSE_SERIES = "close"
"""Nome da serie no endereco de mercado. Um so, por enquanto.

Volume, abertura e maxima existem no JSON e nao entram: cada serie que entra e
uma que alguem tem que saber ler, e nenhuma metrica registrada usa as outras.
Acrescentar quando houver quem consuma."""


@dataclass(frozen=True)
class QuoteLine:
    """Um fechamento ajustado, num pregao."""

    symbol: str
    currency: str
    trade_date: date
    close: Decimal
    ordinal: int


@dataclass
class ChartParseStats:
    points_read: int = 0
    skipped_null: int = 0
    """Pregao sem fechamento publicado. Yahoo devolve `null` em feriado parcial
    e em papel suspenso; o buraco e da fonte, e preenche-lo seria inventar
    negociacao que nao houve."""

    @property
    def skipped_total(self) -> int:
        return self.skipped_null


def parse_chart(payload: bytes) -> tuple[list[QuoteLine], ChartParseStats]:
    """JSON do chart -> fechamentos ajustados, um por pregao.

    Levanta quando o layout muda na origem: um agregador que reorganiza o JSON
    produziria zero cotacoes em silencio, e zero cotacoes se le como "o papel
    nao negociou".
    """
    documento = json.loads(payload)
    chart = documento.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"a origem devolveu erro: {chart['error']}")

    resultados = chart.get("result") or []
    if not resultados:
        raise ValueError("chart sem `result`; layout mudou na origem")
    r = resultados[0]

    meta = r.get("meta") or {}
    simbolo = str(meta.get("symbol") or "").upper()
    moeda = str(meta.get("currency") or "").upper()
    if not simbolo or not moeda:
        raise ValueError("chart sem `symbol` ou `currency`; layout mudou na origem")

    offset = int(meta.get("gmtoffset") or 0)
    timestamps = r.get("timestamp") or []
    ajustados = _serie_ajustada(r)
    if ajustados is None:
        raise ValueError(
            "chart sem `adjclose`. O fechamento bruto existe e NAO serve de "
            "substituto: uma serie nao ajustada mostra queda de 90% num "
            "desdobramento, e trocar um pelo outro em silencio faria a serie "
            "mudar de significado sem mudar de nome."
        )

    stats = ChartParseStats()
    linhas: list[QuoteLine] = []
    for i, (ts, fechamento) in enumerate(zip(timestamps, ajustados, strict=False)):
        stats.points_read += 1
        if fechamento is None or ts is None:
            stats.skipped_null += 1
            continue
        linhas.append(
            QuoteLine(
                symbol=simbolo,
                currency=moeda,
                trade_date=_data_do_pregao(int(ts), offset),
                # Pelo `str`: `Decimal(float)` traria o erro binario do float
                # para dentro do dinheiro, que e o que `Decimal` existe para
                # impedir.
                close=Decimal(str(fechamento)),
                ordinal=i,
            )
        )
    return linhas, stats


def _serie_ajustada(resultado: dict) -> list | None:
    indicadores = resultado.get("indicators") or {}
    ajustado = indicadores.get("adjclose") or []
    if ajustado and isinstance(ajustado[0], dict):
        return ajustado[0].get("adjclose")
    return None


def _data_do_pregao(epoch: int, gmtoffset: int) -> date:
    """Epoch UTC -> data LOCAL da bolsa.

    O deslocamento vem do JSON e nao de uma tabela local: uma tabela de fusos
    escrita aqui envelheceria com o horario de verao, e o erro apareceria como
    um pregao no dia errado - uma vez por ano, no pior momento para notar.
    """
    return (datetime.fromtimestamp(epoch, tz=UTC) + timedelta(seconds=gmtoffset)).date()

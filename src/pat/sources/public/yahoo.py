"""Yahoo Finance - cotacao diaria. A PRIMEIRA fonte nao oficial do projeto.

Por que ela existe, e o que muda por ela existir
------------------------------------------------
Nenhum regulador publica preco. A SEC publica o que a companhia declarou; o
preco e do mercado, e chega por terceiro. Sem ele o sistema faz analise de
NEGOCIO e nao de INVESTIMENTO: nao ha multiplo, e a pergunta que uma tese
honesta faz antes das outras - "o que o preco de hoje ja esta assumindo?" -
nao tem contra o que resolver.

O custo e a procedencia. `tier = PUBLIC_WEB`, e nao `PRIMARY_OFFICIAL`: um
agregador nao e a bolsa. A distincao viaja em todo `Retrieval` e chega ao
`MetricResult`, e e o que permite auditar depois quanto de uma conclusao
dependeu de fonte fraca. Um preco e um numero da CVM nao valem a mesma coisa, e
o sistema nao pode deixar de saber disso.

Por que Yahoo e nao Stooq
-------------------------
Stooq foi a primeira escolha e esta FORA: ele passou a servir um desafio de
prova de trabalho em JavaScript a qualquer cliente HTTP. Resolve-lo em Python e
tecnicamente simples e seria contornar um controle de acesso que o site pos ali
de proposito - alem de fragil, porque o desafio muda e o pipeline quebra em
silencio, num dado que alimenta valuation.

Este endpoint responde a uma requisicao identificada honestamente, sem desafio e
sem fingir navegador - conferido com User-Agent proprio e ate sem User-Agent
nenhum. Nao ha contrato de uso automatizado, e por isso o intervalo minimo aqui
e maior que o da SEC: pedir menos do que se pode e a postura certa com
infraestrutura de terceiro que nao te deve nada.

O que este provider NAO faz
---------------------------
Nao interpreta o JSON, nao converte timestamp e nao decide o que e "o preco".
Ele baixa bytes e registra procedencia, como todo provider. O parsing e da
silver, em `parse/yahoo_chart.py`.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pat.contracts.common import SourceTier
from pat.contracts.documents import ResourceRef
from pat.sources.base import DatasetSpec, PublicSourceProvider, SourceError

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
_HOMEPAGE = "https://finance.yahoo.com"
_LICENSE = (
    "Sem licenca declarada para uso automatizado. Agregador; use com moderacao "
    "e trate como PUBLIC_WEB, nunca como fonte oficial."
)

# Simbolo de bolsa: letras, digitos, ponto e hifen. Estreito de proposito - um
# simbolo livre viraria a porta por onde uma URL arbitraria entra no fetch.
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-]{1,20}$")

RANGES = frozenset(
    {"1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}
)
"""Janelas que o endpoint aceita. Tabela declarada: uma janela invalida volta
como erro do servidor, e uma tabela local recusa antes de gastar a requisicao."""


class YahooProvider(PublicSourceProvider):
    provider_id: ClassVar[str] = "yahoo"
    tier: ClassVar[SourceTier] = SourceTier.PUBLIC_WEB
    version: ClassVar[str] = "1.0.0"

    min_interval_s: ClassVar[float] = 2.0
    """Mais lento que a SEC de proposito.

    A SEC publica um teto e uma politica de acesso justo; aqui nao ha nem uma
    coisa nem outra. Na duvida sobre o que o terceiro tolera, a escolha certa e
    pedir menos."""

    def datasets(self) -> tuple[DatasetSpec, ...]:
        return (
            DatasetSpec(
                dataset_id="yahoo.chart",
                title="Cotacao diaria de um papel",
                tier=self.tier,
                params=("symbol", "range"),
                homepage=_HOMEPAGE,
                license=_LICENSE,
                notes=(
                    "JSON com serie de fechamento ajustado por evento corporativo. "
                    "Snapshot corrente: buscar de novo traz a serie inteira de "
                    "novo, e o conteudo muda a cada pregao. `pat changed` vai "
                    "acusar mudanca todo dia, e isso e a fonte se comportando "
                    "como fonte de mercado."
                ),
            ),
        )

    def resolve(self, dataset_id: str, **params: str) -> tuple[ResourceRef, ...]:
        spec = self.spec(dataset_id)
        unexpected = set(params) - set(spec.params)
        if unexpected:
            raise SourceError(f"{dataset_id}: parametros desconhecidos {sorted(unexpected)}")

        simbolo = str(params.get("symbol") or "").strip().upper()
        if not _SYMBOL_RE.match(simbolo):
            raise SourceError(
                f"{dataset_id}: simbolo invalido {simbolo!r}. Letras, digitos, ponto "
                "e hifen, ate 20 caracteres."
            )
        janela = str(params.get("range") or "max").strip().lower()
        if janela not in RANGES:
            raise SourceError(
                f"{dataset_id}: janela {janela!r} nao suportada. "
                f"Use uma de: {', '.join(sorted(RANGES))}."
            )

        return (
            ResourceRef(
                provider_id=self.provider_id,
                dataset_id=dataset_id,
                # O recurso logico e o PAPEL, e nao papel+janela: buscar `1y` e
                # depois `max` traz o mesmo instrumento com mais historia, e
                # trata-los como recursos diferentes faria o catalogo ter duas
                # historias do mesmo papel que nunca se encontram.
                resource_key=simbolo,
                url=f"{_CHART}/{simbolo}?range={janela}&interval=1d",
                media_type="application/json",
                params=(("symbol", simbolo), ("range", janela)),
            ),
        )

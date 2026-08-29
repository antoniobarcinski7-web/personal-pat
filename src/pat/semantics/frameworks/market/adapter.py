"""Adapter da taxonomia `market.quote`.

Um endereco de mercado tem UM campo: o nome da serie. Nao ha demonstracao, nao
ha dimensao e nao ha escopo - um preco de fechamento e um preco de fechamento.

A lista de series e FECHADA, ao contrario do us-gaap. La a lista seria
impraticavel (milhares de elementos, muda todo ano); aqui sao tres nomes e cada
um precisa de alguem que saiba le-lo no parser. Uma serie que o adapter
aceitasse e o parser nao lesse viraria `MISSING_FACT_AS_OF` eterno, apontando
para um endereco que nunca vai existir.
"""

from __future__ import annotations

from typing import ClassVar

from pat.contracts.semantics import LineAddress, StatementKind, TaxonomyId
from pat.semantics.frameworks import AdapterError

SERIES = frozenset({"close"})
"""Series enderecaveis. Fechada, e por enquanto uma.

`open`, `high` e `volume` existem no JSON da fonte e NAO entram: cada serie que
entra e uma que alguem tem que saber ler, e nenhuma metrica registrada usa as
outras. Acrescentar quando houver quem consuma."""

_ALLOWED_KEYS = frozenset({"series", "symbol"})


class MarketQuoteAdapter:
    taxonomy: ClassVar[TaxonomyId] = TaxonomyId.MARKET_QUOTE

    def parse_address(self, raw: dict) -> LineAddress:
        desconhecidas = set(raw) - _ALLOWED_KEYS - {"label_as_reported"}
        if desconhecidas:
            raise AdapterError(
                f"chaves desconhecidas num endereco de mercado: {sorted(desconhecidas)}. "
                f"Aceitas: {sorted(_ALLOWED_KEYS)}."
            )

        serie = str(raw.get("series") or "").strip().lower()
        if serie not in SERIES:
            raise AdapterError(
                f"serie de mercado desconhecida: {serie!r}. "
                f"Enderecaveis: {sorted(SERIES)}. Uma serie que o adapter aceitasse "
                "e o parser nao lesse viraria endereco que nunca resolve."
            )

        # O papel NAO entra no endereco. A companhia ja e o `entity_id` da
        # consulta, e o vinculo papel -> companhia esta no catalogo de
        # identidades. Repeti-lo aqui criaria duas verdades sobre qual papel e
        # daquela empresa, e a do mapeamento envelheceria calada num
        # redomicilio ou numa troca de bolsa.
        return LineAddress(
            taxonomy=self.taxonomy,
            address=(("series", serie),),
            statement_kind=StatementKind.MARKET,
            label_as_reported=raw.get("label_as_reported"),
        )


def series_of(address: LineAddress) -> str:
    """O nome da serie de um endereco de mercado."""
    return dict(address.address).get("series", "")

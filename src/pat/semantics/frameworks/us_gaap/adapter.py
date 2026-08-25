"""Adapter da taxonomia `us-gaap.xbrl`.

Traduz o bloco de linha de um mapeamento TOML num `LineAddress`. Valida
estrutura, nunca semantica - se `GrossProfit` significa lucro bruto para
AQUELA companhia e afirmacao do binding, conferida por `pat mapping-check`.

Por que o elemento nao e validado contra uma lista
--------------------------------------------------
A taxonomia us-gaap tem milhares de elementos e muda a cada ano. Embutir uma
lista aqui a deixaria desatualizada em doze meses e recusaria mapeamento
correto. O que se valida e a FORMA (nome de elemento sem prefixo); se o
elemento existe para a companhia, quem responde e o gold - e a resposta chega
como `MISSING_FACT_AS_OF`, com o endereco tentado.

A Intel prova por que a lista nao ajudaria: ela nao usa `Revenues` nem
`CostOfRevenue`, e sim `RevenueFromContractWithCustomerExcludingAssessedTax` e
`CostOfGoodsAndServicesSold`. Casar elemento por semelhanca de nome escolheria
o errado com confianca, que e o que `equivalence_basis` existe para impedir.

`segment` e opcional e carrega a dimensao
-----------------------------------------
Vazio, o endereco aponta para o fato consolidado. Preenchido
(`BusinessSegments=IntelFoundry`), aponta para o fato daquele segmento. E a
mesma forma que a DERA publica, preservada sem reescrita: normalizar o membro
aqui obrigaria o resolver a desnormalizar depois, e as duas conversoes
divergiriam no primeiro caso irregular.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pat.contracts.semantics import LineAddress, StatementKind, TaxonomyId
from pat.semantics.frameworks import AdapterError

_ELEMENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_SEGMENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*=[A-Za-z0-9_.;-]+$")

STATEMENT_KINDS: dict[str, StatementKind] = {
    "income": StatementKind.INCOME,
    "balance": StatementKind.BALANCE,
    "cash_flow": StatementKind.CASH_FLOW,
    "comprehensive_income": StatementKind.COMPREHENSIVE_INCOME,
    "equity_changes": StatementKind.EQUITY_CHANGES,
}

_ALLOWED_KEYS = frozenset({"element", "statement", "segment"})


class UsGaapXbrlAdapter:
    taxonomy: ClassVar[TaxonomyId] = TaxonomyId.US_GAAP_XBRL

    def parse_address(self, raw: dict[str, object]) -> LineAddress:
        unknown = set(raw) - _ALLOWED_KEYS - {"label_as_reported"}
        if unknown:
            raise AdapterError(
                f"campos desconhecidos no endereco us-gaap: {sorted(unknown)}. "
                f"Aceitos: {sorted(_ALLOWED_KEYS)}"
            )

        element = str(raw.get("element", "")).strip()
        if not _ELEMENT_RE.match(element):
            raise AdapterError(
                f"elemento XBRL invalido: {element!r}. Esperado o nome sem prefixo, "
                "como 'GrossProfit' - e nao 'us-gaap:GrossProfit'."
            )

        statement = str(raw.get("statement", "income")).strip().lower()
        if statement not in STATEMENT_KINDS:
            raise AdapterError(
                f"demonstracao desconhecida: {statement!r}. "
                f"Conhecidas: {sorted(STATEMENT_KINDS)}"
            )

        segment = str(raw.get("segment", "") or "").strip()
        if segment and not _SEGMENT_RE.match(segment):
            raise AdapterError(
                f"dimensao invalida: {segment!r}. Esperado 'Eixo=Membro', como "
                "'BusinessSegments=IntelFoundry' - a forma que a SEC publica."
            )

        address: list[tuple[str, str]] = [("element", element), ("statement", statement)]
        if segment:
            address.append(("segment", segment))

        label = raw.get("label_as_reported")
        return LineAddress(
            taxonomy=self.taxonomy,
            address=tuple(sorted(address)),
            statement_kind=STATEMENT_KINDS[statement],
            label_as_reported=str(label).strip() if label else None,
        )

    def describe(self, address: LineAddress) -> str:
        parts = dict(address.address)
        out = f"us-gaap:{parts.get('element')}"
        if segmento := parts.get("segment"):
            out += f" [{segmento}]"
        if address.label_as_reported:
            out += f'  "{address.label_as_reported}"'
        return out


def parts_of(address: LineAddress) -> tuple[str, str]:
    """(element, segment). Usado pelo resolver."""
    if address.taxonomy is not TaxonomyId.US_GAAP_XBRL:
        raise AdapterError(f"endereco nao e da taxonomia us-gaap: {address.taxonomy}")
    parts = dict(address.address)
    return parts["element"], parts.get("segment", "")

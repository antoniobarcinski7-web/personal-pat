"""Adapter da taxonomia `cvm.plano_padronizado`.

Traduz o bloco de linha de um mapeamento TOML num `LineAddress`. Valida
estrutura, nao semantica: se a conta existe para aquela companhia e se ela
significa o que o binding afirma, quem responde e `pat mapping-check` contra
o gold, e no fim os golden tests.

Campo desconhecido levanta. Um mapeamento com `cd_contas` no plural precisa
falhar no load - se passasse batido, viraria conceito nao resolvido, e um
conceito nao resolvido parece problema de dado quando na verdade e erro de
digitacao.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pat.contracts.semantics import LineAddress, StatementKind, TaxonomyId
from pat.semantics.frameworks import AdapterError

_CD_CONTA_RE = re.compile(r"^\d+(\.\d{2})*$")

STATEMENT_KINDS: dict[str, StatementKind] = {
    "DRE": StatementKind.INCOME,
    "BPA": StatementKind.BALANCE,
    "BPP": StatementKind.BALANCE,
    "DFC_MI": StatementKind.CASH_FLOW,
    "DFC_MD": StatementKind.CASH_FLOW,
    "DVA": StatementKind.VALUE_ADDED,
    "DRA": StatementKind.COMPREHENSIVE_INCOME,
    "DMPL": StatementKind.EQUITY_CHANGES,
}

_ALLOWED_KEYS = frozenset({"statement", "cd_conta", "coluna_df"})


class CvmPlanoPadronizadoAdapter:
    taxonomy: ClassVar[TaxonomyId] = TaxonomyId.CVM_PLANO_PADRONIZADO

    def parse_address(self, raw: dict[str, object]) -> LineAddress:
        unknown = set(raw) - _ALLOWED_KEYS - {"label_as_reported"}
        if unknown:
            raise AdapterError(
                f"campos desconhecidos no endereco CVM: {sorted(unknown)}. "
                f"Aceitos: {sorted(_ALLOWED_KEYS)}"
            )

        statement = str(raw.get("statement", "")).strip().upper()
        if statement not in STATEMENT_KINDS:
            raise AdapterError(
                f"demonstracao desconhecida: {statement!r}. "
                f"Conhecidas: {sorted(STATEMENT_KINDS)}"
            )

        cd_conta = str(raw.get("cd_conta", "")).strip()
        if not _CD_CONTA_RE.match(cd_conta):
            raise AdapterError(
                f"cd_conta invalido: {cd_conta!r} (esperado no formato '3.01' ou '6.01.01.04')"
            )

        coluna_df = str(raw.get("coluna_df", "") or "").strip()
        if coluna_df and statement != "DMPL":
            raise AdapterError(
                f"coluna_df so faz sentido na DMPL; recebida em {statement}. "
                "Nas demais demonstracoes a dimensao nao existe."
            )

        address: list[tuple[str, str]] = [("cd_conta", cd_conta)]
        if coluna_df:
            address.append(("coluna_df", coluna_df))
        address.append(("statement", statement))

        label = raw.get("label_as_reported")
        return LineAddress(
            taxonomy=self.taxonomy,
            address=tuple(sorted(address)),
            statement_kind=STATEMENT_KINDS[statement],
            label_as_reported=str(label).strip() if label else None,
        )

    def describe(self, address: LineAddress) -> str:
        parts = dict(address.address)
        out = f"{parts.get('statement')}/{parts.get('cd_conta')}"
        if coluna := parts.get("coluna_df"):
            out += f" [{coluna}]"
        if address.label_as_reported:
            out += f"  \"{address.label_as_reported}\""
        return out


def parts_of(address: LineAddress) -> tuple[str, str, str]:
    """(statement, cd_conta, coluna_df). Usado pelo resolver."""
    if address.taxonomy is not TaxonomyId.CVM_PLANO_PADRONIZADO:
        raise AdapterError(f"endereco nao e da taxonomia da CVM: {address.taxonomy}")
    parts = dict(address.address)
    return parts["statement"], parts["cd_conta"], parts.get("coluna_df", "")

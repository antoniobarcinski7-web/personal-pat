"""Adapters de taxonomia: o unico lugar do sistema que le enderecos concretos.

Um adapter sabe traduzir o bloco de linha de um arquivo TOML de mapeamento
num `LineAddress` validado, e sabe descreve-lo para um humano. Nada acima
desta pasta pode importar daqui - ver `tests/semantics/test_layering.py`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pat.contracts.semantics import LineAddress, TaxonomyId


@runtime_checkable
class TaxonomyAdapter(Protocol):
    taxonomy: TaxonomyId

    def parse_address(self, raw: dict[str, object]) -> LineAddress:
        """Bloco cru do TOML -> endereco validado. Levanta em campo desconhecido:
        erro de mapeamento tem que aparecer no load, nao como fato faltante."""
        ...

    def describe(self, address: LineAddress) -> str: ...


class AdapterError(ValueError):
    pass


_ADAPTERS: dict[TaxonomyId, TaxonomyAdapter] = {}


def register(adapter: TaxonomyAdapter) -> None:
    _ADAPTERS[adapter.taxonomy] = adapter


def get(taxonomy: TaxonomyId) -> TaxonomyAdapter:
    try:
        return _ADAPTERS[taxonomy]
    except KeyError:
        raise AdapterError(
            f"nenhum adapter registrado para a taxonomia {taxonomy!r}. "
            f"Registradas: {sorted(_ADAPTERS) or 'nenhuma'}"
        ) from None


def registered() -> tuple[TaxonomyId, ...]:
    return tuple(sorted(_ADAPTERS))


def install_builtins() -> None:
    """Registra os adapters embutidos. Chamado pela raiz de composicao
    (`pat.semantics`), nunca na importacao deste modulo.

    A diferenca importa: se este modulo importasse o adapter da CVM sozinho,
    qualquer coisa que tocasse o registro de taxonomias - inclusive o loader,
    que e universal - passaria a arrastar codigo brasileiro junto. O registro
    e o mecanismo de plugin; quais plugins entram e decisao de quem monta o
    sistema.
    """
    from pat.semantics.frameworks.cvm_dfp.adapter import CvmPlanoPadronizadoAdapter
    from pat.semantics.frameworks.us_gaap.adapter import UsGaapXbrlAdapter

    if TaxonomyId.CVM_PLANO_PADRONIZADO not in _ADAPTERS:
        register(CvmPlanoPadronizadoAdapter())
    if TaxonomyId.US_GAAP_XBRL not in _ADAPTERS:
        register(UsGaapXbrlAdapter())

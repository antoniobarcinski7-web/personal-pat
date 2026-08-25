"""`lucro_liquido@v1` nas duas jurisdicoes, e a decisao A3.

O teste que carrega o arquivo e
`test_o_alvo_e_a_controladora_e_nao_o_consolidado`: as duas jurisdicoes
publicam a distincao, e escolher a linha errada da um numero plausivel e
errado.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    MetricUnavailable,
    ReportingScope,
    TaxonomyId,
    UnavailableReason,
)
from pat.semantics import concepts
from pat.semantics.engine import Engine
from pat.semantics.frameworks import install_builtins
from pat.semantics.loader import load_dir
from pat.semantics.registry import default_registry
from pat.semantics.resolver import ResolutionFailure, ResolvedFact

MM = Decimal(1_000_000)
AS_OF = date(2026, 6, 30)

PETROBRAS = "br:cnpj:33000167000101"
INTEL = "us:cik:0000050863"
FY24_BR = date(2024, 12, 31)
FY24_US = date(2024, 12, 28)
FY23_US = date(2023, 12, 30)

# Publicado. Inclui DELIBERADAMENTE as linhas do resultado TOTAL, que o
# binding nao pode escolher - e o que o teste do alvo confere.
CVM = {
    ("3.11.01", FY24_BR): Decimal("36606"),   # atribuivel a controladora
    ("3.11", FY24_BR): Decimal("37009"),      # consolidado total
    ("3.11.02", FY24_BR): Decimal("403"),     # nao controladores
}
SEC = {
    ("NetIncomeLoss", FY24_US): Decimal("-18756"),   # atribuivel a controladora
    ("NetIncomeLoss", FY23_US): Decimal("1689"),
    ("ProfitLoss", FY24_US): Decimal("-19233"),      # total, incl. nao controladores
}


class _Resolver:
    def __init__(self, taxonomy, publicado, chave):
        self.taxonomy = taxonomy
        self._publicado = publicado
        self._chave = chave

    def resolve(self, *, entity_id, address, period_end, period_kind, scope, as_of, member=None):
        partes = dict(address.address)
        chave = (partes[self._chave], period_end)
        if chave not in self._publicado:
            return ResolutionFailure(
                UnavailableReason.MISSING_FACT_AS_OF, f"nada para {chave}"
            )
        return ResolvedFact(
            value=self._publicado[chave] * MM,
            currency="BRL" if self._chave == "cd_conta" else "USD",
            period_type=PeriodType.YEAR,
            period_start=date(period_end.year, 1, 1),
            period_end=period_end,
            knowledge_date=date(period_end.year + 1, 3, 1),
            fact_id=f"{chave[0]}-{period_end}",
            locator=str(chave),
        )

    def entity_display(self, entity_id):
        return "TESTE", ()


def _engine(taxonomy, publicado, chave, source):
    install_builtins()
    return Engine(
        resolvers={taxonomy: _Resolver(taxonomy, publicado, chave)},
        mappings=load_dir(),
        registry=default_registry(),
        source=source,
        pat_version="teste",
    )


@pytest.fixture
def engine_br():
    return _engine(TaxonomyId.CVM_PLANO_PADRONIZADO, CVM, "cd_conta", "cvm")


@pytest.fixture
def engine_us():
    return _engine(TaxonomyId.US_GAAP_XBRL, SEC, "element", "sec.companyfacts")


def _valor(engine, entity_id, period_end):
    return engine.compute(
        "lucro_liquido@v1",
        entity_id=entity_id,
        period_end=period_end,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )


# -- O que a metrica devolve -------------------------------------------------


def test_petrobras_resolve_pela_familia_da_cvm(engine_br):
    """O binding vive na FAMILIA: 3.11.01 e estavel no plano padronizado.

    Diferente de `d_and_a_pnl`, que precisa de override por empresa porque a
    linha do DFC e subconta especifica de cada uma.
    """
    resultado = _valor(engine_br, PETROBRAS, FY24_BR)
    assert not isinstance(resultado, MetricUnavailable), resultado
    assert resultado.value == Decimal("36606") * MM
    assert resultado.currency == "BRL"
    assert resultado.fidelity == "exact"


def test_intel_resolve_pelo_mapeamento_us_gaap(engine_us):
    resultado = _valor(engine_us, INTEL, FY24_US)
    assert not isinstance(resultado, MetricUnavailable), resultado
    assert resultado.value == Decimal("-18756") * MM
    assert resultado.currency == "USD"


def test_prejuizo_atravessa_sem_tratamento_especial(engine_us):
    """FY2023 lucro, FY2024 prejuizo. Nenhum dos dois e suprimido.

    Nao ha check de nao-negatividade aqui, ao contrario de `receita_liquida`:
    prejuizo e comum e legitimo, e um alarme que soa todo ano ruim e um alarme
    que se aprende a ignorar.
    """
    lucro = _valor(engine_us, INTEL, FY23_US)
    prejuizo = _valor(engine_us, INTEL, FY24_US)
    assert lucro.value > 0
    assert prejuizo.value < 0


# -- A decisao A3, que e o ponto do arquivo ----------------------------------


def test_o_alvo_e_a_controladora_e_nao_o_consolidado(engine_br, engine_us):
    """As duas jurisdicoes publicam as duas linhas, e a metrica escolhe UMA.

    Petrobras FY2024: 3.11.01 = 36.606, 3.11 = 37.009 (diferenca 403).
    Intel FY2024: NetIncomeLoss = -18.756, ProfitLoss = -19.233 (diferenca 477).

    Quem calcula LPA ou retorno sobre o patrimonio do acionista quer o
    atribuivel; somar os dois e contar o que nao lhe pertence. O resolver de
    teste tem AS DUAS linhas disponiveis - se o binding apontasse para a
    errada, o valor sairia diferente e este teste quebraria.
    """
    assert _valor(engine_br, PETROBRAS, FY24_BR).value == CVM[("3.11.01", FY24_BR)] * MM
    assert _valor(engine_br, PETROBRAS, FY24_BR).value != CVM[("3.11", FY24_BR)] * MM

    assert _valor(engine_us, INTEL, FY24_US).value == SEC[("NetIncomeLoss", FY24_US)] * MM
    assert _valor(engine_us, INTEL, FY24_US).value != SEC[("ProfitLoss", FY24_US)] * MM


def test_a_diferenca_e_os_nao_controladores(engine_br):
    """Sanidade contabil: 3.11.01 + 3.11.02 = 3.11."""
    assert (
        CVM[("3.11.01", FY24_BR)] + CVM[("3.11.02", FY24_BR)] == CVM[("3.11", FY24_BR)]
    )


def test_nao_existe_fallback_para_o_resultado_total(engine_br):
    """Sem a linha da controladora, a metrica RECUSA - nao cai para 3.11.

    Uma companhia sem nao controladores tem os dois numeros iguais e uma com
    tem diferentes; devolver o que estiver disponivel faria a resposta mudar de
    significado conforme a estrutura societaria, em silencio.
    """
    parcial = {k: v for k, v in CVM.items() if k[0] != "3.11.01"}
    engine = _engine(TaxonomyId.CVM_PLANO_PADRONIZADO, parcial, "cd_conta", "cvm")
    resultado = _valor(engine, PETROBRAS, FY24_BR)
    assert isinstance(resultado, MetricUnavailable)
    assert resultado.reason is UnavailableReason.MISSING_FACT_AS_OF


# -- A camada universal continua universal -----------------------------------


def test_a_definicao_nao_cita_plano_de_contas_nem_elemento():
    """`lucro_liquido@v1` vale nos dois regimes porque nao conhece nenhum.

    `tests/semantics/test_layering.py` ja varre `definitions/` inteiro; esta
    assercao e explicita para o arquivo novo.
    """
    from pathlib import Path

    texto = Path("src/pat/semantics/definitions/lucro_liquido.py").read_text(
        encoding="utf-8"
    )
    for proibido in ("3.11", "cd_conta", "NetIncomeLoss", "us-gaap", "cod_cvm"):
        assert proibido not in texto, f"a definicao cita {proibido}"


def test_o_conceito_diz_que_o_total_e_outro_conceito():
    """A decisao A3 fica registrada no proprio catalogo, e nao so no commit."""
    conceito = concepts.get(concepts.NET_INCOME_CONTROLLING)
    notas = " ".join(conceito.boundary_notes)
    assert "A3" in notas
    assert "SEPARADO" in notas or "separado" in notas

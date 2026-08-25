"""Intel -> SEC/DERA -> SEGMENT: resolucao estruturada, e o que ela evita.

O outro lado de `test_segment_axis.py`. La o eixo recusa porque a CVM nao
publica a dimensao; aqui ele resolve porque a SEC publica - e resolve fechando
exatamente, com a eliminacao intersegmento como membro declarado.

Os numeros sao transcritos a mao da nota de segmentos da Intel, via dataset da
DERA, FY2023 e FY2024.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.common import PeriodType
from pat.contracts.decomposition import DecompositionResult
from pat.contracts.semantics import ReportingScope, TaxonomyId, UnavailableReason
from pat.research.decompose import decompose
from pat.semantics.engine import Engine
from pat.semantics.frameworks import install_builtins
from pat.semantics.loader import load_dir
from pat.semantics.registry import default_registry
from pat.semantics.resolver import ResolutionFailure, ResolvedFact

INTEL = "us:cik:0000050863"
FY23 = date(2023, 12, 31)
FY24 = date(2024, 12, 31)
AS_OF = date(2026, 6, 30)
MM = Decimal(1_000_000)

_OP = "ConsolidationItems=OperatingSegments;"
_ROLLUP = "ClientComputingGroupDatacenterAndAIAndNetworkAndEdge"

# As chaves sao a string de dimensao EXATAMENTE como a fonte publica.
SEGMENTOS: dict[tuple[str, date], Decimal] = {
    (f"BusinessSegments=ClientComputingGroup;{_OP}", FY23): Decimal("29258"),
    (f"BusinessSegments=ClientComputingGroup;{_OP}", FY24): Decimal("30290"),
    (f"BusinessSegments=DatacenterAndAI;{_OP}", FY23): Decimal("12635"),
    (f"BusinessSegments=DatacenterAndAI;{_OP}", FY24): Decimal("12817"),
    (f"BusinessSegments=NetworkAndEdge;{_OP}", FY23): Decimal("5774"),
    (f"BusinessSegments=NetworkAndEdge;{_OP}", FY24): Decimal("5842"),
    (f"BusinessSegments=IntelFoundry;{_OP}", FY23): Decimal("18910"),
    (f"BusinessSegments=IntelFoundry;{_OP}", FY24): Decimal("17543"),
    (f"BusinessSegments=AllOtherSegments;{_OP}", FY23): Decimal("5608"),
    (f"BusinessSegments=AllOtherSegments;{_OP}", FY24): Decimal("3824"),
    ("ConsolidationItems=IntersegmentElimination;", FY23): Decimal("-17957"),
    ("ConsolidationItems=IntersegmentElimination;", FY24): Decimal("-17215"),
    # O consolidado, sem dimensao. Vem da MESMA fonte que os membros, e e por
    # isso que as datas casam: a DERA normaliza `ddate` para fim de mes,
    # enquanto o exercicio fiscal da Intel fecha em 2024-12-28.
    ("", FY23): Decimal("54228"),
    ("", FY24): Decimal("53101"),
    # ARMADILHAS que a fonte publica e que NAO estao declaradas no mapeamento.
    (f"BusinessSegments={_ROLLUP};{_OP}", FY24): Decimal("48949"),
    (f"BusinessSegments=IntelFoundry;{_OP}ProductOrService=AssemblyAndTest;", FY24): Decimal("385"),
}


class ResolverSegmentoFalso:
    taxonomy = TaxonomyId.US_GAAP_XBRL

    def resolve(
        self, *, entity_id, address, period_end, period_kind, scope, as_of, member=None
    ):
        # A dimensao chega pelo `member`, como no resolver real: quem a aplica
        # e o resolver, e nao o motor.
        partes = dict(address.address)
        chave = (member if member is not None else partes.get("segment", ""), period_end)
        if chave not in SEGMENTOS:
            return ResolutionFailure(
                UnavailableReason.MISSING_FACT_AS_OF,
                f"nada para {partes.get('segment') or '(consolidado)'} em {period_end}",
            )
        return ResolvedFact(
            value=SEGMENTOS[chave] * MM,
            currency="USD",
            period_type=PeriodType.YEAR,
            period_start=date(period_end.year - 1, 12, 31),
            period_end=period_end,
            knowledge_date=date(period_end.year + 1, 1, 26),
            fact_id=f"intel-seg-{chave[0]}-{period_end}",
            locator="num.txt#seg",
        )

    def entity_display(self, entity_id):
        return "INTEL CORP", (("cik", "0000050863"),)


@pytest.fixture
def engine():
    install_builtins()
    return Engine(
        resolvers={TaxonomyId.US_GAAP_XBRL: ResolverSegmentoFalso()},
        mappings=load_dir(),
        registry=default_registry(),
        source="sec.companyfacts",
        pat_version="teste",
    )


def _decompor(engine):
    return decompose(
        engine,
        "revenue_by_segment@v1",
        entity_id=INTEL,
        period_from=FY23,
        period_to=FY24,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )


def test_intel_sec_segment_resolve_e_fecha_exatamente(engine):
    """A receita cai 1.127 MM, e os seis membros declarados somam isso.

    Residual ZERO. A identidade fecha porque a eliminacao intersegmento e um
    MEMBRO declarado, e nao um ajuste escondido.
    """
    resultado = _decompor(engine)
    assert isinstance(resultado, DecompositionResult), resultado

    assert resultado.target_from == Decimal("54228") * MM
    assert resultado.target_to == Decimal("53101") * MM
    assert resultado.target_delta == Decimal("-1127") * MM
    assert resultado.residual == 0
    assert resultado.closes is True
    assert resultado.currency == "USD"

    por_membro = {c.member_id: c.contribution for c in resultado.contributions}
    assert por_membro["ClientComputingGroup"] == Decimal("1032") * MM
    assert por_membro["IntelFoundry"] == Decimal("-1367") * MM
    assert por_membro["AllOtherSegments"] == Decimal("-1784") * MM
    assert por_membro["IntersegmentElimination"] == Decimal("742") * MM


def test_o_roll_up_nao_declarado_nao_participa(engine):
    """A armadilha que motiva declarar os membros um a um.

    `ClientComputingGroupDatacenterAndAIAndNetworkAndEdge` (48.949 MM) e a soma
    exata de CCG + DCAI + NEX, e a fonte o publica lado a lado com eles. Um
    sistema que somasse tudo que a fonte tem contaria esses tres DUAS VEZES - e
    o total pareceria plausivel.

    Ele existe no resolver acima e nao aparece no resultado, porque nao esta
    declarado no TOML. Idem o recorte `ProductOrService=AssemblyAndTest`, que e
    parte da Foundry e nao um segmento irmao.
    """
    resultado = _decompor(engine)
    assert isinstance(resultado, DecompositionResult)
    ids = {c.member_id for c in resultado.contributions}
    assert _ROLLUP not in ids
    assert len(ids) == 6


def test_a_eliminacao_e_membro_e_nao_residual(engine):
    """Sem ela, a soma dos segmentos excederia o consolidado em 17,2 bi.

    Esse valor apareceria como "residual nao explicado", o que seria falso: ele
    e uma eliminacao publicada. Como membro declarado, aparece com nome
    proprio - e o relatorio nao afirma que a Intel tem um negocio de receita
    negativa.
    """
    resultado = _decompor(engine)
    assert isinstance(resultado, DecompositionResult)
    elim = next(
        c for c in resultado.contributions if c.member_id == "IntersegmentElimination"
    )
    assert elim.member_label == "Eliminacao intersegmento"
    assert elim.value_to == Decimal("-17215") * MM

    operacionais = sum(
        c.value_to
        for c in resultado.contributions
        if c.member_id != "IntersegmentElimination"
    )
    assert operacionais == Decimal("70316") * MM
    assert operacionais + elim.value_to == resultado.target_to


def test_cada_contribuicao_carrega_linhagem(engine):
    """Cada membro resolve ate um `fact_id`, como qualquer outro numero."""
    resultado = _decompor(engine)
    assert isinstance(resultado, DecompositionResult)
    for contribuicao in resultado.contributions:
        assert contribuicao.inputs, contribuicao.member_id
        assert all(ref.fact_id for ref in contribuicao.inputs)

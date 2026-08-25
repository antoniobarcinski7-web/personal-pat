"""O eixo SEGMENT: bloqueado onde nao ha fonte estruturada.

A metade brasileira da regra, que e conferivel hoje. A metade americana
depende de decisoes de reconciliacao que os dados da DERA levantaram e que
estao registradas em `docs/phase5_m56_segment.md`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pat.contracts.decomposition import (
    BreakdownAxis,
    DecompositionFailureReason,
    DecompositionUnavailable,
)
from pat.contracts.semantics import ReportingScope
from pat.research.decompose import decompose
from pat.semantics import concepts, decompositions
from pat.semantics.decompositions import DecompositionDefinition, Term
from tests.research.test_decompose import (
    AS_OF,
    ENTIDADE,
    FY23,
    FY24,
    ResolverFalso,
    _engine,
    _petrobras_real,
)


def _com_definicao_de_segmento(entity_id: str, ref: str = "receita_por_segmento_teste@v1"):
    """Registra temporariamente uma decomposicao de eixo SEGMENT."""
    definicao = DecompositionDefinition(
        decomposition_id=ref.split("@")[0],
        version=ref.split("@")[1],
        axis=BreakdownAxis.SEGMENT,
        target_concept=concepts.REVENUE_NET,
        target_label="Receita liquida",
        terms=(Term(concepts.REVENUE_NET, 1, "por segmento"),),
        definition="receita liquida aberta por segmento operacional",
        rationale="teste do bloqueio do eixo",
        tolerance_abs=Decimal(1000),
    )
    decompositions.CATALOG[definicao.ref] = definicao
    return definicao


def test_petrobras_cvm_segment_recusa_com_no_breakdown_source():
    """Petrobras -> CVM -> SEGMENT = NO_BREAKDOWN_SOURCE.

    A DFP da CVM publica so as demonstracoes padronizadas: verificado sobre o
    ZIP real, sao 19 membros e nenhum carrega dimensao de segmento. A
    informacao por segmento (IFRS 8) existe apenas em nota explicativa, em PDF.

    A recusa distingue "o sistema nao tem por onde ler" de "a empresa nao
    reporta" - a Petrobras REPORTA, so que num formato do qual este projeto
    nao deriva fato quantitativo. Colapsar as duas seria a ausencia que se le
    como evidencia de ausencia.
    """
    definicao = _com_definicao_de_segmento(ENTIDADE)
    try:
        resultado = decompose(
            _engine(ResolverFalso(_petrobras_real())),
            definicao.ref,
            entity_id=ENTIDADE,
            period_from=FY23,
            period_to=FY24,
            scope=ReportingScope.CONSOLIDATED,
            as_of=AS_OF,
        )
    finally:
        del decompositions.CATALOG[definicao.ref]

    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.NO_BREAKDOWN_SOURCE
    assert resultado.axis is BreakdownAxis.SEGMENT
    assert "PDF" in resultado.message
    assert resultado.remedy and "estruturada" in resultado.remedy


def test_a_recusa_nao_depende_de_a_empresa_ter_dados():
    """O bloqueio e da FONTE, e nao do estado do warehouse.

    Mesmo com todos os fatos da Petrobras presentes - e eles estao, o eixo
    COMPONENT decompoe o EBIT dela sem residual - o eixo SEGMENT recusa. E o
    que torna a recusa uma afirmacao sobre o regime, e nao sobre a ingestao.
    """
    resolver = ResolverFalso(_petrobras_real())
    engine = _engine(resolver)

    # O mesmo warehouse, o mesmo periodo: COMPONENT resolve.
    componente = decompose(
        engine, "ebit_by_line@v1", entity_id=ENTIDADE,
        period_from=FY23, period_to=FY24,
        scope=ReportingScope.CONSOLIDATED, as_of=AS_OF,
    )
    assert not isinstance(componente, DecompositionUnavailable)

    # E SEGMENT nao.
    definicao = _com_definicao_de_segmento(ENTIDADE)
    try:
        segmento = decompose(
            engine, definicao.ref, entity_id=ENTIDADE,
            period_from=FY23, period_to=FY24,
            scope=ReportingScope.CONSOLIDATED, as_of=AS_OF,
        )
    finally:
        del decompositions.CATALOG[definicao.ref]

    assert isinstance(segmento, DecompositionUnavailable)
    assert segmento.reason is DecompositionFailureReason.NO_BREAKDOWN_SOURCE


def test_decomposicao_de_eixo_dimensional_declara_o_eixo():
    """Toda decomposicao nao-COMPONENT diz de qual eixo ela fala.

    Sem `member_axis`, a decomposicao nao teria como saber quais membros do
    mapeamento sao dela - e um mapeamento pode declarar mais de um eixo
    (segmento e geografia, por exemplo).
    """
    for definicao in decompositions.all_definitions():
        if definicao.axis is BreakdownAxis.COMPONENT:
            assert not definicao.member_axis, (
                f"{definicao.ref}: no eixo COMPONENT os membros sao conceitos "
                "universais, e nao membros de um eixo da fonte"
            )
        else:
            assert definicao.member_axis, (
                f"{definicao.ref} usa o eixo {definicao.axis} e nao diz qual eixo "
                "da fonte os membros habitam"
            )

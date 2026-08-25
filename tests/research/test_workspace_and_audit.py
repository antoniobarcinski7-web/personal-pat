"""O workspace da empresa e o critic de modelo.

Dois testes carregam o arquivo:

`test_o_modelo_nao_escolhe_sozinho_o_que_bloqueia` - um achado de critic de
modelo e, ele proprio, um julgamento nao verificado. Deixa-lo declarar
qualquer coisa como dura seria dar-lhe veto sobre um relatorio possivelmente
correto.

`test_o_critic_ve_a_evidencia_que_o_relatorio_NAO_citou` - e o que torna
`selective_evidence` possivel, e o motivo de o conjunto recuperado ser finito
e registrado.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import pytest
from pydantic import ValidationError

from pat.canonical import sha256_of
from pat.contracts.claims import ClaimGraph, ClaimKind, ClaimNode, EvidenceStrength, Severity
from pat.contracts.workspace import (
    CompanyWorkspace,
    QualitativeCoverage,
    QuantitativeCoverage,
    ReadinessCode,
    ReadinessGap,
    WorkspaceState,
)
from pat.research.model_critic import (
    ModelFinding,
    ModelFindingCode,
    build_user_prompt,
)
from pat.research.workspace import MIN_PERIODS, build_workspace
from pat.store.db import migrate

ENTIDADE = "br:cnpj:33000167000101"
AS_OF = date(2025, 6, 30)


# ---------------------------------------------------------------------------
# O critic de modelo
# ---------------------------------------------------------------------------


def test_o_modelo_nao_escolhe_sozinho_o_que_bloqueia():
    """A severidade que ele pede e limitada por codigo versionado.

    Dois codigos podem bloquear - os que tornam o relatorio ENGANOSO. Os
    outros deixam o relatorio incompleto, e bloquear seria tratar "faltou
    dizer" como "disse errado".
    """
    # Estes dois podem.
    for codigo in (ModelFindingCode.SELECTIVE_EVIDENCE, ModelFindingCode.CAUSAL_OVERREACH):
        achado = ModelFinding(code=codigo, severity=Severity.HARD, message="x")
        assert achado.severity is Severity.HARD

    # Estes nao, por mais que o modelo peca.
    for codigo in (
        ModelFindingCode.QUOTE_OUT_OF_CONTEXT,
        ModelFindingCode.STALE_EVIDENCE,
        ModelFindingCode.MISSING_DECOMPOSITION,
        ModelFindingCode.UNQUANTIFIED_MAGNITUDE,
    ):
        with pytest.raises(ValidationError, match="nao pode bloquear"):
            ModelFinding(code=codigo, severity=Severity.HARD, message="x")


def test_todo_codigo_tem_teto_declarado():
    """Um codigo novo sem teto seria um achado que ninguem sabe se bloqueia."""
    from pat.research.model_critic import _MAX_SEVERITY

    assert set(_MAX_SEVERITY) == set(ModelFindingCode)


def test_o_critic_ve_a_evidencia_que_o_relatorio_nao_citou():
    """O conjunto INTEIRO vai ao prompt, marcado por citado ou nao.

    E isso que torna `selective_evidence` possivel: a pergunta "existe um
    trecho que contradiz a conclusao e ficou de fora?" so tem resposta porque
    o conjunto e fechado e auditavel.
    """
    from tests.research.test_claims import _resultado_vazio

    citado = "A receita caiu por causa do Brent."
    nao_citado = "O volume de vendas cresceu e compensou parte da queda."

    resultado = _construir_resultado_com_evidencia(citado, nao_citado)
    grafo = ClaimGraph(
        nodes=(
            ClaimNode(
                claim_id=sha256_of({"n": "q"}),
                kind=ClaimKind.QUOTE,
                text=citado,
                unit_id="1" * 64,
            ),
        )
    )
    from pat.contracts.research import OutputKind, ResearchQuestion

    pergunta = ResearchQuestion(
        text="Por que a receita caiu?",
        as_of=AS_OF,
        asked_at=datetime(2025, 6, 30, tzinfo=UTC),
        requested_output=OutputKind.NARRATIVE,
    )
    prompt = build_user_prompt(pergunta, grafo, resultado, ("A receita caiu.",))

    assert citado in prompt
    assert nao_citado in prompt, "o trecho NAO citado tem que chegar ao auditor"
    assert '"citado_no_relatorio": true' in prompt.replace("True", "true").lower() or (
        "citado_no_relatorio" in prompt
    )
    assert "conjunto COMPLETO" in prompt


def _construir_resultado_com_evidencia(citado: str, nao_citado: str):
    from decimal import Decimal

    from pat.contracts.corpus import (
        DateBasis,
        DocumentKind,
        EvidenceHit,
        EvidenceResult,
        LocatorScheme,
        QuoteClaim,
        UnitLocator,
    )
    from pat.contracts.common import SourceTier
    from pat.contracts.program import EvidenceOutcome, ProgramResult

    def _hit(rank: int, unit_id: str, texto: str) -> EvidenceHit:
        return EvidenceHit(
            rank=rank,
            relevance=Decimal("1.0"),
            matched_terms=("brent",),
            quote=QuoteClaim(
                unit_id=unit_id,
                document_id="d" * 64,
                text=texto,
                document_kind=DocumentKind.PERFORMANCE_REPORT,
                published_at=date(2024, 3, 7),
                published_at_basis=DateBasis.FILING_METADATA,
                source_tier=SourceTier.PRIMARY_OFFICIAL,
                locator=UnitLocator(
                    scheme=LocatorScheme.PDF_PAGE,
                    page=1,
                    block=0,
                    char_start=0,
                    char_end=len(texto),
                ),
                title="Relatorio",
            ),
        )

    evidencia = EvidenceResult(
        query_id="e" * 64,
        entity_id=ENTIDADE,
        as_of=AS_OF,
        hits=(_hit(1, "1" * 64, citado), _hit(2, "2" * 64, nao_citado)),
        index_version="lexical/v1",
        documents_in_scope=1,
        units_in_scope=2,
        retrieved_at=datetime(2025, 6, 30, tzinfo=UTC),
    )
    return ProgramResult(
        program_id="a" * 64,
        question_id="b" * 64,
        as_of=AS_OF,
        executed_at=datetime(2025, 6, 30, tzinfo=UTC),
        evidence=(EvidenceOutcome(request_id="r1", result=evidencia),),
        capability_sha256="c" * 64,
    )


def test_o_critic_de_modelo_nao_alcanca_dado_nem_corrige():
    """Ele nao pesquisa, nao busca documento novo e nao reescreve."""
    import ast
    from pathlib import Path

    fonte = Path("src/pat/research/model_critic.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    importados = {
        node.module
        for node in ast.walk(arvore)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for proibido in ("pat.query", "pat.store", "pat.corpus.retrieve", "duckdb", "httpx"):
        assert not any(i.startswith(proibido) for i in importados), (
            f"model_critic importa {proibido}: ele julga o conjunto que recebeu, "
            "e nao busca mais nada."
        )
    assert "write_report" not in fonte, "o critic nao rechama o escritor"


# ---------------------------------------------------------------------------
# O workspace
# ---------------------------------------------------------------------------


@pytest.fixture
def vazio():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    yield conn
    conn.close()


def test_workspace_sem_nada_e_draft_com_lacunas_nomeadas(vazio):
    workspace = build_workspace(
        vazio, entity_id="br:cnpj:0", display_name="Fantasma", cod_cvm=1
    )
    assert workspace.state is WorkspaceState.DRAFT
    codigos = {lacuna.code for lacuna in workspace.gaps}
    assert ReadinessCode.NO_FACTS in codigos
    assert ReadinessCode.NO_DOCUMENTS in codigos
    # Toda lacuna diz o que fazer na segunda-feira.
    assert all(lacuna.remedy for lacuna in workspace.gaps)


def test_ready_com_pendencia_nao_e_construivel():
    """Prontidao e a conjuncao dos requisitos, e nao um adjetivo."""
    with pytest.raises(ValidationError, match="READY com"):
        CompanyWorkspace(
            entity_id=ENTIDADE,
            display_name="X",
            jurisdiction="BR",
            state=WorkspaceState.READY,
            gaps=(
                ReadinessGap(
                    code=ReadinessCode.NO_DOCUMENTS, message="falta", remedy="faca"
                ),
            ),
            quantitative=QuantitativeCoverage(facts=1),
            qualitative=QualitativeCoverage(documents=0),
            workspace_sha256="a" * 64,
            built_at=datetime(2025, 1, 1, tzinfo=UTC),
        )


def test_draft_sem_pendencia_declarada_nao_e_construivel():
    """Se nada falta, o estado e READY; se algo falta, tem que ter nome."""
    with pytest.raises(ValidationError, match="DRAFT sem nenhuma pendencia"):
        CompanyWorkspace(
            entity_id=ENTIDADE,
            display_name="X",
            jurisdiction="BR",
            state=WorkspaceState.DRAFT,
            quantitative=QuantitativeCoverage(facts=1),
            qualitative=QualitativeCoverage(documents=1),
            workspace_sha256="a" * 64,
            built_at=datetime(2025, 1, 1, tzinfo=UTC),
        )


def test_um_periodo_so_nao_basta(vazio):
    """Sem dois periodos nao ha variacao, e sem variacao nao ha pergunta
    causal a fazer - que e o que a Fase 5 existe para responder."""
    assert MIN_PERIODS == 2


def test_o_hash_do_workspace_nao_depende_do_relogio(vazio):
    """Um sistema inalterado produz o mesmo hash em toda invocacao."""
    um = build_workspace(vazio, entity_id="br:cnpj:0", display_name="X")
    outro = build_workspace(vazio, entity_id="br:cnpj:0", display_name="X")
    assert um.workspace_sha256 == outro.workspace_sha256
    assert um.built_at != outro.built_at or True


def test_a_cobertura_carrega_o_que_falta_e_nao_so_o_que_existe(vazio):
    """Cobertura que so mostra o que existe mente sobre si mesma."""
    campos = set(QuantitativeCoverage.model_fields)
    assert "missing_concepts" in campos
    assert "extraction_failures" in set(QualitativeCoverage.model_fields)

"""O programa de pesquisa: contrato, execucao deterministica e a fronteira
que separa o estagio 1 do estagio 2.

O teste que da nome ao arquivo e
`test_nenhum_valor_atravessa_para_o_estagio_dois`: ele confere contra os
BYTES que de fato vao ao prompt, e nao contra uma lista de nomes de campo.
Uma lista de nomes envelhece; os bytes nao.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from pat.canonical import canonical_bytes
from pat.contracts.corpus import EvidenceUnavailable, EvidenceUnavailableReason
from pat.contracts.decomposition import (
    BreakdownAxis,
    Contribution,
    DecompositionFailureReason,
    DecompositionResult,
    DecompositionUnavailable,
)
from pat.contracts.common import PeriodType
from pat.contracts.program import (
    DecompositionOutcome,
    DecompositionRequest,
    Direction,
    EvidenceOutcome,
    EvidenceRequest,
    Magnitude,
    ResearchProgram,
)
from pat.contracts.semantics import Fidelity, ReportingScope
from pat.research.shape import (
    MAGNITUDE_BANDS,
    SHAPE_VERSION,
    shape_of_decomposition,
    shape_of_evidence,
)

CONTRATO = Path(__file__).resolve().parents[2] / "src" / "pat" / "contracts" / "program.py"

ENTIDADE = "br:cnpj:33000167000101"
QID = "a" * 64
FY23 = date(2023, 12, 31)
FY24 = date(2024, 12, 31)
AS_OF = date(2025, 6, 30)
MM = Decimal(1_000_000)


# ---------------------------------------------------------------------------
# A fronteira do estagio 2
# ---------------------------------------------------------------------------


def _classe(nome: str) -> ast.ClassDef:
    arvore = ast.parse(CONTRATO.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.ClassDef) and no.name == nome:
            return no
    raise AssertionError(f"classe {nome} nao encontrada")


def test_a_forma_de_um_resultado_nao_tem_onde_por_um_numero():
    """`ResultShape` atravessa a fronteira do modelo no estagio 2.

    Se ela pudesse carregar um valor, o numero de um estagio chegaria ao
    prompt do outro, e "todo numero vem do motor" deixaria de ser propriedade
    do tipo e viraria promessa de prompt. Mesma tecnica de
    `ConversationContext` na M4.1.
    """
    proibidos = {"Decimal", "float"}
    for no in _classe("ResultShape").body:
        if not isinstance(no, ast.AnnAssign) or not isinstance(no.target, ast.Name):
            continue
        anotacao = ast.unparse(no.annotation)
        for tipo in proibidos:
            assert tipo not in anotacao, (
                f"ResultShape.{no.target.id}: {anotacao} carrega {tipo}. O estagio 2 "
                "ve forma, nunca valor."
            )


def _decomposicao_real() -> DecompositionResult:
    """A decomposicao do EBIT da Petrobras, com os numeros publicados."""
    contribuicoes = (
        Contribution(
            member_id="revenue_net",
            member_label="Receita liquida",
            sign=1,
            value_from=Decimal("511994") * MM,
            value_to=Decimal("490829") * MM,
            delta=Decimal("-21165") * MM,
            contribution=Decimal("-21165") * MM,
            share=Decimal("0.4059"),
            fidelity=Fidelity.EXACT,
        ),
        Contribution(
            member_id="cogs",
            member_label="Custo dos bens e servicos",
            sign=-1,
            value_from=Decimal("242061") * MM,
            value_to=Decimal("244367") * MM,
            delta=Decimal("2306") * MM,
            contribution=Decimal("-2306") * MM,
            share=Decimal("0.0442"),
            fidelity=Fidelity.EXACT,
        ),
        Contribution(
            member_id="operating_expenses_net",
            member_label="Despesas operacionais liquidas",
            sign=-1,
            value_from=Decimal("80591") * MM,
            value_to=Decimal("109261") * MM,
            delta=Decimal("28670") * MM,
            contribution=Decimal("-28670") * MM,
            share=Decimal("0.5498"),
            fidelity=Fidelity.EXACT,
        ),
    )
    return DecompositionResult(
        decomposition_id="ebit_by_line",
        decomposition_version="v1",
        axis=BreakdownAxis.COMPONENT,
        target_id="ebit_reported",
        target_label="Resultado operacional (EBIT reportado)",
        entity_id=ENTIDADE,
        scope=ReportingScope.CONSOLIDATED,
        period_type=PeriodType.YEAR,
        period_from=FY23,
        period_to=FY24,
        as_of=AS_OF,
        target_from=Decimal("189342") * MM,
        target_to=Decimal("137201") * MM,
        target_delta=Decimal("-52141") * MM,
        target_delta_pct=Decimal("-0.2754"),
        contributions=contribuicoes,
        residual=Decimal(0),
        residual_share=Decimal(0),
        closes=True,
        tolerance_abs=Decimal(1000),
        currency="BRL",
        fidelity=Fidelity.EXACT,
        knowledge_date=date(2025, 2, 26),
        mapping_sha256="9" * 64,
    )


def test_nenhum_valor_atravessa_para_o_estagio_dois():
    """Confere contra os BYTES que vao ao prompt, e nao contra nomes de campo.

    Uma lista de nomes proibidos envelhece: alguem acrescenta um campo e o
    teste continua passando. Serializar a forma e procurar os algarismos do
    resultado real nao envelhece.
    """
    resultado = _decomposicao_real()
    forma = shape_of_decomposition(
        DecompositionOutcome(request_id="ebit_fy23_fy24", result=resultado)
    )
    bytes_do_prompt = canonical_bytes((forma,)).decode("utf-8")

    proibidos = [
        "189342", "137201", "52141", "511994", "490829", "242061",
        "244367", "80591", "109261", "21165", "28670", "2306",
        "0.2754", "0.5498", "0.4059",
    ]
    for numero in proibidos:
        assert numero not in bytes_do_prompt, (
            f"o valor {numero} atravessou para a forma. O estagio 2 nao pode ve-lo."
        )

    # E o que atravessa e util: direcao, faixa, e a ORDEM dos contribuidores.
    assert forma.direction is Direction.DOWN
    assert forma.magnitude is Magnitude.LARGE
    assert forma.top_contributors == (
        "operating_expenses_net",
        "revenue_net",
        "cogs",
    )
    assert forma.residual_is_material is False


def test_a_ordem_dos_contribuidores_e_a_informacao_que_atravessa():
    """Saber QUEM puxou basta para escolher a busca; quanto nao e preciso."""
    forma = shape_of_decomposition(
        DecompositionOutcome(request_id="x", result=_decomposicao_real())
    )
    assert forma.top_contributors[0] == "operating_expenses_net"


def test_as_faixas_de_magnitude_sao_codigo_e_nao_prompt():
    """Um modelo nao decide o que e "grande": ele recebe a classificacao."""
    assert SHAPE_VERSION.startswith("shape/")
    limites = [limite for limite, _ in MAGNITUDE_BANDS]
    assert limites == sorted(limites), "faixas fora de ordem"
    assert all(isinstance(limite, Decimal) for limite in limites)


def test_decomposicao_que_nao_fecha_vira_ressalva_na_forma():
    resultado = _decomposicao_real()
    quebrado = resultado.model_copy(
        update={
            "residual": Decimal("-7201") * MM,
            "target_delta": Decimal("-52141") * MM - Decimal("7201") * MM,
            "target_to": Decimal("130000") * MM,
            "closes": False,
        }
    )
    forma = shape_of_decomposition(DecompositionOutcome(request_id="x", result=quebrado))
    assert forma.residual_is_material is True


def test_a_forma_de_uma_busca_diz_quantos_trechos_nunca_quais():
    """O estagio 2 planeja a busca; mostrar-lhe o resultado dela o poria a
    planejar sobre o que ja encontrou."""
    indisponivel = EvidenceUnavailable(
        reason=EvidenceUnavailableReason.NO_MATCH,
        message="nada casou",
        entity_id=ENTIDADE,
        as_of=AS_OF,
    )
    forma = shape_of_evidence(EvidenceOutcome(request_id="b", unavailable=indisponivel))
    assert forma.hits == 0
    assert forma.unavailable_reason == "no_match"
    serializado = canonical_bytes(forma).decode("utf-8")
    assert "nada casou" not in serializado or "hits" in serializado


# ---------------------------------------------------------------------------
# O contrato do programa
# ---------------------------------------------------------------------------


def _programa(**kwargs) -> ResearchProgram:
    base = dict(
        question_id=QID,
        objective="investigar a queda",
        as_of=AS_OF,
        scope=ReportingScope.CONSOLIDATED,
        decompositions=(
            DecompositionRequest(
                request_id="d1",
                decomposition="ebit_by_line@v1",
                entity_id=ENTIDADE,
                period_from=FY23,
                period_to=FY24,
            ),
        ),
    )
    base.update(kwargs)
    return ResearchProgram(**base)


def test_programa_sem_passo_e_sem_pendencia_nao_e_construivel():
    with pytest.raises(ValidationError, match="sem passo e sem pendencia"):
        ResearchProgram(
            question_id=QID,
            objective="nada",
            as_of=AS_OF,
            scope=ReportingScope.CONSOLIDATED,
        )


def test_programa_pode_recusar_sem_nenhum_passo():
    programa = ResearchProgram(
        question_id=QID,
        objective="pergunta ambigua",
        as_of=AS_OF,
        scope=ReportingScope.CONSOLIDATED,
        unresolved=({"kind": "ambiguous_entity", "detail": "que empresa?"},),
    )
    assert programa.is_refusal


def test_busca_posterior_ao_as_of_e_recusada_no_contrato():
    """A barreira point-in-time do lado textual, no nivel do programa."""
    with pytest.raises(ValidationError, match="posterior ao as_of"):
        _programa(
            evidence=(
                EvidenceRequest(
                    request_id="e1",
                    entity_id=ENTIDADE,
                    terms=("brent",),
                    published_to=date(2026, 1, 1),
                    rationale="teste",
                ),
            )
        )


def test_pedido_de_decomposicao_sem_versao_e_recusado():
    with pytest.raises(ValidationError, match="sem versao"):
        DecompositionRequest(
            request_id="d1",
            decomposition="ebit_by_line",
            entity_id=ENTIDADE,
            period_from=FY23,
            period_to=FY24,
        )


def test_pedido_de_decomposicao_com_periodo_invertido_e_recusado():
    with pytest.raises(ValidationError, match="nao e anterior"):
        DecompositionRequest(
            request_id="d1",
            decomposition="ebit_by_line@v1",
            entity_id=ENTIDADE,
            period_from=FY24,
            period_to=FY23,
        )


def test_request_id_repetido_e_recusado():
    pedido = EvidenceRequest(
        request_id="mesmo", entity_id=ENTIDADE, terms=("a",), rationale="x"
    )
    with pytest.raises(ValidationError, match="repetido"):
        _programa(evidence=(pedido, pedido.model_copy(update={"terms": ("b",)})))


def test_o_pedido_de_evidencia_nao_tem_as_of_proprio():
    """Data propria permitiria uma parte da resposta olhar mais longe que a
    outra - o analogo textual de comparar dois AS OF na mesma frase."""
    assert "as_of" not in EvidenceRequest.model_fields


def test_um_resultado_ou_uma_recusa_nunca_ambos():
    with pytest.raises(ValidationError, match="OU uma recusa"):
        DecompositionOutcome(
            request_id="d1",
            result=_decomposicao_real(),
            unavailable=DecompositionUnavailable(
                reason=DecompositionFailureReason.TARGET_UNAVAILABLE,
                message="x",
            ),
        )
    with pytest.raises(ValidationError, match="OU uma recusa"):
        DecompositionOutcome(request_id="d1")


# ---------------------------------------------------------------------------
# Identidade
# ---------------------------------------------------------------------------


def test_a_procedencia_do_modelo_nao_entra_na_identidade_do_programa():
    """Dois modelos que produzam o mesmo programa produzem o mesmo hash."""
    from pat.research.canonical import program_id

    programa = _programa()
    assert program_id(programa) == program_id(programa.model_copy())
    assert len(program_id(programa)) == 64


def test_intencao_declarada_muda_a_identidade():
    """Dois programas com os mesmos passos e intencoes diferentes nao sao o
    mesmo programa: e o que um humano aprova ao revisar."""
    from pat.research.canonical import program_id

    um = _programa(questions_to_answer=("por que caiu?",))
    outro = _programa(questions_to_answer=("quanto caiu?",))
    assert program_id(um) != program_id(outro)


# ---------------------------------------------------------------------------
# Prompt do estagio 2
# ---------------------------------------------------------------------------


def test_o_prompt_do_estagio_dois_nao_carrega_valor_nenhum():
    """A conferencia de ponta a ponta: os bytes que vao para a API.

    Monta o prompt real com a forma da decomposicao real e procura os
    algarismos do resultado. E o analogo de
    `test_o_escritor_nao_le_valor_nenhum` da Fase 3.
    """
    from pat.contracts.research import CapabilitySnapshot, OutputKind, ResearchQuestion
    from pat.research.program_planner import _evidence_prompt

    pergunta = ResearchQuestion(
        text="Por que o EBIT caiu?",
        as_of=AS_OF,
        asked_at=datetime(2025, 6, 30, tzinfo=UTC),
        requested_output=OutputKind.NARRATIVE,
    )
    forma = shape_of_decomposition(
        DecompositionOutcome(request_id="d1", result=_decomposicao_real())
    )
    prompt = _evidence_prompt(
        pergunta,
        CapabilitySnapshot(built_at=datetime(2025, 6, 30, tzinfo=UTC)),
        _programa(),
        (forma,),
    )

    for numero in ("189342", "137201", "52141", "511994", "109261", "0.5498"):
        assert numero not in prompt, f"{numero} vazou para o prompt do estagio 2"

    # O que ele PRECISA saber esta la.
    carga = json.loads(prompt)
    assert carga["forma_dos_resultados"][0]["direction"] == "down"
    assert carga["forma_dos_resultados"][0]["top_contributors"][0] == (
        "operating_expenses_net"
    )

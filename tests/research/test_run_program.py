"""O executor de programa, ponta a ponta e sem modelo nenhum.

Monta um warehouse temporario com fato E documento - as duas metades do
workspace - e roda o programa inteiro. E o criterio da M5.3: decomposicao
quantitativa e evidencia textual na mesma execucao auditavel.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import pytest

from pat.contracts.program import (
    DecompositionRequest,
    EvidenceRequest,
    ResearchProgram,
)
from pat.contracts.research import OutputKind, ResearchQuestion
from pat.contracts.semantics import ReportingScope
from pat.corpus.extract import extract
from pat.corpus.index import INDEX_VERSION, build_index
from pat.research.canonical import program_id, question_id
from pat.research.program import run_program
from pat.store import corpus as corpus_store
from pat.store.db import migrate
from tests.corpus.conftest import make_document, make_pdf
from tests.research.test_decompose import (
    BINDINGS,
    ENTIDADE,
    FY23,
    FY24,
    ResolverFalso,
    _engine,
    _petrobras_real,
)

AS_OF = date(2025, 6, 30)


@pytest.fixture
def pergunta() -> ResearchQuestion:
    return ResearchQuestion(
        text="Por que o resultado operacional da Petrobras caiu em 2024?",
        as_of=AS_OF,
        asked_at=datetime(2025, 6, 30, tzinfo=UTC),
        requested_output=OutputKind.NARRATIVE,
    )


@pytest.fixture
def conn():
    connection = duckdb.connect(":memory:")
    migrate(connection)

    document_id = "c" * 64
    pdf = make_pdf(
        [
            [
                "Relatorio de Desempenho 4T24",
                "",
                "As despesas operacionais cresceram por provisoes em campos devolvidos.",
            ]
        ]
    )
    corpus_store.write_documents(
        connection,
        [make_document(document_id, published_at=date(2025, 2, 26), byte_size=len(pdf))],
    )
    outcome = extract(document_id, pdf)
    assert outcome.failure is None
    corpus_store.write_units(connection, outcome.units)
    build_index(connection)
    yield connection
    connection.close()


def _programa(pergunta: ResearchQuestion, **kwargs) -> ResearchProgram:
    base = dict(
        question_id=question_id(pergunta),
        objective="investigar a queda do resultado operacional",
        as_of=AS_OF,
        scope=ReportingScope.CONSOLIDATED,
        questions_to_answer=("Que componentes explicam a queda?",),
        decompositions=(
            DecompositionRequest(
                request_id="ebit_fy23_fy24",
                decomposition="ebit_by_line@v1",
                entity_id=ENTIDADE,
                period_from=FY23,
                period_to=FY24,
            ),
        ),
        evidence=(
            EvidenceRequest(
                request_id="sobre_despesas",
                entity_id=ENTIDADE,
                terms=("despesas", "provisoes"),
                limit=3,
                rationale="despesa foi o maior contribuidor",
            ),
        ),
    )
    base.update(kwargs)
    return ResearchProgram(**base)


def _rodar(conn, pergunta, program):
    return run_program(
        conn,
        program,
        engine=_engine(ResolverFalso(_petrobras_real())),
        question=pergunta,
        program_id=program_id(program),
        capability_sha256="d" * 64,
    )


# -- O criterio da M5.3 ------------------------------------------------------


def test_decomposicao_e_evidencia_na_mesma_execucao(conn, pergunta):
    """As duas metades do workspace, num programa so e sem modelo.

    E o que a Fase 5 existe para produzir: o numero que mede a queda, a
    abertura que diz de onde ela veio, e o trecho verbatim em que a
    administracao fala do maior contribuidor - tudo com procedencia.
    """
    resultado = _rodar(conn, pergunta, _programa(pergunta))

    assert len(resultado.decompositions) == 1
    decomposicao = resultado.decompositions[0].result
    assert decomposicao is not None
    assert decomposicao.residual == 0
    assert decomposicao.ranked()[0].member_id == "operating_expenses_net"

    assert len(resultado.evidence) == 1
    evidencia = resultado.evidence[0].result
    assert evidencia is not None
    assert evidencia.hits
    assert "provisoes" in evidencia.hits[0].quote.text

    assert resultado.has_any_output


def test_a_execucao_nao_chama_modelo_nenhum(conn, pergunta, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resultado = _rodar(conn, pergunta, _programa(pergunta))
    assert resultado.has_any_output


def test_a_forma_de_cada_resultado_acompanha_a_execucao(conn, pergunta):
    """`shapes` sai junto porque e o que alimenta o estagio 2 - e o que
    torna a decisao do planejador auditavel depois."""
    resultado = _rodar(conn, pergunta, _programa(pergunta))
    por_id = {forma.request_id: forma for forma in resultado.shapes}

    assert por_id["ebit_fy23_fy24"].direction == "down"
    assert por_id["ebit_fy23_fy24"].top_contributors[0] == "operating_expenses_net"
    assert por_id["sobre_despesas"].hits == len(resultado.evidence[0].result.hits)


# -- Point-in-time -----------------------------------------------------------


def test_o_as_of_do_programa_desce_para_a_busca(conn, pergunta):
    """O pedido nao tem `as_of`; ele vem do programa, num lugar so.

    Com `as_of` anterior ao documento, a busca recusa - e a recusa e nomeada,
    nao uma lista vazia.
    """
    anterior = ResearchQuestion(
        text=pergunta.text,
        as_of=date(2024, 1, 1),
        asked_at=pergunta.asked_at,
        requested_output=OutputKind.NARRATIVE,
    )
    programa = _programa(
        anterior,
        question_id=question_id(anterior),
        as_of=date(2024, 1, 1),
        decompositions=(),
    )
    resultado = _rodar(conn, anterior, programa)

    assert resultado.evidence[0].result is None
    assert resultado.evidence[0].unavailable is not None
    assert resultado.evidence[0].unavailable.as_of == date(2024, 1, 1)


# -- Falha parcial -----------------------------------------------------------


def test_uma_busca_vazia_nao_aborta_a_decomposicao(conn, pergunta):
    """Numa investigacao, saber que o corpus nao tem nada sobre o assunto e
    um resultado util - e abortar jogaria fora o que ja foi apurado."""
    programa = _programa(
        pergunta,
        evidence=(
            EvidenceRequest(
                request_id="nao_existe",
                entity_id=ENTIDADE,
                terms=("criptomoeda",),
                rationale="teste",
            ),
        ),
    )
    resultado = _rodar(conn, pergunta, programa)

    assert resultado.evidence[0].unavailable is not None
    assert resultado.decompositions[0].result is not None
    assert resultado.has_any_output


def test_uma_decomposicao_indisponivel_vira_recusa_nomeada(conn, pergunta):
    programa = _programa(
        pergunta,
        decompositions=(
            DecompositionRequest(
                request_id="inexistente",
                decomposition="nao_existe@v1",
                entity_id=ENTIDADE,
                period_from=FY23,
                period_to=FY24,
            ),
        ),
    )
    resultado = _rodar(conn, pergunta, programa)
    recusa = resultado.decompositions[0].unavailable
    assert recusa is not None
    assert recusa.reason == "unknown_decomposition"


# -- Procedencia da execucao -------------------------------------------------


def test_a_versao_do_indice_so_aparece_quando_houve_busca(conn, pergunta):
    """Gravar a versao de um indice nao consultado sugeriria que ele
    participou do resultado."""
    com_busca = _rodar(conn, pergunta, _programa(pergunta))
    assert com_busca.index_version == INDEX_VERSION

    sem_busca = _rodar(conn, pergunta, _programa(pergunta, evidence=()))
    assert sem_busca.index_version is None


def test_o_resultado_carrega_a_identidade_do_programa(conn, pergunta):
    programa = _programa(pergunta)
    resultado = _rodar(conn, pergunta, programa)
    assert resultado.program_id == program_id(programa)
    assert resultado.question_id == question_id(pergunta)

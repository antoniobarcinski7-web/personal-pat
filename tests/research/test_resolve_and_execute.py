"""Resolucao contra o warehouse, e propagacao de falha na execucao."""

from __future__ import annotations

from datetime import date

import pytest

from pat.contracts.research import (
    DerivationFailureReason,
    ResearchConstraints,
    ResolutionCode,
)
from pat.contracts.semantics import ReportingScope, UnavailableReason
from pat.research import review_plan, run_plan
from tests.research.conftest import EXECUTED_AT, make_plan, make_question
from tests.semantics import golden_gpa as gpa


def _codes(review) -> set[ResolutionCode]:
    return {issue.code for issue in review.issues}


# -- resolucao ---------------------------------------------------------------


def test_plano_do_gpa_resolve(gpa_conn, plan, question):
    review = review_plan(gpa_conn, plan=plan, question=question)
    assert review.executable, (review.violations, review.issues)


def test_entidade_desconhecida(gpa_conn, question):
    passo = make_plan(question).steps[0].model_copy(
        update={"entity_id": "br:cnpj:00000000000000"}
    )
    plano = make_plan(question, steps=(passo,), outputs=("margin_fy2023",))
    review = review_plan(gpa_conn, plan=plano, question=question)
    assert ResolutionCode.UNKNOWN_ENTITY in _codes(review)


def test_periodo_nao_coberto(gpa_conn, question):
    passo = make_plan(question).steps[0].model_copy(update={"period_end": date(2019, 12, 31)})
    plano = make_plan(question, steps=(passo,), outputs=("margin_fy2023",))
    review = review_plan(gpa_conn, plan=plano, question=question)
    assert ResolutionCode.PERIOD_NOT_COVERED in _codes(review)


def test_escopo_nao_coberto(gpa_conn, question):
    """O fixture so tem consolidado. Individual precisa faltar, nao herdar."""
    pergunta = make_question(pinned_scope=ReportingScope.PARENT_ONLY)
    plano = make_plan(pergunta, scope=ReportingScope.PARENT_ONLY)
    review = review_plan(gpa_conn, plan=plano, question=pergunta)
    assert ResolutionCode.SCOPE_NOT_COVERED in _codes(review)


def test_mapeamento_nao_conferido_e_recusado_por_default(gpa_conn, question, monkeypatch):
    """Default `allow_unconfirmed_mapping=False`: recusa, nao avisa.

    Simulado tirando o mapeamento proprio do GPA do conjunto carregado - a
    companhia passa a cair na familia default, que e a situacao de quase toda
    empresa ainda nao mapeada.
    """
    from pat.semantics.loader import MappingSet
    import pat.research as research

    original = research._pieces

    def sem_gpa(conn, *, as_of, source):
        mappings, registry, snapshot, sha = original(conn, as_of=as_of, source=source)
        so_familia = MappingSet(
            tuple(m for m in mappings.all() if m.entity_id is None)
        )
        return so_familia, registry, snapshot, sha

    monkeypatch.setattr(research, "_pieces", sem_gpa)

    review = review_plan(gpa_conn, plan=make_plan(question), question=question)
    assert ResolutionCode.UNCONFIRMED_MAPPING in _codes(review)

    permissivo = make_question(
        constraints=ResearchConstraints(allow_unconfirmed_mapping=True)
    )
    liberado = review_plan(
        gpa_conn, plan=make_plan(permissivo), question=permissivo
    )
    assert ResolutionCode.UNCONFIRMED_MAPPING not in _codes(liberado)


# -- execucao ----------------------------------------------------------------


def test_metric_unavailable_atravessa_inteiro(tmp_path, question):
    """O objeto da Fase 2 chega sem reescrita: motivo, conceito e enderecos."""
    from pat.store.bronze import BronzeStore
    from pat.store.db import connect, migrate
    from tests.semantics.conftest import load_zips
    from tests.semantics.test_golden_gpa import _rebuild_sem_dfc

    conn = connect(tmp_path / "sem_dfc.duckdb")
    migrate(conn)
    load_zips(conn, BronzeStore(tmp_path / "bronze"), {2023: _rebuild_sem_dfc()})
    try:
        pergunta = make_question(as_of=date(2024, 6, 30))
        plano = make_plan(
            pergunta,
            as_of=date(2024, 6, 30),
            steps=(make_plan(pergunta).steps[0],),
            outputs=("margin_fy2023",),
        )
        outcome = run_plan(conn, plan=plano, question=pergunta, executed_at=EXECUTED_AT)

        assert outcome.executable, (outcome.violations, outcome.issues)
        assert not outcome.execution.outputs_available
        assert outcome.answer is None, "saida que falhou nao produz resposta"

        (falha,) = outcome.execution.failures
        assert falha.reason is DerivationFailureReason.INPUT_FAILED
        assert falha.unavailable is not None
        assert falha.unavailable.reason is UnavailableReason.DEPENDENCY_UNAVAILABLE
        assert falha.unavailable.concept_id == "d_and_a_pnl"
        assert "6.01.01.04" in " ".join(falha.unavailable.tried)
    finally:
        conn.close()


def test_derivacao_falha_junto_com_o_insumo(tmp_path, question):
    """Passo que depende de um passo falho falha nomeado, e nao vira zero."""
    from pat.store.bronze import BronzeStore
    from pat.store.db import connect, migrate
    from tests.semantics.conftest import load_zips
    from tests.semantics.test_golden_gpa import _rebuild_sem_dfc

    conn = connect(tmp_path / "sem_dfc2.duckdb")
    migrate(conn)
    load_zips(conn, BronzeStore(tmp_path / "bronze"), {2023: _rebuild_sem_dfc()})
    try:
        from pat.contracts.research import DerivationOp, DerivationStep

        pergunta = make_question(as_of=date(2024, 6, 30))
        primeiro = make_plan(pergunta).steps[0]
        plano = make_plan(
            pergunta,
            as_of=date(2024, 6, 30),
            steps=(
                primeiro,
                primeiro.model_copy(update={"step_id": "outra"}),
                DerivationStep(
                    step_id="change",
                    op=DerivationOp.DELTA,
                    inputs=("margin_fy2023", "outra"),
                ),
            ),
            outputs=("change",),
        )
        outcome = run_plan(conn, plan=plano, question=pergunta, executed_at=EXECUTED_AT)
        motivos = {f.step_id: f.reason for f in outcome.execution.failures}
        assert motivos["change"] is DerivationFailureReason.INPUT_FAILED
    finally:
        conn.close()


def test_ordem_de_execucao_nao_depende_de_dicionario(gpa_conn, plan, question):
    uma = run_plan(gpa_conn, plan=plan, question=question, executed_at=EXECUTED_AT)
    ordem = [r.step_id for r in uma.execution.results]
    assert ordem == ["margin_fy2023", "margin_fy2024", "change"]


# -- AsOf: as duas adicoes ---------------------------------------------------


def test_entities_e_coverage(gpa_conn):
    from pat.query.asof import AsOf

    asof = AsOf(gpa_conn)
    (ref,) = asof.entities()
    assert ref.entity_id == gpa.ENTITY_ID
    assert ref.display_name == "CIA BRASILEIRA DE DISTRIBUICAO"

    cover = asof.coverage(gpa.ENTITY_ID)
    assert cover is not None
    assert cover.period_ends == (gpa.FY2023, gpa.FY2024)
    assert cover.consolidated_scopes == (True,)


def test_coverage_respeita_o_as_of(gpa_conn):
    """Cobertura em 2024-06-30 nao inclui o que so ficou publico em 2025."""
    from pat.query.asof import AsOf

    asof = AsOf(gpa_conn)
    cover = asof.coverage(gpa.ENTITY_ID, as_of=date(2024, 6, 30))
    assert cover is not None
    assert cover.period_ends == (gpa.FY2023,)
    assert gpa.FY2024 not in cover.period_ends


def test_coverage_de_entidade_inexistente_e_none(gpa_conn):
    from pat.query.asof import AsOf

    assert AsOf(gpa_conn).coverage("br:cnpj:00000000000000") is None

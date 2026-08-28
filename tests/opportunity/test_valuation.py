"""O8 - valuation deterministica, conferida a mao.

`test_dcf_bate_com_a_conta_feita_a_mao` transcreve a aritmetica esperada linha
a linha, calculada fora deste codigo. E o mesmo criterio dos golden tests da
Fase 2: se o teste so comparasse o resultado com o que a funcao devolve, ele
provaria que a funcao e estavel, e nao que ela esta certa.

O outro criterio do milestone - o modelo nunca inventa resultado numerico - e
testado pela ausencia de default: falta premissa, sai `ValuationUnavailable`
nomeando qual.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pat.contracts.opportunity import Actor
from pat.contracts.opportunity.valuation import (
    Assumption,
    AssumptionBasis,
    AssumptionSet,
    DataPoint,
    ValuationInterpretation,
    ValuationModel,
    ValuationResult,
    ValuationUnavailable,
    ValuationUnavailableReason,
)
from pat.opportunity.valuation import (
    REQUIRED_ASSUMPTIONS,
    implied_growth,
    run_dcf,
    sensitivity,
)

AT = datetime(2025, 7, 1, tzinfo=UTC)


def _a(slug: str, valor: str, *, basis=AssumptionBasis.JUDGMENT, **kw) -> Assumption:
    return Assumption(
        slug=slug,
        label=slug,
        value=Decimal(valor),
        unit="ratio",
        basis=basis,
        rationale=f"escolha declarada para {slug}",
        author=Actor.USER,
        at=AT,
        **kw,
    )


def _modelo(**overrides) -> ValuationModel:
    premissas = AssumptionSet(
        assumptions=(
            _a("revenue-growth", "0.05"),
            _a("ebit-margin", "0.20"),
            _a("tax-rate", "0.34"),
            _a("reinvestment-rate", "0.30"),
            _a("wacc", "0.12"),
            _a("terminal-growth", "0.03"),
        )
    )
    base = {
        "slug": "dcf-base",
        "currency": "BRL",
        "horizon_years": 3,
        "data": (
            DataPoint(
                slug="revenue-base",
                label="receita liquida FY2024",
                value=Decimal("1000"),
                unit="BRL",
                result_id="receita_liquida@v1|br:cnpj:1|2024-12-31|consolidated|2026-06-30",
                period_end=date(2024, 12, 31),
            ),
        ),
        "assumptions": premissas,
        "created_by": Actor.USER,
        "at": AT,
    }
    return ValuationModel(**(base | overrides))


# -- o criterio de aceitacao -------------------------------------------------


def test_dcf_bate_com_a_conta_feita_a_mao():
    """A aritmetica, transcrita fora deste codigo.

    receita_0 = 1000, g = 5%, margem = 20%, imposto = 34%, reinv = 30%,
    wacc = 12%, g_terminal = 3%, horizonte = 3 anos.

        ano 1: receita 1050,00   ebit 210,000   nopat 138,600  fcf 97,020
        ano 2: receita 1102,50   ebit 220,500   nopat 145,530  fcf 101,871
        ano 3: receita 1157,625  ebit 231,525   nopat 152,8065 fcf 106,96455
    """
    resultado = run_dcf(_modelo())
    assert isinstance(resultado, ValuationResult)

    esperado_fcf = [Decimal("97.020"), Decimal("101.871"), Decimal("106.96455")]
    for linha, esperado in zip(resultado.lines, esperado_fcf, strict=True):
        assert linha.free_cash_flow == esperado, f"ano {linha.year}"

    # Valor presente de cada ano, descontado a 12%.
    vp = [
        Decimal("97.020") / Decimal("1.12"),
        Decimal("101.871") / (Decimal("1.12") ** 2),
        Decimal("106.96455") / (Decimal("1.12") ** 3),
    ]
    for linha, esperado in zip(resultado.lines, vp, strict=True):
        assert linha.present_value == esperado

    # Terminal: fcf_3 * 1,03 / (0,12 - 0,03), trazido a valor presente.
    terminal = Decimal("106.96455") * Decimal("1.03") / Decimal("0.09")
    assert resultado.terminal_value == terminal
    esperado_ev = sum(vp) + terminal / (Decimal("1.12") ** 3)
    assert resultado.enterprise_value == esperado_ev

    # Sem divida liquida declarada, equity = enterprise.
    assert resultado.equity_value == esperado_ev
    assert resultado.per_share is None, "sem numero de acoes, nao sai valor por acao"


def test_o_terminal_dominante_e_visivel():
    """Acima de ~75%, o modelo diz mais sobre a premissa terminal do que sobre
    a companhia - e quem le precisa saber disso."""
    resultado = run_dcf(_modelo())
    assert resultado.terminal_share > Decimal("0.75")
    assert resultado.terminal_dominates


def test_divida_liquida_e_acoes_saem_no_valor_por_acao():
    modelo = _modelo(
        data=(
            _modelo().data[0],
            DataPoint(
                slug="net-debt",
                label="divida liquida",
                value=Decimal("500"),
                unit="BRL",
                result_id="divida@v1|br:cnpj:1|2024-12-31|consolidated|2026-06-30",
            ),
            DataPoint(
                slug="shares-outstanding",
                label="acoes",
                value=Decimal("100"),
                unit="shares",
                result_id="acoes@v1|br:cnpj:1|2024-12-31|consolidated|2026-06-30",
            ),
        )
    )
    resultado = run_dcf(modelo)
    assert resultado.equity_value == resultado.enterprise_value - Decimal("500")
    assert resultado.per_share == resultado.equity_value / Decimal("100")


# -- o modelo nunca inventa --------------------------------------------------


@pytest.mark.parametrize("faltando", REQUIRED_ASSUMPTIONS)
def test_premissa_faltando_para_a_conta_e_diz_qual(faltando):
    """Nao ha default. Um default seria uma escolha de investimento embutida
    numa biblioteca, e sairia no relatorio como se alguem a tivesse feito."""
    premissas = AssumptionSet(
        assumptions=tuple(
            a for a in _modelo().assumptions.assumptions if a.slug != faltando
        )
    )
    resultado = run_dcf(_modelo(assumptions=premissas))
    assert isinstance(resultado, ValuationUnavailable)
    assert resultado.reason is ValuationUnavailableReason.MISSING_ASSUMPTION
    assert faltando in resultado.missing
    assert resultado.remedy


def test_dado_do_motor_faltando_para_a_conta():
    resultado = run_dcf(_modelo(data=()))
    assert isinstance(resultado, ValuationUnavailable)
    assert resultado.reason is ValuationUnavailableReason.MISSING_DATA
    assert "revenue-base" in resultado.missing


def test_ler_premissa_ausente_levanta_em_vez_de_devolver_zero():
    with pytest.raises(KeyError, match="Nao existe default"):
        AssumptionSet().value("wacc")


def test_crescimento_terminal_acima_do_wacc_nao_e_ajustado():
    """Ajustar em silencio produziria um numero finito onde a formula nao tem
    solucao, e ele seria lido como um preco."""
    premissas = AssumptionSet(
        assumptions=tuple(
            _a("terminal-growth", "0.15") if a.slug == "terminal-growth" else a
            for a in _modelo().assumptions.assumptions
        )
    )
    resultado = run_dcf(_modelo(assumptions=premissas))
    assert isinstance(resultado, ValuationUnavailable)
    assert resultado.reason is ValuationUnavailableReason.NON_CONVERGENT
    assert "terminal-growth" in resultado.missing
    assert "nao ajusta sozinho" in resultado.remedy


def test_a_falta_e_reportada_antes_da_nao_convergencia():
    """Reportar nao-convergencia primeiro mandaria o leitor arrumar a premissa
    errada."""
    premissas = AssumptionSet(
        assumptions=tuple(
            a
            for a in _modelo().assumptions.assumptions
            if a.slug not in ("wacc", "terminal-growth")
        )
    )
    resultado = run_dcf(_modelo(assumptions=premissas))
    assert resultado.reason is ValuationUnavailableReason.MISSING_ASSUMPTION


# -- DATA / ASSUMPTION / CALCULATION / INTERPRETATION ------------------------


def test_premissa_derivada_precisa_dizer_de_que():
    """Senao ela e JUDGMENT com nome melhor."""
    with pytest.raises(ValueError, match="nao aponta para nada"):
        _a("revenue-growth", "0.05", basis=AssumptionBasis.HISTORICAL)
    ok = _a(
        "revenue-growth",
        "0.05",
        basis=AssumptionBasis.HISTORICAL,
        derived_from=("receita_liquida@v1|br:cnpj:1|2024-12-31|consolidated|2026-06-30",),
    )
    assert ok.derived_from


def test_o_motor_nao_assina_premissa():
    """O motor produz numero, nunca escolha."""
    with pytest.raises(ValueError, match="atribuida ao motor"):
        Assumption(
            slug="wacc",
            label="wacc",
            value=Decimal("0.12"),
            unit="ratio",
            basis=AssumptionBasis.JUDGMENT,
            rationale="porque sim",
            author=Actor.ENGINE,
            at=AT,
        )


def test_premissa_sem_justificativa_nao_existe():
    with pytest.raises(ValueError):
        Assumption(
            slug="wacc",
            label="wacc",
            value=Decimal("0.12"),
            unit="ratio",
            basis=AssumptionBasis.JUDGMENT,
            rationale="",
            author=Actor.USER,
            at=AT,
        )


def test_interpretacao_nao_tem_como_voltar_para_a_conta():
    """Tipo proprio, sem `value`. Mesma razao de `QuoteClaim` nao ter."""
    campos = set(ValuationInterpretation.model_fields)
    assert not {"value", "equity_value", "wacc"} & campos


def test_dado_e_premissa_sao_tipos_diferentes():
    """E o que impede um valor escolhido de entrar na conta pela porta dos
    dados."""
    assert "result_id" in DataPoint.model_fields
    assert "result_id" not in Assumption.model_fields
    assert "basis" in Assumption.model_fields
    assert "basis" not in DataPoint.model_fields


def test_o_resultado_carrega_as_premissas_que_o_produziram():
    """Um valor por acao apresentado sozinho e uma precisao que o modelo nao
    tem."""
    resultado = run_dcf(_modelo())
    assert set(resultado.assumptions_used) == set(REQUIRED_ASSUMPTIONS)
    assert resultado.data_used


def test_premissas_de_juizo_ficam_identificaveis():
    """Sao elas que decidem o numero, e a tese precisa dize-las em voz alta."""
    modelo = _modelo()
    assert len(modelo.assumptions.judgment_only) == 6


def test_moeda_nunca_e_convertida_implicitamente():
    with pytest.raises(ValueError, match="Moeda nunca"):
        _modelo(
            currency="USD",
            data=(
                DataPoint(
                    slug="revenue-base",
                    label="receita",
                    value=Decimal("1000"),
                    unit="BRL",
                    result_id="r|e|2024-12-31|consolidated|2026-06-30",
                ),
            ),
        )


# -- sensibilidade -----------------------------------------------------------


def test_a_grade_reexecuta_o_modelo_por_celula():
    """Interpolar erraria mais nas bordas, que e justamente onde a tese
    quebra."""
    grade = sensitivity(
        _modelo(),
        row="wacc",
        row_values=(Decimal("0.10"), Decimal("0.12"), Decimal("0.14")),
        column="terminal-growth",
        column_values=(Decimal("0.02"), Decimal("0.03")),
    )
    assert len(grade.cells) == 6
    assert grade.row_assumption == "wacc"
    assert grade.column_assumption == "terminal-growth"

    # Wacc maior derruba o valor; g terminal maior levanta.
    por_chave = {(c.row, c.column): c.equity_value for c in grade.cells}
    assert por_chave[(Decimal("0.10"), Decimal("0.03"))] > por_chave[
        (Decimal("0.14"), Decimal("0.03"))
    ]
    assert por_chave[(Decimal("0.12"), Decimal("0.03"))] > por_chave[
        (Decimal("0.12"), Decimal("0.02"))
    ]


def test_celula_que_nao_converge_some_em_vez_de_virar_zero():
    """Zero num canto se le como "vale zero neste cenario", quando o certo e
    "a formula nao tem solucao aqui"."""
    grade = sensitivity(
        _modelo(),
        row="wacc",
        row_values=(Decimal("0.12"),),
        column="terminal-growth",
        column_values=(Decimal("0.03"), Decimal("0.20")),
    )
    assert len(grade.cells) == 1
    assert grade.cells[0].column == Decimal("0.03")
    assert all(c.equity_value != 0 for c in grade.cells)


def test_a_grade_preserva_a_justificativa_da_premissa():
    """Uma celula e a MESMA premissa em outro valor; reescrever a
    justificativa faria a grade parecer seis analises independentes."""
    from pat.opportunity.valuation import _with_values

    original = _modelo().assumptions.get("wacc")
    variante = _with_values(_modelo(), {"wacc": Decimal("0.15")})
    trocada = variante.assumptions.get("wacc")
    assert trocada.value == Decimal("0.15")
    assert trocada.rationale == original.rationale
    assert trocada.basis is original.basis
    assert trocada.author is original.author


def test_eixo_que_nao_e_premissa_do_modelo():
    resultado = sensitivity(
        _modelo(),
        row="inventado",
        row_values=(Decimal("1"),),
        column="wacc",
        column_values=(Decimal("0.12"),),
    )
    assert isinstance(resultado, ValuationUnavailable)
    assert "inventado" in resultado.missing


# -- valuation reversa -------------------------------------------------------


def test_valuation_reversa_devolve_o_crescimento_implicito():
    """"O que o preco de hoje ja esta assumindo?" - e a pergunta que nao
    depende de o analista acertar a premissa."""
    modelo = _modelo()
    direto = run_dcf(modelo)

    implicito = implied_growth(modelo, target_equity_value=direto.equity_value)
    assert isinstance(implicito, Decimal)
    # O crescimento que reproduz o proprio valor e o do modelo, a menos da
    # tolerancia da biseccao.
    assert abs(implicito - Decimal("0.05")) < Decimal("0.005")


def test_valuation_reversa_com_alvo_maior_pede_mais_crescimento():
    modelo = _modelo()
    base = run_dcf(modelo).equity_value
    maior = implied_growth(modelo, target_equity_value=base * Decimal("1.5"))
    assert isinstance(maior, Decimal)
    assert maior > Decimal("0.05")


def test_alvo_fora_do_alcance_e_um_achado_e_nao_um_erro():
    """"O preco nao e explicavel so por crescimento de receita" e informacao,
    e das boas."""
    resultado = implied_growth(
        _modelo(), target_equity_value=Decimal("999999999999")
    )
    assert isinstance(resultado, ValuationUnavailable)
    assert resultado.reason is ValuationUnavailableReason.NON_CONVERGENT
    assert "achado" in resultado.remedy


def test_tudo_e_decimal_nunca_float():
    """Um DCF encadeia dezenas de operacoes, e binario flutuante acumula erro
    exatamente onde e mais dificil notar - no valor terminal."""
    resultado = run_dcf(_modelo())
    for linha in resultado.lines:
        for campo in (
            linha.revenue,
            linha.ebit,
            linha.nopat,
            linha.free_cash_flow,
            linha.present_value,
        ):
            assert isinstance(campo, Decimal)
    assert isinstance(resultado.enterprise_value, Decimal)
    assert isinstance(resultado.terminal_value, Decimal)


def test_duas_execucoes_dao_o_mesmo_bit():
    a, b = run_dcf(_modelo()), run_dcf(_modelo())
    assert a.enterprise_value == b.enterprise_value
    assert a.terminal_value == b.terminal_value
    assert [x.free_cash_flow for x in a.lines] == [x.free_cash_flow for x in b.lines]


# -- persistencia ------------------------------------------------------------


def test_o_diario_guarda_o_modelo_e_nao_o_resultado(root, gpa_profile):
    """O resultado e derivado - `run_dcf` sobre o mesmo modelo da o mesmo bit.

    Gravar os dois criaria uma segunda fonte de verdade, e no dia em que
    divergissem ninguem saberia qual esta certa. E a mesma razao pela qual o
    catalogo do PAT e derivado do bronze.
    """
    from pat.contracts.opportunity import ValuationDeclared
    from pat.opportunity import create_workspace, open_workspace
    from tests.opportunity.conftest import AS_OF, CREATED_AT

    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    ws.apply(ValuationDeclared(model=_modelo()), actor=Actor.USER)

    reaberto = open_workspace(root, ws.workspace_id)
    modelo = reaberto.state.valuation("dcf-base")
    assert modelo is not None
    assert modelo.assumptions.value("wacc") == Decimal("0.12")
    # O resultado nao esta no diario: recalcula-se, e da o mesmo.
    assert run_dcf(modelo).enterprise_value == run_dcf(_modelo()).enterprise_value


def test_trocar_premissa_deixa_o_valor_antigo_no_diario(root, gpa_profile):
    """Trocar premissa e onde uma tese se auto-ajusta ate dar o numero que o
    autor queria. O historico nao proibe - torna visivel."""
    from pat.contracts.opportunity import AssumptionChanged, ValuationDeclared
    from pat.opportunity import create_workspace, open_workspace
    from tests.opportunity.conftest import AS_OF, CREATED_AT

    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    ws.apply(ValuationDeclared(model=_modelo()), actor=Actor.USER)
    ws.apply(
        AssumptionChanged(model="dcf-base", assumption=_a("wacc", "0.09")),
        actor=Actor.USER,
    )

    reaberto = open_workspace(root, ws.workspace_id)
    assert reaberto.state.valuation("dcf-base").assumptions.value("wacc") == Decimal("0.09")

    # O valor antigo continua legivel no diario.
    eventos = reaberto.journal.read()
    declarado = next(e for e in eventos if e.kind == "valuation_declared")
    assert declarado.body.model.assumptions.value("wacc") == Decimal("0.12")

    # E o numero muda: wacc menor, valor maior.
    depois = run_dcf(reaberto.state.valuation("dcf-base"))
    assert depois.enterprise_value > run_dcf(_modelo()).enterprise_value


def test_interpretacao_sem_modelo_declarado_e_recusada(root, gpa_profile):
    from pat.contracts.opportunity import ValuationInterpreted
    from pat.opportunity import FoldError, create_workspace
    from tests.opportunity.conftest import AS_OF, CREATED_AT

    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    with pytest.raises(FoldError, match="nao foi declarada"):
        ws.apply(
            ValuationInterpreted(model="fantasma", text="parece barato"),
            actor=Actor.AGENT,
        )

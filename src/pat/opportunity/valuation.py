"""O8 - a aritmetica da valuation. Deterministica, e a unica desta camada.

Este e o unico modulo do Opportunity que calcula, e ele existe isolado por
isso: o resto da camada nao produz numero, e a excecao fica visivel num
arquivo so, testada contra valores conferidos a mao.

`Decimal` em toda parte, `float` em lugar nenhum. Um DCF encadeia dezenas de
multiplicacoes e divisoes, e binario flutuante acumula erro exatamente onde e
mais dificil notar - no valor terminal, que costuma ser dois tercos do
resultado.

O arredondamento e do relatorio, nunca da conta
-----------------------------------------------
Nenhuma etapa intermediaria arredonda. `QUANTIZE` existe para apresentar, e e
aplicado no fim. Arredondar no meio produziria resultados que mudam conforme a
ordem das operacoes, e duas execucoes do mesmo modelo tem que dar o mesmo bit.

O que este modulo recusa a fazer
--------------------------------
- Nao tem premissa default. `AssumptionSet.value` levanta se faltar.
- Nao converte moeda.
- Nao "ajusta" crescimento terminal para caber abaixo do WACC: g >= WACC vira
  `NON_CONVERGENT` com remedio. Ajustar em silencio produziria um numero
  finito onde a formula nao tem solucao, e ele seria lido como um preco.
- Nao devolve zero por falta de insumo, nunca.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pat.contracts.opportunity.valuation import (
    AssumptionSet,
    CashFlowLine,
    SensitivityCell,
    SensitivityGrid,
    ValuationModel,
    ValuationResult,
    ValuationUnavailable,
    ValuationUnavailableReason,
)

__all__ = [
    "QUANTIZE",
    "REQUIRED_ASSUMPTIONS",
    "implied_growth",
    "run_dcf",
    "sensitivity",
]

QUANTIZE = Decimal("0.01")
"""Casas do relatorio. Aplicado na apresentacao, nunca no meio da conta."""

REQUIRED_ASSUMPTIONS: tuple[str, ...] = (
    "revenue-growth",
    "ebit-margin",
    "tax-rate",
    "reinvestment-rate",
    "wacc",
    "terminal-growth",
)
"""As seis que um DCF nao tem como inventar.

A lista e explicita para que a mensagem de falta seja util: "falta
premissa" mandaria o leitor descobrir qual, e a descoberta e sempre depois de
um traceback.
"""

REQUIRED_DATA: tuple[str, ...] = ("revenue-base",)
"""O que precisa vir do motor. A receita base e dado, e nao premissa: ela
existe no gold, com procedencia, e deixar alguem digita-la abriria a porta
para o numero do emissor entrar como insumo."""


def run_dcf(model: ValuationModel) -> ValuationResult | ValuationUnavailable:
    """Modelo -> valor. Sem estimativa, sem default, sem conversao.

    A ordem das checagens importa: primeiro o que falta, depois o que nao
    converge. Reportar nao-convergencia antes de reportar premissa faltando
    mandaria o leitor arrumar a premissa errada.
    """
    faltando = [s for s in REQUIRED_ASSUMPTIONS if model.assumptions.get(s) is None]
    if faltando:
        return ValuationUnavailable(
            reason=ValuationUnavailableReason.MISSING_ASSUMPTION,
            message=f"faltam {len(faltando)} premissa(s): {faltando}",
            remedy=(
                "Declare cada uma com base e justificativa. Nao ha default: um "
                "default seria uma escolha de investimento embutida na biblioteca."
            ),
            missing=tuple(faltando),
        )
    sem_dado = [s for s in REQUIRED_DATA if model.data_point(s) is None]
    if sem_dado:
        return ValuationUnavailable(
            reason=ValuationUnavailableReason.MISSING_DATA,
            message=f"faltam {len(sem_dado)} dado(s) do motor: {sem_dado}",
            remedy="Rode a metrica correspondente e ligue o `result_id` ao modelo.",
            missing=tuple(sem_dado),
        )

    a = model.assumptions
    wacc = a.value("wacc")
    g = a.value("terminal-growth")
    if g >= wacc:
        return ValuationUnavailable(
            reason=ValuationUnavailableReason.NON_CONVERGENT,
            message=(
                f"crescimento na perpetuidade ({g}) >= custo de capital ({wacc}): a "
                "formula de Gordon nao tem solucao finita positiva"
            ),
            remedy=(
                "Baixe `terminal-growth` abaixo de `wacc`, ou justifique um horizonte "
                "explicito em vez de perpetuidade. O modelo nao ajusta sozinho: um "
                "numero finito onde a formula nao tem solucao seria lido como preco."
            ),
            missing=("terminal-growth",),
        )

    base = model.data_point("revenue-base")
    crescimento = a.value("revenue-growth")
    margem = a.value("ebit-margin")
    imposto = a.value("tax-rate")
    reinvestimento = a.value("reinvestment-rate")

    linhas: list[CashFlowLine] = []
    receita = base.value
    valor_presente_total = Decimal(0)
    for ano in range(1, model.horizon_years + 1):
        receita = receita * (Decimal(1) + crescimento)
        ebit = receita * margem
        nopat = ebit * (Decimal(1) - imposto)
        reinveste = nopat * reinvestimento
        fcf = nopat - reinveste
        fator = (Decimal(1) + wacc) ** ano
        presente = fcf / fator
        valor_presente_total += presente
        linhas.append(
            CashFlowLine(
                year=ano,
                revenue=receita,
                ebit=ebit,
                nopat=nopat,
                reinvestment=reinveste,
                free_cash_flow=fcf,
                # Guardado como o inverso do fator para leitura direta; a conta
                # usa o fator, e nao esta arredondada.
                discount_factor=Decimal(1) / fator,
                present_value=presente,
            )
        )

    fcf_final = linhas[-1].free_cash_flow
    terminal = fcf_final * (Decimal(1) + g) / (wacc - g)
    terminal_presente = terminal / ((Decimal(1) + wacc) ** model.horizon_years)

    enterprise = valor_presente_total + terminal_presente
    divida = _optional(model, "net-debt")
    equity = enterprise - divida

    acoes = _optional(model, "shares-outstanding")
    por_acao = equity / acoes if acoes > 0 else None

    return ValuationResult(
        model_slug=model.slug,
        currency=model.currency,
        enterprise_value=enterprise,
        equity_value=equity,
        per_share=por_acao,
        terminal_value=terminal,
        terminal_share=terminal_presente / enterprise if enterprise != 0 else Decimal(0),
        lines=tuple(linhas),
        assumptions_used=tuple(x.slug for x in a.assumptions),
        data_used=tuple(d.result_id for d in model.data),
        computed_at=datetime.now(UTC),
    )


def _optional(model: ValuationModel, slug: str) -> Decimal:
    """Dado opcional: divida liquida e numero de acoes.

    Ausencia vira zero AQUI, e so aqui, e a escolha e defensavel porque as
    duas tem significado nulo natural - companhia sem divida liquida e um
    estado real, e sem numero de acoes o resultado simplesmente nao vira valor
    por acao (fica `None`, nunca um valor por acao errado). Em nenhum outro
    ponto do sistema ausencia vira zero.
    """
    ponto = model.data_point(slug)
    if ponto is not None:
        return ponto.value
    premissa = model.assumptions.get(slug)
    return premissa.value if premissa is not None else Decimal(0)


def sensitivity(
    model: ValuationModel,
    *,
    row: str,
    row_values: tuple[Decimal, ...],
    column: str,
    column_values: tuple[Decimal, ...],
) -> SensitivityGrid | ValuationUnavailable:
    """A grade: reexecuta o modelo inteiro por celula.

    Reexecutar em vez de interpolar e o ponto. Uma grade interpolada e uma
    aproximacao apresentada com a mesma aparencia do resultado exato, e as
    bordas - que sao justamente onde a tese quebra - sao onde a aproximacao
    erra mais.

    Celulas que nao convergem SOMEM da grade, e nao viram zero. Uma celula com
    zero num canto se le como "vale zero neste cenario", quando o certo e "a
    formula nao tem solucao aqui".
    """
    if model.assumptions.get(row) is None or model.assumptions.get(column) is None:
        return ValuationUnavailable(
            reason=ValuationUnavailableReason.MISSING_ASSUMPTION,
            message=f"eixo {row!r} ou {column!r} nao e uma premissa do modelo",
            remedy="Use slugs de premissas declaradas no modelo.",
            missing=tuple(
                s for s in (row, column) if model.assumptions.get(s) is None
            ),
        )

    celulas: list[SensitivityCell] = []
    for v_linha in row_values:
        for v_coluna in column_values:
            variante = _with_values(model, {row: v_linha, column: v_coluna})
            resultado = run_dcf(variante)
            if isinstance(resultado, ValuationUnavailable):
                continue
            celulas.append(
                SensitivityCell(
                    row=v_linha,
                    column=v_coluna,
                    equity_value=resultado.equity_value,
                    per_share=resultado.per_share,
                )
            )
    return SensitivityGrid(
        row_assumption=row, column_assumption=column, cells=tuple(celulas)
    )


def _with_values(model: ValuationModel, novos: dict[str, Decimal]) -> ValuationModel:
    """Copia do modelo com premissas trocadas.

    A copia preserva `basis`, `rationale` e `author` da premissa original: uma
    celula de sensibilidade e a MESMA premissa em outro valor, e reescrever a
    justificativa faria a grade parecer seis analises independentes.
    """
    trocadas = tuple(
        a.model_copy(update={"value": novos[a.slug]}) if a.slug in novos else a
        for a in model.assumptions.assumptions
    )
    return model.model_copy(update={"assumptions": AssumptionSet(assumptions=trocadas)})


def implied_growth(
    model: ValuationModel,
    *,
    target_equity_value: Decimal,
    tolerance: Decimal = Decimal("0.0001"),
    max_iterations: int = 80,
) -> Decimal | ValuationUnavailable:
    """Valuation reversa: que crescimento de receita justifica este preco?

    Responde a pergunta que uma tese honesta faz antes das outras - "o que o
    preco de hoje ja esta assumindo?" - e ela e mais util que o DCF direto,
    porque nao depende de o analista acertar a premissa: depende de ele achar
    absurda, ou nao, a premissa que o mercado ja embutiu.

    Busca por biseccao, e nao por Newton: a derivada nao e analitica aqui, e
    uma diferenca finita perto de `wacc` fica instavel. Biseccao e mais lenta
    e sempre converge dentro do intervalo.
    """
    baixo, alto = Decimal("-0.5"), Decimal("1.0")

    def equity_para(g: Decimal) -> Decimal | None:
        resultado = run_dcf(_with_values(model, {"revenue-growth": g}))
        return None if isinstance(resultado, ValuationUnavailable) else resultado.equity_value

    v_baixo, v_alto = equity_para(baixo), equity_para(alto)
    if v_baixo is None or v_alto is None:
        return ValuationUnavailable(
            reason=ValuationUnavailableReason.MISSING_ASSUMPTION,
            message="o modelo nao roda nos extremos do intervalo de busca",
            remedy="Confira as premissas: a valuation reversa reusa o mesmo modelo.",
        )
    if not (v_baixo <= target_equity_value <= v_alto):
        return ValuationUnavailable(
            reason=ValuationUnavailableReason.NON_CONVERGENT,
            message=(
                f"o valor alvo {target_equity_value} esta fora do que o modelo produz "
                f"com crescimento entre {baixo} e {alto} ({v_baixo} a {v_alto})"
            ),
            remedy=(
                "O preco nao e explicavel so por crescimento de receita neste modelo. "
                "Isso e um achado, e nao um erro: outra premissa tem que estar em jogo."
            ),
        )

    for _ in range(max_iterations):
        meio = (baixo + alto) / 2
        valor = equity_para(meio)
        if valor is None:
            return ValuationUnavailable(
                reason=ValuationUnavailableReason.NON_CONVERGENT,
                message=f"o modelo deixou de convergir em crescimento={meio}",
                remedy="Estreite o intervalo de busca ou revise `terminal-growth`.",
            )
        if abs(valor - target_equity_value) <= abs(target_equity_value) * tolerance:
            return meio
        if valor < target_equity_value:
            baixo = meio
        else:
            alto = meio
    return (baixo + alto) / 2

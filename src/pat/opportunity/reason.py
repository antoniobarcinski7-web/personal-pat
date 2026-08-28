"""Quem decide o que perguntar. Contrato primeiro, implementacao depois.

`Reasoner` e um Protocol pela mesma razao que `FactResolver` e `LLMClient` sao:
a camada fala com o mundo por uma forma, e trocar quem raciocina e escrever um
adapter. Aqui isso vale duplamente, porque o raciocinador default NAO e um
modelo de linguagem.

Por que existe um raciocinador deterministico
---------------------------------------------
`ShapeReasoner` planeja, interpreta e julga sem chamar modelo nenhum. Ele nao
e um simulacro para testes: e o piso do sistema. Tres consequencias, e as tres
foram o motivo do desenho:

1. **O ciclo inteiro roda offline.** `opportunity research` funciona sem chave
   de API, e a suite testa o laco de verdade em vez de testar um dublê.
2. **A fronteira fica visivel.** Tudo que o `ShapeReasoner` consegue fazer e,
   por definicao, coisa que nao precisava de um modelo. O que sobra - ler um
   release, notar que a explicacao da administracao nao bate com o numero,
   propor a hipotese que ninguem escreveu - e o trabalho de verdade do
   `LlmReasoner`, e agora da para ver qual e qual.
3. **A degradacao e honesta.** Sem chave, o sistema nao para nem inventa: ele
   produz menos, e o `reasoner_id` no relatorio diz quem produziu.

O que o `ShapeReasoner` NAO faz, e por que ele nao tenta
--------------------------------------------------------
Ele nao escreve prosa sobre causa. Uma frase como "a margem caiu por pressao
competitiva" nao sai de uma regra - sairia de um template, e um template
produziria a mesma frase para qualquer empresa cuja margem caisse. As
interpretacoes dele sao afirmacoes de FORMA: direcao, faixa de magnitude, e
quais periodos. Sao sempre verdadeiras sobre os numeros que citam, e nunca
dizem por que.

E deliberado que isso pareca pouco. Um raciocinador deterministico que
"explicasse" seria um gerador de causa plausivel, que e exatamente o modo de
falha que a camada inteira existe para evitar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pat.contracts.opportunity import CompanyProfile, ResearchTask, TaskStatus
from pat.contracts.opportunity.loop import (
    FindingProposal,
    HypothesisProposal,
    StepKind,
    StepOutcome,
    StepRequest,
    TaskProposal,
    TaskVerdict,
)
from pat.contracts.program import Direction, Magnitude
from pat.research.shape import FLAT_THRESHOLD, MAGNITUDE_BANDS

__all__ = [
    "ResearchContext",
    "Reasoner",
    "ScriptedReasoner",
    "ShapeReasoner",
    "series_shape",
]


@dataclass(frozen=True)
class ResearchContext:
    """O que o raciocinador pode saber antes de perguntar qualquer coisa.

    Deliberadamente NAO inclui valor nenhum. Um contexto que carregasse
    numeros deixaria o planejamento depender do que ja se sabe, e o passo
    seguinte seria o raciocinador "planejar" um numero que ele ja viu.
    """

    company: CompanyProfile
    as_of: date
    periods: tuple[date, ...]
    metrics_available: tuple[str, ...]
    decompositions_available: tuple[str, ...]
    documents: int = 0
    missing_concepts: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    mandate: str | None = None

    @property
    def has_corpus(self) -> bool:
        return self.documents > 0


@runtime_checkable
class Reasoner(Protocol):
    """Quem decide o que perguntar e o que os resultados querem dizer.

    Nenhum metodo devolve numero. `plan` devolve perguntas, `interpret`
    devolve prosa que cita IDs, `judge` devolve um estado com razao.
    """

    @property
    def reasoner_id(self) -> str:
        """Quem raciocinou. Entra no relatorio: uma conclusao de regra e uma
        conclusao de modelo tem forcas diferentes."""
        ...

    def decompose(
        self, *, objective: str, context: ResearchContext
    ) -> tuple[tuple[TaskProposal, ...], tuple[HypothesisProposal, ...]]:
        """Objetivo -> tarefas e hipoteses. Nao toca no warehouse."""
        ...

    def plan(
        self, *, task: ResearchTask, context: ResearchContext
    ) -> tuple[StepRequest, ...]:
        """Tarefa -> passos, dentro da gramatica fechada."""
        ...

    def interpret(
        self,
        *,
        task: ResearchTask,
        outcomes: tuple[StepOutcome, ...],
        context: ResearchContext,
    ) -> tuple[FindingProposal, ...]:
        """Resultados -> interpretacao, citando por ID."""
        ...

    def judge(
        self,
        *,
        task: ResearchTask,
        outcomes: tuple[StepOutcome, ...],
        findings: tuple[FindingProposal, ...],
        context: ResearchContext,
    ) -> TaskVerdict:
        """A tarefa cumpriu o criterio de conclusao dela?"""
        ...


# ---------------------------------------------------------------------------
# Forma de uma serie
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeriesShape:
    """O que uma serie fez, sem dizer por que.

    Reusa os degraus de `research/shape.py` de proposito: se "grande" quer
    dizer coisas diferentes em dois lugares do sistema, o relatorio fica
    incoerente consigo mesmo. Os degraus tem uma fonte so.
    """

    direction: Direction | None
    magnitude: Magnitude | None
    relative: Decimal | None
    first_period: date | None
    last_period: date | None
    points: int
    reversals: int = 0
    """Quantas vezes a serie mudou de sentido no caminho.

    Existe porque comparar so as pontas descreve `-565, +660, -436` como
    "subiu". A aritmetica esta certa e a frase esta errada: o ano do meio e a
    coisa mais importante dessa serie, e uma forma que o esconde e o analogo
    textual do numero aproximado que se apresenta como exato."""

    crosses_zero: bool = False
    """A serie trocou de sinal em algum ponto.

    Quando isso acontece, variacao percentual sobre a base deixa de ser uma
    grandeza que alguem possa ler: 22% de melhora sobre um EBIT negativo nao e
    22% de nada. A magnitude vira `None` - ausencia declarada, nunca um numero
    que engana."""

    @property
    def is_monotonic(self) -> bool:
        return self.reversals == 0


def series_shape(pares: list[tuple[date, Decimal]]) -> SeriesShape:
    """Pares (periodo, valor) ordenados -> forma.

    Compara a ponta com a ponta. Nao ajusta tendencia, nao regride e nao
    interpola: uma linha de regressao sobre tres pontos daria uma inclinacao
    com cara de precisao que os tres pontos nao sustentam.
    """
    if len(pares) < 2:
        return SeriesShape(None, None, None, None, None, len(pares))
    ordenados = sorted(pares, key=lambda p: p[0])
    (primeiro_p, primeiro_v), (ultimo_p, ultimo_v) = ordenados[0], ordenados[-1]
    reversoes = _reversals([v for _, v in ordenados])
    troca_de_sinal = _crosses_zero([v for _, v in ordenados])

    if primeiro_v == 0:
        # Base zero nao tem variacao relativa. Devolver `None` e o certo:
        # inventar "infinito" ou "100%" daria uma magnitude que nao existe.
        return SeriesShape(
            None, None, None, primeiro_p, ultimo_p, len(ordenados),
            reversals=reversoes, crosses_zero=troca_de_sinal,
        )

    relativo = (ultimo_v - primeiro_v) / abs(primeiro_v)
    return SeriesShape(
        direction=_direction(relativo),
        # Sinal trocado no caminho anula a magnitude, e nao o sentido. "Menos
        # negativo" continua sendo uma direcao que se pode afirmar; "22% maior"
        # sobre uma base negativa nao e uma grandeza que alguem possa ler.
        magnitude=None if troca_de_sinal else _magnitude(relativo),
        relative=relativo,
        first_period=primeiro_p,
        last_period=ultimo_p,
        points=len(ordenados),
        reversals=reversoes,
        crosses_zero=troca_de_sinal,
    )


def _reversals(valores: list[Decimal]) -> int:
    """Quantas vezes o sentido de variacao mudou entre pontos consecutivos.

    Diferencas nulas sao ignoradas: um ano de lado no meio de uma subida nao e
    uma reversao, e conta-lo como uma faria toda serie longa parecer
    acidentada.
    """
    sentidos = [
        1 if depois > antes else -1
        for antes, depois in zip(valores, valores[1:], strict=False)
        if depois != antes
    ]
    return sum(1 for a, b in zip(sentidos, sentidos[1:], strict=False) if a != b)


def _crosses_zero(valores: list[Decimal]) -> bool:
    positivos = any(v > 0 for v in valores)
    negativos = any(v < 0 for v in valores)
    return positivos and negativos


def _direction(relativo: Decimal) -> Direction:
    if abs(relativo) < FLAT_THRESHOLD:
        return Direction.FLAT
    return Direction.UP if relativo > 0 else Direction.DOWN


def _magnitude(relativo: Decimal) -> Magnitude:
    absoluto = abs(relativo)
    for limite, faixa in MAGNITUDE_BANDS:
        if absoluto < limite:
            return faixa
    return Magnitude.EXTREME


# ---------------------------------------------------------------------------
# O raciocinador deterministico
# ---------------------------------------------------------------------------

# Palavras que ligam um objetivo em prosa a uma metrica registrada. A tabela e
# DECLARADA, e nao inferida por semelhanca de string: casar "margem" com
# `margem_ebitda@v1` por distancia de edicao seria a mesma classe de erro que
# casar conta por rotulo. Um termo que nao esta aqui simplesmente nao casa, e
# a tarefa vira NEEDS_HUMAN dizendo isso.
TERMOS_DE_METRICA: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("receita", ("receita_liquida",)),
    ("revenue", ("receita_liquida",)),
    ("crescimento", ("receita_liquida",)),
    ("growth", ("receita_liquida",)),
    ("ebitda", ("ebitda", "margem_ebitda")),
    ("margem", ("margem_ebitda",)),
    ("margin", ("margem_ebitda",)),
    ("ebit", ("ebit",)),
    ("operacional", ("ebit",)),
    ("lucro", ("lucro_liquido",)),
    ("resultado", ("lucro_liquido",)),
    ("earnings", ("lucro_liquido",)),
    ("depreciacao", ("d_and_a",)),
)

MAX_PERIODOS = 6
"""Teto de periodos por passo. Uma serie de vinte pontos nao responde melhor
do que uma de seis, e enche o relatorio de linha que ninguem le."""


class ShapeReasoner:
    """Planeja por tabela declarada, interpreta por forma, julga por criterio.

    Nao chama modelo, nao inventa metrica e nao explica causa.
    """

    reasoner_id = "shape/v1"

    def decompose(
        self, *, objective: str, context: ResearchContext
    ) -> tuple[tuple[TaskProposal, ...], tuple[HypothesisProposal, ...]]:
        """Objetivo -> uma tarefa por metrica que a tabela reconhece.

        Nao propoe hipotese. Uma hipotese e uma afirmacao sobre o mundo com um
        falsificador junto, e nenhuma regra sabe qual afirmacao vale a pena
        testar nesta empresa - propor uma por template daria a mesma hipotese
        para todas.
        """
        alvos = self._metricas_do_texto(objective, context)
        propostas = []
        for metrica in alvos:
            slug = metrica.split("@")[0].replace("_", "-")
            propostas.append(
                TaskProposal(
                    slug=slug,
                    objective=f"levantar {metrica} nos periodos disponiveis",
                    completion_criteria=(
                        f"{metrica} calculado pelo motor em pelo menos dois periodos, "
                        "ou uma recusa nomeada dizendo por que nao"
                    ),
                )
            )
        if not propostas:
            # Nenhuma metrica reconhecida NAO vira uma tarefa generica: uma
            # tarefa "pesquisar a empresa" seria concluida por sensacao, que e
            # exatamente o que `completion_criteria` existe para impedir.
            propostas.append(
                TaskProposal(
                    slug="escopo",
                    objective=f"decidir o que investigar em: {objective}",
                    completion_criteria=(
                        "um humano escolhe as metricas ou os termos de busca que "
                        "respondem a este objetivo"
                    ),
                )
            )
        return tuple(propostas), ()

    def plan(
        self, *, task: ResearchTask, context: ResearchContext
    ) -> tuple[StepRequest, ...]:
        periodos = context.periods[-MAX_PERIODOS:]
        passos: list[StepRequest] = []
        for i, metrica in enumerate(self._metricas_do_texto(task.objective, context)):
            passos.append(
                StepRequest(
                    step_id=f"m{i}-{metrica.split('@')[0].replace('_', '-')}"[:63],
                    kind=StepKind.METRIC,
                    ref=metrica,
                    period_ends=periodos,
                    rationale=(
                        f"{metrica} e a metrica registrada que corresponde ao "
                        f"objetivo {task.objective!r}"
                    ),
                )
            )
        if context.has_corpus:
            termos = self._termos_de_busca(task.objective)
            if termos:
                passos.append(
                    StepRequest(
                        step_id="evidencia",
                        kind=StepKind.EVIDENCE,
                        terms=termos,
                        rationale=(
                            "o que a companhia disse sobre o assunto da tarefa, para "
                            "confrontar com o que os numeros mostram"
                        ),
                    )
                )
        return tuple(passos)

    def interpret(
        self,
        *,
        task: ResearchTask,
        outcomes: tuple[StepOutcome, ...],
        context: ResearchContext,
    ) -> tuple[FindingProposal, ...]:
        """Uma afirmacao de forma por passo que produziu algo.

        As frases dizem direcao e faixa, nunca causa - e citam todos os IDs
        que as sustentam.
        """
        achados: list[FindingProposal] = []
        for outcome in outcomes:
            if not outcome.produced_anything:
                if outcome.unavailable:
                    achados.append(
                        FindingProposal(
                            text=(
                                f"{outcome.step_id}: sem resultado. "
                                + "; ".join(outcome.unavailable[:2])
                            )
                        )
                    )
                continue
            if outcome.kind is StepKind.EVIDENCE:
                achados.append(
                    FindingProposal(
                        text=(
                            f"{len(outcome.unit_ids)} trecho(s) do corpus mencionam os "
                            f"termos do passo {outcome.step_id}"
                        ),
                        cites=outcome.unit_ids,
                    )
                )
                continue
            achados.append(
                FindingProposal(
                    text=self._forma_em_prosa(outcome),
                    cites=outcome.result_ids,
                )
            )
        return tuple(achados)

    def judge(
        self,
        *,
        task: ResearchTask,
        outcomes: tuple[StepOutcome, ...],
        findings: tuple[FindingProposal, ...],
        context: ResearchContext,
    ) -> TaskVerdict:
        """COMPLETE so quando algum passo produziu numero citavel.

        Um veredito que aceitasse "planejei e nada saiu" como conclusao faria
        a tese se declarar pronta sobre uma agenda toda executada e vazia.
        """
        if not outcomes:
            return TaskVerdict(
                status=TaskStatus.NEEDS_HUMAN,
                rationale=(
                    "nenhum passo foi planejado: o objetivo nao casa com metrica "
                    "registrada nenhuma, e a tabela de termos e declarada, nao inferida"
                ),
                blocker_reason="objetivo sem passo executavel",
                blocker_remedy=(
                    "reescreva o objetivo citando uma metrica de `pat metrics`, ou "
                    "acrescente o termo a tabela de `reason.py`"
                ),
                needs_human=True,
            )
        if not any(o.produced_anything for o in outcomes):
            motivos = [m for o in outcomes for m in o.unavailable]
            return TaskVerdict(
                status=TaskStatus.BLOCKED,
                rationale="todos os passos foram executados e nenhum produziu resultado",
                blocker_reason="; ".join(motivos[:3]) or "sem resultado e sem motivo registrado",
                blocker_remedy=(
                    "confira a cobertura com `pat company` e o mapeamento com "
                    "`pat mapping-check`"
                ),
            )
        return TaskVerdict(
            status=TaskStatus.COMPLETE,
            rationale=(
                f"{sum(len(o.result_ids) for o in outcomes)} resultado(s) do motor e "
                f"{sum(len(o.unit_ids) for o in outcomes)} trecho(s) citaveis"
            ),
        )

    # -- internos ------------------------------------------------------------

    def _metricas_do_texto(
        self, texto: str, context: ResearchContext
    ) -> tuple[str, ...]:
        """Texto -> metricas registradas, pela tabela declarada.

        O resultado e sempre interseccao com o que o motor de fato tem: uma
        metrica que a tabela conhece e o registro nao serve so para produzir
        uma recusa mais adiante.
        """
        baixo = texto.lower()
        disponiveis = {m.split("@")[0]: m for m in context.metrics_available}
        escolhidas: list[str] = []
        for termo, nomes in TERMOS_DE_METRICA:
            if termo not in baixo:
                continue
            for nome in nomes:
                ref = disponiveis.get(nome)
                if ref and ref not in escolhidas:
                    escolhidas.append(ref)
        return tuple(escolhidas)

    @staticmethod
    def _termos_de_busca(objetivo: str) -> tuple[str, ...]:
        """Palavras do objetivo que valem uma busca.

        Filtra as curtas e as funcionais. Nao expande sinonimo e nao traduz:
        expandir aqui faria a busca achar coisa que ninguem pediu, e a
        citacao viria de um documento sobre outro assunto.
        """
        descartar = {
            "para", "sobre", "como", "quais", "qual", "esta", "essa", "esse",
            "com", "sem", "dos", "das", "por", "que", "the", "and", "for",
            "levantar", "periodos", "disponiveis", "investigar", "entender",
        }
        palavras = [
            p.strip(".,;:()").lower()
            for p in objetivo.split()
            if len(p.strip(".,;:()")) > 3
        ]
        return tuple(dict.fromkeys(p for p in palavras if p not in descartar))[:4]

    @staticmethod
    def _forma_em_prosa(outcome: StepOutcome) -> str:
        """A forma, dita. Nunca a causa."""
        n = len(outcome.result_ids)
        if outcome.shape_hint:
            return f"{outcome.ref}: {outcome.shape_hint} ({n} periodo(s) calculados)"
        return f"{outcome.ref}: {n} periodo(s) calculados pelo motor"


@dataclass
class ScriptedReasoner:
    """Raciocinador de roteiro: devolve o que lhe deram, na ordem.

    Existe para o teste do LACO - que precisa fixar o que o raciocinador diz
    para poder afirmar o que o laco faz com isso. Nao e um `ShapeReasoner`
    mais simples: e um instrumento, e por isso mora aqui ao lado e nao finge
    ser outra coisa.
    """

    reasoner_id: str = "scripted"
    tasks: tuple[TaskProposal, ...] = ()
    hypotheses: tuple[HypothesisProposal, ...] = ()
    steps: dict[str, tuple[StepRequest, ...]] = field(default_factory=dict)
    findings: dict[str, tuple[FindingProposal, ...]] = field(default_factory=dict)
    verdicts: dict[str, TaskVerdict] = field(default_factory=dict)

    def decompose(self, *, objective, context):
        return self.tasks, self.hypotheses

    def plan(self, *, task, context):
        return self.steps.get(task.slug, ())

    def interpret(self, *, task, outcomes, context):
        return self.findings.get(task.slug, ())

    def judge(self, *, task, outcomes, findings, context):
        veredito = self.verdicts.get(task.slug)
        if veredito is not None:
            return veredito
        return TaskVerdict(
            status=TaskStatus.COMPLETE if findings else TaskStatus.NEEDS_HUMAN,
            rationale="roteiro sem veredito explicito",
            blocker_reason=None if findings else "o roteiro nao deu achado",
            blocker_remedy=None if findings else "escreva o veredito no roteiro",
            needs_human=not findings,
        )

"""Resultado -> forma. A unica coisa que atravessa do estagio 1 para o 2.

O problema que este modulo resolve
----------------------------------
Nao da para saber que evidencia pedir antes de saber o que os numeros
fizeram. "Por que a receita caiu?" - a pergunta a fazer ao corpus depende de a
queda ter sido de 3% ou de 30%, e de qual componente puxou.

A saida obvia seria mostrar os resultados ao segundo estagio. Ela e proibida:
o planejador nunca ve um valor, em nenhum dos dois estagios, e essa e a mesma
fronteira que a Fase 3 estabeleceu.

A saida deste modulo e mostrar a FORMA. Direcao (`down`), faixa de magnitude
(`large`), quais membros contribuiram mais (por identificador, na ordem), e se
a decomposicao fechou. Com isso o estagio 2 planeja bem - "procure o que a
administracao disse sobre despesa operacional" - sem que um `Decimal` chegue
perto de um prompt.

As faixas sao codigo, nao prompt
--------------------------------
Os degraus de `Magnitude` estao aqui, versionados em `SHAPE_VERSION`, e nao
numa instrucao de sistema. Um modelo nao decide o que e "grande": ele recebe
a classificacao ja feita. Mudar um degrau muda a versao, do mesmo jeito que
mudar a aritmetica de uma metrica muda a versao dela - porque muda o que o
estagio 2 vai procurar, e portanto pode mudar a resposta.
"""

from __future__ import annotations

from decimal import Decimal

from pat.contracts.corpus import EvidenceResult, EvidenceUnavailable
from pat.contracts.decomposition import DecompositionResult, DecompositionUnavailable
from pat.contracts.program import (
    DecompositionOutcome,
    Direction,
    EvidenceOutcome,
    Magnitude,
    ResultShape,
)
from pat.contracts.research import ComputationFailure, ComputationResult, ResultKind
from pat.contracts.semantics import Dimension
from pat.research.render import describe

__all__ = [
    "MAGNITUDE_BANDS",
    "SHAPE_VERSION",
    "FLAT_THRESHOLD",
    "shape_of_computation",
    "shape_of_computation_failure",
    "shape_of_decomposition",
    "shape_of_evidence",
]

SHAPE_VERSION = "shape/v1"

FLAT_THRESHOLD = Decimal("0.005")
"""Abaixo de 0,5% em modulo, a direcao e `flat`.

Existe porque "subiu 0,02%" e ruido de arredondamento apresentado como
tendencia, e um estagio 2 que fosse procurar no corpus a explicacao de 0,02%
gastaria a busca com uma pergunta que ninguem fez."""

MAGNITUDE_BANDS: tuple[tuple[Decimal, Magnitude], ...] = (
    (Decimal("0.01"), Magnitude.NEGLIGIBLE),
    (Decimal("0.05"), Magnitude.SMALL),
    (Decimal("0.15"), Magnitude.MODERATE),
    (Decimal("0.40"), Magnitude.LARGE),
)
"""Degraus em variacao relativa. Acima do ultimo, `EXTREME`.

Sao grosseiros de proposito. A funcao deles e escolher o que investigar, e nao
medir - quem mede e o motor, e o numero medido sai na resposta final com
todas as casas. Uma faixa fina daria falsa precisao a uma classificacao cuja
unica finalidade e direcionar uma busca."""


def _direction(relative: Decimal | None) -> Direction | None:
    if relative is None:
        return None
    if abs(relative) < FLAT_THRESHOLD:
        return Direction.FLAT
    return Direction.UP if relative > 0 else Direction.DOWN


def _magnitude(relative: Decimal | None) -> Magnitude | None:
    if relative is None:
        return None
    magnitude = abs(relative)
    for limite, faixa in MAGNITUDE_BANDS:
        if magnitude < limite:
            return faixa
    return Magnitude.EXTREME


def shape_of_computation(result: ComputationResult) -> ResultShape:
    """Um passo de calculo -> forma.

    Direcao e magnitude so existem para valores que JA sao variacao
    (`delta_pct`, `cagr`). Um nivel absoluto - "a receita foi X" - nao tem
    direcao, e inventar uma exigiria compara-lo com alguma coisa que o passo
    nao declarou. Nesse caso os campos ficam nulos, que e a resposta honesta.
    """
    relative: Decimal | None = None
    if result.kind is ResultKind.DERIVED and result.dimension is Dimension.RATIO:
        derived = result.derived
        if derived is not None and derived.op in ("delta_pct", "cagr"):
            relative = derived.value

    return ResultShape(
        request_id=result.step_id,
        kind="derived" if result.kind is ResultKind.DERIVED else "metric",
        label=describe(result),
        direction=_direction(relative),
        magnitude=_magnitude(relative),
        shape_version=SHAPE_VERSION,
    )


def shape_of_computation_failure(failure: ComputationFailure) -> ResultShape:
    return ResultShape(
        request_id=failure.step_id,
        kind="metric",
        label=f"passo {failure.step_id}: nao foi possivel calcular",
        shape_version=SHAPE_VERSION,
        unavailable_reason=str(failure.reason),
    )


def shape_of_decomposition(outcome: DecompositionOutcome) -> ResultShape:
    """Uma decomposicao -> forma, com os membros ORDENADOS por contribuicao.

    `top_contributors` e o campo que faz o estagio 2 valer a pena: saber que
    `operating_expenses_net` explicou mais do que `revenue_net` e o que
    permite buscar no corpus o que a administracao disse sobre despesa. E
    ordem, nao valor - a informacao atravessa, o numero nao.
    """
    if outcome.unavailable is not None:
        unavailable: DecompositionUnavailable = outcome.unavailable
        return ResultShape(
            request_id=outcome.request_id,
            kind="decomposition",
            label=f"decomposicao {unavailable.decomposition_id or ''}: indisponivel".strip(),
            shape_version=SHAPE_VERSION,
            unavailable_reason=str(unavailable.reason),
        )

    result: DecompositionResult = outcome.result  # type: ignore[assignment]
    relative = result.target_delta_pct

    return ResultShape(
        request_id=outcome.request_id,
        kind="decomposition",
        label=(
            f"{result.target_label} de {result.period_from} para {result.period_to}, "
            f"aberto por {result.decomposition_id}@{result.decomposition_version}"
        ),
        direction=_direction(relative),
        magnitude=_magnitude(relative),
        shape_version=SHAPE_VERSION,
        top_contributors=tuple(c.member_id for c in result.ranked()),
        residual_is_material=not result.closes,
    )


def shape_of_evidence(outcome: EvidenceOutcome) -> ResultShape:
    """Uma busca -> forma. Quantos trechos vieram, jamais quais.

    O TEXTO dos trechos nao entra aqui, e nao e por medo de vazar numero: e
    porque o estagio 2 planeja a busca, e mostrar-lhe o resultado dela o
    poria a planejar sobre o que ja encontrou. Quem le os trechos e o
    escritor, depois, e com a citacao inteira.
    """
    if outcome.unavailable is not None:
        unavailable: EvidenceUnavailable = outcome.unavailable
        return ResultShape(
            request_id=outcome.request_id,
            kind="evidence",
            label=f"busca no corpus de {unavailable.entity_id}: sem evidencia",
            shape_version=SHAPE_VERSION,
            hits=0,
            unavailable_reason=str(unavailable.reason),
        )

    result: EvidenceResult = outcome.result  # type: ignore[assignment]
    return ResultShape(
        request_id=outcome.request_id,
        kind="evidence",
        label=(
            f"busca no corpus de {result.entity_id}, AS OF {result.as_of}: "
            f"{result.documents_in_scope} documento(s) no escopo"
        ),
        shape_version=SHAPE_VERSION,
        hits=len(result.hits),
    )

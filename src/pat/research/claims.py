"""`ProgramResult` -> nos ancorados do grafo. Deterministico, sem modelo.

Os nos FACT, CALCULATION e QUOTE sao construidos AQUI, a partir do que o motor
e o corpus produziram. O modelo nunca os cria - ele so pode apontar para eles.

E a mesma separacao da Fase 3, um nivel acima: la o escritor recebia tokens
prontos e nao podia inventar um numero; aqui ele recebe um grafo de nos
ancorados e nao pode inventar um fato nem uma citacao. O que ele acrescenta sao
INFERENCE e CONCLUSION, que nao tem campo de valor nem de endereco.

A identidade dos nos
--------------------
`claim_id` e enderecado por conteudo, sobre a especie e a ancora - `result_id`
para numero, `unit_id` para citacao. Assim o mesmo resultado sempre produz o
mesmo no, e um modelo que aponte para um `claim_id` esta apontando para algo
que existe ou para nada - nunca para "quase".
"""

from __future__ import annotations

from decimal import Decimal

from pat.canonical import sha256_of
from pat.contracts.claims import ClaimKind, ClaimNode
from pat.contracts.decomposition import DecompositionResult
from pat.contracts.program import ProgramResult
from pat.contracts.research import ComputationResult
from pat.research.render import describe, value_token

__all__ = [
    "GroundedClaims",
    "claim_id_for_quote",
    "claim_id_for_result",
    "ground_claims",
]


def claim_id_for_result(kind: ClaimKind, result_id: str) -> str:
    return sha256_of({"kind": str(kind), "result_id": result_id})


def claim_id_for_quote(unit_id: str) -> str:
    return sha256_of({"kind": str(ClaimKind.QUOTE), "unit_id": unit_id})


class GroundedClaims:
    """Os nos ancorados, mais a tabela de substituicao que os acompanha.

    A tabela de valores fica FORA do grafo, de proposito: `ClaimNode` nao tem
    campo para valor renderizado, e e assim que um valor nao chega ao prompt do
    escritor. A substituicao acontece depois, deterministicamente.
    """

    __slots__ = ("nodes", "values", "units", "warnings")

    def __init__(self) -> None:
        self.nodes: list[ClaimNode] = []
        self.values: dict[str, str] = {}
        self.units: dict[str, str] = {}
        self.warnings: list[str] = []


def ground_claims(result: ProgramResult) -> GroundedClaims:
    """Constroi FACT, CALCULATION e QUOTE a partir do que foi executado."""
    from pat.research.render import format_value

    saida = GroundedClaims()

    for computation in result.computations:
        kind = (
            ClaimKind.CALCULATION
            if computation.metric_result is None
            else ClaimKind.FACT
        )
        claim_id = claim_id_for_result(kind, computation.result_id)
        token = value_token(computation.step_id)
        saida.nodes.append(
            ClaimNode(
                claim_id=claim_id,
                kind=kind,
                text=describe(computation),
                token=token,
                result_id=computation.result_id,
            )
        )
        saida.values[token] = format_value(computation)

    for outcome in result.decompositions:
        if outcome.result is None:
            continue
        saida.nodes.extend(_decomposition_nodes(outcome.request_id, outcome.result, saida))

    for outcome in result.evidence:
        if outcome.result is None:
            continue
        for hit in outcome.result.hits:
            quote = hit.quote
            claim_id = claim_id_for_quote(quote.unit_id)
            if any(no.claim_id == claim_id for no in saida.nodes):
                # A mesma unidade pode voltar em duas buscas. Um no so, porque
                # duas citacoes do mesmo trecho nao sao duas evidencias.
                continue
            saida.nodes.append(
                ClaimNode(
                    claim_id=claim_id,
                    kind=ClaimKind.QUOTE,
                    # VERBATIM: o texto entra intacto, sem strip, sem corte.
                    text=quote.text,
                    unit_id=quote.unit_id,
                )
            )
            saida.units[claim_id] = quote.unit_id

    return saida


def _decomposition_nodes(
    request_id: str, result: DecompositionResult, saida: GroundedClaims
) -> list[ClaimNode]:
    """Uma decomposicao vira um no de total e um por contribuicao.

    O residual TAMBEM vira no quando e material. Isso e deliberado: se ele
    ficasse so no rodape, uma conclusao poderia se apoiar nas contribuicoes e
    ignorar os 14% nao explicados sem que nada no grafo registrasse a omissao.
    Sendo um no, ele pode ser citado - e a ausencia dele numa conclusao vira
    algo que o critic de modelo consegue apontar.
    """
    from pat.research.render import format_money

    nos: list[ClaimNode] = []

    total_id = sha256_of(
        {"kind": "calculation", "decomposition": request_id, "part": "total"}
    )
    total_token = value_token(f"{request_id}_total")
    nos.append(
        ClaimNode(
            claim_id=total_id,
            kind=ClaimKind.CALCULATION,
            text=(
                f"variacao de {result.target_label} entre {result.period_from} e "
                f"{result.period_to}, escopo {result.scope}, AS OF {result.as_of}, "
                f"fidelidade {result.fidelity}"
            ),
            token=total_token,
            result_id=total_id,
        )
    )
    saida.values[total_token] = format_money(result.target_delta, result.currency)

    for contribuicao in result.ranked():
        parte_id = sha256_of(
            {
                "kind": "calculation",
                "decomposition": request_id,
                "part": contribuicao.member_id,
            }
        )
        token = value_token(f"{request_id}_{contribuicao.member_id}")
        nos.append(
            ClaimNode(
                claim_id=parte_id,
                kind=ClaimKind.CALCULATION,
                text=(
                    f"contribuicao de {contribuicao.member_label} para a variacao de "
                    f"{result.target_label}, fidelidade {contribuicao.fidelity}"
                ),
                token=token,
                result_id=parte_id,
                supports=(total_id,),
            )
        )
        saida.values[token] = format_money(contribuicao.contribution, result.currency)

    if not result.closes:
        residual_id = sha256_of(
            {"kind": "calculation", "decomposition": request_id, "part": "residual"}
        )
        token = value_token(f"{request_id}_residual")
        nos.append(
            ClaimNode(
                claim_id=residual_id,
                kind=ClaimKind.CALCULATION,
                text=(
                    f"parcela NAO EXPLICADA da variacao de {result.target_label}: as "
                    "partes nao somam o todo dentro da tolerancia da identidade"
                ),
                token=token,
                result_id=residual_id,
                supports=(total_id,),
            )
        )
        saida.values[token] = format_money(result.residual, result.currency)
        saida.warnings.append(
            f"{request_id}: a decomposicao nao fecha; residual de "
            f"{format_money(result.residual, result.currency)} nao explicado"
        )

    if result.fidelity != "exact":
        saida.warnings.append(
            f"{request_id}: montada sobre binding {result.fidelity}; ver `pat mappings`"
        )

    return nos


def rendered_digits(values: dict[str, str]) -> set[str]:
    """Os algarismos que existem na tabela de substituicao.

    Usado pelo critic para distinguir "o modelo escreveu um numero" de "o
    sistema substituiu um token".
    """
    return {valor for valor in values.values() if any(c.isdigit() for c in valor)}


def as_decimal_free(values: dict[str, str]) -> dict[str, str]:
    """Confere que a tabela so carrega STRING ja renderizada.

    Um `Decimal` solto aqui seria um valor a um `str()` de distancia de um
    prompt.
    """
    for chave, valor in values.items():
        if isinstance(valor, Decimal):  # pragma: no cover - defensivo
            raise TypeError(f"{chave}: tabela de substituicao carrega Decimal, nao string")
    return values

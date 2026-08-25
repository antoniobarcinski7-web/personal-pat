"""O critic de modelo: julga o que a maquina nao consegue julgar.

Ele entra DEPOIS do critic mecanico, e so sobre o que sobrou. A divisao e o
ponto do desenho: citacao que nao bate com os bytes, digito fora de citacao e
evidencia posterior ao `as_of` sao conferiveis por codigo, e um modelo no meio
disso so pioraria a auditoria. O que resta - "esta conclusao vai alem do que a
evidencia sustenta?" - nao tem como ser conferido mecanicamente, e e aqui.

O que ele recebe, e por que isso importa
----------------------------------------
O CONJUNTO FINITO de evidencias que a execucao recuperou - inclusive as que o
escritor NAO citou. E isso que torna `SELECTIVE_EVIDENCE` possivel: existe um
trecho no conjunto que contradiz a conclusao e que ficou de fora? A pergunta so
tem resposta porque o conjunto e fechado, registrado e auditavel.

O que ele nao faz
-----------------
Nao pesquisa. Nao busca documento novo. Nao acessa o warehouse. Nao corrige o
escritor. Nao reescreve o relatorio. Nao rechama ninguem.

Sem loop, de novo
-----------------
Nao ha `critic -> writer -> critic`. "Criticar ate passar" e um amostrador que
uma hora aprova algo errado-mas-aprovado. O critic aponta e para; um humano
decide.

Por que o modelo nao escolhe sozinho o que bloqueia
---------------------------------------------------
Um achado de critic de modelo e, ele proprio, um julgamento nao verificado.
Deixar o modelo declarar qualquer achado como duro seria dar-lhe veto sobre um
relatorio possivelmente correto. Entao a severidade que ele pede e LIMITADA por
`_MAX_SEVERITY`, que e codigo versionado: dois codigos podem bloquear, os outros
so acompanham. A escolha de quais e uma decisao de arquitetura, revisavel num
diff - nao uma linha de prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from pat.contracts.claims import ClaimGraph, ClaimKind, Severity
from pat.contracts.common import Frozen, Sha256
from pat.contracts.program import ProgramResult
from pat.contracts.research import PlanProvenance, ResearchQuestion
from pat.research.canonical import capability_sha256
from pat.research.llm import LLMClient, LLMRequest, LLMResponse
from pat.research.planner import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_S,
    PlannerError,
    PlannerFailure,
)

__all__ = [
    "SYSTEM_PROMPT",
    "ModelCriticOutcome",
    "ModelFinding",
    "ModelFindingCode",
    "ModelCriticReport",
    "build_request",
    "build_user_prompt",
    "parse_findings",
    "run_model_critic",
]


class ModelFindingCode(StrEnum):
    """Taxonomia FECHADA. Um achado com nome novo a cada execucao nao da para
    contar, nem para bloquear, nem para ignorar conscientemente."""

    SELECTIVE_EVIDENCE = "selective_evidence"
    """Existe no conjunto recuperado um trecho que contradiz a conclusao e que
    nao foi citado.

    E o achado de maior valor do critic inteiro, e ele so e possivel porque o
    conjunto recuperado e finito, registrado e auditavel."""

    CAUSAL_OVERREACH = "causal_overreach"
    """Atribuicao causal cuja evidencia so sustenta correlacao ou mencao."""

    QUOTE_OUT_OF_CONTEXT = "quote_out_of_context"
    STALE_EVIDENCE = "stale_evidence"
    MISSING_DECOMPOSITION = "missing_decomposition"
    """Havia dado para quantificar e a resposta ficou so narrativa."""

    UNQUANTIFIED_MAGNITUDE = "unquantified_magnitude"
    """"queda relevante" sem o numero que existe."""


_MAX_SEVERITY: dict[ModelFindingCode, Severity] = {
    # Estes dois tornam o relatorio ENGANOSO, e nao apenas incompleto: um cita
    # so o que confirma, o outro afirma causa onde ha mencao. Em research,
    # enganoso e pior do que ausente - por isso podem bloquear.
    ModelFindingCode.SELECTIVE_EVIDENCE: Severity.HARD,
    ModelFindingCode.CAUSAL_OVERREACH: Severity.HARD,
    # Os demais deixam o relatorio incompleto, nao errado. Bloquear seria
    # tratar "faltou dizer" como "disse errado", e o usuario perderia uma
    # resposta correta por causa de um julgamento nao verificado.
    ModelFindingCode.QUOTE_OUT_OF_CONTEXT: Severity.SOFT,
    ModelFindingCode.STALE_EVIDENCE: Severity.SOFT,
    ModelFindingCode.MISSING_DECOMPOSITION: Severity.SOFT,
    ModelFindingCode.UNQUANTIFIED_MAGNITUDE: Severity.SOFT,
}


class ModelFinding(Frozen):
    """Um achado, com o que o sustenta.

    `evidence` aponta para `claim_id` ou `unit_id` do conjunto recuperado - um
    achado que nao aponta para nada e indistinguivel de uma impressao, e nao
    deveria pesar mais do que uma.
    """

    code: ModelFindingCode
    severity: Severity
    message: str = Field(min_length=1, max_length=600)
    claim_id: Sha256 | None = None
    evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "ModelFinding":
        teto = _MAX_SEVERITY[self.code]
        if self.severity is Severity.HARD and teto is Severity.SOFT:
            raise ValueError(
                f"{self.code} nao pode bloquear: o teto de severidade deste codigo e "
                f"{teto}. Quem decide o que bloqueia e codigo versionado, nao o modelo."
            )
        return self


class ModelCriticReport(Frozen):
    findings: tuple[ModelFinding, ...] = ()
    evidence_considered: int = Field(ge=0)
    """Quantos trechos o critic tinha diante de si. Entra no relatorio porque
    "nao achei evidencia seletiva" sobre dois trechos e uma afirmacao bem mais
    fraca do que sobre vinte."""

    @property
    def hard(self) -> tuple[ModelFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.HARD)

    @property
    def soft(self) -> tuple[ModelFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.SOFT)

    @property
    def blocks(self) -> bool:
        return bool(self.hard)


@dataclass(frozen=True)
class ModelCriticOutcome:
    report: ModelCriticReport
    provenance: PlanProvenance


SYSTEM_PROMPT = """\
Voce e o auditor de um sistema de pesquisa financeira. Recebe um relatorio JA \
ESCRITO e o CONJUNTO COMPLETO de evidencias que a execucao recuperou - \
inclusive as que o relatorio nao citou.

Sua funcao e apontar problemas. Voce NAO corrige, NAO reescreve, NAO sugere \
texto novo e NAO pede nova busca. Um humano le o que voce apontar.

VOCE NAO TEM ACESSO A NADA ALEM DO QUE ESTA NESTA MENSAGEM. Nao existe outra \
evidencia para consultar. Se algo nao esta aqui, ele nao foi recuperado - e \
apontar sua ausencia como falha do relatorio seria injusto com quem o escreveu.

CODIGOS (use SOMENTE estes)

  selective_evidence
    Existe no conjunto um trecho que CONTRADIZ ou enfraquece uma conclusao, e \
o relatorio nao o citou. Este e o achado mais importante que voce pode fazer. \
Aponte o trecho especifico.

  causal_overreach
    O relatorio afirma que A causou B, e a evidencia so mostra que A e B \
aconteceram, ou que alguem mencionou A. Correlacao apresentada como causa.

  quote_out_of_context
    O trecho citado, lido inteiro, nao sustenta o uso que o relatorio faz dele.

  stale_evidence
    Evidencia antiga apresentada como estado atual.

  missing_decomposition
    Havia numero apurado que quantificaria a afirmacao, e o relatorio ficou so \
na narrativa.

  unquantified_magnitude
    O relatorio diz "queda relevante" ou equivalente quando o numero existe \
entre as afirmacoes apuradas.

SEVERIDADE
  "hard"  o relatorio esta ENGANOSO. So `selective_evidence` e \
`causal_overreach` podem ser hard.
  "soft"  o relatorio esta INCOMPLETO mas o que ele diz e verdadeiro.

Na duvida, use "soft". Um relatorio correto bloqueado por engano custa caro, e \
achado leve acompanha a resposta de qualquer forma - ele nao some.

SAIDA
Um unico objeto JSON, nada mais:

{"findings": [
  {"code": "<codigo>", "severity": "hard"|"soft",
   "message": "<o problema, em uma ou duas frases>",
   "claim_id": "<claim_id da afirmacao problematica, se houver>",
   "evidence": ["<unit_id ou claim_id que sustenta seu achado>", ...]}
]}

Lista vazia e uma resposta legitima e comum. Nao invente achado para parecer \
util: um auditor que sempre acha algo deixa de ser informativo.
"""


def build_user_prompt(
    question: ResearchQuestion,
    graph: ClaimGraph,
    result: ProgramResult,
    prose: tuple[str, ...],
) -> str:
    """Monta o payload: o relatorio e TODA a evidencia recuperada.

    A prosa entra JA SUBSTITUIDA - com os numeros dentro. E deliberado e
    diferente do escritor: para julgar "queda relevante sem o numero que
    existe" ou "conclusao que ignora o residual", o auditor precisa ler o que
    o leitor vai ler. Ele nao produz numero nenhum, entao nao ha o que vazar -
    a saida dele e um enum e uma mensagem.
    """
    afirmacoes = [
        {
            "claim_id": no.claim_id,
            "kind": str(no.kind),
            "text": no.text,
            "supports": list(no.supports),
            **({"strength": str(no.strength)} if no.strength else {}),
            **({"falsified_by": list(no.falsified_by)} if no.falsified_by else {}),
        }
        for no in graph.nodes
    ]

    # O conjunto INTEIRO, e nao so o citado: e o que torna
    # `selective_evidence` possivel.
    citados = {no.unit_id for no in graph.of_kind(ClaimKind.QUOTE)}
    recuperado = []
    for outcome in result.evidence:
        if outcome.result is None:
            continue
        for hit in outcome.result.hits:
            recuperado.append(
                {
                    "unit_id": hit.quote.unit_id,
                    "published_at": hit.quote.published_at.isoformat(),
                    "kind": str(hit.quote.document_kind),
                    "title": hit.quote.title,
                    "text": hit.quote.text,
                    "citado_no_relatorio": hit.quote.unit_id in citados,
                }
            )

    return json.dumps(
        {
            "pergunta": question.text,
            "as_of": question.as_of.isoformat(),
            "relatorio": list(prose),
            "afirmacoes": afirmacoes,
            "evidencia_recuperada": recuperado,
            "nota": (
                "evidencia_recuperada e o conjunto COMPLETO. Nao existe outra "
                "para consultar."
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def build_request(
    question: ResearchQuestion,
    graph: ClaimGraph,
    result: ProgramResult,
    prose: tuple[str, ...],
    *,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> LLMRequest:
    return LLMRequest(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(question, graph, result, prose),
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )


def parse_findings(response: LLMResponse, *, evidence_considered: int) -> ModelCriticReport:
    """Texto -> achados tipados. Codigo desconhecido e recusa, nao aviso.

    Aceitar um codigo fora da taxonomia transformaria a lista fechada numa
    sugestao, e a primeira consequencia seria um achado que ninguem sabe se
    deve bloquear.
    """
    if response.stop_reason == "max_tokens":
        raise PlannerError(
            PlannerFailure.TRUNCATED_RESPONSE,
            "o critic atingiu o teto de tokens antes de fechar o JSON",
            response_sha256=response.response_sha256,
        )
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise PlannerError(
            PlannerFailure.MALFORMED_JSON,
            f"a resposta do critic nao e JSON: {exc}",
            response_sha256=response.response_sha256,
            detail=response.text[:200],
        ) from exc
    if not isinstance(payload, dict):
        raise PlannerError(
            PlannerFailure.NOT_AN_OBJECT,
            f"a resposta do critic e {type(payload).__name__}",
            response_sha256=response.response_sha256,
        )

    try:
        achados = tuple(
            ModelFinding.model_validate(item) for item in payload.get("findings", [])
        )
    except ValidationError as exc:
        raise PlannerError(
            PlannerFailure.CONTRACT_VIOLATION,
            f"achado invalido: {exc.error_count()} erro(s)",
            response_sha256=response.response_sha256,
            detail=str(exc),
        ) from exc

    return ModelCriticReport(findings=achados, evidence_considered=evidence_considered)


def run_model_critic(
    question: ResearchQuestion,
    graph: ClaimGraph,
    result: ProgramResult,
    prose: tuple[str, ...],
    snapshot,
    *,
    llm: LLMClient,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> ModelCriticOutcome:
    considerado = sum(
        len(outcome.result.hits) for outcome in result.evidence if outcome.result is not None
    )
    request = build_request(
        question,
        graph,
        result,
        prose,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )
    response = llm.complete(request)
    relatorio = parse_findings(response, evidence_considered=considerado)

    return ModelCriticOutcome(
        report=relatorio,
        provenance=PlanProvenance(
            model_id=response.model_id,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            system_prompt_sha256=request.system_prompt_sha256,
            prompt_sha256=request.prompt_sha256,
            response_sha256=response.response_sha256,
            capability_sha256=capability_sha256(snapshot),
            called_at=response.called_at,
            cached=response.cached,
            client_fingerprint=llm.fingerprint,
        ),
    )

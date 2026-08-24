"""Pergunta -> `ResearchProgram`, em DOIS estagios.

Por que dois, e nao um
----------------------
Uma verdade incomoda: nao da para saber que evidencia pedir antes de saber o
que os numeros fizeram. "Por que a receita caiu?" - a pergunta a fazer ao
corpus depende de a queda ter sido de 3% ou de 30%, e de qual componente
puxou. Um prompt unico que produzisse plano, decomposicao e busca de uma vez
estaria escolhendo a citacao antes de conhecer o numero, que e exatamente como
se produz uma narrativa convincente e errada.

    estagio 1  planner.compute    pergunta + capability
                                  -> plano de calculo + decomposicoes
               [executa DETERMINISTICAMENTE]
    estagio 2  planner.evidence   pergunta + capability + FORMA dos resultados
                                  -> plano de evidencia

Dois estagios NAO e retentativa. Sao dois papeis distintos, com entradas
distintas e prompts distintos, cada um com sua propria linha em `llm_call` e
sua propria `PlanProvenance`. Colapsar as duas procedencias esconderia qual
deles produziu o que.

O que o estagio 2 ve, e o que ele nunca ve
------------------------------------------
Ve `ResultShape`: direcao, faixa de magnitude, quais membros contribuiram mais
(por identificador, na ordem) e se a decomposicao fechou. Nao ve um `Decimal`
- `ResultShape` nao tem campo capaz de carregar um, e ha teste por AST que
confere. E a mesma tecnica de `ConversationContext` na M4.1: a garantia e a
forma do tipo, e nao uma instrucao no prompt.

Sem retentativa, como sempre
----------------------------
Uma resposta que nao vira programa e um erro, nao uma primeira tentativa. Uma
segunda chamada seria um segundo caminho de influencia, que teria que ser
hasheado e manifestado por conta propria.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from pat.contracts.program import (
    DecompositionRequest,
    EvidenceRequest,
    ResearchProgram,
    ResultShape,
)
from pat.contracts.research import (
    CapabilitySnapshot,
    PlanProvenance,
    ResearchPlan,
    ResearchQuestion,
)
from pat.research.canonical import canonical_bytes, capability_sha256, question_id
from pat.research.llm import LLMClient, LLMRequest, LLMResponse
from pat.research.planner import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_S,
    PlannerError,
    PlannerFailure,
)

__all__ = [
    "COMPUTE_SYSTEM_PROMPT",
    "EVIDENCE_SYSTEM_PROMPT",
    "ProgramPlannerOutcome",
    "build_compute_request",
    "build_evidence_request",
    "parse_compute_stage",
    "parse_evidence_stage",
    "plan_compute_stage",
    "plan_evidence_stage",
]


COMPUTE_SYSTEM_PROMPT = """\
Voce e o primeiro estagio do planejador de um sistema de pesquisa financeira. \
Sua funcao e decidir O QUE MEDIR e O QUE DECOMPOR para investigar uma \
pergunta.

REGRA CENTRAL
Voce nunca produz numeros. Os valores sao calculados por um motor \
deterministico a partir de demonstracoes auditadas. Voce escolhe o que deve \
ser calculado; o motor calcula. Nao existe campo nesta gramatica onde caiba \
um valor.

VOCE NAO BUSCA EVIDENCIA NESTE ESTAGIO. Um segundo estagio faz isso, depois \
de ver o que os numeros fizeram. Nao tente adivinhar agora o que procurar em \
documento.

SAIDA
Responda com um unico objeto JSON e nada mais. Sem texto antes ou depois, sem \
markdown, sem cerca de codigo.

{
  "objective": "<uma frase dizendo o que o programa investiga>",
  "as_of": "AAAA-MM-DD",
  "scope": "consolidated" | "parent_only",
  "questions_to_answer": ["<pergunta que o programa pretende responder>", ...],
  "compute": <plano de calculo, ou null>,
  "decompositions": [ <pedido de decomposicao>, ... ],
  "unresolved": [ <pendencia>, ... ]
}

O PLANO DE CALCULO ("compute")
Mesma gramatica de sempre, ou null se a pergunta nao pedir medida direta:

{
  "objective": "...", "as_of": "AAAA-MM-DD", "scope": "...",
  "steps": [ <passo>, ... ], "outputs": [ "<step_id>", ... ],
  "assumptions": [], "unresolved": []
}

Passo de metrica:
{"step_id": "<minusculas/digitos/_>", "step_kind": "metric",
 "metric": {"name": "<nome>", "version": "<versao>"},
 "entity_id": "<do snapshot>", "period_end": "AAAA-MM-DD"}

Passo de derivacao:
{"step_id": "...", "step_kind": "derivation", "op": "<operacao>",
 "inputs": ["<step_id anterior>", ...], "params": {"years": <int>}}

DECOMPOSICOES
Uma decomposicao abre a VARIACAO de um total entre dois periodos nas partes \
que a produziram. E o primeiro passo de toda resposta causal, e ele e \
quantitativo. Se a pergunta e "por que X mudou", peca a decomposicao de X.

{"request_id": "<minusculas/digitos/_>",
 "decomposition": "<nome@versao, do snapshot>",
 "entity_id": "<do snapshot>",
 "period_from": "AAAA-MM-DD", "period_to": "AAAA-MM-DD"}

Use apenas refs que aparecem em "decompositions" no snapshot COM \
"available": true. As marcadas com "available": false existem mas estao \
bloqueadas por falta de fonte estruturada - nao as peca.

REGRAS
- `as_of` e `scope` do programa e do plano tem que ser IDENTICOS.
- Nao emita "question_id" nem "program_version": nao sao escolha sua.
- period_from tem que ser anterior a period_to.
- Se a pergunta for ambigua, devolva "unresolved" e deixe o resto vazio.

PENDENCIA
{"kind": "ambiguous_entity"|"ambiguous_period"|"ambiguous_scope"|
         "unsupported_metric"|"unsupported_question",
 "detail": "<o que falta>", "candidates": ["<opcao>", ...]}
"""


EVIDENCE_SYSTEM_PROMPT = """\
Voce e o segundo estagio do planejador de um sistema de pesquisa financeira. \
Os numeros JA FORAM CALCULADOS. Sua funcao e decidir O QUE PROCURAR nos \
documentos publicados pela companhia para explicar o que os numeros mostram.

O QUE VOCE RECEBE
Para cada resultado, a FORMA dele - nunca o valor:
  "direction"          up | down | flat
  "magnitude"          negligible | small | moderate | large | extreme
  "top_contributors"   membros da decomposicao ORDENADOS por contribuicao
  "residual_is_material"  a decomposicao nao fechou

Voce NAO recebe os valores, e nao deve pedi-los. Saber que a despesa \
operacional foi o maior contribuidor ja basta para decidir o que procurar.

REGRA CENTRAL
Voce nao explica nada neste estagio. Voce so escolhe BUSCAS. A explicacao vem \
depois, de trechos verbatim que o sistema recupera - e um trecho que nao \
existir nao pode ser inventado.

SAIDA
Um unico objeto JSON, nada mais:

{
  "evidence": [
    {"request_id": "<minusculas/digitos/_>",
     "entity_id": "<do snapshot>",
     "terms": ["<termo>", ...],
     "kinds": ["<DocumentKind>", ...],
     "published_from": "AAAA-MM-DD" | null,
     "published_to": "AAAA-MM-DD" | null,
     "limit": <1 a 20>,
     "rationale": "<por que esta busca, em uma frase>"}
  ]
}

COMO ESCOLHER TERMOS
- Priorize o que os "top_contributors" sugerem: se despesa operacional puxou \
a queda, procure o que a administracao disse sobre despesa, custo, provisao, \
impairment.
- Termos sao palavras que apareceriam NO DOCUMENTO, em portugues, sem acento \
obrigatorio. Nao use nomes de conceito do sistema como termo de busca.
- Uma busca por pergunta que voce quer responder. Nao repita a mesma busca \
com sinonimos.

COBERTURA
"corpus" no snapshot diz que documentos existem para cada empresa, de que \
tipo e em que janela. Nao peca tipo que nao existe: a busca voltaria vazia, e \
vazio se le como "a empresa nao falou disso".

LIMITES
- Nao emita `as_of`: ele vem do programa e vale para todas as buscas.
- published_from/published_to nunca podem passar do `as_of` do programa.
- No maximo 6 buscas. Mais do que isso nao e investigacao, e varredura.
"""


@dataclass(frozen=True)
class ProgramPlannerOutcome:
    """O programa e as DUAS procedencias que o produziram."""

    program: ResearchProgram
    compute_provenance: PlanProvenance
    evidence_provenance: PlanProvenance | None = None


# ---------------------------------------------------------------------------
# Estagio 1 - calculo e decomposicao
# ---------------------------------------------------------------------------


def build_compute_request(
    question: ResearchQuestion,
    snapshot: CapabilitySnapshot,
    *,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> LLMRequest:
    return LLMRequest(
        system=COMPUTE_SYSTEM_PROMPT,
        user=_compute_prompt(question, snapshot),
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )


def _compute_prompt(question: ResearchQuestion, snapshot: CapabilitySnapshot) -> str:
    return json.dumps(
        {
            "pergunta": question.text,
            "as_of": question.as_of.isoformat(),
            "escopo_fixado": question.pinned_scope,
            "empresas_fixadas": list(question.pinned_entities),
            "periodos_fixados": [p.isoformat() for p in question.pinned_periods],
            "capacidades": json.loads(canonical_bytes(snapshot).decode("utf-8")),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def parse_compute_stage(
    response: LLMResponse, question: ResearchQuestion
) -> tuple[ResearchPlan | None, tuple[DecompositionRequest, ...], dict]:
    """Texto -> (plano, decomposicoes, cabecalho do programa).

    Recusa em vez de consertar, pela mesma razao do planejador da Fase 3:
    corrigir a saida de um modelo confuso esconde a confusao e adia o sintoma.
    """
    payload = _payload(response)

    for proibido in ("question_id", "program_version"):
        if proibido in payload:
            raise PlannerError(
                PlannerFailure.QUESTION_ID_EMITTED,
                f"o modelo emitiu {proibido}, que nao e escolha dele",
                response_sha256=response.response_sha256,
            )

    qid = question_id(question)
    plano: ResearchPlan | None = None
    bruto = payload.get("compute")
    if bruto is not None:
        try:
            plano = ResearchPlan.model_validate({**bruto, "question_id": qid})
        except ValidationError as exc:
            raise PlannerError(
                PlannerFailure.CONTRACT_VIOLATION,
                f"o plano de calculo nao satisfaz a gramatica: {exc.error_count()} erro(s)",
                response_sha256=response.response_sha256,
                detail=str(exc),
            ) from exc

    try:
        pedidos = tuple(
            DecompositionRequest.model_validate(item)
            for item in payload.get("decompositions", [])
        )
    except ValidationError as exc:
        raise PlannerError(
            PlannerFailure.CONTRACT_VIOLATION,
            f"pedido de decomposicao invalido: {exc.error_count()} erro(s)",
            response_sha256=response.response_sha256,
            detail=str(exc),
        ) from exc

    cabecalho = {
        "question_id": qid,
        "objective": payload.get("objective", ""),
        "as_of": payload.get("as_of"),
        "scope": payload.get("scope"),
        "questions_to_answer": tuple(payload.get("questions_to_answer", ())),
        "unresolved": tuple(payload.get("unresolved", ())),
    }
    return plano, pedidos, cabecalho


def plan_compute_stage(
    question: ResearchQuestion,
    snapshot: CapabilitySnapshot,
    *,
    llm: LLMClient,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[ResearchProgram, PlanProvenance]:
    """Primeira chamada. Devolve um programa SEM evidencia."""
    request = build_compute_request(
        question,
        snapshot,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )
    response = llm.complete(request)
    plano, decomposicoes, cabecalho = parse_compute_stage(response, question)

    try:
        program = ResearchProgram.model_validate(
            {**cabecalho, "compute": plano, "decompositions": decomposicoes, "evidence": ()}
        )
    except ValidationError as exc:
        raise PlannerError(
            PlannerFailure.CONTRACT_VIOLATION,
            f"o programa nao satisfaz o contrato: {exc.error_count()} erro(s)",
            response_sha256=response.response_sha256,
            detail=str(exc),
        ) from exc

    return program, _provenance(request, response, snapshot, llm)


# ---------------------------------------------------------------------------
# Estagio 2 - evidencia
# ---------------------------------------------------------------------------


def build_evidence_request(
    question: ResearchQuestion,
    snapshot: CapabilitySnapshot,
    program: ResearchProgram,
    shapes: tuple[ResultShape, ...],
    *,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> LLMRequest:
    return LLMRequest(
        system=EVIDENCE_SYSTEM_PROMPT,
        user=_evidence_prompt(question, snapshot, program, shapes),
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )


def _evidence_prompt(
    question: ResearchQuestion,
    snapshot: CapabilitySnapshot,
    program: ResearchProgram,
    shapes: tuple[ResultShape, ...],
) -> str:
    """Monta o prompt do estagio 2.

    `shapes` entra por `canonical_bytes`, que serializa `ResultShape` - um
    contrato sem campo `Decimal`. Nenhum valor pode atravessar por aqui, e o
    teste `test_nenhum_valor_atravessa_para_o_estagio_dois` confere contra os
    resultados reais, e nao contra uma lista de nomes de campo.
    """
    return json.dumps(
        {
            "pergunta": question.text,
            "as_of": program.as_of.isoformat(),
            "objetivo": program.objective,
            "perguntas_a_responder": list(program.questions_to_answer),
            "forma_dos_resultados": json.loads(canonical_bytes(shapes).decode("utf-8")),
            "cobertura_de_documentos": json.loads(
                canonical_bytes(snapshot.corpus).decode("utf-8")
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def parse_evidence_stage(response: LLMResponse) -> tuple[EvidenceRequest, ...]:
    payload = _payload(response)
    try:
        return tuple(
            EvidenceRequest.model_validate(item) for item in payload.get("evidence", [])
        )
    except ValidationError as exc:
        raise PlannerError(
            PlannerFailure.CONTRACT_VIOLATION,
            f"pedido de evidencia invalido: {exc.error_count()} erro(s)",
            response_sha256=response.response_sha256,
            detail=str(exc),
        ) from exc


def plan_evidence_stage(
    question: ResearchQuestion,
    snapshot: CapabilitySnapshot,
    program: ResearchProgram,
    shapes: tuple[ResultShape, ...],
    *,
    llm: LLMClient,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[ResearchProgram, PlanProvenance]:
    """Segunda chamada. Devolve o programa COM evidencia."""
    request = build_evidence_request(
        question,
        snapshot,
        program,
        shapes,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )
    response = llm.complete(request)
    pedidos = parse_evidence_stage(response)

    try:
        completo = program.model_copy(update={"evidence": pedidos})
        # `model_copy` nao revalida; a reconstrucao abaixo forca os validadores
        # a rodar de novo - inclusive o que recusa janela posterior ao `as_of`.
        completo = ResearchProgram.model_validate(completo.model_dump())
    except ValidationError as exc:
        raise PlannerError(
            PlannerFailure.CONTRACT_VIOLATION,
            f"o programa com evidencia nao satisfaz o contrato: {exc.error_count()} erro(s)",
            response_sha256=response.response_sha256,
            detail=str(exc),
        ) from exc

    return completo, _provenance(request, response, snapshot, llm)


# ---------------------------------------------------------------------------


def _payload(response: LLMResponse) -> dict:
    if response.stop_reason == "max_tokens":
        raise PlannerError(
            PlannerFailure.TRUNCATED_RESPONSE,
            "a geracao atingiu o teto de tokens antes de fechar o JSON",
            response_sha256=response.response_sha256,
            detail=response.text[-200:],
        )
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise PlannerError(
            PlannerFailure.MALFORMED_JSON,
            f"a resposta nao e JSON: {exc}",
            response_sha256=response.response_sha256,
            detail=response.text[:200],
        ) from exc
    if not isinstance(payload, dict):
        raise PlannerError(
            PlannerFailure.NOT_AN_OBJECT,
            f"a resposta e {type(payload).__name__}, e um programa e um objeto",
            response_sha256=response.response_sha256,
        )
    return payload


def _provenance(
    request: LLMRequest,
    response: LLMResponse,
    snapshot: CapabilitySnapshot,
    llm: LLMClient,
) -> PlanProvenance:
    return PlanProvenance(
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
    )

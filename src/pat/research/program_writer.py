"""Grafo ancorado -> leituras, conclusoes e prosa. O uso final de LLM.

O escritor da Fase 3 recebia tokens e rotulos. Este recebe um GRAFO: nos de
fato, calculo e citacao, todos ja construidos deterministicamente. O que ele
acrescenta sao dois tipos de no que nao tem campo de valor nem de endereco -
`INFERENCE` e `CONCLUSION` - e blocos de prosa que citam tokens.

O que ele nao pode, por construcao
----------------------------------
- Inventar um numero: nao recebe valor. Escreve `{{s:passo}}`; a substituicao
  acontece depois, e um token fora da tabela e recusado.
- Inventar uma citacao: nao escreve `QUOTE`. Ele so pode apontar para os nos
  de citacao que a execucao produziu, pelo `claim_id`.
- Transformar citacao em insumo: `CALCULATION -> QUOTE` e recusado na
  construcao do grafo, antes de o critic olhar.
- Concluir sem dizer o que a derrubaria: `falsified_by` e obrigatorio no
  contrato.

Ele VE o texto das citacoes - precisa, para escrever sobre elas. O guarda-corpo
nao e cegueira, e a verificacao: o texto que ele repetir tem que bater byte a
byte com a unidade, e o critic confere.

Sem retentativa
---------------
Pela razao de sempre. Prosa recusada pela regra do digito e um erro nomeado, e
nao uma primeira tentativa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from pat.canonical import sha256_of
from pat.contracts.claims import (
    ClaimGraph,
    ClaimKind,
    ClaimNode,
    EvidenceStrength,
)
from pat.contracts.research import PlanProvenance, ResearchQuestion
from pat.research.canonical import capability_sha256
from pat.research.claims import GroundedClaims
from pat.research.llm import LLMClient, LLMRequest, LLMResponse
from pat.research.planner import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_S,
)
from pat.research.writer import WriterError, WriterFailure

__all__ = [
    "SYSTEM_PROMPT",
    "ProgramWriterOutcome",
    "build_request",
    "build_user_prompt",
    "parse_report",
    "write_report",
]


SYSTEM_PROMPT = """\
Voce e o redator de um sistema de pesquisa financeira. Recebe um GRAFO de \
afirmacoes JA APURADAS e escreve um relatorio de research a partir delas.

O QUE VOCE RECEBE
Nos de tres especies, todos ja verificados:
  "fact"         um numero do motor. Vem com "token" e "text" (o que o numero \
E, sem o valor). Voce NUNCA ve o valor.
  "calculation"  uma conta entre resultados, incluindo as contribuicoes de uma \
decomposicao. Mesmo formato.
  "quote"        um trecho VERBATIM de documento publicado pela companhia. \
Vem com o texto inteiro.

O QUE VOCE PRODUZ
Dois tipos de no novos, e blocos de prosa.

  "inference"    uma leitura sua. EXIGE "supports" (claim_id dos nos que a \
sustentam) e "strength".
  "conclusion"   uma sintese. EXIGE "supports" e "falsified_by".

REGRAS QUE NAO SE NEGOCIAM

1. NUMEROS. Voce nunca escreve algarismo na prosa. No lugar de cada valor use \
o token do no ("{{s:passo}}"). Um texto seu com algarismo fora de citacao e \
recusado inteiro. Isso inclui datas e anos.

2. CITACOES. Voce nao inventa citacao. Para citar, referencie o claim_id de um \
no "quote" no bloco. O sistema imprime o texto verbatim; voce nao o reescreve, \
nao o apara e nao o resume.

3. SUPORTE. Toda inferencia e toda conclusao aponta para claim_ids que existem \
na lista recebida. Nao invente claim_id.

4. FORCA. "strength" e um dos tres:
     "quantified"  a leitura esta ligada a uma decomposicao que a MEDE
     "attributed"  alguem com nome disse isso, e voce esta citando
     "suggested"   compativel com a evidencia, sem que ela afirme a relacao
   Nao infle. "suggested" e uma resposta respeitavel.

5. FALSIFICADOR. Toda conclusao diz o que a derrubaria, em "falsified_by". Nao \
e formalidade: e a lista do que monitorar no proximo trimestre.

6. O NAO EXPLICADO. Se houver um no de parcela nao explicada (residual), a \
conclusao TEM que mencionar que a explicacao e parcial. Explicacao parcial que \
se apresenta como completa e o pior resultado possivel aqui.

SAIDA
Um unico objeto JSON, nada mais:

{
  "claims": [
    {"claim_id": "<voce NAO emite: omita>", "kind": "inference",
     "text": "<a leitura, sem algarismo>",
     "supports": ["<claim_id>", ...], "strength": "<forca>"},
    {"kind": "conclusion", "text": "<a sintese, sem algarismo>",
     "supports": ["<claim_id>", ...],
     "falsified_by": ["<o que derrubaria>", ...]}
  ],
  "blocks": [
    {"kind": "prose", "text": "<paragrafo, com tokens no lugar de numeros>"},
    {"kind": "quote", "claim_id": "<claim_id de um no quote>"}
  ]
}

Nao emita "claim_id" nos nos que voce cria: a identidade e derivada do \
conteudo, e nao escolha sua.
"""


@dataclass(frozen=True)
class ProgramWriterOutcome:
    """O grafo completo, os blocos e de onde o texto veio."""

    graph: ClaimGraph
    blocks: tuple[tuple[str, str], ...]
    """(claim_id, texto). `claim_id` vazio em bloco de prosa; preenchido em
    bloco de citacao, e e por ele que o critic sabe qual bloco pode ter
    algarismo."""
    provenance: PlanProvenance


def build_user_prompt(question: ResearchQuestion, grounded: GroundedClaims) -> str:
    """Monta o payload. O VALOR nunca entra.

    `grounded.values` fica de fora por construcao: a tabela de substituicao e
    do sistema, e o escritor recebe so token e rotulo. E o mesmo recorte de
    `writer.evidence_payload` na Fase 3, e ha teste que confere que nenhum
    valor renderizado aparece no prompt.
    """
    nos = []
    for no in grounded.nodes:
        entrada = {"claim_id": no.claim_id, "kind": str(no.kind), "text": no.text}
        if no.token:
            entrada["token"] = no.token
        if no.supports:
            entrada["supports"] = list(no.supports)
        nos.append(entrada)

    return json.dumps(
        {
            "pergunta": question.text,
            "as_of": question.as_of.isoformat(),
            "afirmacoes_apuradas": nos,
            "tokens_disponiveis": sorted(grounded.values),
            "ressalvas_a_dizer": grounded.warnings,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def build_request(
    question: ResearchQuestion,
    grounded: GroundedClaims,
    *,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> LLMRequest:
    return LLMRequest(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(question, grounded),
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )


def parse_report(
    response: LLMResponse, grounded: GroundedClaims
) -> tuple[ClaimGraph, tuple[tuple[str, str], ...]]:
    """Texto -> grafo completo e blocos. Recusa em vez de consertar."""
    if response.stop_reason == "max_tokens":
        raise WriterError(
            WriterFailure.TRUNCATED_RESPONSE,
            "a geracao atingiu o teto antes de fechar o JSON",
            response_sha256=response.response_sha256,
        )
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise WriterError(
            WriterFailure.MALFORMED_JSON,
            f"a resposta nao e JSON: {exc}",
            response_sha256=response.response_sha256,
            detail=response.text[:200],
        ) from exc
    if not isinstance(payload, dict):
        raise WriterError(
            WriterFailure.MALFORMED_JSON,
            f"a resposta e {type(payload).__name__}, e um relatorio e um objeto",
            response_sha256=response.response_sha256,
        )

    novos: list[ClaimNode] = []
    for bruto in payload.get("claims", []):
        if not isinstance(bruto, dict):
            raise WriterError(
                WriterFailure.MALFORMED_JSON,
                "item de `claims` nao e objeto",
                response_sha256=response.response_sha256,
            )
        especie = bruto.get("kind")
        if especie not in ("inference", "conclusion"):
            raise WriterError(
                WriterFailure.CONTRACT_VIOLATION,
                f"o escritor so cria inference e conclusion; veio {especie!r}. "
                "Fato, calculo e citacao sao construidos pelo motor.",
                response_sha256=response.response_sha256,
            )
        corpo = {
            "kind": especie,
            "text": bruto.get("text", ""),
            "supports": tuple(bruto.get("supports", ())),
        }
        if especie == "inference":
            corpo["strength"] = bruto.get("strength")
        else:
            corpo["falsified_by"] = tuple(bruto.get("falsified_by", ()))

        # A identidade e derivada do conteudo, e nao pedida ao modelo - pelo
        # mesmo argumento que faz o planejador injetar `question_id`.
        corpo["claim_id"] = sha256_of(corpo)
        try:
            novos.append(ClaimNode.model_validate(corpo))
        except ValidationError as exc:
            raise WriterError(
                WriterFailure.CONTRACT_VIOLATION,
                f"afirmacao invalida: {exc.error_count()} erro(s)",
                response_sha256=response.response_sha256,
                detail=str(exc),
            ) from exc

    blocos: list[tuple[str, str]] = []
    ancorados = {no.claim_id: no for no in grounded.nodes}
    for bruto in payload.get("blocks", []):
        if not isinstance(bruto, dict):
            continue
        if bruto.get("kind") == "quote":
            claim_id = bruto.get("claim_id", "")
            no = ancorados.get(claim_id)
            if no is None or no.kind is not ClaimKind.QUOTE:
                raise WriterError(
                    WriterFailure.CONTRACT_VIOLATION,
                    f"bloco de citacao aponta para {claim_id[:12]!r}, que nao e um "
                    "no de citacao desta execucao",
                    response_sha256=response.response_sha256,
                )
            # O texto vem da UNIDADE, nao do modelo: e o que faz a citacao ser
            # verbatim por construcao, e nao por conferencia.
            blocos.append((claim_id, no.text))
        else:
            blocos.append(("", str(bruto.get("text", ""))))

    try:
        graph = ClaimGraph(nodes=tuple(grounded.nodes) + tuple(novos))
    except ValidationError as exc:
        raise WriterError(
            WriterFailure.CONTRACT_VIOLATION,
            f"o grafo de afirmacoes nao fecha: {exc.error_count()} erro(s)",
            response_sha256=response.response_sha256,
            detail=str(exc),
        ) from exc

    return graph, tuple(blocos)


def write_report(
    question: ResearchQuestion,
    grounded: GroundedClaims,
    snapshot,
    *,
    llm: LLMClient,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> ProgramWriterOutcome:
    request = build_request(
        question,
        grounded,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )
    response = llm.complete(request)
    graph, blocos = parse_report(response, grounded)

    return ProgramWriterOutcome(
        graph=graph,
        blocks=blocos,
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


def substitute(blocks: tuple[tuple[str, str], ...], values: dict[str, str]) -> tuple[str, ...]:
    """Troca token por valor. Deterministico, e o ultimo passo.

    Acontece DEPOIS do critic, e nao antes: o critic confere a prosa com os
    tokens intactos, que e a unica forma de distinguir "o modelo escreveu um
    numero" de "o sistema substituiu um token".
    """
    saida = []
    for claim_id, texto in blocks:
        if claim_id:
            saida.append(texto)  # citacao: sai intacta
            continue
        for token, valor in values.items():
            texto = texto.replace(token, valor)
        saida.append(texto)
    return tuple(saida)

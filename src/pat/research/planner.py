"""Pergunta em linguagem natural + capacidades -> `ResearchPlan`.

E o unico ponto do caminho de dados onde um modelo participa, e o que ele
produz e um *plano*, nunca um numero. A separacao que este modulo existe para
manter:

    LLM       escolhe O QUE perguntar, dentro de uma gramatica fechada
    Executor  produz os numeros, deterministicamente, a partir do warehouse

O planejador nao executa, nao consulta banco, nao calcula, nao resolve fato e
nao persiste nada. Ele traduz texto em estrutura e para.

Por que ele nao valida
----------------------
A tentacao obvia e conferir aqui se a metrica existe, se a empresa existe, se
o periodo esta coberto. Nao e daqui: `validate.py` responde "este plano e
bem-formado?" e `resolve.py` responde "este warehouse consegue servir?".
Repetir qualquer uma das duas aqui criaria uma segunda implementacao das
mesmas regras, e duas implementacoes divergem - normalmente na direcao de a
mais permissiva deixar passar o que a outra recusaria.

O planejador so recusa o que impede o texto de *virar* um plano: JSON
malformado e violacao de contrato. Plano estruturalmente valido e
semanticamente proibido segue adiante, para ser recusado por quem sabe
recusar com codigo e remedio.

Sem retentativa
---------------
Uma resposta que nao vira plano e um erro, e nao uma primeira tentativa. Uma
segunda chamada seria um segundo caminho de influencia, que teria que ser
hasheado e manifestado por conta propria - ou a procedencia passa a
sub-relatar o que aconteceu. Alem disso, "tentar de novo ate passar" e
exatamente como um validador vira um laco de amostragem que uma hora emite
algo errado-mas-aprovado.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pydantic import ValidationError

from pat.contracts.research import (
    CapabilitySnapshot,
    PlanProvenance,
    ResearchPlan,
    ResearchQuestion,
)
from pat.research.canonical import canonical_bytes, capability_sha256, question_id
from pat.research.llm import LLMClient, LLMRequest, LLMResponse

__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT_S",
    "SYSTEM_PROMPT",
    "PlannerError",
    "PlannerFailure",
    "PlannerOutcome",
    "build_request",
    "plan_question",
]


DEFAULT_TEMPERATURE: Decimal | None = None
"""Nenhuma preferencia de amostragem, por decisao de desenho.

A proposta original pedia zero, com a ressalva dita por extenso: temperatura
zero reduz variancia, nao produz determinismo, e nenhuma parte da
reprodutibilidade do sistema se apoia nisso - ela se apoia no plano salvo e nos
hashes. A auditoria do M2.3 tirou a consequencia que a ressalva ja implicava:
se a reprodutibilidade nao se apoia no campo, nada se perde ao nao pedi-lo, e
pedir zero num contrato universal so obrigaria adapters a lidar com um
parametro que varios modelos ja nem aceitam.

`None` nao e "zero implicito". E a afirmacao de que quem escolhe a
configuracao de geracao e o adapter, que a declara em `client_fingerprint`."""

DEFAULT_MAX_TOKENS = 16384
"""Teto de geracao da chamada, nao orcamento do plano.

O valor anterior (4096) foi dimensionado para um mundo sem raciocinio interno,
e acumulava um segundo papel: teto apertado como canario de truncamento. O
canario deixou de funcionar quando o teto passou a cobrir raciocinio e texto
juntos - ele dispara quando o *raciocinio* foi longo, que nao e a mesma coisa
que o plano ter ficado grande, nem e controlavel pelo prompt.

O papel de canario voltou para onde pertence: `stop_reason`, com uma falha
nomeada (`PlannerFailure.TRUNCATED_RESPONSE`). O teto aqui so precisa ser
folgado."""

DEFAULT_TIMEOUT_S = 60


SYSTEM_PROMPT = """\
Voce e o planejador de um sistema de pesquisa financeira. Sua unica funcao e \
traduzir uma pergunta em um plano declarativo de calculo.

REGRA CENTRAL
Voce nunca produz numeros. Os valores sao calculados por um motor \
deterministico a partir de demonstracoes financeiras auditadas. Voce escolhe \
O QUE deve ser calculado; o motor calcula. Um plano nao tem onde escrever um \
valor: nao existe campo para isso na gramatica.

SAIDA
Responda com um unico objeto JSON e nada mais. Sem texto antes ou depois, sem \
comentario, sem markdown, sem cerca de codigo, sem explicacao.

O objeto tem exatamente estes campos:

{
  "objective": "<uma frase dizendo o que o plano calcula>",
  "as_of": "AAAA-MM-DD",
  "scope": "consolidated" | "parent_only",
  "steps": [ <passo>, ... ],
  "outputs": [ "<step_id>", ... ],
  "assumptions": [ "<texto>", ... ],
  "unresolved": [ <pendencia>, ... ]
}

Nao emita "question_id" nem "plan_version": eles nao sao escolha sua.

PASSOS
Ha dois tipos, e apenas dois.

Passo de metrica:
{
  "step_id": "<minusculas, digitos e _; comeca com letra>",
  "step_kind": "metric",
  "metric": {"name": "<nome>", "version": "<versao>"},
  "entity_id": "<entity_id do snapshot>",
  "period_end": "AAAA-MM-DD"
}

O snapshot lista metricas como "nome@versao"; no plano elas vao separadas.
"margem_ebitda@v1" vira {"name": "margem_ebitda", "version": "v1"}. Use
exatamente o nome e a versao que aparecem no snapshot - nao troque a versao.

Passo de derivacao:
{
  "step_id": "...",
  "step_kind": "derivation",
  "op": "delta" | "delta_pct" | "ratio" | "cagr" | "min" | "max" | "mean",
  "inputs": ["<step_id anterior>", ...],
  "params": {"years": <inteiro>}
}

"params" so leva "years", e so para "cagr". Um passo de derivacao so pode \
citar step_id que aparece ANTES dele na lista.

"scope" e "as_of" pertencem ao plano inteiro e nao ao passo: todos os passos \
herdam os dois.

O QUE VOCE NAO PODE FAZER
- Nao invente metrica: use somente as que estao em "metrics" no snapshot, com \
a versao exata.
- Nao invente empresa: use somente "entity_id" que aparece em "entities".
- Nao invente periodo: use somente datas listadas em "period_ends" da empresa.
- Nao escreva valores, estimativas, resultados ou numeros de demonstracoes.
- Nao escreva codigo, consulta SQL ou chamada de ferramenta.
- Nao contradiga o que a pergunta fixou (entidades, periodos, escopo). Voce \
pode completar o que ficou em aberto; nunca contrariar o que foi fixado.

QUANDO NAO DA PARA PLANEJAR
Se a pergunta for ambigua ou pedir algo que as capacidades nao cobrem, diga \
isso em "unresolved" em vez de aproximar:

{"kind": "ambiguous_entity" | "ambiguous_period" | "ambiguous_scope" | \
"unsupported_metric" | "unsupported_question",
 "detail": "<o que falta para decidir>",
 "candidates": ["<opcao>", ...]}

Um plano com "unresolved" nao vazio nao sera executado, e isso e o \
comportamento correto: e melhor devolver a duvida do que devolver um numero \
que responde a outra pergunta. Nao preencha lacuna com suposicao.
"""


class PlannerFailure(StrEnum):
    """Por que a resposta nao virou plano. Sempre um destes, nunca uma
    excecao crua de biblioteca."""

    TRUNCATED_RESPONSE = "truncated_response"
    MALFORMED_JSON = "malformed_json"
    NOT_AN_OBJECT = "not_an_object"
    CONTRACT_VIOLATION = "contract_violation"
    QUESTION_ID_EMITTED = "question_id_emitted"


class PlannerError(RuntimeError):
    """A resposta do modelo nao corresponde a gramatica de plano.

    Carrega `response_sha256` de proposito: o texto cru fica recuperavel pelo
    hash (no cache, quando ele existir), entao o diagnostico nao depende de a
    mensagem de erro ter copiado o suficiente da resposta.
    """

    def __init__(
        self,
        failure: PlannerFailure,
        message: str,
        *,
        response_sha256: str,
        detail: str | None = None,
    ) -> None:
        super().__init__(f"[{failure}] {message}")
        self.failure = failure
        self.response_sha256 = response_sha256
        self.detail = detail


@dataclass(frozen=True)
class PlannerOutcome:
    """O plano e de onde ele veio.

    Os dois juntos e separados: `PlanProvenance` fica fora de `ResearchPlan`
    justamente para nao entrar no `plan_id`. Dois modelos que produzam o mesmo
    plano produzem o mesmo hash.
    """

    plan: ResearchPlan
    provenance: PlanProvenance
    response: LLMResponse


def _snapshot_payload(snapshot: CapabilitySnapshot) -> dict:
    """O snapshot como o modelo vai ve-lo: tudo menos `built_at`.

    Excluir o relogio nao e cosmetico. Com ele no prompt, duas execucoes
    identicas produziriam `prompt_sha256` diferentes, e o cache (Milestone 2.3)
    nunca acertaria - alem de tornar impossivel afirmar que a mesma entrada
    gera a mesma chamada. E o mesmo recorte de `capability_sha256`.
    """
    return {
        name: getattr(snapshot, name)
        for name in CapabilitySnapshot.model_fields
        if name != "built_at"
    }


def build_user_prompt(question: ResearchQuestion, snapshot: CapabilitySnapshot) -> str:
    """Pergunta + capacidades, em forma estavel.

    A serializacao passa pela canonicalizacao do projeto - chaves ordenadas,
    sem espaco, `Decimal` como string. Nada de `repr()`: a representacao
    padrao de um objeto Python nao promete estabilidade entre versoes, e um
    prompt que muda sozinho e um cache que nunca acerta.
    """
    pergunta = {
        "text": question.text,
        "as_of": question.as_of,
        "requested_output": question.requested_output,
        "pinned_entities": question.pinned_entities,
        "pinned_periods": question.pinned_periods,
        "pinned_scope": question.pinned_scope,
        "constraints": question.constraints,
    }
    return (
        "PERGUNTA\n"
        f"{canonical_bytes(pergunta).decode('utf-8')}\n\n"
        "CAPACIDADES DISPONIVEIS\n"
        f"{canonical_bytes(_snapshot_payload(snapshot)).decode('utf-8')}\n"
    )


def build_request(
    question: ResearchQuestion,
    snapshot: CapabilitySnapshot,
    *,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> LLMRequest:
    """Monta a chamada. Separado de `plan_question` para poder ser inspecionado
    - e hasheado - sem chamar modelo nenhum.

    `model` nao tem default: escolher o modelo e decisao de produto, e um
    default aqui viraria configuracao global escondida numa biblioteca.
    """
    return LLMRequest(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(question, snapshot),
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )


def parse_plan(response: LLMResponse, question: ResearchQuestion) -> ResearchPlan:
    """Texto -> `ResearchPlan`. Recusa em vez de consertar.

    `question_id` e injetado, e nao pedido ao modelo: e funcao pura da
    pergunta, entao pedi-lo seria pedir um sha256 a quem nao consegue
    calcular um. Se o modelo emitir mesmo assim, a resposta e recusada em vez
    de sobrescrita - sobrescrever esconderia um modelo confuso sobre
    identidade, e o proximo sintoma apareceria mais longe da causa.
    """
    # Antes do parse, de proposito: um JSON cortado ao meio tambem levanta
    # `JSONDecodeError`, e sairia como MALFORMED_JSON - o mesmo nome que "o
    # modelo escreveu prosa em vez de JSON". Duas causas com remedios opostos
    # ("aumente o teto" e "conserte o prompt") sob um nome so e a definicao de
    # achado ruim, e o pior tipo deles: intermitente, porque depende do
    # tamanho do plano do dia.
    if response.stop_reason == "max_tokens":
        raise PlannerError(
            PlannerFailure.TRUNCATED_RESPONSE,
            "a geracao atingiu o teto de tokens antes de fechar o JSON; "
            "aumente max_tokens",
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
            f"a resposta e {type(payload).__name__}, e um plano e um objeto",
            response_sha256=response.response_sha256,
        )

    if "question_id" in payload:
        raise PlannerError(
            PlannerFailure.QUESTION_ID_EMITTED,
            "o modelo emitiu question_id, que e derivado da pergunta e nao "
            "escolha dele",
            response_sha256=response.response_sha256,
        )

    try:
        return ResearchPlan.model_validate({**payload, "question_id": question_id(question)})
    except ValidationError as exc:
        raise PlannerError(
            PlannerFailure.CONTRACT_VIOLATION,
            f"a resposta nao satisfaz a gramatica de plano: {exc.error_count()} erro(s)",
            response_sha256=response.response_sha256,
            detail=str(exc),
        ) from exc


def plan_question(
    question: ResearchQuestion,
    snapshot: CapabilitySnapshot,
    *,
    llm: LLMClient,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> PlannerOutcome:
    """Uma pergunta, uma chamada, um plano - ou um erro nomeado.

    O planejador nao carimba o relogio. `called_at` vem da resposta, porque so
    quem produziu a resposta sabe quando ela foi feita (M-1). Isso resolve a
    pendencia deixada no M2.1: uma resposta servida do cache carrega o instante
    da chamada *original*, e carimbar "agora" aqui faria o manifesto afirmar
    uma chamada que nao aconteceu - com a aparencia de rastro.

    Consequencia para teste: quem quer fixar o instante configura o cliente, e
    nao esta funcao. E mais fiel, porque e assim que funciona de verdade.
    """
    request = build_request(
        question,
        snapshot,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )

    response = llm.complete(request)  # uma chamada, sem laco e sem retentativa
    plan = parse_plan(response, question)

    provenance = PlanProvenance(
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

    return PlannerOutcome(plan=plan, provenance=provenance, response=response)

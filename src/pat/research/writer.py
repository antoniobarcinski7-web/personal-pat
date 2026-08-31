"""Resultados deterministicos -> prosa. O segundo e ultimo uso de LLM.

A simetria com o planejador e exata, e nao estetica:

    Planejador  texto -> ESTRUTURA   (escolhe o que calcular; nao calcula)
    Escritor    ESTRUTURA -> texto   (escolhe o que dizer; nao calcula)

Nos dois casos o modelo fica de fora do caminho do numero. No planejador
porque a gramatica de plano nao tem onde escrever um valor; aqui por uma razao
mais forte, que e o ponto deste modulo:

    o escritor nunca ve um numero.

Nao "e instruido a nao copiar": nao recebe. O prompt e montado a partir de
`render.describe()` - rotulo, metrica, escopo, periodo, datas - e da lista de
*tokens* disponiveis. O valor formatado (`NumericClaim.rendered_value`) nunca
entra no texto enviado. Nao da para copiar um numero que nunca foi mostrado, e
isso e verificavel: `tests/research/test_writer.py` confere que nenhum valor
renderizado aparece no prompt.

O modelo escreve `{{s:margem_fy2024}}`; `answer.substitute` troca pelo valor
que o executor produziu. O caminho do numero vai do motor ao texto final sem
passar por um modelo em nenhum ponto - e por isso "preservar exatamente os
valores do executor" nao e uma promessa a conferir, e uma propriedade da
montagem.

A regra do digito ja existia
----------------------------
`answer.check_prose` foi escrita no Milestone 1 e exercitada pela prosa
deterministica desde entao, de proposito: o portao que o escritor enfrenta
hoje ja estava em producao antes de existir escritor. Este modulo nao afrouxa
nem duplica a regra - chama a mesma funcao e da nome proprio a rejeicao
(`WriterFailure.PROSE_REJECTED`), para que a falha seja atribuivel ao modelo
em vez de sair como uma excecao de montagem tres camadas adiante.

Duas classes de afirmacao, e nenhuma terceira
---------------------------------------------
`NumericClaim` carrega numero e sai de `render.py`; `InterpretiveClaim` carrega
leitura, nao tem campo de valor e e o que o escritor pode produzir. E a
separacao que impede "a margem caiu por alavancagem operacional" de virar
indistinguivel de "a margem foi 3,89%".

Uma interpretacao cita `step_id`, e a traducao para `result_id` acontece aqui.
Pedir o sha256 ao modelo seria pedir um hash a quem nao consegue calcular um -
o mesmo argumento que faz o planejador injetar `question_id` em vez de pedi-lo.

Sem retentativa
---------------
Pela razao de sempre: uma segunda chamada e um segundo caminho de influencia,
que teria que ser hasheado e manifestado por conta propria. Prosa recusada
pela regra do digito e um erro nomeado, e nao uma primeira tentativa - "tentar
de novo ate passar" e como um portao vira laco de amostragem que uma hora
deixa passar algo errado-mas-aprovado.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, ValidationError

from pat.contracts.common import Frozen
from pat.contracts.research import (
    ComputationResult,
    InterpretiveClaim,
    PlanProvenance,
    ResearchPlan,
    ResearchQuestion,
)
from pat.research.answer import ProseRejected, check_prose, collect_warnings, substitution_table
from pat.research.canonical import canonical_bytes
from pat.research.llm import LLMClient, LLMRequest, LLMResponse
from pat.research.render import describe

__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT_S",
    "SYSTEM_PROMPT",
    "WriterError",
    "WriterFailure",
    "WriterOutcome",
    "build_request",
    "build_user_prompt",
    "evidence_payload",
    "parse_prose",
    "write_prose",
]


DEFAULT_TEMPERATURE: Decimal | None = None
"""Nenhuma preferencia de amostragem, pela mesma decisao do planejador.

`None` nao e "zero implicito": e a afirmacao de que quem escolhe a
configuracao de geracao e o adapter, que a declara em `client_fingerprint`.
Vale identico aqui - a reprodutibilidade do texto nao se apoia em temperatura,
se apoia no `response_sha256` gravado e na tabela de substituicao fechada."""

DEFAULT_MAX_TOKENS = 16384
"""Mesmo teto do planejador, e pela mesma razao: cobre raciocinio e texto
juntos, entao nao e orcamento de prosa. Truncamento tem nome proprio
(`WriterFailure.TRUNCATED_RESPONSE`) em vez de ser diagnosticado por teto
apertado."""

DEFAULT_TIMEOUT_S = 60


SYSTEM_PROMPT = """\
Voce e o redator de um sistema de pesquisa financeira. Sua unica funcao e \
transformar resultados JA CALCULADOS em um texto de research legivel.

REGRA CENTRAL
Voce nunca escreve numeros. Os valores foram calculados por um motor \
deterministico a partir de demonstracoes financeiras auditadas, e voce nao os \
recebe: no lugar de cada valor existe um TOKEN. Voce escreve o token; o \
sistema o substitui pelo numero depois. Um texto seu com um algarismo dentro \
e recusado inteiro.

ISSO INCLUI PERIODOS. Nao escreva "2024" nem "31/12/2024": use o token de \
periodo correspondente, que esta na lista de tokens disponiveis.

TOKENS
Existem dois tipos, e voce so pode usar os que aparecem em "available_tokens":

  {{s:<step_id>}}  o valor calculado naquele passo
  {{p:<rotulo>}}   um rotulo de periodo

Token que nao esta na lista faz o texto ser recusado. Nao invente token, nao \
altere um step_id e nao componha token novo.

O QUE VOCE RECEBE
Para cada resultado: o "step_id", o token que o cita, e "means" - o que aquele \
numero E (metrica, versao, escopo, periodo, data de conhecimento, AS OF), sem \
o valor. Use "means" para escrever a frase certa em volta do token; nunca \
copie datas de "means" para o texto como algarismo.

SAIDA
Responda com um unico objeto JSON e nada mais. Sem texto antes ou depois, sem \
comentario, sem markdown, sem cerca de codigo.

{
  "prose": "<o texto de research, com tokens no lugar dos numeros>",
  "interpretations": [
    {"text": "<uma leitura, sem numero e sem token>",
     "supports": ["<step_id em que a leitura se apoia>", ...]}
  ]
}

PROSE
Um ou dois paragrafos curtos. Precisa citar pelo menos um {{s:...}} - um texto \
sem numero citado e interpretacao sem base, e sera recusado. Diga o que o \
numero e antes de cita-lo: qual metrica, de que periodo, em que escopo.

INTERPRETATIONS
Opcional; lista vazia e resposta valida e preferivel a leitura inventada. Cada \
item e uma AFIRMACAO SUA - uma leitura do que os numeros mostram - e fica \
registrada separada da prosa justamente para que ninguem a confunda com um \
fato medido. Nao ponha numero nem token em "text"; a ligacao com o numero e \
feita por "supports", que so aceita step_id que existe nos resultados.

O QUE VOCE NAO PODE FAZER
- Nao escreva algarismo nenhum, em lugar nenhum do texto.
- Nao estime, arredonde, converta moeda, some, subtraia nem compare valores \
que voce nao recebeu. Voce nao tem os valores.
- Nao afirme causa que os resultados nao mostram ("caiu por causa de X").
- Nao invente metrica, empresa, periodo ou resultado ausente.
- Nao recomende compra, venda ou posicao.
- Se os resultados nao respondem a pergunta, diga isso na prosa em vez de \
completar a lacuna.

RESSALVAS
"warnings" lista ressalvas que os resultados carregam (fidelidade aproximada, \
mapeamento nao conferido, check que falhou). Elas ja saem na resposta final \
por conta propria - voce nao precisa reproduzi-las, mas nao escreva nada que \
as contradiga.
"""


class WriterFailure(StrEnum):
    """Por que a resposta nao virou prosa. Sempre um destes, nunca uma
    excecao crua de biblioteca."""

    TRUNCATED_RESPONSE = "truncated_response"
    MALFORMED_JSON = "malformed_json"
    NOT_AN_OBJECT = "not_an_object"
    CONTRACT_VIOLATION = "contract_violation"
    PROSE_REJECTED = "prose_rejected"
    UNKNOWN_STEP_REFERENCE = "unknown_step_reference"


class WriterError(RuntimeError):
    """O texto do modelo nao pode virar resposta.

    Carrega `response_sha256` pela razao do `PlannerError`: o texto cru fica
    recuperavel pelo hash (no cache), entao o diagnostico nao depende de a
    mensagem de erro ter copiado o suficiente da resposta.
    """

    def __init__(
        self,
        failure: WriterFailure,
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
class WriterOutcome:
    """O que o modelo escreveu, e de onde veio.

    `prose` sai daqui COM os tokens, ainda nao substituidos. E deliberado: e
    isto que o modelo de fato escreveu, e e o que se quer ler ao auditar. A
    substituicao e de `answer.build_answer`, que confere a regra do digito de
    novo por conta propria - ele nao confia em quem chama, e o mesmo texto
    passando duas vezes pelo mesmo portao e idempotente.

    A resposta final nao e montada aqui porque `ResearchAnswer` exige
    `manifest_id`, e o manifesto so fecha depois de conhecer esta procedencia.
    A ordem - executar, escrever, manifestar, responder - mora na raiz de
    composicao, que e quem tem as tres pontas.
    """

    prose: str
    interpretations: tuple[InterpretiveClaim, ...]
    provenance: PlanProvenance
    response: LLMResponse


# ---------------------------------------------------------------------------
# O que o modelo ve
# ---------------------------------------------------------------------------


class _Interpretation(Frozen):
    """A leitura como o modelo a escreve: cita `step_id`, nunca `result_id`."""

    text: str = Field(min_length=1)
    supports: tuple[str, ...] = Field(min_length=1)


class _WriterPayload(Frozen):
    """A forma de fio desta chamada - nao um contrato do projeto.

    Mora aqui, e nao em `contracts/`, porque descreve o que ESTE prompt pede.
    `extra="forbid"` vem de `Frozen`: um campo a mais na resposta ("value",
    "summary_number") falha na fronteira, que e exatamente onde se quer ver um
    modelo tentando devolver algo que nao lhe foi pedido.
    """

    prose: str = Field(min_length=1)
    interpretations: tuple[_Interpretation, ...] = ()


def evidence_payload(
    question: ResearchQuestion,
    plan: ResearchPlan,
    results: tuple[ComputationResult, ...],
    table: dict[str, str],
) -> dict:
    """Tudo que o modelo recebe, e nada mais.

    O recorte e a peca central do modulo, entao vale dizer o que fica de fora
    e por que:

    - **O valor.** Nem cru (`result.value`) nem formatado
      (`NumericClaim.rendered_value`). Do dicionario de substituicao entram as
      CHAVES - o universo de tokens permitido -, nunca os valores.
    - **A mensagem do aviso.** `WarningKind.CHECK_FAILED` traz `observed` e
      `expected` no texto, que sao numeros do motor. Mostrar a mensagem daria
      ao modelo um numero para copiar; `kind` sozinho ja permite ressalvar. O
      aviso completo chega ao leitor de qualquer jeito - `build_answer` o
      recolhe de novo, direto dos resultados.
    - **`result_id`.** Nao e escolha do modelo e ele nao teria o que fazer com
      um sha256; a ligacao entre leitura e resultado e feita por `step_id` e
      traduzida na volta.
    """
    por_result = {result.result_id: result.step_id for result in results}

    return {
        "question": question.text,
        "requested_output": question.requested_output,
        "objective": plan.objective,
        "as_of": plan.as_of,
        "scope": plan.scope,
        "results": [
            {
                "step_id": result.step_id,
                "token": f"{{{{s:{result.step_id}}}}}",
                "means": describe(result),
                "unit": result.currency,
                "fidelity": result.fidelity,
            }
            for result in results
        ],
        "available_tokens": sorted(table),
        "warnings": [
            {"kind": warning.kind, "step_id": por_result.get(warning.result_id or "")}
            for warning in collect_warnings(results)
        ],
        "assumptions": list(plan.assumptions),
    }


def build_user_prompt(
    question: ResearchQuestion,
    plan: ResearchPlan,
    results: tuple[ComputationResult, ...],
    table: dict[str, str],
) -> str:
    """A evidencia em forma estavel.

    Passa pela canonicalizacao do projeto - chaves ordenadas, sem espaco,
    `Decimal` como string - pela razao do planejador: um prompt que muda
    sozinho e um cache que nunca acerta, e `repr()` nao promete estabilidade
    entre versoes de Python.
    """
    return (
        "RESULTADOS DA PESQUISA\n"
        f"{canonical_bytes(evidence_payload(question, plan, results, table)).decode('utf-8')}\n"
    )


def build_request(
    question: ResearchQuestion,
    plan: ResearchPlan,
    results: tuple[ComputationResult, ...],
    table: dict[str, str],
    *,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> LLMRequest:
    """Monta a chamada. Separada de `write_prose` para poder ser inspecionada -
    e hasheada - sem chamar modelo nenhum.

    `model` nao tem default: escolher o modelo e decisao de produto, e um
    default aqui viraria configuracao global escondida numa biblioteca.
    """
    return LLMRequest(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(question, plan, results, table),
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )


# ---------------------------------------------------------------------------
# O que volta
# ---------------------------------------------------------------------------


def parse_prose(
    response: LLMResponse,
    results: tuple[ComputationResult, ...],
    table: dict[str, str],
) -> tuple[str, tuple[InterpretiveClaim, ...]]:
    """Texto -> prosa conferida + leituras. Recusa em vez de consertar.

    Nenhum ramo aqui tenta salvar uma resposta: sem remocao de cerca de
    codigo, sem "extrair o primeiro objeto JSON", sem apagar o algarismo que
    escapou. Todo conserto seria o sistema adivinhando o que o modelo quis
    dizer, e a diferenca entre um numero certo e um errado nao e adivinhavel.
    """
    # Antes do parse, de proposito: um JSON cortado ao meio tambem levanta
    # `JSONDecodeError`, e sairia como MALFORMED_JSON - o mesmo nome que "o
    # modelo escreveu prosa em vez de JSON". Duas causas com remedios opostos
    # ("aumente max_tokens" e "conserte o prompt") sob um nome so e a definicao
    # de achado ruim.
    if response.stop_reason == "max_tokens":
        raise WriterError(
            WriterFailure.TRUNCATED_RESPONSE,
            "a geracao atingiu o teto de tokens antes de fechar o JSON; aumente max_tokens",
            response_sha256=response.response_sha256,
            detail=response.text[-200:],
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
            WriterFailure.NOT_AN_OBJECT,
            f"a resposta e {type(payload).__name__}, e a saida do escritor e um objeto",
            response_sha256=response.response_sha256,
        )

    try:
        escrito = _WriterPayload.model_validate(payload)
    except ValidationError as exc:
        raise WriterError(
            WriterFailure.CONTRACT_VIOLATION,
            f"a resposta nao satisfaz a forma pedida: {exc.error_count()} erro(s)",
            response_sha256=response.response_sha256,
            detail=str(exc),
        ) from exc

    # O portao do Milestone 1, sem afrouxamento e sem segunda implementacao.
    # So o nome da falha e do escritor - a regra e a mesma que a prosa
    # deterministica atravessa desde antes de existir escritor.
    try:
        check_prose(escrito.prose, table)
    except ProseRejected as exc:
        raise WriterError(
            WriterFailure.PROSE_REJECTED,
            f"a prosa nao passou na regra do digito ({exc.code})",
            response_sha256=response.response_sha256,
            detail=exc.detail,
        ) from exc

    return escrito.prose, _to_claims(escrito.interpretations, results, response)


def _to_claims(
    interpretations: tuple[_Interpretation, ...],
    results: tuple[ComputationResult, ...],
    response: LLMResponse,
) -> tuple[InterpretiveClaim, ...]:
    """`step_id` -> `result_id`, recusando referencia que nao existe.

    Uma leitura apoiada num passo inventado e uma leitura sem base, e deixar
    passar (ignorando o item, ou gravando a lista vazia) produziria uma
    afirmacao que PARECE citada. Rastro que nao bate e pior do que rastro
    nenhum, porque parece prova.
    """
    por_step = {result.step_id: result.result_id for result in results}
    claims: list[InterpretiveClaim] = []

    for item in interpretations:
        desconhecidos = [step for step in item.supports if step not in por_step]
        if desconhecidos:
            raise WriterError(
                WriterFailure.UNKNOWN_STEP_REFERENCE,
                f"a leitura cita passo inexistente {desconhecidos}; "
                f"os passos deste plano sao {sorted(por_step)}",
                response_sha256=response.response_sha256,
                detail=item.text,
            )
        claims.append(
            InterpretiveClaim(
                text=item.text,
                supports=tuple(por_step[step] for step in item.supports),
            )
        )

    return tuple(claims)


# ---------------------------------------------------------------------------
# A chamada
# ---------------------------------------------------------------------------


def write_prose(
    question: ResearchQuestion,
    plan: ResearchPlan,
    results: tuple[ComputationResult, ...],
    *,
    llm: LLMClient,
    model: str,
    capability_sha256: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Decimal | None = DEFAULT_TEMPERATURE,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> WriterOutcome:
    """Resultados -> prosa. Uma chamada, sem laco e sem retentativa.

    `results` sao os do executor, ja calculados: este modulo nao recebe
    conexao, nao recebe motor e nao tem como produzir um `ComputationResult`.
    Recebe o que existe ou nao escreve.

    `capability_sha256` e parametro porque `PlanProvenance` o exige e o
    escritor nao ve o snapshot - ele nao precisa saber o que o sistema SABERIA
    fazer, so o que de fato foi feito. O valor e o da corrida a que este texto
    pertence, repassado por quem a conduziu.

    O relogio nao e carimbado aqui: `called_at` vem da resposta, porque so quem
    a produziu sabe quando ela foi feita. Resposta servida do cache carrega o
    instante da chamada original (M-1).
    """
    table = substitution_table(results)
    request = build_request(
        question,
        plan,
        results,
        table,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )

    response = llm.complete(request)  # uma chamada, sem laco e sem retentativa
    prose, interpretations = parse_prose(response, results, table)

    provenance = PlanProvenance(
        model_id=response.model_id,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        system_prompt_sha256=request.system_prompt_sha256,
        prompt_sha256=request.prompt_sha256,
        response_sha256=response.response_sha256,
        capability_sha256=capability_sha256,
        called_at=response.called_at,
        cached=response.cached,
        client_fingerprint=llm.fingerprint,
    )

    return WriterOutcome(
        prose=prose,
        interpretations=interpretations,
        provenance=provenance,
        response=response,
    )

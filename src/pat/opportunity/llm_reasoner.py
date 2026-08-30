"""`LlmReasoner` - o raciocinador que o `Reasoner` Protocol estava esperando.

O `ShapeReasoner` e o piso: planeja por tabela, descreve forma, e nunca diz por
que. Isso e deliberado e continua valendo. O que falta e o andar de cima - ler
um resultado, notar que a explicacao da administracao nao bate com o numero,
propor a hipotese que ninguem escreveu. E o que este modulo faz.

O que o modelo pode e o que ele nao pode
----------------------------------------
Ele decide o que PERGUNTAR e o que os resultados QUEREM DIZER. Ele nunca
produz um numero - e a garantia nao e o prompt, e o tipo:

- `plan` devolve `StepRequest`, que nao tem campo de valor. O passo pergunta;
  quem responde e o motor deterministico.
- `interpret` devolve `FindingProposal`, que tem `text` e `cites` e nao tem
  `value`, `unit` nem `currency` - a mesma separacao de tipo de `QuoteClaim`
  na Fase 5. Uma frase com um numero dentro dela e prosa sobre um numero que o
  motor calculou, e o ID citado permite conferir.
- `judge` devolve um estado de um conjunto fechado, com razao.

Por que o passo invalido NAO e filtrado aqui
---------------------------------------------
A tentacao e conferir cada `ref` contra `metrics_available` e descartar o que
nao casa. Nao fazemos, e a razao e a mesma que vale para o resto do sistema: um
passo descartado em silencio some do relatorio, e some justamente a informacao
de que o raciocinador pediu o que nao existe. Deixado passar, ele vira um
`StepOutcome` com `unavailable` nomeando o motivo - visivel, contavel, e no
mesmo lugar onde toda outra recusa do PAT aparece.

O que E conferido, e antes de qualquer chamada, e a GRAMATICA: `StepRequest`
valida sozinho que metrica tem periodo, que decomposicao tem as duas pontas e
que busca tem termo. Um passo que nao satisfaz isso nao chega ao motor.

E a citacao inventada ja tem dono: `_verify` em `research.py` barra e
REGISTRA, como `RejectedFinding`. Este modulo nao repete essa checagem - duas
implementacoes da mesma regra divergem, e a que diverge para o lado permissivo
e a que ninguem nota.

Falha do modelo nao vira silencio
----------------------------------
Nao ha degradacao automatica para o `ShapeReasoner`. JSON malformado, resposta
truncada ou contrato violado levantam `LlmReasonerError`, com o motivo
nomeado. Um fallback silencioso produziria uma investigacao metade de um
raciocinador e metade de outro, com um `reasoner_id` so - e o relatorio diria
que um modelo concluiu o que uma tabela concluiu. Quem escolhe o raciocinador
e a raiz de composicao, e a escolha aparece no relatorio.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import ValidationError

from pat.contracts.opportunity import ResearchTask
from pat.contracts.opportunity.loop import (
    FindingProposal,
    HypothesisProposal,
    StepOutcome,
    StepRequest,
    TaskProposal,
    TaskVerdict,
)
from pat.opportunity.reason import ResearchContext
from pat.research.llm import LLMClient, LLMRequest, LLMResponse

__all__ = [
    "LlmReasoner",
    "LlmReasonerError",
    "LlmReasonerFailure",
    "SYSTEM_PROMPT",
    "build_decompose_prompt",
    "build_interpret_prompt",
    "build_judge_prompt",
    "build_plan_prompt",
]

DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = Decimal("0")
DEFAULT_TIMEOUT_S = 120

REASONER_VERSION = "v1"


class LlmReasonerFailure(StrEnum):
    """Por que a resposta do modelo nao virou raciocinio.

    Nomes separados porque os remedios sao opostos: `TRUNCATED_RESPONSE` pede
    teto maior e `MALFORMED_JSON` pede prompt melhor. Sob um nome so, o achado
    seria intermitente - dependeria do tamanho da resposta do dia.
    """

    TRUNCATED_RESPONSE = "truncated_response"
    MALFORMED_JSON = "malformed_json"
    NOT_AN_OBJECT = "not_an_object"
    CONTRACT_VIOLATION = "contract_violation"
    EMPTY_RESULT = "empty_result"


class LlmReasonerError(Exception):
    """Recusa nomeada, no mesmo formato de `MetricUnavailable`.

    Carrega o sha256 da resposta para que o caso seja reencontravel no cache
    sem depender de alguem ter copiado o texto na hora.
    """

    def __init__(
        self,
        failure: LlmReasonerFailure,
        message: str,
        *,
        response_sha256: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure = failure
        self.message = message
        self.response_sha256 = response_sha256
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.failure.value}: {self.message}"


# ---------------------------------------------------------------------------
# O prompt de sistema
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Voce raciocina dentro do PAT, um sistema de pesquisa de investimento cujos
numeros sao todos calculados por um motor deterministico. Voce NAO calcula.

Regras que nao se negociam:

1. Voce nunca produz um numero novo. Nem estimativa, nem arredondamento, nem
   conversao de moeda, nem soma de dois numeros que lhe mostraram. Se voce
   precisa de um valor, peca um passo que o motor responde.

2. Voce so cita identificadores que aparecem nos resultados que lhe foram
   mostrados nesta tarefa. Citar um ID que nao existe e barrado e registrado
   contra voce. Se nao ha o que citar, escreva menos.

3. Voce nao diz que algo aconteceu por um motivo que os dados nao mostram.
   Voce pode escrever que a administracao AFIRMA uma causa, citando o trecho.
   "A margem caiu por pressao competitiva" sem trecho que sustente e invencao.

4. Ausencia e informacao. Um passo que voltou indisponivel nao vira silencio:
   diga o que faltou e o que resolveria.

5. Responda SOMENTE com um objeto JSON valido, sem cerca de codigo e sem
   comentario antes ou depois.
"""


# ---------------------------------------------------------------------------
# Prompts. Puros, para poderem ser inspecionados e hasheados sem chamar modelo
# ---------------------------------------------------------------------------


def _datas(datas: tuple[date, ...]) -> list[str]:
    return [d.isoformat() for d in datas]


def _contexto_json(context: ResearchContext) -> dict:
    """O que o modelo pode saber ANTES de perguntar.

    Nao carrega valor nenhum, pela mesma razao que `ResearchContext` nao
    carrega: um contexto com numeros faria o planejamento depender do que ja
    se sabe, e o passo seguinte seria "planejar" um numero ja visto.
    """
    return {
        "empresa": context.company.display_name,
        "as_of": context.as_of.isoformat(),
        "mandato": context.mandate,
        "metricas_disponiveis": list(context.metrics_available),
        "decomposicoes_disponiveis": list(context.decompositions_available),
        "periodos_anuais": _datas(context.annual_periods),
        "periodos_instantaneos": _datas(context.instant_periods),
        "documentos_no_corpus": context.documents,
        "conceitos_ausentes": list(context.missing_concepts),
        "perguntas_abertas": list(context.open_questions),
    }


def build_decompose_prompt(*, objective: str, context: ResearchContext) -> str:
    return (
        "Decomponha o objetivo em tarefas de pesquisa e, quando fizer sentido, "
        "em hipoteses.\n\n"
        f"OBJETIVO: {objective}\n\n"
        f"CONTEXTO:\n{json.dumps(_contexto_json(context), ensure_ascii=False, indent=2)}\n\n"
        "Uma tarefa so vale a pena se as metricas ou os documentos disponiveis "
        "conseguem responde-la. Uma hipotese e uma afirmacao sobre o mundo COM "
        "falsificador: o que teria que ser verdade para ela estar errada. "
        "Hipotese sem falsificador nao e hipotese, e opiniao.\n\n"
        "Responda com JSON:\n"
        '{"tasks": [{"slug": "kebab-case", "objective": "...", '
        '"completion_criteria": "como saber que terminou", '
        '"priority": "high|normal|low", "depends_on": ["slug"], '
        '"hypothesis": "slug ou null"}], '
        '"hypotheses": [{"slug": "kebab-case", "statement": "...", '
        '"falsifiers": ["o que a refutaria"]}]}'
    )


def build_plan_prompt(*, task: ResearchTask, context: ResearchContext) -> str:
    return (
        "Planeje os passos que respondem a esta tarefa.\n\n"
        f"TAREFA: {task.objective}\n"
        f"CRITERIO DE CONCLUSAO: {task.completion_criteria}\n\n"
        f"CONTEXTO:\n{json.dumps(_contexto_json(context), ensure_ascii=False, indent=2)}\n\n"
        "A gramatica e FECHADA. Quatro tipos de passo:\n"
        '- "metric": exige `ref` ("nome@versao") e `period_ends` (lista de datas).\n'
        '- "breakdown": exige `ref`, `period_from` e `period_to`.\n'
        '- "evidence": exige `terms` (lista). Aceita `sections` e `limit`.\n'
        '- "accounts": exige `statement` e `period_ends`.\n\n'
        "Peca metrica de fluxo (receita, EBITDA, lucro) nos periodos anuais e "
        "metrica de saldo (caixa, divida, patrimonio) nos instantaneos. Peca "
        "varios periodos no mesmo passo: uma serie diz o que um ponto nao diz.\n\n"
        "Responda com JSON:\n"
        '{"steps": [{"step_id": "kebab-case", "kind": "metric", '
        '"rationale": "por que este passo responde a tarefa", "ref": "...", '
        '"period_ends": ["AAAA-MM-DD"], "terms": [], "sections": [], '
        '"limit": 5}]}'
    )


def _resultado_json(outcome: StepOutcome) -> dict:
    return {
        "step_id": outcome.step_id,
        "kind": outcome.kind.value,
        "ref": outcome.ref,
        "ids_citaveis": sorted(outcome.citable_ids),
        "linhas": list(outcome.rendered),
        "forma_da_serie": outcome.shape_hint,
        "indisponivel": list(outcome.unavailable),
    }


def build_interpret_prompt(
    *,
    task: ResearchTask,
    outcomes: tuple[StepOutcome, ...],
    context: ResearchContext,
) -> str:
    resultados = [_resultado_json(o) for o in outcomes]
    return (
        "Interprete o que os passos produziram.\n\n"
        f"TAREFA: {task.objective}\n"
        f"CRITERIO DE CONCLUSAO: {task.completion_criteria}\n"
        f"EMPRESA: {context.company.display_name}  AS OF: {context.as_of.isoformat()}\n\n"
        f"RESULTADOS:\n{json.dumps(resultados, ensure_ascii=False, indent=2)}\n\n"
        "Cada achado e uma frase que se sustenta sozinha e cita, em `cites`, os "
        "IDs em `ids_citaveis` que a sustentam. So esses IDs existem.\n\n"
        "Quando `forma_da_serie` disser que a serie NAO e monotonica, nao "
        "descreva a serie pelas pontas: o caminho e a informacao.\n"
        "Quando algo estiver em `indisponivel`, diga o que faltou - ausencia "
        "nomeada vale mais que silencio.\n\n"
        "Responda com JSON:\n"
        '{"findings": [{"text": "...", "cites": ["id"]}]}'
    )


def build_judge_prompt(
    *,
    task: ResearchTask,
    outcomes: tuple[StepOutcome, ...],
    findings: tuple[FindingProposal, ...],
    context: ResearchContext,
) -> str:
    return (
        "A tarefa cumpriu o criterio de conclusao dela?\n\n"
        f"TAREFA: {task.objective}\n"
        f"CRITERIO DE CONCLUSAO: {task.completion_criteria}\n\n"
        f"PASSOS: {json.dumps([_resultado_json(o) for o in outcomes], ensure_ascii=False)}\n"
        f"ACHADOS: {json.dumps([f.text for f in findings], ensure_ascii=False)}\n\n"
        "Estados: `complete` se o criterio foi cumprido; `blocked` se depende "
        "de algo que o SISTEMA resolve sozinho (ingerir dado, sincronizar "
        "documento); `needs_human` se depende de decisao que nao e sua - "
        "escolher a linha de um plano de contas, aceitar uma premissa. "
        "`blocked` e `needs_human` exigem `blocker_reason` e `blocker_remedy`.\n\n"
        "Nao declare `complete` porque houve resultado. Declare porque o "
        "criterio foi cumprido.\n\n"
        "Responda com JSON:\n"
        '{"status": "complete", "rationale": "...", "blocker_reason": null, '
        '"blocker_remedy": null, "needs_human": false}'
    )


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _payload(response: LLMResponse) -> dict:
    """Texto -> objeto. Recusa em vez de consertar.

    A checagem de truncamento vem ANTES do parse de proposito: um JSON cortado
    ao meio tambem levanta `JSONDecodeError`, e sairia como "o modelo escreveu
    prosa" - remedio oposto ao certo.
    """
    if response.stop_reason == "max_tokens":
        raise LlmReasonerError(
            LlmReasonerFailure.TRUNCATED_RESPONSE,
            "a geracao atingiu o teto de tokens antes de fechar o JSON; "
            "aumente max_tokens",
            response_sha256=response.response_sha256,
            detail=response.text[-200:],
        )
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise LlmReasonerError(
            LlmReasonerFailure.MALFORMED_JSON,
            f"a resposta nao e JSON: {exc}",
            response_sha256=response.response_sha256,
            detail=response.text[:200],
        ) from exc
    if not isinstance(payload, dict):
        raise LlmReasonerError(
            LlmReasonerFailure.NOT_AN_OBJECT,
            f"a resposta e {type(payload).__name__}, e o esperado e um objeto",
            response_sha256=response.response_sha256,
        )
    return payload


def _validar(modelo, itens, *, o_que: str, sha: str | None) -> tuple:
    """Lista crua -> tupla de contratos. Um item invalido reprova a resposta.

    Nao pulamos o item ruim: uma agenda a que faltou a tarefa que o modelo
    propos, sem nada dizendo isso, e pior que uma recusa - ela parece
    completa.
    """
    if itens is None:
        return ()
    if not isinstance(itens, list):
        raise LlmReasonerError(
            LlmReasonerFailure.CONTRACT_VIOLATION,
            f"{o_que} deveria ser uma lista, e veio {type(itens).__name__}",
            response_sha256=sha,
        )
    try:
        return tuple(modelo.model_validate(item) for item in itens)
    except ValidationError as exc:
        raise LlmReasonerError(
            LlmReasonerFailure.CONTRACT_VIOLATION,
            f"{o_que} nao satisfaz a gramatica: {exc.error_count()} erro(s)",
            response_sha256=sha,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# O raciocinador
# ---------------------------------------------------------------------------


@dataclass
class LlmReasoner:
    """Raciocinador de modelo, atras do mesmo Protocol do `ShapeReasoner`.

    `model` nao tem default: escolher o modelo e decisao de produto, e um
    default aqui viraria configuracao global escondida numa biblioteca.
    """

    client: LLMClient
    model: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: Decimal | None = DEFAULT_TEMPERATURE
    timeout_s: int = DEFAULT_TIMEOUT_S

    @property
    def reasoner_id(self) -> str:
        """Quem raciocinou, com a configuracao junto.

        O `fingerprint` do cliente entra porque uma conclusao produzida sob
        outra configuracao de geracao e outra conclusao - a mesma razao pela
        qual o cache da Fase 3 indexa por ele e nao so pelo prompt.
        """
        return f"llm/{REASONER_VERSION}/{self.model}/{self.client.fingerprint}"

    def _pergunta(self, prompt: str) -> dict:
        request = LLMRequest(
            system=SYSTEM_PROMPT,
            user=prompt,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            timeout_s=self.timeout_s,
        )
        return _payload(self.client.complete(request))

    def decompose(
        self, *, objective: str, context: ResearchContext
    ) -> tuple[tuple[TaskProposal, ...], tuple[HypothesisProposal, ...]]:
        payload = self._pergunta(
            build_decompose_prompt(objective=objective, context=context)
        )
        tarefas = _validar(TaskProposal, payload.get("tasks"), o_que="tasks", sha=None)
        hipoteses = _validar(
            HypothesisProposal, payload.get("hypotheses"), o_que="hypotheses", sha=None
        )
        if not tarefas:
            raise LlmReasonerError(
                LlmReasonerFailure.EMPTY_RESULT,
                "o modelo nao propos nenhuma tarefa; sem agenda nao ha "
                "investigacao. Verifique se ha metrica ou documento disponivel "
                "para esta empresa nesta data.",
            )
        return tarefas, hipoteses

    def plan(
        self, *, task: ResearchTask, context: ResearchContext
    ) -> tuple[StepRequest, ...]:
        payload = self._pergunta(build_plan_prompt(task=task, context=context))
        return _validar(StepRequest, payload.get("steps"), o_que="steps", sha=None)

    def interpret(
        self,
        *,
        task: ResearchTask,
        outcomes: tuple[StepOutcome, ...],
        context: ResearchContext,
    ) -> tuple[FindingProposal, ...]:
        payload = self._pergunta(
            build_interpret_prompt(task=task, outcomes=outcomes, context=context)
        )
        return _validar(
            FindingProposal, payload.get("findings"), o_que="findings", sha=None
        )

    def judge(
        self,
        *,
        task: ResearchTask,
        outcomes: tuple[StepOutcome, ...],
        findings: tuple[FindingProposal, ...],
        context: ResearchContext,
    ) -> TaskVerdict:
        payload = self._pergunta(
            build_judge_prompt(
                task=task, outcomes=outcomes, findings=findings, context=context
            )
        )
        try:
            return TaskVerdict.model_validate(payload)
        except ValidationError as exc:
            raise LlmReasonerError(
                LlmReasonerFailure.CONTRACT_VIOLATION,
                f"o veredito nao satisfaz a gramatica: {exc.error_count()} erro(s)",
                detail=str(exc),
            ) from exc

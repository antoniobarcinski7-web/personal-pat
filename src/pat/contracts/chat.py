"""Contratos da camada conversacional (L4). Universais e sem LLM.

Nada neste modulo importa `pat.chat.*`, `pat.research.*` (fora dos contratos),
`pat.semantics.*`, `pat.query` ou `pat.store`. Ele so conhece
`contracts.common`, `contracts.semantics` e `contracts.research` - e e isso que
permite que a forma de um turno seja conferivel sem servidor, sem banco e sem
modelo.

A propriedade que este arquivo existe para garantir
---------------------------------------------------
Nenhum campo de `ConversationContext` ou `TurnPlanSummary` aceita `Decimal`, e
nenhum deriva de `ComputationResult`. O contexto conversacional atravessa a
fronteira do modelo - ele entra dentro do prompt do planejador. Se ele pudesse
carregar um valor, o numero de um turno anterior chegaria ao modelo, e a
afirmacao "todo numero vem do motor, recalculado neste turno" deixaria de ser
propriedade da forma do tipo e viraria promessa de prompt. E a mesma razao pela
qual `MetricStep` nao tem campo `Decimal`.

Consequencia pratica: quem quiser afrouxar isso vai ter que adicionar um campo
aqui, num arquivo que so tem contrato, e o diff vai aparecer.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen, Sha256
from pat.contracts.research import (
    DerivationOp,
    OutputKind,
    PlanProvenance,
    ResearchAnswer,
    ResearchPlan,
    ResearchQuestion,
    ResearchRunManifest,
)
from pat.contracts.semantics import ReportingScope

# ---------------------------------------------------------------------------
# Contexto conversacional
# ---------------------------------------------------------------------------


class TurnPlanSummary(Frozen):
    """O que UM turno anterior faz saber ao planejador.

    So estrutura: que empresas, que metricas, que periodos e que escopo foram
    planejados, e se o turno foi respondido ou recusado. Nada aqui e conteudo
    da resposta - nem prosa, nem claim, nem `result_id`, nem valor.

    A ausencia de `Decimal` e de qualquer coisa derivada de `ComputationResult`
    e conferida por AST em `tests/chat/test_contracts_chat.py`, com a mesma
    tecnica de `test_a_gramatica_de_plano_nao_tem_onde_por_um_numero`.
    """

    question_text: str = Field(min_length=1, description="O que o usuario digitou")
    objective: str | None = None
    """Frase do planejador, nao do escritor. Ela ja passou pela validacao do
    plano, e o plano nao tem onde por um numero."""

    entity_ids: tuple[str, ...] = ()
    metric_refs: tuple[str, ...] = Field(default=(), description="'ebitda@v1'")
    period_ends: tuple[date, ...] = ()
    scope: ReportingScope | None = None
    derivation_ops: tuple[DerivationOp, ...] = ()

    outcome: Literal["answered", "refused", "failed"]
    """Estado, nunca conteudo. `refused` e informacao util para o turno
    seguinte: replanejar identico o que ja foi recusado pelo mesmo motivo e o
    erro mais barato de evitar."""

    refusal_codes: tuple[str, ...] = ()
    """Codigos nomeados (`unknown_entity`), nunca texto livre de mensagem: as
    mensagens de `CHECK_FAILED` carregam `observed`/`expected`, que sao numeros
    do motor."""


class ConversationContext(Frozen):
    """A conversa ate aqui, na unica forma que o planejador ve.

    `as_of` e da sessao inteira, nao da mensagem: se cada turno escolhesse o
    seu, "isso mudou?" compararia duas visoes do mundo diferentes sem avisar
    ninguem. Trocar `as_of` abre sessao nova - o analogo conversacional do
    `AS OF` obrigatorio do I1.
    """

    context_version: Literal["v1"] = "v1"
    as_of: date
    turns: tuple[TurnPlanSummary, ...] = Field(max_length=8)
    """Teto no contrato, e nao so na janela de quem monta: um prompt que cresce
    sem limite muda de comportamento devagar, e o sintoma aparece longe da
    causa."""


# ---------------------------------------------------------------------------
# Pedido
# ---------------------------------------------------------------------------


class ChatRequest(Frozen):
    """Uma mensagem do usuario numa sessao.

    `session_id` nao vem do cliente por escolha livre: ele e emitido pelo
    servidor e conferido contra o padrao aqui, na fronteira de entrada, antes
    de qualquer chance de virar caminho de arquivo.
    """

    session_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    text: str = Field(min_length=1, max_length=2000)

    # `pinned_*` e o que o USUARIO fixou, e continua sendo whitelist soberana
    # (`validate.py` rejeita passo fora do pino). Contexto conversacional NAO
    # vira pino: `pinned_periods=(2024,)` herdado do turno anterior faria
    # "E como isso mudou desde 2023?" ser recusado com PIN_CONTRADICTED_PERIOD.
    # Heranca de contexto e assunto de `ConversationContext`; pino e assunto do
    # usuario.
    pinned_entities: tuple[str, ...] = ()
    pinned_periods: tuple[date, ...] = ()
    pinned_scope: ReportingScope | None = None

    requested_output: OutputKind = OutputKind.NARRATIVE


# ---------------------------------------------------------------------------
# Recusa
# ---------------------------------------------------------------------------


class RefusalKind(StrEnum):
    """Por que um turno nao virou resposta.

    Conjunto fechado e alinhado um a um com as falhas que a Fase 3 ja nomeia:
    nenhuma recusa da camada de chat inventa categoria propria, porque
    categoria propria seria reescrita de um motivo que ja tem nome.
    """

    PLANNER_UNRESOLVED = "planner_unresolved"
    """O modelo devolveu a duvida em vez de chutar."""

    PLAN_INVALID = "plan_invalid"
    """`PlanViolation`: falha estrutural, detectada sem banco e sem modelo."""

    PLAN_UNRESOLVABLE = "plan_unresolvable"
    """`ResolutionIssue`: o warehouse nao serve o que o plano pede."""

    METRIC_UNAVAILABLE = "metric_unavailable"
    """`ComputationFailure`: nunca zero, nunca parcial."""

    PLANNER_FAILED = "planner_failed"
    """`PlannerError`: o modelo nao produziu saida valida."""

    WRITER_FAILED = "writer_failed"
    """`WriterError`. Nao existe queda silenciosa para prosa deterministica:
    apresentar um texto que ninguem pediu como se fosse o pedido e pior que
    falhar."""


class TurnRefusal(Frozen):
    """Uma recusa apresentavel, sem perder o que a camada de baixo disse.

    `codes` e `remedies` viajam como vieram da Fase 3 - a recusa chega a tela
    sem reescrita, do mesmo jeito que `MetricUnavailable` chega a linha de
    comando.
    """

    kind: RefusalKind
    summary: str = Field(min_length=1)
    detail: str | None = None
    codes: tuple[str, ...] = ()
    remedies: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    """De `UnresolvedItem.candidates`: e o que permite desambiguar virando pino
    do usuario, em vez de o sistema escolher por ele."""


# ---------------------------------------------------------------------------
# Turno
# ---------------------------------------------------------------------------


class ProgramSummary(Frozen):
    """O que um programa investigou, na forma que a UI mostra.

    Resumo, e nao o programa inteiro: a tela precisa saber o que foi medido,
    decomposto e buscado, e o artefato completo continua recuperavel pelo
    `program_id`. Nenhum campo aqui carrega `Decimal` - pela mesma razao de
    sempre, o valor viaja pela substituicao de token e nao pelo contexto.
    """

    objective: str = Field(min_length=1)
    questions_to_answer: tuple[str, ...] = ()
    metric_steps: int = Field(default=0, ge=0)
    decompositions: tuple[str, ...] = ()
    """`ref` de cada decomposicao pedida, ex. 'ebit_by_line@v1'."""
    evidence_queries: tuple[tuple[str, str], ...] = ()
    """(termos, por que) de cada busca. O `rationale` do planejador."""
    claims: tuple[tuple[str, int], ...] = ()
    """(especie, quantidade) no grafo de afirmacoes."""


class ChatTurn(Frozen):
    """Um turno inteiro, do que foi perguntado ao que foi respondido.

    Guarda `plan`, `manifest` e as duas procedencias porque a conversa nao e um
    universo paralelo: o `manifest_id` daqui e o mesmo que
    `pat runs --research` consulta, e o par pergunta+plano e o mesmo formato
    que `pat ask --plan-file` reexecuta sem chamada de modelo nenhuma.
    """

    turn_index: int = Field(ge=0)
    session_id: str
    asked_at: AwareDatetime
    question: ResearchQuestion

    context_sha256: Sha256 | None = None
    """Sob que contexto esta pergunta foi interpretada. Existe porque
    `question_id` exclui o contexto por construcao: o mesmo texto em duas
    conversas tem o mesmo `question_id` e planos diferentes, e sem este campo a
    unica forma de responder "por que planos diferentes" seria recompor o
    prompt."""

    plan: ResearchPlan | None = None
    plan_id: Sha256 | None = None
    answer: ResearchAnswer | None = None
    manifest: ResearchRunManifest | None = None
    planner_provenance: PlanProvenance | None = None
    writer_provenance: PlanProvenance | None = None
    refusal: TurnRefusal | None = None
    elapsed_ms: int = Field(ge=0)

    # -- caminho do PROGRAMA (Fase 5) ---------------------------------------
    #
    # Campos opcionais, e nao um `ChatTurn` novo: um turno continua sendo um
    # turno, e a UI, o log de sessao e a auditoria nao deviam se bifurcar em
    # dois formatos por causa de qual caminho respondeu. O que muda e o que
    # esta preenchido.
    #
    # O caminho da Fase 3 (`plan` + `manifest`) continua valendo e continua
    # testado: ele e o que `pat ask --plan-file` reexecuta sem modelo.
    program_id: Sha256 | None = None
    program_summary: "ProgramSummary | None" = None
    prose: tuple[str, ...] = ()
    """O relatorio ja com os tokens substituidos. Vazio no caminho da Fase 3,
    onde a prosa mora em `answer.prose`."""

    critic_findings: tuple[tuple[str, str, str], ...] = ()
    """(severidade, codigo, mensagem) do critic mecanico. Ressalvas acompanham
    a resposta; achado duro vira recusa antes de chegar aqui."""

    @property
    def answered(self) -> bool:
        """Houve resposta, por qualquer um dos dois caminhos.

        Fase 3 responde em `answer`; Fase 5 responde em `prose`, porque o
        relatorio nasce de um grafo de afirmacoes e nao de um `ResearchAnswer`.
        A UI e a auditoria perguntam ISTO, e nao qual campo esta preenchido.
        """
        return self.answer is not None or bool(self.prose)

    @model_validator(mode="after")
    def _check(self) -> "ChatTurn":
        # Nao existe resposta parcial: se alguma saida nao foi calculada, o
        # escritor nem e chamado e o turno e recusa. Turno com os dois - ou com
        # nenhum - e o estado que a UI nao sabe mostrar, e que um dia seria
        # mostrado como se fosse resposta.
        #
        # A condicao passou a olhar `answered` na M5.6+ porque o caminho do
        # programa responde em `prose`. A invariante e a mesma; o que mudou foi
        # deixar de confundi-la com "o campo `answer` esta preenchido".
        if self.answered == (self.refusal is not None):
            raise ValueError("um turno e exatamente uma resposta ou exatamente uma recusa")
        if self.answer is not None and self.prose:
            raise ValueError(
                "um turno responde por UM caminho: `answer` (Fase 3) ou `prose` "
                "(programa), nunca os dois"
            )
        return self

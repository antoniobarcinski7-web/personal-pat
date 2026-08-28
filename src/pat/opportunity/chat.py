"""O5 - a porta: o que o analista digitou vira acao das camadas de baixo.

Este modulo e deliberadamente FINO. Ele nao pesquisa, nao critica, nao calcula
e nao escreve tese - ele decide qual das funcoes que ja existem responde ao
turno, chama, e grava o que foi feito ao lado do que foi dito.

A ordem em que os milestones foram construidos e essa de proposito: a conversa
veio depois do laco, do critico, da valuation e da tese, porque uma conversa
escrita antes das capacidades vira o lugar onde as capacidades acabam morando.
Um agente cujo `chat.py` e grande e um agente cujo comportamento so existe
dentro de uma frase, e nao da para testar sem falar com ele.

Classificacao por tabela declarada
----------------------------------
`_MARCADORES` casa palavra com intencao, e a tabela e lida na ordem. Nao ha
modelo classificando, e nao ha semelhanca de string: e a mesma disciplina de
`TERMOS_DE_METRICA` no raciocinador, pela mesma razao - casar "critica" com
CRITIQUE por distancia de edicao produziria uma corrida de pesquisa que
ninguem pediu, e o diario e append-only.

O que nao casa vira `UNCLEAR`, e `UNCLEAR` pergunta de volta. Perguntar custa
um turno; adivinhar custa uma agenda inteira.

O que o turno nunca faz
-----------------------
Nao produz numero. Quando a resposta tem numero, ele veio de um `MetricResult`
do motor e `grounded_in` carrega o endereco dele. Uma pergunta sobre grandeza
que o motor nao responde vira recusa nomeada - nunca prosa que preenche a
lacuna, que e como um sistema desses passa a inventar com confianca.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

from pat.contracts.claims import Severity
from pat.contracts.opportunity import (
    Actor,
    HypothesisOpened,
    HypothesisStatus,
    TaskStatus,
)
from pat.contracts.opportunity.chat import (
    ChatTurnRecorded,
    Intent,
    TurnAction,
    TurnRequest,
    TurnResponse,
)
from pat.contracts.opportunity.critic import WorkspaceFinding
from pat.contracts.opportunity.loop import StepKind, StepRequest
from pat.opportunity.critic import critique, falsification_agenda
from pat.opportunity.reason import MAX_PERIODOS, Reasoner, ShapeReasoner
from pat.opportunity.research import _research, plan_agenda, run_agenda
from pat.opportunity.store import Workspace
from pat.opportunity.tools import PatTools

__all__ = ["ChatAgent", "classify", "respond"]


# ---------------------------------------------------------------------------
# Classificacao
# ---------------------------------------------------------------------------

# Tabela DECLARADA, lida na ordem. A ordem importa: "investiga a critica" e um
# pedido de pesquisa, e nao um pedido de critica, porque o verbo manda.
_MARCADORES: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (
        Intent.INVESTIGATE,
        ("investiga", "investigar", "pesquisa", "pesquisar", "apura", "levanta"),
    ),
    (Intent.CRITIQUE, ("critica", "criticar", "advogado do diabo", "revisa a tese")),
    (Intent.RESUME, ("volta", "retoma", "retomar", "continua de onde")),
    (Intent.STATUS, ("onde estamos", "status", "resumo", "o que ja temos")),
    (
        Intent.CHALLENGE,
        ("mas e ", "e se ", "contra-evidencia", "e a concorrencia", "e o risco"),
    ),
    (
        Intent.ASSERT,
        ("acho que", "acredito que", "minha tese e", "na minha opiniao", "eu diria que"),
    ),
)

_INTERROGATIVAS = ("?", "por que", "porque", "qual", "quanto", "quais", "como")


def _sem_acento(texto: str) -> str:
    """Compara sem acento porque ninguem digita acento com pressa.

    Nao normaliza mais que isso: minuscula e acento sao variacoes de escrita
    da mesma palavra, e sinonimo nao e.
    """
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def classify(text: str) -> Intent:
    """Texto -> intencao, pela tabela. Sem modelo, sem heuristica de distancia.

    Pergunta que nao casa com nenhum marcador ainda e `ASK`: uma frase com "?"
    e uma pergunta mesmo quando nenhuma palavra-chave aparece. O que sobra -
    afirmacao curta que nao casa com nada - e `UNCLEAR`, e nao um chute pelo
    verbo mais provavel.
    """
    baixo = _sem_acento(text.strip())
    for intencao, marcadores in _MARCADORES:
        if any(m in baixo for m in marcadores):
            return intencao
    if any(m in baixo for m in _INTERROGATIVAS):
        return Intent.ASK
    return Intent.UNCLEAR


_NAO_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(texto: str, *, prefixo: str = "h") -> str:
    """Slug legivel a partir da frase do analista.

    Legivel de proposito: o slug aparece na agenda, na critica e na tese, e um
    `h-3f2a` obrigaria quem le a voltar ao diario para saber do que se trata.
    """
    limpo = _NAO_SLUG.sub("-", _sem_acento(texto)).strip("-")
    palavras = [p for p in limpo.split("-") if len(p) > 3][:4]
    base = "-".join(palavras) or "afirmacao"
    return f"{prefixo}-{base}"[:63].rstrip("-")


# Palavras que aparecem em toda frase de retomada e nao apontam para nada.
# Sem descarta-las, "volta naquela hipotese" casaria com qualquer hipotese que
# tivesse a palavra "hipotese" no enunciado - e o casamento parece funcionar
# ate o dia em que retoma a errada.
_RUIDO_DE_RETOMADA = frozenset(
    {
        "volta", "voltar", "naquela", "naquele", "aquela", "aquele", "retoma",
        "retomar", "hipotese", "hipoteses", "sobre", "aquilo", "continua",
        "continuar", "tese", "para", "essa", "esse", "esta", "isso",
    }
)


def _pistas_de_retomada(texto: str) -> tuple[str, ...]:
    """As palavras da FALA que apontam para um fio.

    A direcao importa: procurar as palavras da fala dentro da hipotese, e nao
    o contrario. "volta naquela hipotese de moat" tem que achar uma hipotese
    cujo enunciado fala de moat, mesmo que o enunciado use vinte outras
    palavras que nao aparecem na fala.
    """
    palavras = [p.strip(".,;:()!?") for p in _sem_acento(texto).split()]
    return tuple(
        dict.fromkeys(p for p in palavras if len(p) > 3 and p not in _RUIDO_DE_RETOMADA)
    )


# ---------------------------------------------------------------------------
# O agente
# ---------------------------------------------------------------------------


@dataclass
class _Turn:
    """O que um tratador devolveu, antes de virar `TurnResponse`.

    Existe para que cada tratador se preocupe so com o que ele faz, e a
    montagem do turno - indice, `as_of`, relogio, gravacao - aconteca num
    lugar so.
    """

    text: str
    actions: tuple[TurnAction, ...] = ()
    grounded_in: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    follow_up: tuple[str, ...] = ()
    disagreement: str | None = None


class ChatAgent:
    """Uma conversa sobre UM workspace.

    Sem estado proprio: o fio da conversa e o diario, e o indice do turno sai
    da dobra. E o que faz "continuar a sessao" ser a mesma coisa que "abrir o
    workspace" - nao ha sessao para perder.
    """

    def __init__(
        self,
        workspace: Workspace,
        tools: PatTools,
        reasoner: Reasoner | None = None,
    ) -> None:
        self._ws = workspace
        self._tools = tools
        self._reasoner = reasoner or ShapeReasoner()

    @property
    def workspace(self) -> Workspace:
        return self._ws

    def respond(self, request: TurnRequest, *, at: datetime | None = None) -> TurnResponse:
        """Um turno. Classifica, age, grava, responde.

        A gravacao acontece DEPOIS da acao, e nao antes: um turno que morre no
        meio da pesquisa deixa no diario os eventos que a pesquisa gravou, e
        nao um turno que afirma ter feito o que nao terminou.
        """
        if request.workspace_id != self._ws.workspace_id:
            raise ValueError(
                f"turno enderecado a {request.workspace_id!r} num agente aberto sobre "
                f"{self._ws.workspace_id!r}. Um agente fala de um workspace so."
            )
        intencao = request.intent_hint or classify(request.text)
        turno = self._handle(intencao, request.text)
        momento = at or datetime.now(UTC)
        indice = len(self._ws.state.turns)

        self._ws.apply(
            ChatTurnRecorded(
                turn_index=indice,
                user_text=request.text,
                intent=intencao,
                response_text=turno.text,
                actions=turno.actions,
                grounded_in=turno.grounded_in,
            ),
            actor=Actor.AGENT,
            at=momento,
        )
        return TurnResponse(
            turn_index=indice,
            intent=intencao,
            text=turno.text,
            actions=turno.actions,
            grounded_in=turno.grounded_in,
            tasks_touched=turno.tasks,
            hypotheses_touched=turno.hypotheses,
            follow_up=turno.follow_up,
            disagreement=turno.disagreement,
            as_of=self._ws.state.as_of,
            at=momento,
        )

    # -- despacho ------------------------------------------------------------

    def _handle(self, intencao: Intent, texto: str) -> _Turn:
        if intencao is Intent.ASK:
            return self._ask(texto)
        if intencao is Intent.INVESTIGATE:
            return self._investigate(texto)
        if intencao is Intent.ASSERT:
            return self._assert(texto)
        if intencao in (Intent.CHALLENGE, Intent.CRITIQUE):
            return self._challenge(texto, propor_agenda=intencao is Intent.CHALLENGE)
        if intencao is Intent.RESUME:
            return self._resume(texto)
        if intencao is Intent.STATUS:
            return self._status()
        return self._unclear(texto)

    # -- ASK -----------------------------------------------------------------

    def _ask(self, texto: str) -> _Turn:
        """Pergunta. Motor primeiro, corpus depois, recusa nomeada por ultimo.

        Nunca as tres juntas numa prosa: um numero do motor e um trecho de
        release lado a lado, sem dizer qual e qual, e como um numero de emissor
        entra numa resposta parecendo calculado.
        """
        contexto = self._context()
        metricas = ShapeReasoner()._metricas_do_texto(texto, contexto)
        periodos = contexto.periods[-MAX_PERIODOS:]

        if metricas and periodos:
            passos = tuple(
                StepRequest(
                    step_id=f"ask-{i}",
                    kind=StepKind.METRIC,
                    ref=ref,
                    period_ends=periodos,
                    rationale=f"a pergunta cita {ref}",
                )
                for i, ref in enumerate(metricas)
            )
            resultados = _research(self._tools, passos)
            linhas = [linha for r in resultados for linha in r.rendered]
            recusas = [x for r in resultados for x in r.unavailable]
            ids = tuple(i for r in resultados for i in r.result_ids)
            if linhas:
                corpo = "\n".join(linhas)
                if recusas:
                    corpo += "\n\nSem resposta do motor para:\n" + "\n".join(recusas)
                return _Turn(
                    text=corpo,
                    actions=(TurnAction.QUERIED_ENGINE,),
                    grounded_in=ids,
                    follow_up=(
                        f"Posso investigar a fundo: 'investiga {metricas[0]}'.",
                    ),
                )
            return _Turn(
                text=(
                    "O motor nao produziu numero para esta pergunta:\n"
                    + "\n".join(recusas or ["cobertura insuficiente"])
                ),
                actions=(TurnAction.QUERIED_ENGINE, TurnAction.REFUSED),
            )

        if contexto.has_corpus:
            termos = ShapeReasoner._termos_de_busca(texto)
            resultado = self._tools.evidence(termos) if termos else None
            hits = getattr(resultado, "hits", ()) if resultado is not None else ()
            if hits:
                return _Turn(
                    text=(
                        "Nao ha metrica registrada que responda isso. O que os "
                        "documentos dizem, verbatim e atribuido:\n"
                        + "\n".join(
                            f"[{h.quote.published_at.isoformat()}] {h.quote.title}: "
                            f"{h.quote.text}"
                            for h in hits
                        )
                        + "\n\nIsto e citacao do emissor, nao numero calculado."
                    ),
                    actions=(TurnAction.SEARCHED_CORPUS,),
                    grounded_in=tuple(h.quote.unit_id for h in hits),
                )

        return _Turn(
            text=(
                "Nao tenho como responder isso com o que esta coberto ate "
                f"{self._ws.state.as_of.isoformat()}. Nenhuma metrica registrada casa "
                "com a pergunta e o corpus nao trouxe trecho. Diga qual metrica ou "
                "qual documento voce quer que eu olhe."
            ),
            actions=(TurnAction.REFUSED,),
            follow_up=self._metricas_sugeridas(),
        )

    # -- INVESTIGATE ---------------------------------------------------------

    def _investigate(self, texto: str) -> _Turn:
        criados = plan_agenda(
            self._ws, self._tools, self._reasoner, objective=texto, actor=Actor.AGENT
        )
        corrida = run_agenda(self._ws, self._tools, self._reasoner)
        acoes: tuple[TurnAction, ...] = (TurnAction.PLANNED_AGENDA,)
        if corrida.reports:
            acoes += (TurnAction.RAN_TASKS,)

        ancoras: list[str] = []
        for relatorio in corrida.reports:
            for achado in relatorio.findings:
                ancoras.extend(achado.cites)

        linhas = [f"Agenda: {len(criados)} tarefa(s) nova(s)."]
        for relatorio in corrida.reports:
            linhas.append(
                f"  {relatorio.task}: {relatorio.verdict.status.value} - "
                f"{relatorio.verdict.rationale}"
            )
            linhas.extend(f"    - {a.text}" for a in relatorio.findings)
        linhas.append(f"Parou porque: {corrida.stopped_because}")
        if corrida.blocked:
            linhas.append(
                "Travadas (e por que): "
                + ", ".join(corrida.blocked)
                + ". Nao concluo o que ficou travado."
            )
        return _Turn(
            text="\n".join(linhas),
            actions=acoes,
            grounded_in=tuple(dict.fromkeys(ancoras)),
            tasks=tuple(r.task for r in corrida.reports),
        )

    # -- ASSERT --------------------------------------------------------------

    def _assert(self, texto: str) -> _Turn:
        """Afirmacao do analista vira HIPOTESE, nunca claim.

        Claim exige evidencia. Uma afirmacao do analista e o comeco de uma
        investigacao, e transforma-la em claim faria a conviccao dele entrar no
        sistema com a mesma forca de um numero do motor.
        """
        slug = _slug(texto)
        if self._ws.state.hypothesis(slug) is not None:
            return _Turn(
                text=(
                    f"Ja existe a hipotese {slug}. Nao abro outra com o mesmo nome - "
                    "isso apagaria a evidencia ja ligada a primeira."
                ),
                actions=(TurnAction.ANSWERED_FROM_STATE,),
                hypotheses=(slug,),
            )
        falsificador = (
            "o analista ainda nao declarou o que derrubaria esta afirmacao"
        )
        self._ws.apply(
            HypothesisOpened(slug=slug, statement=texto.strip(), falsifiers=(falsificador,)),
            actor=Actor.USER,
        )
        return _Turn(
            text=(
                f"Registrei como hipotese {slug}, aberta, atribuida a voce - nao como "
                "fato e nao como claim. Falta o falsificador: o que voce veria no "
                "mundo que te faria abandonar isso? Sem ele a hipotese nao e testavel, "
                "e uma hipotese nao testavel nunca vai ser derrubada por nada."
            ),
            actions=(TurnAction.OPENED_HYPOTHESIS,),
            hypotheses=(slug,),
            follow_up=(f"'investiga {slug}' coloca o teste na agenda.",),
        )

    # -- CHALLENGE / CRITIQUE ------------------------------------------------

    def _challenge(self, texto: str, *, propor_agenda: bool) -> _Turn:
        """Roda o critico. Em CHALLENGE, tambem coloca o conserto na agenda.

        A diferenca entre os dois e essa e so essa: CRITIQUE mostra, CHALLENGE
        mostra e agenda. Um critico que so aponta produz relatorio que se le,
        concorda e arquiva.
        """
        veredito = critique(self._ws.state)
        acoes: tuple[TurnAction, ...] = (TurnAction.RAN_CRITIQUE,)

        if not veredito.findings:
            return _Turn(
                text=(
                    f"Passei {veredito.hypotheses_checked} hipotese(s) e "
                    f"{veredito.claims_checked} claim(s) pelo critico e nao achei "
                    "objecao mecanica. Isso nao quer dizer que a tese esta certa - "
                    "quer dizer que ela nao viola nenhuma das checagens que eu sei "
                    "fazer."
                ),
                actions=acoes,
            )

        linhas = [f"{len(veredito.findings)} objecao(oes):"]
        for achado in veredito.findings:
            marca = "DURO" if achado.severity is Severity.HARD else "leve"
            linhas.append(f"  [{marca}] {achado.code.value}: {achado.message}")
            linhas.append(f"         remedio: {achado.remedy}")

        tarefas: tuple[str, ...] = ()
        if propor_agenda:
            propostas = falsification_agenda(self._ws.state)
            existentes = {t.slug for t in self._ws.state.agenda.tasks}
            novas = [p for p in propostas if p.slug not in existentes]
            if novas:
                from pat.contracts.opportunity import TaskCreated

                for proposta in novas:
                    self._ws.apply(
                        TaskCreated(
                            slug=proposta.slug,
                            objective=proposta.objective,
                            completion_criteria=proposta.completion_criteria,
                            priority=proposta.priority,
                            hypothesis=proposta.hypothesis,
                        ),
                        actor=Actor.AGENT,
                    )
                tarefas = tuple(p.slug for p in novas)
                acoes += (TurnAction.PLANNED_AGENDA,)
                linhas.append(
                    f"Coloquei {len(novas)} tarefa(s) de falsificacao na agenda: "
                    + ", ".join(tarefas)
                )

        return _Turn(
            text="\n".join(linhas),
            actions=acoes,
            tasks=tarefas,
            hypotheses=tuple(
                dict.fromkeys(f.hypothesis for f in veredito.findings if f.hypothesis)
            ),
            disagreement=self._disagreement(veredito),
        )

    @staticmethod
    def _disagreement(veredito) -> str | None:
        """O agente discorda quando ha contra-evidencia, e so entao.

        Discordar sem evidencia nao e ceticismo - e outra opiniao, e o sistema
        nao tem por que ter opiniao.
        """
        contradicoes = [
            f
            for f in veredito.findings
            if f.code is WorkspaceFinding.USER_ASSERTION_CONTRADICTED
        ]
        if not contradicoes:
            return None
        return (
            "Eu nao seguiria com essa hipotese como esta. "
            + " ".join(f.message for f in contradicoes)
            + " A contra-evidencia esta registrada; a decisao de manter a hipotese "
            "mesmo assim e sua, e vai ficar no diario como sua."
        )

    # -- RESUME --------------------------------------------------------------

    def _resume(self, texto: str) -> _Turn:
        """Retoma um fio. Casa por palavra do enunciado, e diz quando nao casa.

        Nao escolhe "a hipotese mais provavel" quando ha empate: retomar a
        errada faz o analista continuar raciocinando sobre outra coisa sem
        perceber.
        """
        pistas = _pistas_de_retomada(texto)
        candidatas = [
            h
            for h in self._ws.state.hypotheses
            if pistas
            and any(
                pista in _sem_acento(f"{h.statement} {h.slug}") for pista in pistas
            )
        ]
        if not candidatas:
            abertas = self._ws.state.open_hypotheses
            return _Turn(
                text=(
                    "Nao achei a qual fio voce se refere. Abertas agora: "
                    + (", ".join(h.slug for h in abertas) or "nenhuma")
                    + "."
                ),
                actions=(TurnAction.ASKED_BACK,),
            )
        if len(candidatas) > 1:
            return _Turn(
                text=(
                    "Mais de uma hipotese casa com isso: "
                    + ", ".join(h.slug for h in candidatas)
                    + ". Qual delas? Retomar a errada te faria continuar raciocinando "
                    "sobre outra coisa sem perceber."
                ),
                actions=(TurnAction.ASKED_BACK,),
                hypotheses=tuple(h.slug for h in candidatas),
            )
        return _Turn(
            text=self._resumo_de_hipotese(candidatas[0]),
            actions=(TurnAction.ANSWERED_FROM_STATE,),
            grounded_in=tuple(e.ref for e in candidatas[0].supporting),
            hypotheses=(candidatas[0].slug,),
        )

    def _resumo_de_hipotese(self, h) -> str:
        tarefas = [t for t in self._ws.state.agenda.tasks if t.hypothesis == h.slug]
        linhas = [
            f"{h.slug} - {h.status.value}"
            + (f" ({h.strength.value})" if h.strength else ""),
            f"  enunciado: {h.statement}",
            f"  a favor: {len(h.supporting)} evidencia(s); contra: {len(h.counter)}",
            f"  falsificadores nao testados: "
            + (", ".join(h.untested_falsifiers) or "nenhum"),
        ]
        if tarefas:
            linhas.append(
                "  tarefas: "
                + ", ".join(f"{t.slug} [{t.status.value}]" for t in tarefas)
            )
        conclusao = self._ws.state.conclusion_for(h.slug)
        if conclusao is not None:
            linhas.append(f"  conclusao: {conclusao.text}")
            linhas.append(f"  incerteza residual: {conclusao.residual_uncertainty}")
        return "\n".join(linhas)

    # -- STATUS --------------------------------------------------------------

    def _status(self) -> _Turn:
        estado = self._ws.state
        agenda = estado.agenda
        por_status: dict[str, int] = {}
        for tarefa in agenda.tasks:
            por_status[tarefa.status.value] = por_status.get(tarefa.status.value, 0) + 1
        travadas = [
            t
            for t in agenda.tasks
            if t.status in (TaskStatus.BLOCKED, TaskStatus.NEEDS_HUMAN)
        ]
        linhas = [
            f"{estado.workspace.company.display_name} - as_of {estado.as_of.isoformat()}"
            f" - {estado.workspace.status.value}",
            f"objetivo: {agenda.objective or '(nao declarado)'}",
            "tarefas: "
            + (", ".join(f"{k}={v}" for k, v in sorted(por_status.items())) or "nenhuma"),
            f"hipoteses: {len(estado.hypotheses)} "
            f"({len(estado.open_hypotheses)} aberta(s))",
            f"claims: {len(estado.claims)}; conclusoes: {len(estado.conclusions)}; "
            f"valuations: {len(estado.valuations)}; teses: {len(estado.theses)}",
        ]
        for hipotese in estado.hypotheses:
            linhas.append(
                f"  {hipotese.slug}: {hipotese.status.value}"
                + (
                    f" - {len(hipotese.untested_falsifiers)} falsificador(es) por testar"
                    if hipotese.untested_falsifiers
                    else ""
                )
            )
        if travadas:
            linhas.append("travado, esperando:")
            for tarefa in travadas:
                for bloqueio in tarefa.blockers:
                    linhas.append(
                        f"  {tarefa.slug}: {bloqueio.reason} -> {bloqueio.remedy}"
                    )
        seguir = []
        if agenda.ready():
            seguir.append(f"'investiga' roda {len(agenda.ready())} tarefa(s) pronta(s).")
        if estado.hypotheses:
            seguir.append("'critica' passa o advogado do diabo no que ja existe.")
        return _Turn(
            text="\n".join(linhas),
            actions=(TurnAction.ANSWERED_FROM_STATE,),
            follow_up=tuple(seguir),
        )

    # -- UNCLEAR -------------------------------------------------------------

    def _unclear(self, texto: str) -> _Turn:
        return _Turn(
            text=(
                "Nao entendi o que voce quer deste turno, e prefiro perguntar a "
                "adivinhar - adivinhar errado gasta uma corrida de pesquisa e enche a "
                "agenda de tarefa que ninguem pediu. Voce quer que eu responda uma "
                "pergunta, investigue algo, registre uma afirmacao sua como hipotese, "
                "ou critique o que ja esta aqui?"
            ),
            actions=(TurnAction.ASKED_BACK,),
        )

    # -- auxiliares ----------------------------------------------------------

    def _context(self):
        from pat.opportunity.research import build_context

        return build_context(self._ws, self._tools)

    def _metricas_sugeridas(self) -> tuple[str, ...]:
        disponiveis = self._tools.coverage().metrics_available[:4]
        if not disponiveis:
            return ()
        return (f"Metricas cobertas: {', '.join(disponiveis)}.",)


def respond(
    workspace: Workspace,
    tools: PatTools,
    request: TurnRequest,
    *,
    reasoner: Reasoner | None = None,
    at: datetime | None = None,
) -> TurnResponse:
    """Um turno, sem manter agente. O fio da conversa esta no diario."""
    return ChatAgent(workspace, tools, reasoner).respond(request, at=at)

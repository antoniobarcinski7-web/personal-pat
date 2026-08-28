"""O7 - a etapa explicita de falsificacao.

O critic da Fase 3 pergunta "esta RESPOSTA pode sair?". Este pergunta outra
coisa: **"esta INVESTIGACAO se convenceu cedo demais?"**.

Sao perguntas diferentes e por isso a taxonomia e outra. Uma resposta pode
estar impecavel - cada numero resolvendo ate o byte, cada citacao verbatim - e
a investigacao por tras dela ter parado exatamente antes do dado que a
derrubaria. Nenhuma checagem de procedencia enxerga isso, porque nao ha nada
errado com o que esta escrito. O que esta errado e o que nao foi procurado.

Por que procurar precisa deixar rastro
--------------------------------------
`FalsificationAttempted` existe porque "procuramos contra-evidencia e nao
achamos" e uma afirmacao forte, e ela e indistinguivel de "nao procuramos"
quando as duas produzem o mesmo estado: zero contra-evidencias. As duas pedem
leituras opostas de uma hipotese SUPPORTED.

Entao a tentativa e um evento. Uma hipotese so pode ser sustentada de forma
defensavel se existe registro de que alguem procurou o que a derrubaria - e o
que se registra e COMO se procurou, para que a busca possa ser julgada fraca
depois.

`AlternativeConsidered` pela mesma razao. Uma hipotese que explica os dados
nao esta certa por isso: quase sempre existem duas ou tres explicacoes que
explicam igualmente bem, e a que vira tese e a que ocorreu primeiro a quem
estava escrevendo. Descartar uma alternativa exige `basis` - por que ela nao
serve -, e "nao me pareceu provavel" nao e base.

Duro e leve
-----------
Duro BLOQUEIA a tese: a investigacao nao pode ser apresentada como concluida.
Leve ACOMPANHA, visivel. A distincao responde "da para defender esta tese numa
mesa?", e a resposta para uma hipotese sustentada com um falsificador ja
ocorrido no proprio registro e nao.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.claims import Severity
from pat.contracts.common import Frozen
from pat.contracts.opportunity.base import Slug

__all__ = [
    "AlternativeConsidered",
    "Critique",
    "CritiqueFinding",
    "FalsificationAttempted",
    "Severity",
    "WorkspaceFinding",
]


class WorkspaceFinding(StrEnum):
    """Taxonomia FECHADA do que faz uma investigacao se convencer cedo demais.

    Fechada pela mesma razao de sempre: um achado com nome novo a cada corrida
    nao da para contar, nem para bloquear, nem para ignorar conscientemente.
    """

    # -- duros: a tese nao pode ser apresentada assim ------------------------

    DECISIVE_COUNTER_IGNORED = "decisive_counter_ignored"
    """Ha contra-evidencia marcada como decisiva e a hipotese continua
    sustentada. E o falsificador que ocorreu e nao foi honrado."""

    SUPPORTED_WITHOUT_FALSIFICATION = "supported_without_falsification"
    """Sustentada sem nenhum registro de que alguem procurou o contrario.
    "Nao achamos" e indistinguivel de "nao procuramos" quando as duas dao zero
    contra-evidencias."""

    SUPPORTED_WITH_BLOCKED_TASK = "supported_with_blocked_task"
    """A tarefa que testava a hipotese esta travada, e a hipotese foi
    sustentada assim mesmo. E a tese se declarando pronta tendo pulado o que
    nao conseguiu fazer."""

    USER_ASSERTION_CONTRADICTED = "user_assertion_contradicted"
    """O humano afirmou algo que a contra-evidencia registrada derruba. O
    agente deve discordar - e este achado e o que o autoriza, com o endereco
    da evidencia junto."""

    # -- leves: acompanham a tese, visiveis ----------------------------------

    NO_ALTERNATIVE_CONSIDERED = "no_alternative_considered"
    """Nenhuma explicacao alternativa foi registrada. Uma hipotese que explica
    os dados nao esta certa por isso: quase sempre ha duas ou tres que
    explicam igualmente bem."""

    SINGLE_PERIOD_SUPPORT = "single_period_support"
    """Sustentada por numeros de um periodo so. Um ano pode ser qualquer
    coisa; a persistencia e o que separa caracteristica de acidente."""

    ISSUER_ONLY_SUPPORT = "issuer_only_support"
    """Sustentada apenas por citacao do emissor. Repetir o que a companhia
    disse e legitimo e e diferente de ter verificado."""

    ASSUMPTION_AS_OBSERVATION = "assumption_as_observation"
    """Um claim sobre o que a empresa FEZ esta ancorado numa premissa. Premissa
    sustenta projecao, nunca historico."""

    FALSIFIER_UNTESTED = "falsifier_untested"
    """Um falsificador declarado nunca virou tarefa. Declarar o que derrubaria
    a hipotese e depois nao olhar e o mesmo que nao ter declarado."""

    ORPHAN_HYPOTHESIS = "orphan_hypothesis"
    """Hipotese aberta sem tarefa que a teste. Ela nao vai ser respondida por
    ninguem, e vai aparecer na tese como pergunta em aberto sem que ninguem
    tenha decidido isso."""


HARD_FINDINGS = frozenset(
    {
        WorkspaceFinding.DECISIVE_COUNTER_IGNORED,
        WorkspaceFinding.SUPPORTED_WITHOUT_FALSIFICATION,
        WorkspaceFinding.SUPPORTED_WITH_BLOCKED_TASK,
        WorkspaceFinding.USER_ASSERTION_CONTRADICTED,
    }
)


class CritiqueFinding(Frozen):
    """Uma objecao, com onde ela pega e o que resolve.

    `remedy` e obrigatorio. Uma critica sem remedio e uma reclamacao, e o
    leitor - inclusive o autor, meses depois - fica sabendo que ha um problema
    sem saber o que fazer com ele.
    """

    code: WorkspaceFinding
    severity: Severity
    message: str = Field(min_length=1)
    remedy: str = Field(min_length=1)
    hypothesis: Slug | None = None
    claim: Slug | None = None
    task: Slug | None = None
    evidence_refs: tuple[str, ...] = Field(
        default=(), description="Enderecos que sustentam a objecao, quando ha"
    )


class Critique(Frozen):
    """O que o advogado do diabo achou. Ele nao corrige e nao reescreve.

    Sem laco `critic -> autor -> critic`, pela mesma razao que nao ha
    retentativa de planejador: "criticar ate passar" e um amostrador que uma
    hora aprova algo errado.
    """

    critique_version: Literal["v1"] = "v1"
    findings: tuple[CritiqueFinding, ...] = ()
    hypotheses_checked: int = Field(default=0, ge=0)
    claims_checked: int = Field(default=0, ge=0)

    @property
    def hard(self) -> tuple[CritiqueFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.HARD)

    @property
    def soft(self) -> tuple[CritiqueFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.SOFT)

    @property
    def blocks(self) -> bool:
        """Ha objecao dura: a investigacao nao pode ser apresentada como
        concluida."""
        return bool(self.hard)

    def for_hypothesis(self, slug: str) -> tuple[CritiqueFinding, ...]:
        return tuple(f for f in self.findings if f.hypothesis == slug)


# -- eventos ----------------------------------------------------------------


class FalsificationAttempted(Frozen):
    """Registro de que alguem procurou o que derrubaria a hipotese.

    `how` e obrigatorio e e o campo que faz o evento valer alguma coisa: sem
    ele, o registro viraria um carimbo que qualquer um aplica para destravar a
    critica. Com ele, a busca pode ser julgada fraca depois - e "procurei nos
    documentos de um trimestre" e visivelmente mais fraco que "procurei em
    doze".
    """

    kind: Literal["falsification_attempted"] = "falsification_attempted"
    hypothesis: Slug
    how: str = Field(min_length=1, description="Como se procurou. Concreto, nao retorico")
    falsifier: str | None = Field(
        default=None, description="Qual falsificador esta tentativa foi testar"
    )
    found: bool = Field(
        default=False, description="Se a busca encontrou algo que corta contra"
    )


class AlternativeConsidered(Frozen):
    """Uma explicacao alternativa, e o que se fez com ela.

    Descartar exige `basis`. "Nao me pareceu provavel" nao e base - e a forma
    mais educada de nao ter considerado.
    """

    kind: Literal["alternative_considered"] = "alternative_considered"
    hypothesis: Slug
    explanation: str = Field(min_length=1)
    ruled_out: bool = False
    basis: str | None = Field(
        default=None, description="Por que a alternativa nao serve. Exigido se descartada"
    )

    @model_validator(mode="after")
    def _check(self) -> "AlternativeConsidered":
        if self.ruled_out and not self.basis:
            raise ValueError(
                f"alternativa descartada em {self.hypothesis} sem base. Descartar sem "
                "dizer por que e a forma mais educada de nao ter considerado."
            )
        return self

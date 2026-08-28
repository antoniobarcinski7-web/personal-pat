"""O2 - hipotese, claim, evidencia, contra-evidencia, conclusao.

O modulo existe para que estas cinco coisas nunca se confundam. A confusao
nao acontece por ma-fe: acontece porque, escritas em prosa, todas viram
frases afirmativas, e uma frase afirmativa parece um fato.

    EvidenceLink   um ponteiro para algo que EXISTE fora desta camada: um
                   `result_id` do motor, ou um `unit_id` de trecho verbatim.
                   Nao carrega texto nem numero - carrega endereco.

    Claim          uma afirmacao que se apresenta como sustentada. Exige pelo
                   menos um `EvidenceLink`, no contrato. Claim sem evidencia
                   nao e claim fraco: e categoria errada, e vira `Hypothesis`.

    Hypothesis     uma afirmacao ainda em teste. Nao exige evidencia - exige
                   FALSIFICADOR. E a assimetria que da nome ao milestone: para
                   ser hipotese, tem que existir um resultado observavel que a
                   derrubaria.

    CounterEvidence  evidencia que corta contra. Tipo proprio, e nao um
                   `EvidenceLink` com sinal negativo, para que "quantas
                   evidencias a favor" nunca possa ser respondido somando
                   coisas que apontam para lados opostos.

    Conclusion     o que se concluiu de uma hipotese TESTADA. Nao pode existir
                   sobre hipotese OPEN - o unico estado que quer dizer "ainda
                   nao testada". WEAKENED e INCONCLUSIVE concluem: "ha indicio,
                   insuficiente" e "os dados nao resolvem isso" sao conclusoes,
                   e das mais uteis.

Por que hipotese exige falsificador
-----------------------------------
Uma afirmacao que nenhum dado poderia derrubar nao esta sendo testada, esta
sendo defendida. Na pratica, e assim que uma tese de investimento fica imune a
evidencia: a cada dado contrario, a hipotese se reformula um pouco e sobrevive.
Exigir `falsifiers` na construcao obriga a dizer, ANTES de olhar, o que faria
mudar de ideia - que e a unica hora em que da para dizer isso honestamente.

`REJECTED` nao apaga nada
-------------------------
Uma hipotese rejeitada continua no estado, com as evidencias que a sustentavam
e a que a derrubou. Uma investigacao que apaga o que descartou fica sem o
registro do que ja foi tentado, e a mesma hipotese volta tres semanas depois
como se fosse nova.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen
from pat.contracts.opportunity.base import Actor, Slug

__all__ = [
    "Claim",
    "ClaimAsserted",
    "Conclusion",
    "ConclusionDrawn",
    "CounterEvidence",
    "CounterEvidenceAdded",
    "EvidenceKind",
    "EvidenceLink",
    "EvidenceLinked",
    "FalsifierAdded",
    "Hypothesis",
    "HypothesisOpened",
    "HypothesisStatus",
    "HypothesisStatusChanged",
    "HypothesisStrength",
]


class EvidenceKind(StrEnum):
    """De onde a evidencia vem. Determina o que ela pode sustentar.

    Nao e rotulo de qualidade: e o tipo do endereco. `METRIC` aponta para um
    resultado do motor, `QUOTE` para um trecho verbatim. Os dois sao fortes,
    de maneiras diferentes, e nenhum se converte no outro.
    """

    METRIC = "metric"
    """`ComputationResult.result_id`: numero calculado pelo motor, com
    procedencia, `as_of` e fidelidade."""

    QUOTE = "quote"
    """`DocumentUnit.unit_id`: trecho verbatim de documento, com
    `published_at`. Se o trecho contem um numero do emissor, ele e citacao -
    nunca insumo."""

    ASSUMPTION = "assumption"
    """Premissa declarada por um humano. Nao e observacao, e o tipo diz isso.
    Sustenta valuation; nao sustenta claim sobre o que a empresa fez."""

    ABSENCE = "absence"
    """Ausencia constatada: o motor recusou o numero, ou a busca no corpus nao
    achou nada no escopo. Ausencia e informacao - "a empresa nunca falou disso
    em 12 trimestres" e um achado -, mas so quando o escopo da busca esta
    registrado junto. Por isso `note` e obrigatorio neste tipo."""


class EvidenceLink(Frozen):
    """Ponteiro para evidencia que existe fora desta camada.

    Nao tem `text`, `value` nem `unit`, e nao e esquecimento: uma evidencia que
    carregasse o proprio conteudo poderia divergir da fonte sem que ninguem
    notasse - e a copia, sendo mais comoda de ler, e a que acabaria citada.
    Aqui so mora o endereco; `pat provenance-unit` reextrai e confere.
    """

    kind: EvidenceKind
    ref: str = Field(
        min_length=1,
        description="`result_id`, `unit_id`, ou o slug da premissa, conforme `kind`",
    )
    note: str | None = Field(
        default=None, description="Por que este endereco sustenta a afirmacao"
    )
    linked_by: Actor
    at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> "EvidenceLink":
        if self.kind is EvidenceKind.ABSENCE and not self.note:
            raise ValueError(
                "evidencia de ABSENCE exige `note` com o escopo do que foi procurado. "
                "Ausencia sem escopo nao distingue 'a empresa nunca falou disso' de "
                "'os documentos nao foram ingeridos', e as duas pedem acoes opostas."
            )
        return self


class CounterEvidence(Frozen):
    """Evidencia que corta contra.

    Tipo proprio, e nao `EvidenceLink` com sinal, para que ninguem possa somar
    "quantas evidencias" misturando o que aponta para lados opostos. Carrega
    `undermines`: contra-evidencia que nao diz O QUE ela derruba vira uma
    ressalva decorativa no fim do relatorio.
    """

    link: EvidenceLink
    undermines: str = Field(
        min_length=1, description="O que exatamente esta evidencia poe em duvida"
    )
    decisive: bool = Field(
        default=False,
        description="Verdadeiro quando ela sozinha derruba a hipotese, e nao apenas a enfraquece",
    )


class Claim(Frozen):
    """Uma afirmacao que se apresenta como sustentada.

    `evidence` tem `min_length=1` no contrato. Claim sem evidencia nao e um
    claim fraco - e categoria errada, e o nome dela e hipotese. Deixar passar
    seria permitir que uma frase do agente ficasse indistinguivel, no relatorio
    final, de uma frase ancorada num numero do motor.
    """

    slug: Slug
    text: str = Field(min_length=1)
    evidence: tuple[EvidenceLink, ...] = Field(min_length=1)
    asserted_by: Actor
    at: AwareDatetime

    @property
    def is_engine_grounded(self) -> bool:
        """Sustentado por pelo menos um numero do motor.

        Distincao util para a critica: um claim quantitativo sustentado so por
        citacao esta repetindo o que o emissor disse, o que e uma coisa
        legitima de fazer e uma coisa diferente de ter verificado.
        """
        return any(e.kind is EvidenceKind.METRIC for e in self.evidence)


class HypothesisStatus(StrEnum):
    OPEN = "open"
    """Ainda em teste. Estado inicial e o unico que se pode assumir sem prova."""

    SUPPORTED = "supported"
    """Evidencia a favor, falsificadores procurados e nao encontrados."""

    WEAKENED = "weakened"
    """Sobreviveu, mas contra-evidencia relevante apareceu. Nao e o mesmo que
    REJECTED, e a diferenca importa: uma tese pode se sustentar em hipotese
    enfraquecida, desde que diga que ela esta enfraquecida."""

    REJECTED = "rejected"
    """Um falsificador ocorreu. O registro fica - apagar faria a mesma hipotese
    voltar tres semanas depois como se fosse nova."""

    INCONCLUSIVE = "inconclusive"
    """Testada e sem resposta: o dado que resolveria nao existe, ou o
    falsificador nao e observavel com o que ha. Diferente de OPEN, que e
    'ainda nao testada'."""


class HypothesisStrength(StrEnum):
    """Quanta confianca a hipotese merece, em tres niveis.

    Nao e probabilidade. Um numero entre 0 e 1 aqui seria falsa precisao: nada
    no sistema calcula essa probabilidade, e ela seria lida como se algo
    calculasse. Tres niveis nomeados dizem o que da para dizer.
    """

    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class Hypothesis(Frozen):
    """Uma afirmacao ainda em teste, com o que a derrubaria.

    `falsifiers` e obrigatorio. Uma afirmacao que nenhum dado poderia derrubar
    nao esta sendo testada, esta sendo defendida - e e assim que uma tese fica
    imune a evidencia, reformulando-se um pouco a cada dado contrario. Exigir o
    falsificador na abertura obriga a dizer, antes de olhar, o que faria mudar
    de ideia.
    """

    slug: Slug
    statement: str = Field(min_length=1)
    status: HypothesisStatus = HypothesisStatus.OPEN
    strength: HypothesisStrength | None = Field(
        default=None,
        description="Nulo enquanto OPEN: forca antes do teste seria opiniao com cara de veredito",
    )
    falsifiers: tuple[str, ...] = Field(
        min_length=1,
        description="O que, se observado, derrubaria a hipotese. Observavel, nao retorico",
    )
    supporting: tuple[EvidenceLink, ...] = ()
    counter: tuple[CounterEvidence, ...] = ()
    claims: tuple[Slug, ...] = Field(
        default=(), description="Claims que esta hipotese sustenta, por slug"
    )
    open_questions: tuple[str, ...] = ()
    opened_by: Actor
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> "Hypothesis":
        if self.status is HypothesisStatus.OPEN and self.strength is not None:
            raise ValueError(
                f"hipotese {self.slug} esta OPEN com forca declarada. Forca antes do "
                "teste e opiniao com cara de veredito."
            )
        if self.status is HypothesisStatus.SUPPORTED and not self.supporting:
            raise ValueError(
                f"hipotese {self.slug} marcada SUPPORTED sem nenhuma evidencia a favor."
            )
        if self.status is HypothesisStatus.REJECTED and not self.counter:
            raise ValueError(
                f"hipotese {self.slug} marcada REJECTED sem contra-evidencia. Rejeitar "
                "por mudanca de opiniao apaga a razao, que e o que se le depois."
            )
        return self

    @property
    def is_falsifiable_now(self) -> bool:
        """Ha contra-evidencia decisiva registrada?

        Nao muda o estado sozinho - mudar estado e evento, e evento tem autor.
        Serve para o critico apontar hipotese que continua SUPPORTED com um
        falsificador ja ocorrido no proprio registro.
        """
        return any(c.decisive for c in self.counter)

    @property
    def is_settled(self) -> bool:
        return self.status in (HypothesisStatus.SUPPORTED, HypothesisStatus.REJECTED)


class Conclusion(Frozen):
    """O que se concluiu de uma hipotese, e o que ficou faltando.

    `residual_uncertainty` e obrigatorio. Uma conclusao sem incerteza residual
    declarada e mais forte no papel do que no mundo, e o leitor - inclusive o
    proprio autor, meses depois - nao tem como saber onde ela e fraca.
    """

    hypothesis: Slug
    text: str = Field(min_length=1)
    strength: HypothesisStrength
    residual_uncertainty: str = Field(min_length=1)
    drawn_by: Actor
    at: AwareDatetime


# -- eventos ----------------------------------------------------------------


class HypothesisOpened(Frozen):
    kind: Literal["hypothesis_opened"] = "hypothesis_opened"
    slug: Slug
    statement: str = Field(min_length=1)
    falsifiers: tuple[str, ...] = Field(min_length=1)
    open_questions: tuple[str, ...] = ()


class FalsifierAdded(Frozen):
    """Falsificador acrescentado depois da abertura.

    Legitimo: pesquisar ensina o que observar. O que nao existe e evento para
    REMOVER falsificador - remover o teste que a hipotese nao passaria e a
    forma mais limpa de tornar uma tese imune a evidencia.
    """

    kind: Literal["falsifier_added"] = "falsifier_added"
    slug: Slug
    falsifier: str = Field(min_length=1)


class EvidenceLinked(Frozen):
    kind: Literal["evidence_linked"] = "evidence_linked"
    hypothesis: Slug
    link: EvidenceLink


class CounterEvidenceAdded(Frozen):
    kind: Literal["counter_evidence_added"] = "counter_evidence_added"
    hypothesis: Slug
    counter: CounterEvidence


class HypothesisStatusChanged(Frozen):
    kind: Literal["hypothesis_status_changed"] = "hypothesis_status_changed"
    slug: Slug
    status: HypothesisStatus
    strength: HypothesisStrength | None = None
    rationale: str = Field(
        min_length=1, description="Por que o estado mudou. Sem default, de proposito"
    )


class ClaimAsserted(Frozen):
    kind: Literal["claim_asserted"] = "claim_asserted"
    slug: Slug
    text: str = Field(min_length=1)
    evidence: tuple[EvidenceLink, ...] = Field(min_length=1)
    hypothesis: Slug | None = Field(
        default=None, description="Hipotese que este claim sustenta, quando ha uma"
    )


class ConclusionDrawn(Frozen):
    kind: Literal["conclusion_drawn"] = "conclusion_drawn"
    hypothesis: Slug
    text: str = Field(min_length=1)
    strength: HypothesisStrength
    residual_uncertainty: str = Field(min_length=1)

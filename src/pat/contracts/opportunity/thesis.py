"""O9 - a tese de investimento, auditavel ate a evidencia original.

A tese e a unica coisa desta camada que sai do sistema e vai para uma mesa,
onde alguem vai discordar dela. Por isso o contrato exige mais dela do que de
qualquer outro objeto do projeto - e o que ele exige nao e mais conteudo, e
mais *honestidade estrutural*:

    `key_assumptions`     obrigatorio, minimo 1
    `risks`               obrigatorio, minimo 1
    `falsifiers`          obrigatorio, minimo 1
    `unresolved`          pode ser vazio, mas o campo nunca some
    `counter_thesis`      obrigatorio

Uma tese sem premissa declarada esta escondendo de que ela depende. Uma sem
risco esta afirmando que nao ha nenhum. Uma sem falsificador nao pode ser
derrubada por dado nenhum, e portanto nao esta sendo afirmada - esta sendo
torcida. E uma sem contra-tese nunca foi olhada pelo outro lado.

`counter_thesis` merece o paragrafo dele
----------------------------------------
E o campo que mais custa a preencher e o que mais vale. Escrever "o caso do
outro lado" obriga a formular o argumento de quem venderia a acao, na versao
forte dele - e a versao forte e a unica que informa alguma coisa. A versao
fraca, aquela que se derruba em uma frase, e a que todo relatorio de sell-side
tem, e ela existe para dar a impressao de que o outro lado foi considerado.

Auditabilidade
--------------
`supporting_claims` aponta para claims, que apontam para `EvidenceLink`, que
apontam para `result_id` do motor e `unit_id` de trecho verbatim. A cadeia
inteira e conferivel: `pat provenance` leva o numero ate os bytes,
`pat provenance-unit` reextrai o trecho e compara byte a byte.

`ThesisAudit` percorre essa cadeia e diz onde ela se rompe. Uma tese que cita
um claim que nao existe, ou um claim cuja evidencia nao resolve, e uma tese
que parece sustentada.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen
from pat.contracts.opportunity.base import Actor, Slug
from pat.contracts.opportunity.hypothesis import HypothesisStrength

__all__ = [
    "Catalyst",
    "InvestmentThesis",
    "Risk",
    "RiskSeverity",
    "ThesisAudit",
    "ThesisAuditIssue",
    "ThesisDirection",
    "ThesisDrafted",
    "ThesisIssue",
]


class ThesisDirection(StrEnum):
    """O que a tese conclui. Inclui nao concluir.

    `NO_POSITION` nao e falha: uma investigacao que termina dizendo "os dados
    nao sustentam nem um lado nem o outro" fez o trabalho. Um sistema que so
    permitisse LONG ou SHORT pressionaria toda tese para um extremo, e a
    pressao apareceria como conviccao.
    """

    LONG = "long"
    SHORT = "short"
    NO_POSITION = "no_position"
    """Investigado e sem conclusao acionavel."""

    WATCH = "watch"
    """Sem posicao hoje, com gatilho declarado que mudaria isso."""


class RiskSeverity(StrEnum):
    THESIS_BREAKING = "thesis_breaking"
    """Se ocorrer, a tese acaba. Nao e "risco relevante": e falsificador com
    outro nome, e por isso ele tambem aparece em `falsifiers`."""

    MATERIAL = "material"
    MONITORABLE = "monitorable"


class Risk(Frozen):
    """Um risco, com severidade e - quando existe - o que o observaria.

    `leading_indicator` e opcional de proposito: ha riscos que nao tem sinal
    antecedente, e inventar um seria pior que admitir que nao ha. O que nao e
    opcional e dizer o quao grave e.
    """

    slug: Slug
    text: str = Field(min_length=1)
    severity: RiskSeverity
    leading_indicator: str | None = Field(
        default=None, description="O que se olharia para ver o risco chegando"
    )
    evidence_refs: tuple[str, ...] = ()


class Catalyst(Frozen):
    """Um evento que mudaria o preco, e quando ele e esperado.

    `expected_by` e uma data ou `None`. `None` quer dizer "sem prazo" e nao
    "em breve": um catalisador sem prazo e uma esperanca, e a diferenca
    precisa aparecer no papel.
    """

    slug: Slug
    text: str = Field(min_length=1)
    expected_by: date | None = None
    is_within_control: bool = Field(
        default=False,
        description="A companhia controla, ou depende de terceiro/mercado",
    )


class InvestmentThesis(Frozen):
    """A tese. Persistente, versionada, auditavel.

    Todos os campos obrigatorios existem porque a ausencia de cada um e uma
    forma conhecida de a tese parecer mais forte do que e.
    """

    thesis_version: Literal["v1"] = "v1"
    slug: Slug
    statement: str = Field(min_length=1, description="A tese em uma frase")
    direction: ThesisDirection
    confidence: HypothesisStrength

    supporting_claims: tuple[Slug, ...] = Field(
        default=(), description="Claims que a sustentam. Cada um resolve ate a evidencia"
    )
    supporting_hypotheses: tuple[Slug, ...] = ()

    key_assumptions: tuple[str, ...] = Field(
        min_length=1,
        description="De que a tese depende. Vazio esconderia de que ela depende",
    )
    risks: tuple[Risk, ...] = Field(
        min_length=1, description="Vazio afirmaria que nao ha nenhum"
    )
    falsifiers: tuple[str, ...] = Field(
        min_length=1,
        description="O que a derrubaria. Sem isso a tese nao esta sendo afirmada",
    )
    counter_thesis: str = Field(
        min_length=1,
        description="O caso do outro lado, na versao forte. A fraca da so a impressao",
    )
    unresolved: tuple[str, ...] = Field(
        default=(), description="O que ficou em aberto. Pode ser vazio; o campo nunca some"
    )
    catalysts: tuple[Catalyst, ...] = ()
    valuation: Slug | None = Field(
        default=None, description="Modelo de valuation que a acompanha, quando ha"
    )

    as_of: date
    author: Actor
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> "InvestmentThesis":
        # Risco que quebra a tese E um falsificador. Deixar os dois se
        # divergirem produziria uma tese que lista o que a mata na secao de
        # riscos e jura, na secao de falsificadores, que nada a mata.
        quebram = tuple(
            r for r in self.risks if r.severity is RiskSeverity.THESIS_BREAKING
        )
        for risco in quebram:
            if not any(risco.text in f or f in risco.text for f in self.falsifiers):
                raise ValueError(
                    f"o risco {risco.slug!r} e THESIS_BREAKING e nao aparece entre os "
                    "falsificadores. Um risco que mata a tese e um falsificador com "
                    "outro nome, e listar so num dos dois lugares deixa a tese "
                    "afirmando que nada a derruba."
                )
        if self.direction is ThesisDirection.WATCH and not self.catalysts:
            raise ValueError(
                f"a tese {self.slug!r} e WATCH e nao declara gatilho. WATCH quer dizer "
                "'sem posicao hoje, COM gatilho'; sem ele, e NO_POSITION."
            )
        return self

    @property
    def is_actionable(self) -> bool:
        return self.direction in (ThesisDirection.LONG, ThesisDirection.SHORT)

    @property
    def thesis_breaking_risks(self) -> tuple[Risk, ...]:
        return tuple(
            r for r in self.risks if r.severity is RiskSeverity.THESIS_BREAKING
        )


class ThesisIssue(StrEnum):
    """Onde a cadeia de auditoria se rompe. Conjunto fechado."""

    CLAIM_MISSING = "claim_missing"
    """A tese cita um claim que nao existe no workspace."""

    HYPOTHESIS_MISSING = "hypothesis_missing"

    CLAIM_WITHOUT_EVIDENCE = "claim_without_evidence"
    """O claim existe e nao aponta para nada. O contrato de `Claim` impede
    isso na criacao; aqui a checagem cobre um estado montado por outro
    caminho."""

    HYPOTHESIS_REJECTED = "hypothesis_rejected"
    """A tese se apoia numa hipotese que foi rejeitada. Acontece quando a
    hipotese cai DEPOIS de a tese ter sido escrita, e e exatamente o caso que
    ninguem revisa sozinho."""

    HYPOTHESIS_OPEN = "hypothesis_open"
    """Apoia-se em hipotese que nunca foi testada."""

    VALUATION_MISSING = "valuation_missing"

    BLOCKING_CRITIQUE = "blocking_critique"
    """O advogado do diabo tem objecao dura em aberto sobre uma hipotese que
    a tese usa."""

    OPEN_QUESTIONS_UNLISTED = "open_questions_unlisted"
    """Ha perguntas em aberto na agenda que a tese nao menciona em
    `unresolved`. A tese fica parecendo mais completa do que a investigacao
    esta."""


class ThesisAuditIssue(Frozen):
    issue: ThesisIssue
    message: str = Field(min_length=1)
    remedy: str = Field(min_length=1)
    ref: str | None = Field(default=None, description="Claim, hipotese ou modelo citado")


class ThesisAudit(Frozen):
    """O resultado de percorrer a cadeia da tese ate a evidencia.

    `resolves` e a pergunta que importa: toda afirmacao da tese chega a um
    numero do motor ou a um trecho verbatim? Uma tese que nao resolve nao e
    necessariamente falsa - e nao e defensavel, que e outra coisa e o que
    importa numa mesa.
    """

    thesis: Slug
    issues: tuple[ThesisAuditIssue, ...] = ()
    claims_checked: int = Field(default=0, ge=0)
    evidence_refs: tuple[str, ...] = Field(
        default=(), description="Todos os enderecos que a tese alcanca, sem repeticao"
    )

    @property
    def resolves(self) -> bool:
        return not self.issues

    def of(self, issue: ThesisIssue) -> tuple[ThesisAuditIssue, ...]:
        return tuple(i for i in self.issues if i.issue is issue)


class ThesisDrafted(Frozen):
    """Evento: a tese foi escrita, ou reescrita.

    Reescrever nao substitui: cada versao entra no diario ao lado da anterior,
    e o estado corrente e a ultima. Uma tese que muda de LONG para NO_POSITION
    e a informacao mais valiosa que o diario guarda, e sobrescrever a apagaria.
    """

    kind: Literal["thesis_drafted"] = "thesis_drafted"
    thesis: InvestmentThesis

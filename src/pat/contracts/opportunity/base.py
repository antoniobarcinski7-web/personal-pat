"""Contratos da camada Opportunity: o que um analista sabe, supoe e conclui.

Esta camada nao produz numero. O PAT produz numero. O que vive aqui e o
estado de uma investigacao: o que foi perguntado, o que foi encontrado, o que
foi suposto e o que ainda nao se sabe.

A separacao que da nome ao modulo
---------------------------------
Seis coisas distintas, e nenhuma delas se confunde com a outra por descuido de
tipo:

    FACT         numero do gold, bitemporal, com procedencia    (Fase 1-2)
    EVIDENCE     trecho verbatim de documento, com `as_of`      (Fase 5)
    HYPOTHESIS   afirmacao ainda em teste, com falsificadores
    ASSUMPTION   escolha do analista, declarada, nunca derivada
    CLAIM        afirmacao ancorada em fato ou evidencia
    CONCLUSION   claim que sobreviveu a etapa de falsificacao

`Hypothesis` nao tem campo de valor. `Assumption` tem valor mas carrega
`AssumptionBasis` dizendo de onde veio, e `JUDGMENT` e um valor legitimo -
o que nao e legitimo e uma suposicao que se apresenta como leitura. E o mesmo
desenho de `QuoteClaim`, que nao tem `value` justamente para que numero de
emissor nao possa virar insumo: a garantia e de tipo, nao de disciplina.

Por que o estado e um diario, e nao um registro
-----------------------------------------------
Nada aqui e sobrescrito. Uma hipotese que passa de OPEN para REJECTED nao e
uma linha atualizada: e um evento novo ao lado do antigo, e o estado corrente
e a dobra (fold) da sequencia. E a mesma regra do bronze, aplicada a
raciocinio - e existe pela mesma razao. Uma tese de investimento cuja historia
foi sobrescrita nao pode ser auditada, e uma tese que nao pode ser auditada
nao pode ser defendida: some exatamente a informacao de que o analista mudou
de ideia, que e a informacao que mais importa quando a tese falha.

Por isso todo evento carrega `actor`. Quem afirmou importa: um numero do motor
e uma leitura de documento sao coisas de forca diferente, e uma frase que o
agente escreveu sozinho e mais fraca que as duas.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen

WORKSPACE_ID_PATTERN = r"^[0-9a-f]{16}$"
_WORKSPACE_ID_RE = re.compile(WORKSPACE_ID_PATTERN)

# `slug` e o identificador que um humano digita: tarefa, hipotese, premissa.
# Curto, estavel e sem barra, ponto ou espaco - porque ele aparece em linha de
# comando e nunca deve poder virar um caminho de arquivo.
SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
_SLUG_RE = re.compile(SLUG_PATTERN)


def _check_workspace_id(v: str) -> str:
    if not _WORKSPACE_ID_RE.match(v):
        raise ValueError(f"workspace_id invalido (16 hex minusculos): {v!r}")
    return v


def _check_slug(v: str) -> str:
    if not _SLUG_RE.match(v):
        raise ValueError(
            f"slug invalido {v!r}: use [a-z0-9] seguido de [a-z0-9_-], ate 63 caracteres"
        )
    return v


def check_slug(v: str) -> str:
    """Porta publica do teste de slug. Mesma razao de `check_workspace_id`."""
    return _check_slug(v)


def check_workspace_id(v: str) -> str:
    """Porta publica do mesmo teste. Chamada antes de o valor virar caminho de
    arquivo - e nao depois, com um `..` ja resolvido pelo sistema operacional."""
    return _check_workspace_id(v)


WorkspaceId = Annotated[str, AfterValidator(_check_workspace_id)]
Slug = Annotated[str, AfterValidator(_check_slug)]


class Actor(StrEnum):
    """Quem afirmou. Entra em todo evento do diario.

    Nao e metadado de auditoria: e forca probatoria. Uma conclusao sustentada
    so por `AGENT` e uma conclusao sem ancora, e a critica mecanica da O7 sabe
    disso.
    """

    USER = "user"
    """O analista humano. Instrucoes, premissas e vereditos."""

    AGENT = "agent"
    """O agente. Pode planejar, ler e propor; nunca calcular."""

    ENGINE = "engine"
    """O PAT deterministico. Numero, decomposicao, evidencia verbatim.
    O unico ator cuja saida e reproduzivel bit a bit."""


class WorkspaceStatus(StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"
    """Encerrado. O diario continua legivel; nao aceita evento novo."""


class CompanyProfile(Frozen):
    """A empresa do workspace, como o catalogo a conhecia no momento do init.

    E copia, e nao referencia, de proposito: um workspace precisa continuar
    legivel depois que o warehouse for reconstruido, e o nome de exibicao que
    aparece numa tese antiga deve ser o que estava la quando ela foi escrita.

    `jurisdiction` e campo, e nao derivacao do prefixo de `entity_id`. Ler
    jurisdicao de prefixo e a mesma classe de erro que casar conta por rotulo:
    funciona ate o dia em que alguem muda o formato do identificador.
    """

    entity_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2")
    identifiers: tuple[tuple[str, str], ...] = Field(
        default=(), description="(scheme, local_id), ex. ('cod_cvm', '14826')"
    )

    @model_validator(mode="after")
    def _check(self) -> "CompanyProfile":
        if self.jurisdiction != self.jurisdiction.upper():
            raise ValueError(f"jurisdicao em maiusculas: {self.jurisdiction!r}")
        vistos = [s for s, _ in self.identifiers]
        if len(vistos) != len(set(vistos)):
            raise ValueError(f"scheme repetido em identifiers: {vistos}")
        return self

    def local_id(self, scheme: str) -> str | None:
        for s, local in self.identifiers:
            if s == scheme:
                return local
        return None


class CoverageSnapshot(Frozen):
    """O que o PAT tinha sobre a empresa num instante, resumido.

    Deliberadamente um resumo, e nao o `CompanyWorkspace` inteiro: o diario
    guarda a historia da investigacao, nao uma copia do warehouse. O que
    precisa sobreviver e o suficiente para responder "o que eu sabia quando
    escrevi isso?" - e `workspace_sha256` responde exatamente isso, porque e
    ele que distingue "a resposta mudou" de "os dados mudaram".

    `gaps` fica porque cobertura que so mostra o que existe mente sobre si
    mesma, e a mentira se le como evidencia de ausencia.
    """

    workspace_sha256: str = Field(min_length=1)
    state: str = Field(min_length=1, description="'draft' ou 'ready'")
    as_of: date | None = None
    facts: int = Field(default=0, ge=0)
    period_ends: tuple[date, ...] = ()
    metrics_available: tuple[str, ...] = ()
    missing_concepts: tuple[str, ...] = ()
    documents: int = Field(default=0, ge=0)
    units_indexed: int = Field(default=0, ge=0)
    extraction_failures: int = Field(default=0, ge=0)
    gaps: tuple[tuple[str, str], ...] = Field(
        default=(), description="(code, remedy), na ordem em que o build reportou"
    )
    refreshed_at: AwareDatetime


# ---------------------------------------------------------------------------
# Eventos do diario
#
# Um corpo por transicao possivel. A uniao e discriminada por `kind`, e cada
# corpo e `extra="forbid"`: um campo a mais falha na leitura do diario, e nao
# tres camadas adiante, ja como conclusao errada num relatorio.
# ---------------------------------------------------------------------------


class WorkspaceCreated(Frozen):
    kind: Literal["workspace_created"] = "workspace_created"
    company: CompanyProfile
    as_of: date
    title: str | None = None
    mandate: str | None = Field(
        default=None, description="O desafio de investimento, em uma frase"
    )


class MandateSet(Frozen):
    kind: Literal["mandate_set"] = "mandate_set"
    mandate: str = Field(min_length=1)


class TitleSet(Frozen):
    kind: Literal["title_set"] = "title_set"
    title: str = Field(min_length=1)


class AsOfAdvanced(Frozen):
    """`as_of` so anda para frente, e so por evento explicito.

    Uma investigacao dura semanas; congelar `as_of` para sempre tornaria o
    workspace cego para o que a empresa publicou depois. Mas mover `as_of` em
    silencio - por relogio, a cada abertura - trocaria a visao do mundo entre
    dois turnos da mesma conversa, e a conclusao de ontem passaria a citar
    documento de hoje sem que ninguem tivesse decidido isso.

    Andar so para frente e o que mantem a citacao valida: evidencia admitida
    sob um `as_of` continua admissivel sob um posterior. O contrario nao vale,
    e por isso retroceder exige workspace novo.
    """

    kind: Literal["as_of_advanced"] = "as_of_advanced"
    as_of: date
    reason: str = Field(min_length=1)


class CoverageRefreshed(Frozen):
    kind: Literal["coverage_refreshed"] = "coverage_refreshed"
    coverage: CoverageSnapshot


class NoteAdded(Frozen):
    """Prosa livre no diario. Nao sustenta claim nenhum.

    Existe porque conversa tem partes que nao sao nem pergunta nem achado, e
    forcar tudo a virar hipotese produziria hipotese lixo - que e pior do que
    nao registrar.
    """

    kind: Literal["note_added"] = "note_added"
    text: str = Field(min_length=1)


class WorkspaceArchived(Frozen):
    kind: Literal["workspace_archived"] = "workspace_archived"
    reason: str | None = None


class WorkspaceReopened(Frozen):
    kind: Literal["workspace_reopened"] = "workspace_reopened"


class OpportunityWorkspace(Frozen):
    """O cabecalho do workspace: identidade e escopo, ja dobrados.

    Nao contem a pesquisa. `OpportunityState` (em `opportunity/state.py`) e
    quem junta este cabecalho com agenda, hipoteses, valuation e tese - a
    separacao existe porque `pat opportunity status` precisa listar workspaces
    sem reconstruir a investigacao inteira de cada um.
    """

    workspace_version: Literal["v1"] = "v1"
    workspace_id: WorkspaceId
    company: CompanyProfile
    as_of: date
    status: WorkspaceStatus = WorkspaceStatus.OPEN
    title: str | None = None
    mandate: str | None = None
    coverage: CoverageSnapshot | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    seq: int = Field(ge=1, description="Quantos eventos foram dobrados neste estado")

    @property
    def entity_id(self) -> str:
        return self.company.entity_id

    @property
    def jurisdiction(self) -> str:
        return self.company.jurisdiction


class Note(Frozen):
    """Prosa livre ja dobrada, com quem escreveu e quando.

    Carrega `seq` porque a ordem no diario e a unica ordem que existe: dois
    eventos gravados no mesmo segundo tem `at` igual, e ordenar por relogio
    trocaria a sequencia de um raciocinio.
    """

    seq: int = Field(ge=1)
    at: AwareDatetime
    actor: Actor
    text: str = Field(min_length=1)

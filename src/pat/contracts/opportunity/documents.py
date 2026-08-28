"""O4 - a camada de documento, sem jurisdicao.

O problema que este modulo resolve nao e "como buscar documento". E:

    **como um agente distingue "esta empresa nunca falou disso" de
    "este sistema nao sabe ler os documentos dela".**

As duas coisas chegam ao agente como a mesma ausencia - nenhum trecho
encontrado - e pedem acoes opostas. A primeira e um achado, e pode entrar na
tese. A segunda e uma lacuna do PAT, e entrar na tese como achado seria
inventar informacao a partir do proprio silencio.

Por isso um provider NUNCA devolve lista vazia. Ele devolve os documentos, ou
um `ProviderUnavailable` com capacidade nomeada, motivo e remedio. E a mesma
disciplina de `MetricUnavailable` e `EvidenceUnavailable`, aplicada um nivel
acima: agora a coisa que pode faltar e o proprio provider.

Capacidades declaradas, e nao inferidas
---------------------------------------
`ProviderCapability` existe porque as tres etapas - descobrir o que existe,
trazer os bytes, virar unidade citavel - falham de maneiras diferentes e sao
resolvidas por trabalhos diferentes. Um provider que sabe buscar bytes mas nao
sabe descobrir o que buscar nao esta "quebrado": ele esta parcialmente pronto,
e dizer QUAL parte e o que permite a alguem terminar o trabalho.

Um provider que declarasse simplesmente "indisponivel" mandaria o leitor
reimplementar tudo. Um que nao declarasse nada faria o agente concluir, de uma
lista vazia, que a empresa e calada.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from pat.contracts.common import Frozen, SourceTier
from pat.contracts.corpus import DateBasis, DocumentKind

__all__ = [
    "DiscoveredDocument",
    "ProviderCapability",
    "ProviderProfile",
    "ProviderUnavailable",
    "ProviderUnavailableReason",
]


class ProviderCapability(StrEnum):
    """As tres etapas entre "existe um documento" e "da para cita-lo".

    Separadas porque falham por razoes diferentes e sao consertadas por
    trabalhos diferentes.
    """

    DISCOVER = "discover"
    """Listar o que a empresa publicou, com data e tipo. Exige um indice - e o
    indice e por regime."""

    FETCH = "fetch"
    """Trazer os bytes e grava-los no bronze, com procedencia."""

    EXTRACT = "extract"
    """Virar unidade citavel. Depende do formato: o extrator le PDF, e um
    regime que publica em outro formato nao passa por aqui - o que e uma
    lacuna nomeada, nunca um fallback silencioso."""


class ProviderUnavailableReason(StrEnum):
    """Por que uma capacidade nao esta disponivel. Conjunto fechado."""

    NOT_IMPLEMENTED = "not_implemented"
    """A etapa nao existe no codigo para este regime. Nao e erro nem falha de
    rede: e trabalho que ainda nao foi feito, e o remedio diz qual."""

    UNSUPPORTED_MEDIA = "unsupported_media"
    """Os bytes existem e o extrator nao le este formato. Nao ha fallback -
    nem OCR, nem uma segunda biblioteca: citacao vinda de reconhecimento
    optico e texto que ninguem escreveu."""

    CATALOG_MISSING = "catalog_missing"
    """O indice do periodo nao esta no bronze. Diferente das duas anteriores:
    esta se resolve rodando um comando, e o remedio o nomeia."""

    NEEDS_NETWORK = "needs_network"
    """A operacao exige a fonte publica, e a chamada foi feita sem rede."""


class ProviderProfile(Frozen):
    """O que um provider sabe fazer, declarado.

    Existe para que o agente possa PERGUNTAR antes de tentar, e para que a
    resposta seja a mesma que ele obteria tentando. Um perfil que mentisse
    para mais faria o agente prometer uma pesquisa que nao acontece.
    """

    provider_id: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=2, max_length=2)
    capabilities: tuple[ProviderCapability, ...] = ()
    missing: tuple["ProviderUnavailable", ...] = Field(
        default=(),
        description="Uma entrada por capacidade ausente, com motivo e remedio",
    )
    kinds_supported: tuple[DocumentKind, ...] = Field(
        default=(), description="Tipos que a descoberta deste regime enxerga"
    )

    @model_validator(mode="after")
    def _check(self) -> "ProviderProfile":
        if self.jurisdiction != self.jurisdiction.upper():
            raise ValueError(f"jurisdicao em maiusculas: {self.jurisdiction!r}")
        declaradas = set(self.capabilities)
        ausentes = {m.capability for m in self.missing}
        if declaradas & ausentes:
            raise ValueError(
                f"{self.provider_id} declara e nega as mesmas capacidades: "
                f"{sorted(c.value for c in declaradas & ausentes)}"
            )
        # Toda capacidade tem que estar de um lado ou do outro. Silencio sobre
        # uma etapa e exatamente o que faz o agente descobrir a lacuna tarde,
        # depois de ja ter prometido a pesquisa.
        faltando = set(ProviderCapability) - declaradas - ausentes
        if faltando:
            raise ValueError(
                f"{self.provider_id} nao diz nada sobre "
                f"{sorted(c.value for c in faltando)}. Toda capacidade e "
                "declarada ou negada com motivo; nenhuma fica em silencio."
            )
        return self

    def can(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    def why_not(self, capability: ProviderCapability) -> "ProviderUnavailable | None":
        return next((m for m in self.missing if m.capability is capability), None)


class ProviderUnavailable(Frozen):
    """Uma capacidade que este provider nao tem, e o que fazer a respeito.

    `remedy` e obrigatorio. Sem ele, o registro diria ao agente que pare, sem
    dizer a uma pessoa por onde continuar - e a lacuna sobreviveria a cada
    leitura do relatorio.
    """

    provider_id: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=2, max_length=2)
    capability: ProviderCapability
    reason: ProviderUnavailableReason
    message: str = Field(min_length=1)
    remedy: str = Field(min_length=1)


class DiscoveredDocument(Frozen):
    """Um documento que EXISTE na fonte e ainda nao esta no corpus.

    Distinto de `SourceDocument`, que ja tem bytes, hash e unidades. A
    diferenca importa: um documento descoberto nao e citavel, e trata-lo como
    se fosse deixaria o agente prometer uma citacao que nao existe. So o
    `document_id` - o sha256 dos bytes - transforma um no outro, e ele so
    aparece depois do fetch.

    `external_id` e OPACO aqui. E como o regime chama aquele arquivamento; o
    Opportunity o carrega para poder pedir o fetch, e nunca o interpreta.
    """

    provider_id: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=2, max_length=2)
    external_id: str = Field(min_length=1, description="Identificador do regime, opaco")
    title: str = Field(min_length=1)
    kind: DocumentKind
    published_at: date
    published_at_basis: DateBasis
    source_tier: SourceTier
    source_url: str = Field(min_length=1)
    reference_date: date | None = Field(
        default=None,
        description=(
            "O campo de data do protocolo. NAO e periodo coberto: derivar "
            "periodo daqui e inferencia por formato"
        ),
    )
    origin_category: str | None = Field(
        default=None, description="A categoria crua do regime, preservada sem interpretacao"
    )


ProviderProfile.model_rebuild()

"""Contratos da camada de corpus (L1.5): o documento qualitativo, tipado.

Nada neste modulo importa `pat.corpus.*`, `pat.research.*`, `pat.semantics.*`,
`pat.query` ou `pat.store`. Ele so conhece `contracts.common` - e e isso que
permite conferir a forma de um documento, de uma unidade e de uma citacao sem
banco, sem rede e sem modelo.

A simetria que este arquivo existe para manter
-----------------------------------------------
A Fase 5 faz para evidencia o que a Fase 3 fez para numero:

    Fact              <->  DocumentUnit
    MetricResult      <->  EvidenceResult
    MetricUnavailable <->  EvidenceUnavailable
    NumericClaim      <->  QuoteClaim

O paralelo mais importante e o ultimo, e ele e uma separacao, nao uma
analogia: `NumericClaim` carrega um numero que o motor calculou;
`QuoteClaim` carrega texto que alguem publicou. Um release diz "receita de
R$ 511,9 bilhoes", e esse algarismo pode aparecer num relatorio - entre aspas,
atribuido a quem o disse, resolvendo ate o byte no bronze. O que ele nao pode
e virar insumo de conta.

Por isso `QuoteClaim` nao tem campo numerico, nao tem `Decimal`, nao tem
unidade e nao tem moeda. Nao e uma regra de prompt nem uma checagem tardia: e
a forma do tipo. Um numero de emissor nao consegue entrar numa derivacao
porque nao existe onde ele seria lido como numero - a mesma tecnica que faz
`MetricStep` nao conseguir expressar "receita = 19 bilhoes".

Os dois eixos de tempo, de novo
-------------------------------
`published_at` e o `knowledge_date` do lado textual, e `reference_date` e o
periodo a que o documento se refere. Sao distintos pela mesma razao que
`period_end` e `knowledge_date` sao distintos num `Fact`: um release do 4T23
publicado em marco de 2024 fala de dezembro de 2023. Consulta ao corpus sem
`as_of` nao existe, e citar documento publicado depois do `as_of` e o
vazamento point-in-time mais facil de cometer - porque ele deixa a resposta
*melhor*.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen, Sha256, SourceTier

# ---------------------------------------------------------------------------
# Vocabulario do documento
# ---------------------------------------------------------------------------


class DocumentKind(StrEnum):
    """Que tipo de documento e, do ponto de vista de research.

    Conjunto fechado e universal: nao menciona "IPE", "Categoria" nem
    qualquer vocabulario da CVM, pela mesma razao que `concepts.py` nao
    menciona `cd_conta`. A traducao de um vocabulario de regulador para este
    enum e uma afirmacao declarada no adapter daquele regime, nunca uma
    deducao por semelhanca de rotulo.
    """

    PERFORMANCE_REPORT = "performance_report"
    """Release de resultados / relatorio de desempenho do trimestre."""

    PRESENTATION = "presentation"
    TRANSCRIPT = "transcript"
    """Transcricao de teleconferencia. Estruturada por turno de fala."""

    PRODUCTION_REPORT = "production_report"
    """Relatorio operacional (producao e vendas). Separado do financeiro
    porque a evidencia operacional responde perguntas diferentes."""

    MATERIAL_FACT = "material_fact"
    MARKET_ANNOUNCEMENT = "market_announcement"
    SHAREHOLDER_NOTICE = "shareholder_notice"
    GOVERNANCE = "governance"
    FILING = "filing"

    OTHER = "other"
    """Categoria conhecida da origem que ainda nao tem traducao declarada.

    Existe para que um documento sem classificacao afirmada continue
    armazenado e citavel, marcado como nao classificado. O que nao pode e um
    documento ser silenciosamente empurrado para a classe mais parecida.
    """


class DateBasis(StrEnum):
    """De onde veio uma data. Obrigatorio junto de toda data do corpus.

    Data de publicacao e o `knowledge_date` do texto: e ela que decide se um
    documento existe ou nao numa consulta `AS OF`. Uma data adivinhada que se
    apresenta como lida e o analogo textual do numero aproximado que se
    apresenta como exato - por isso a base viaja junto, ate a citacao.
    """

    DOCUMENT_STATED = "document_stated"
    """Lida do proprio documento."""

    FILING_METADATA = "filing_metadata"
    """Declarada pelo emissor no protocolo de entrega ao regulador."""

    RETRIEVED_AT_FALLBACK = "retrieved_at_fallback"
    """Nao havia data confiavel: usou-se o instante da busca, que e um limite
    SUPERIOR. Um documento assim e mais novo do que parece, nunca mais
    velho - entao ele pode ser indevidamente excluido de um `AS OF`, jamais
    indevidamente incluido. Erra para o lado seguro, e diz que errou."""


class SpeakerRole(StrEnum):
    """Quem falou. So faz sentido em transcricao.

    Resposta de CFO no Q&A e slide de apresentacao nao tem o mesmo peso
    probatorio, e o sistema tem que saber a diferenca antes de o escritor
    citar as duas com a mesma naturalidade.
    """

    MANAGEMENT = "management"
    ANALYST = "analyst"
    OPERATOR = "operator"
    UNKNOWN = "unknown"


class LocatorScheme(StrEnum):
    """Como uma unidade e endereçada dentro do documento."""

    PDF_PAGE = "pdf_page"
    HTML_NODE = "html_node"
    TRANSCRIPT_TURN = "transcript_turn"


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------


class SourceDocument(Frozen):
    """Um documento qualitativo, como objeto de research.

    `document_id` E o sha256 do conteudo, e nao um id proprio: e a mesma
    decisao de `RawDocument`, e o que faz uma reapresentacao de documento ser
    um documento novo em vez de uma edicao do antigo. O bronze continua sendo
    a fonte de verdade; isto aqui e a interpretacao tipada dele.
    """

    document_id: Sha256 = Field(description="sha256 dos bytes; = RawDocument.content_sha256")
    entity_id: str = Field(min_length=1)

    kind: DocumentKind
    title: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=8, description="Tag BCP 47 simplificada")

    source_tier: SourceTier
    published_at: date
    published_at_basis: DateBasis

    reference_date: date | None = None
    """A que data o emissor disse que o documento se refere.

    NAO e periodo coberto: o dado bruto de um protocolo de entrega diz
    "referencia 2024-09-30" tanto para um relatorio do 3T24 quanto para uma
    ata de assembleia marcada naquele dia. Derivar periodo disso seria
    inferencia por formato, que e o mesmo erro de casar conta por rotulo.
    Periodo coberto entra quando houver base para afirma-lo, e com base
    propria."""
    reference_date_basis: DateBasis | None = None

    media_type: str = Field(min_length=1, description="Detectado dos bytes, nao do header HTTP")
    byte_size: int = Field(ge=0)

    provider_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    resource_key: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    retrieval_id: str = Field(min_length=1)

    origin_category: str | None = None
    """A classificacao literal da origem ('Fato Relevante'), preservada para
    conferencia humana da traducao para `kind`. Registrada e conferivel,
    jamais usada para busca - conta renomeada tem que quebrar teste, e
    categoria renomeada tambem."""
    origin_version: str | None = None
    """Versao do documento na origem. A CVM aceita reapresentacao de
    documento; versoes diferentes tem bytes diferentes e portanto
    `document_id` diferente. As duas coexistem."""

    first_seen_at: AwareDatetime
    first_seen_run_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "SourceDocument":
        if (self.reference_date is None) != (self.reference_date_basis is None):
            raise ValueError(
                "reference_date e reference_date_basis andam juntos: uma data sem "
                "base declarada e indistinguivel de uma data adivinhada"
            )
        if self.reference_date is not None and self.reference_date > self.published_at:
            # Nao e impossivel (documento pode referir-se a evento futuro, como
            # uma assembleia convocada), entao nao e erro. E so nao ser tratado
            # como periodo, que e o que o campo ja diz nao ser.
            pass
        return self


# ---------------------------------------------------------------------------
# Unidade citavel
# ---------------------------------------------------------------------------


class UnitLocator(Frozen):
    """Onde a unidade comeca e termina, dentro do documento.

    Endereco verificavel, e nao descritivo: `char_start` e `char_end` sao
    offsets no texto extraido daquela pagina por aquele
    `extraction_version`. Reextrair com a mesma versao e fatiar tem que
    devolver exatamente `DocumentUnit.text`, byte a byte. E isso que faz uma
    citacao ser conferivel em vez de acreditavel.
    """

    scheme: LocatorScheme
    page: int | None = Field(default=None, ge=1, description="1-based, como o leitor conta")
    block: int | None = Field(default=None, ge=0, description="Ordinal do bloco dentro da pagina")
    node_path: str | None = None
    turn: int | None = Field(default=None, ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> "UnitLocator":
        if self.char_end <= self.char_start:
            raise ValueError(
                f"intervalo vazio ou invertido: [{self.char_start}, {self.char_end})"
            )
        if self.scheme is LocatorScheme.PDF_PAGE:
            if self.page is None or self.block is None:
                raise ValueError("locator de PDF exige `page` e `block`")
            if self.node_path is not None or self.turn is not None:
                raise ValueError("locator de PDF nao leva `node_path` nem `turn`")
        elif self.scheme is LocatorScheme.HTML_NODE:
            if not self.node_path:
                raise ValueError("locator de HTML exige `node_path`")
        elif self.scheme is LocatorScheme.TRANSCRIPT_TURN:
            if self.turn is None:
                raise ValueError("locator de transcricao exige `turn`")
        return self

    def as_text(self) -> str:
        """Forma legivel, estavel, para linha de comando e citacao."""
        if self.scheme is LocatorScheme.PDF_PAGE:
            return f"p.{self.page}#b{self.block}[{self.char_start}:{self.char_end}]"
        if self.scheme is LocatorScheme.HTML_NODE:
            return f"{self.node_path}[{self.char_start}:{self.char_end}]"
        return f"turn={self.turn}[{self.char_start}:{self.char_end}]"


class SpeakerRef(Frozen):
    name: str = Field(min_length=1)
    role: SpeakerRole
    is_qna: bool = False


class DocumentUnit(Frozen):
    """Um trecho endereçavel de um documento. A menor coisa citavel.

    `text` e guardado, e nao so referenciado por offsets, porque extracao de
    PDF nao e estavel entre versoes de biblioteca. Guardar as duas coisas
    permite as duas conferencias: o texto contra a citacao (ela e verbatim?) e
    os offsets contra o blob (ele veio mesmo dali?).

    Reprocessar com extrator novo cria unidades novas ao lado das antigas -
    `extraction_version` entra em `unit_id`. E a regra de `extractor_version`
    da Fase 1, e ela e o que faz uma citacao de seis meses atras continuar
    resolvendo depois de um upgrade de biblioteca.
    """

    unit_id: Sha256
    document_id: Sha256
    ordinal: int = Field(ge=0, description="Ordem de leitura dentro do documento")
    locator: UnitLocator
    text: str = Field(min_length=1)
    char_count: int = Field(gt=0)

    section_path: tuple[str, ...] = ()
    """So quando deduzivel da ESTRUTURA do documento (sumario, heading,
    ancora). Nunca inferida por modelo. Vazia quando nao da para afirmar - o
    analogo de `MetricUnavailable`, e nao de uma string vazia que se le como
    raiz."""

    speaker: SpeakerRef | None = None
    extraction_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "DocumentUnit":
        if self.char_count != len(self.text):
            raise ValueError(
                f"char_count={self.char_count} nao bate com len(text)={len(self.text)}"
            )
        span = self.locator.char_end - self.locator.char_start
        if span != self.char_count:
            raise ValueError(
                f"locator cobre {span} caracteres mas o texto tem {self.char_count}: "
                "o endereco tem que ser conferivel contra o texto"
            )
        return self


# ---------------------------------------------------------------------------
# Falha de extracao: registrada, nunca silenciada
# ---------------------------------------------------------------------------


class ExtractionFailureReason(StrEnum):
    """Por que um documento nao virou unidades.

    Enum proprio e obrigatorio porque a alternativa - documento sem unidade e
    sem explicacao - e indistinguivel de documento que nao existe. Um corpus
    que esconde o que nao conseguiu ler mente sobre a propria cobertura, e a
    mentira aparece como ausencia de evidencia, que e exatamente o que um
    analista leria como evidencia de ausencia.
    """

    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    ENCRYPTED = "encrypted"
    MALFORMED = "malformed"
    NO_TEXT_LAYER = "no_text_layer"
    """PDF sem camada de texto - tipicamente digitalizado. NAO existe fallback
    para OCR: uma citacao vinda de reconhecimento optico e um texto que
    ninguem escreveu, e ela entraria no sistema indistinguivel de uma
    citacao real."""
    EMPTY_TEXT = "empty_text"
    EXTRACTOR_ERROR = "extractor_error"


class ExtractionFailure(Frozen):
    document_id: Sha256
    reason: ExtractionFailureReason
    message: str = Field(min_length=1)
    extraction_version: str = Field(min_length=1)
    remedy: str | None = None
    failed_at: AwareDatetime


class ExtractionOutcome(Frozen):
    """Extrair ou falhar - e exatamente um dos dois.

    Mesma forma de `ComputationResult`: nao existe terceira possibilidade, e
    portanto nao existe forma que "extraiu mais ou menos" possa assumir.
    """

    document_id: Sha256
    extraction_version: str = Field(min_length=1)
    units: tuple[DocumentUnit, ...] = ()
    failure: ExtractionFailure | None = None

    @model_validator(mode="after")
    def _check(self) -> "ExtractionOutcome":
        if bool(self.units) == (self.failure is not None):
            raise ValueError(
                f"{self.document_id[:12]}: um ExtractionOutcome carrega unidades OU "
                "uma falha nomeada. Documento sem unidade e sem falha e uma "
                "ausencia que se le como 'nao ha nada a dizer'."
            )
        for unit in self.units:
            if unit.document_id != self.document_id:
                raise ValueError(f"unidade {unit.unit_id[:12]} e de outro documento")
            if unit.extraction_version != self.extraction_version:
                raise ValueError(f"unidade {unit.unit_id[:12]} tem outra versao de extracao")
        if self.failure is not None and self.failure.document_id != self.document_id:
            raise ValueError("falha e de outro documento")
        return self


# ---------------------------------------------------------------------------
# Citacao
# ---------------------------------------------------------------------------


class QuoteClaim(Frozen):
    """Um trecho verbatim, com o caminho de volta ate quem o publicou.

    NAO TEM CAMPO NUMERICO, e nao deve ganhar um.

    Um release diz "receita de R$ 511,9 bilhoes". Esse algarismo pode sair no
    relatorio, dentro desta citacao, atribuido ao emissor. O que ele nao pode
    e alimentar uma conta: numero de documento nao e fato do motor, nao entra
    no gold e nao vira insumo de derivacao. Sem `Decimal`, sem `value`, sem
    `unit`, sem `currency`, nao ha por onde ele ser lido como quantidade -
    do mesmo jeito que `MetricStep` nao tem onde escrever um valor.

    `text` e byte-identico a `DocumentUnit.text`. Nao "essencialmente igual",
    nao normalizado, nao reescrito: identico, e conferido mecanicamente.
    Citacao parafraseada nao e citacao.
    """

    claim_kind: Literal["quote"] = "quote"
    unit_id: Sha256
    document_id: Sha256
    text: str = Field(min_length=1)

    document_kind: DocumentKind
    published_at: date
    published_at_basis: DateBasis
    source_tier: SourceTier
    locator: UnitLocator
    speaker: SpeakerRef | None = None
    title: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Consulta ao corpus
# ---------------------------------------------------------------------------


class EvidenceQuery(Frozen):
    """O que se quer do corpus. `as_of` obrigatorio, sem default.

    A mesma regra de `ResearchQuestion` e de `Engine.compute`: nao existe
    consulta que devolva "o que a empresa disse" sem dizer segundo quando.
    """

    query_version: Literal["v1"] = "v1"
    entity_id: str = Field(min_length=1)
    terms: tuple[str, ...] = Field(min_length=1)
    as_of: date

    kinds: tuple[DocumentKind, ...] = ()
    published_from: date | None = None
    published_to: date | None = None
    speaker_roles: tuple[SpeakerRole, ...] = ()
    sections: tuple[str, ...] = Field(
        default=(),
        description=(
            "Restringe a busca a estas secoes do documento, pelo primeiro nivel "
            "de `section_path` - 'Item 1A', 'Item 7'. Vazio busca em tudo, "
            "INCLUSIVE nas unidades sem secao (capa, indice, rodape)"
        ),
    )
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def _check(self) -> "EvidenceQuery":
        if not any(term.strip() for term in self.terms):
            raise ValueError("consulta sem termo util")
        if len(self.terms) != len(set(self.terms)):
            raise ValueError(f"termos repetidos: {self.terms}")
        if self.published_to is not None and self.published_to > self.as_of:
            raise ValueError(
                f"published_to {self.published_to} e posterior ao as_of {self.as_of}: "
                "nao havia como saber daqueles documentos naquela data"
            )
        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_from > self.published_to
        ):
            raise ValueError("janela de publicacao invertida")
        if self.published_from is not None and self.published_from > self.as_of:
            raise ValueError(
                f"published_from {self.published_from} e posterior ao as_of {self.as_of}"
            )
        return self


class EvidenceHit(Frozen):
    """Um resultado de busca: a citacao, mais por que ela foi escolhida.

    `relevance` e escore de recuperacao, e nao grandeza financeira: nao tem
    unidade, nao tem moeda, nunca entra num calculo e nunca aparece na prosa.
    Ele existe para tornar o ranking auditavel - "por que este trecho voltou
    em primeiro" tem que ter resposta.
    """

    rank: int = Field(ge=1)
    relevance: Decimal
    matched_terms: tuple[str, ...] = Field(min_length=1)
    quote: QuoteClaim

    @model_validator(mode="after")
    def _check(self) -> "EvidenceHit":
        if not self.relevance.is_finite():
            raise ValueError("relevancia nao finita")
        return self


class EvidenceResult(Frozen):
    """O que a consulta devolveu, com o suficiente para reproduzi-la.

    `index_version` e `extraction_versions` viajam junto pela mesma razao que
    `mapping_sha256` viaja num `MetricResult`: os dois podem mudar o conjunto
    devolvido sem que a consulta tenha mudado.
    """

    query_id: Sha256
    entity_id: str
    as_of: date
    hits: tuple[EvidenceHit, ...] = ()
    index_version: str = Field(min_length=1)
    extraction_versions: tuple[str, ...] = ()
    documents_in_scope: int = Field(ge=0)
    units_in_scope: int = Field(ge=0)
    retrieved_at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> "EvidenceResult":
        for position, hit in enumerate(self.hits, start=1):
            if hit.rank != position:
                raise ValueError(f"rank fora de ordem: esperado {position}, veio {hit.rank}")
            if hit.quote.published_at > self.as_of:
                raise ValueError(
                    f"documento {hit.quote.document_id[:12]} publicado em "
                    f"{hit.quote.published_at}, depois do as_of {self.as_of}: "
                    "vazamento point-in-time"
                )
        return self


class EvidenceUnavailableReason(StrEnum):
    """Por que nao houve evidencia. Nunca lista vazia sem motivo.

    Mesma disciplina de `MetricUnavailable`: "nao encontrei nada" e uma
    resposta ambigua entre 'a empresa nunca falou disso', 'nao ingeri os
    documentos' e 'o indice esta desatualizado' - e as tres pedem acoes
    diferentes de quem esta pesquisando.
    """

    UNKNOWN_ENTITY = "unknown_entity"
    NO_DOCUMENTS_FOR_ENTITY = "no_documents_for_entity"
    NO_DOCUMENTS_AS_OF = "no_documents_as_of"
    NO_UNITS_EXTRACTED = "no_units_extracted"
    NO_MATCH = "no_match"
    INDEX_MISSING = "index_missing"


class EvidenceUnavailable(Frozen):
    reason: EvidenceUnavailableReason
    message: str = Field(min_length=1)
    entity_id: str
    as_of: date
    remedy: str | None = None
    documents_in_scope: int = Field(default=0, ge=0)
    documents_excluded_by_as_of: int = Field(default=0, ge=0)
    """Quantos documentos existem mas sao posteriores ao `as_of`.

    Numero util e honesto: distingue "a empresa nao falou disso" de "a empresa
    falou disso depois da data que voce pediu", que e a diferenca entre nao ter
    tese e estar olhando o passado corretamente."""


class DocumentCandidate(Frozen):
    """Um documento que a origem DIZ existir, antes de os bytes chegarem.

    Regime-neutro de proposito. O catalogo IPE da CVM e o historico de
    arquivamentos da SEC descrevem a mesma coisa - "existe um documento, aqui
    esta como busca-lo, e isto e o que sabemos sobre ele" - com vocabularios
    diferentes. Sem um tipo comum, `sync_documents` teria um ramo por regime, e
    o terceiro regime pediria um terceiro ramo.

    NAO tem `document_id`. O identificador de um documento e o sha256 do
    conteudo, e antes do fetch nao existe conteudo: um candidato e uma promessa
    de documento, e a promessa pode nao se cumprir (URL morta, 404, bytes
    vazios). Manter os dois tipos separados e o que impede o catalogo de virar
    um documento que ninguem leu.

    `params` sao os argumentos do `resolve()` do provider, e nao uma URL: quem
    monta URL e o provider, e um candidato que carregasse URL pronta faria a
    camada de catalogo conhecer a estrutura de endereco da origem.
    """

    dataset_id: str = Field(min_length=1, description="Dataset que busca os bytes")
    params: tuple[tuple[str, str], ...] = Field(
        description="Argumentos de `provider.resolve()`, como pares ordenados"
    )
    provider_id: str = Field(min_length=1)

    kind: DocumentKind
    title: str = Field(min_length=1)
    language: str = Field(min_length=2)

    published_at: date
    published_at_basis: DateBasis
    reference_date: date | None = None
    reference_date_basis: DateBasis | None = None

    resource_key: str = Field(min_length=1)
    source_url: str = Field(min_length=1, description="So para procedencia; nao e usada para buscar")
    origin_category: str = Field(
        min_length=1,
        description="O rotulo do REGULADOR: 'Fato Relevante', '10-K'. Nunca traduzido",
    )
    origin_version: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "DocumentCandidate":
        if self.reference_date is not None and self.reference_date_basis is None:
            raise ValueError(
                f"candidato {self.resource_key!r} tem `reference_date` sem base. "
                "Data adivinhada que se apresenta como lida e o analogo textual "
                "do numero aproximado que se apresenta como exato."
            )
        return self

    @property
    def fetch_params(self) -> dict[str, str]:
        return dict(self.params)


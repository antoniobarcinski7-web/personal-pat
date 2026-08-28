"""Catalogo IPE da CVM -> entradas tipadas. Camada silver, nunca provider.

O que este modulo le e um *catalogo*, nao um documento: um CSV por ano
listando o que cada companhia entregou, com data de entrega, categoria,
versao e o link para os bytes. O documento em si e buscado depois, por
`cvm.ipe_doc`.

A traducao que este modulo faz, e o cuidado que ela exige
---------------------------------------------------------
`Categoria` e vocabulario da CVM. `DocumentKind` e universal. A ponte entre os
dois e a tabela `_CATEGORIA_TO_KIND` - declarada, revisavel, e a unica coisa
neste arquivo que afirma equivalencia semantica.

Ela e uma tabela e nao uma heuristica pela mesma razao que um `[[binding]]`
exige `equivalence_basis`: casar "Fato Relevante" com `MATERIAL_FACT` por
semelhanca de rotulo funcionaria hoje e passaria a casar a coisa errada no dia
em que a CVM criasse "Fato Relevante - Reapresentacao". Categoria
desconhecida vira `OTHER`, explicitamente - o documento continua armazenado e
citavel, marcado como nao classificado. O que nao pode e ser empurrado em
silencio para a classe mais parecida.

A subclassificacao pelo assunto
-------------------------------
Um "Comunicado ao Mercado" pode ser um aviso de webcast ou o relatorio de
desempenho do trimestre. A CVM nao distingue os dois, e a distincao importa
para research. `_ASSUNTO_REFINEMENTS` faz esse refino - tambem por tabela
declarada, tambem conservador: sem casamento, fica a classificacao da
categoria. Nenhuma regra aqui *rebaixa* um documento; elas so promovem
`MARKET_ANNOUNCEMENT` a uma classe mais especifica quando o proprio emissor
disse do que se trata.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date
from typing import Iterator

from pydantic import Field

from pat.contracts.common import Frozen
from pat.contracts.corpus import DocumentKind

EXTRACTOR = "cvm_ipe"
EXTRACTOR_VERSION = "1.0.0"

_EXPECTED_COLUMNS = (
    "CNPJ_Companhia",
    "Nome_Companhia",
    "Codigo_CVM",
    "Data_Referencia",
    "Categoria",
    "Tipo",
    "Especie",
    "Assunto",
    "Data_Entrega",
    "Tipo_Apresentacao",
    "Protocolo_Entrega",
    "Versao",
    "Link_Download",
)

# Traducao declarada. Toda linha aqui e uma afirmacao humana de equivalencia,
# e o comentario ao lado e a razao dela - o analogo do `equivalence_basis`.
_CATEGORIA_TO_KIND: dict[str, DocumentKind] = {
    # Release de resultados. E a categoria sob a qual a Petrobras entrega o
    # "Relatorio de Desempenho" do trimestre.
    "Dados Econômico-Financeiros": DocumentKind.PERFORMANCE_REPORT,
    "Fato Relevante": DocumentKind.MATERIAL_FACT,
    "Comunicado ao Mercado": DocumentKind.MARKET_ANNOUNCEMENT,
    "Informação Prestada às Bolsas Estrangeiras": DocumentKind.FILING,
    "Aviso aos Acionistas": DocumentKind.SHAREHOLDER_NOTICE,
    "Aviso aos Debenturistas": DocumentKind.SHAREHOLDER_NOTICE,
    "Assembleia": DocumentKind.GOVERNANCE,
    "Reunião da Administração": DocumentKind.GOVERNANCE,
    "Estatuto Social": DocumentKind.GOVERNANCE,
    "Política de indicação": DocumentKind.GOVERNANCE,
    "Política de Transações entre Partes Relacionadas": DocumentKind.GOVERNANCE,
    "Regimento interno de Comitês": DocumentKind.GOVERNANCE,
    "Regimento Interno do Comitê de Auditoria Estatutário": DocumentKind.GOVERNANCE,
    "Regimento interno do Conselho Fiscal": DocumentKind.GOVERNANCE,
    "Carta Anual de Governança Corporativa": DocumentKind.GOVERNANCE,
    "Calendário de Eventos Corporativos": DocumentKind.SHAREHOLDER_NOTICE,
    "Relatório Proventos": DocumentKind.SHAREHOLDER_NOTICE,
    "Comunicação sobre demandas societárias": DocumentKind.MARKET_ANNOUNCEMENT,
    "Comunicação sobre Transação entre Partes Relacionadas": DocumentKind.MARKET_ANNOUNCEMENT,
    "Valores Mobiliários negociados e detidos (art. 11 da Instr. CVM nº 358)": (
        DocumentKind.SHAREHOLDER_NOTICE
    ),
    "Relatório de Sustentabilidade": DocumentKind.FILING,
}

# Refino por assunto, aplicado SOMENTE sobre categorias genericas. Cada padrao
# e ancorado em vocabulario que o proprio emissor usa no titulo do documento.
_ASSUNTO_REFINEMENTS: tuple[tuple[re.Pattern[str], DocumentKind], ...] = (
    (
        re.compile(r"relat[óo]rio de produ[çc][ãa]o e vendas", re.IGNORECASE),
        DocumentKind.PRODUCTION_REPORT,
    ),
    (
        re.compile(r"^(relat[óo]rio de )?desempenho(\s+financeiro)?\b", re.IGNORECASE),
        DocumentKind.PERFORMANCE_REPORT,
    ),
    (
        re.compile(r"^desempenho d[ao] .{0,40}\bn[oa] \d[ºo]?T\d", re.IGNORECASE),
        DocumentKind.PERFORMANCE_REPORT,
    ),
)

_REFINABLE = frozenset(
    {DocumentKind.MARKET_ANNOUNCEMENT, DocumentKind.MATERIAL_FACT, DocumentKind.FILING}
)


class IpeCatalogEntry(Frozen):
    """Uma linha do catalogo, tipada. Ainda nao e um documento.

    Vira `SourceDocument` so depois que os bytes forem buscados: `document_id`
    e o sha256 do conteudo, e antes do fetch nao existe conteudo. Manter os
    dois separados e o que impede o catalogo de virar uma promessa de
    documento que talvez nao exista.
    """

    cnpj: str = Field(min_length=1)
    denom_cia: str = Field(min_length=1)
    cod_cvm: int
    categoria: str = Field(min_length=1)
    kind: DocumentKind
    tipo: str = ""
    especie: str = ""
    assunto: str = ""
    reference_date: date | None = None
    delivered_at: date
    tipo_apresentacao: str = ""
    protocolo: str = Field(min_length=1)
    versao: str = ""
    url: str = Field(min_length=1)

    def as_candidate(self):
        """A entrada do catalogo IPE, no tipo regime-neutro do corpus.

        Existe para que `sync_documents` nao precise conhecer a CVM. O que ela
        traduz e vocabulario - `Categoria` vira `origin_category`,
        `Data_Entrega` vira `published_at` - e nao semantica: `kind` ja saiu da
        tabela declarada acima, e `title` continua sendo concatenacao do que a
        origem publicou.
        """
        from pat.contracts.corpus import DateBasis, DocumentCandidate

        return DocumentCandidate(
            dataset_id="cvm.ipe_doc",
            params=(("url", self.url), ("protocolo", self.protocolo)),
            provider_id="cvm",
            kind=self.kind,
            title=self.title,
            language="pt-BR",
            # `Data_Entrega` e o que a companhia declarou ao regulador. E
            # metadado de protocolo, e nao leitura do documento - por isso a
            # base e FILING_METADATA, e ela viaja ate a citacao.
            published_at=self.delivered_at,
            published_at_basis=DateBasis.FILING_METADATA,
            reference_date=self.reference_date,
            reference_date_basis=(
                DateBasis.FILING_METADATA if self.reference_date is not None else None
            ),
            resource_key=self.protocolo,
            source_url=self.url,
            origin_category=self.categoria,
            origin_version=self.versao or None,
        )

    @property
    def title(self) -> str:
        """Titulo utilizavel, sem inventar texto.

        O IPE deixa `Assunto` vazio em parte das entregas. Nesses casos o
        titulo cai para a combinacao dos campos que existem, na ordem em que a
        propria CVM os apresenta. Nao ha geracao de texto: so concatenacao do
        que foi declarado."""
        for candidate in (self.assunto, self.especie, self.tipo):
            if candidate.strip():
                return candidate.strip()
        return self.categoria


def classify(categoria: str, assunto: str) -> DocumentKind:
    """Categoria + assunto -> `DocumentKind`, por tabela declarada."""
    kind = _CATEGORIA_TO_KIND.get(categoria.strip(), DocumentKind.OTHER)
    if kind in _REFINABLE:
        subject = (assunto or "").strip()
        for pattern, refined in _ASSUNTO_REFINEMENTS:
            if pattern.search(subject):
                return refined
    return kind


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_catalog(payload: bytes, *, cod_cvm: int | None = None) -> list[IpeCatalogEntry]:
    """ZIP do catalogo anual -> entradas tipadas.

    `cod_cvm` filtra na leitura em vez de depois: o catalogo de um ano tem
    dezenas de milhares de linhas e apenas algumas centenas interessam a uma
    empresa. Filtrar aqui nao esconde nada - o ZIP inteiro continua no bronze,
    imutavel, e reprocessar com outro filtro nao depende de re-baixar.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not members:
            raise ValueError(f"catalogo IPE sem CSV dentro: {archive.namelist()}")
        raw = archive.read(sorted(members)[0])

    text = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")

    missing = set(_EXPECTED_COLUMNS) - set(reader.fieldnames or ())
    if missing:
        raise ValueError(
            f"catalogo IPE mudou de layout na origem; faltam colunas: {sorted(missing)}"
        )

    return list(_rows(reader, cod_cvm))


def _rows(reader: csv.DictReader, cod_cvm: int | None) -> Iterator[IpeCatalogEntry]:
    for row in reader:
        raw_cod = (row.get("Codigo_CVM") or "").strip()
        if not raw_cod.isdigit():
            continue
        code = int(raw_cod)
        if cod_cvm is not None and code != cod_cvm:
            continue

        delivered = _parse_date(row.get("Data_Entrega", ""))
        if delivered is None:
            # Sem data de entrega nao ha `knowledge_date`, e sem
            # `knowledge_date` o documento nao pode participar de uma consulta
            # AS OF. Descartar aqui e mais honesto do que carimbar uma data.
            continue

        url = (row.get("Link_Download") or "").strip()
        protocolo = (row.get("Protocolo_Entrega") or "").strip()
        if not url or not protocolo:
            continue

        categoria = (row.get("Categoria") or "").strip()
        assunto = (row.get("Assunto") or "").strip()

        yield IpeCatalogEntry(
            cnpj=(row.get("CNPJ_Companhia") or "").strip(),
            denom_cia=(row.get("Nome_Companhia") or "").strip(),
            cod_cvm=code,
            categoria=categoria or "(sem categoria)",
            kind=classify(categoria, assunto),
            tipo=(row.get("Tipo") or "").strip(),
            especie=(row.get("Especie") or "").strip(),
            assunto=assunto,
            reference_date=_parse_date(row.get("Data_Referencia", "")),
            delivered_at=delivered,
            tipo_apresentacao=(row.get("Tipo_Apresentacao") or "").strip(),
            protocolo=protocolo,
            versao=(row.get("Versao") or "").strip(),
            url=url,
        )

"""`sec.filing_index` -> os arquivos de UM arquivamento, com o tipo declarado.

Existe porque `sec_submissions.py` so alcanca o documento PRINCIPAL. Num 8-K de
resultado o principal e a folha de rosto de duas paginas, que diz "a carta aos
acionistas esta anexa como Exhibit 99.1" - e a carta e onde a administracao da
guidance e discute preco, engajamento e publicidade. Ingerir 31 capas e ter o
indice de uma biblioteca em vez dos livros.

Por que o cabecalho SGML, e nao o `index.json`
----------------------------------------------
O `index.json` do accession lista nome e tamanho de cada arquivo, mas o campo
`type` dele e o ICONE da listagem (`text.gif`, `compressed.gif`) - nao o tipo
do documento. Achar o EX-99.1 ali exigiria adivinhar pelo nome do arquivo, e o
nome e escolha do arquivador: `ex991_q425.htm` na Netflix, `d65144dex991.htm`
quando o mesmo 8-K passa por outro agente. Casar por padrao de nome e
exatamente o casamento por rotulo que o resto do projeto proibe.

O `-index-headers.html` traz blocos SGML em que o arquivador DECLARA o tipo:

    <DOCUMENT>
    <TYPE>EX-99.1
    <SEQUENCE>2
    <FILENAME>ex991_q425.htm

E uma afirmacao da origem, nao uma deducao nossa. Por isso este parser le
`<TYPE>` e nunca olha o nome do arquivo para decidir o que a coisa e.

Tipo de exibicao -> DocumentKind e TABELA DECLARADA
---------------------------------------------------
Mesma disciplina de `FORM_TO_KIND`. Tipo desconhecido nao vira palpite: fica
fora do conjunto buscavel e e CONTADO, para que "nao ingerimos" nunca se
apresente como "nao existe".
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date

from pat.contracts.corpus import DateBasis, DocumentCandidate, DocumentKind

__all__ = [
    "EXHIBIT_TYPE_TO_KIND",
    "FilingIndex",
    "IndexedFile",
    "TECHNICAL_TYPES",
    "exhibit_candidates",
    "parse_filing_index",
]

DATASET = "sec.filing_doc"
PROVIDER = "sec"
LANGUAGE = "en-US"

EXHIBIT_TYPE_TO_KIND: dict[str, DocumentKind] = {
    "EX-99.1": DocumentKind.PERFORMANCE_REPORT,
    "EX-99.2": DocumentKind.PERFORMANCE_REPORT,
    "EX-99.3": DocumentKind.PERFORMANCE_REPORT,
}
"""Tipo declarado -> tipo de research. Cada linha e uma afirmacao.

`EX-99.1` e `PERFORMANCE_REPORT` e nao `MATERIAL_FACT` porque e o release de
resultados em si - o analogo americano do que a CVM entrega como categoria
propria. A capa 8-K que o aponta continua `MATERIAL_FACT`, vinda por
`sec_submissions`, e as duas coexistirem e o certo: sao dois documentos com
conteudos diferentes, e o `document_id` (sha256) ja os separa.

99.2 e 99.3 entram porque uma companhia que divide release e apresentacao usa
as duas, e deixar de fora perderia metade do release sem avisar. Nao ha
`EX-99.*` generico: exibicao 99 e um balde onde cabe consentimento de auditor e
comunicado de imprensa, e ingerir tudo que comeca com 99 traria ruido chamado
de release.
"""

TECHNICAL_TYPES: frozenset[str] = frozenset(
    {
        "GRAPHIC",
        "XML",
        "EXCEL",
        "ZIP",
        "JSON",
    }
)
"""Tipos que sao maquinario do arquivamento, nao texto para citar.

Separados dos meramente DESCONHECIDOS de proposito: um XBRL linkbase ficar de
fora e uma decisao, e um `EX-10.1` (contrato) ficar de fora e uma lacuna que
alguem pode querer fechar depois. Colapsar os dois em "nao ingerido" apagaria a
diferenca. As exibicoes `EX-101.*` e `EX-99.SD` sao pegas pelo prefixo em
`_e_tecnico`, porque a SEC versiona a taxonomia e a lista literal envelheceria.
"""

_TAXONOMY_PREFIXES = ("EX-101.", "EX-100.")

_DOCUMENT_RE = re.compile(
    r"<DOCUMENT>(.*?)(?:</DOCUMENT>|<DOCUMENT>)", re.DOTALL | re.IGNORECASE
)
_TAG_RE = {
    "type": re.compile(r"<TYPE>([^\r\n<]*)", re.IGNORECASE),
    "sequence": re.compile(r"<SEQUENCE>([^\r\n<]*)", re.IGNORECASE),
    "filename": re.compile(r"<FILENAME>([^\r\n<]*)", re.IGNORECASE),
    "description": re.compile(r"<DESCRIPTION>([^\r\n<]*)", re.IGNORECASE),
}
_ACCESSION_RE = re.compile(r"ACCESSION NUMBER:\s*(\d{10}-\d{2}-\d{6})", re.IGNORECASE)
_FORM_RE = re.compile(r"CONFORMED SUBMISSION TYPE:\s*([^\r\n]+)", re.IGNORECASE)


@dataclass(frozen=True)
class IndexedFile:
    """Um arquivo do accession, como o arquivador o declarou."""

    doc_type: str
    filename: str
    sequence: int | None
    description: str


@dataclass(frozen=True)
class FilingIndex:
    """O que o accession contem, e o que deste conteudo nao e ingerivel.

    `unknown_types` e o campo que impede o silencio: um tipo de exibicao que
    esta no arquivamento e nao esta na tabela aparece aqui, contado e nomeado,
    em vez de sumir. E o mesmo motivo de `older_files` existir em
    `SubmissionsIndex`.
    """

    accession: str
    form: str
    files: tuple[IndexedFile, ...]
    unknown_types: tuple[str, ...] = ()


def parse_filing_index(payload: bytes) -> FilingIndex:
    """Cabecalho SGML -> os arquivos declarados. Nao busca bytes de nada."""
    # A origem entrega o SGML ESCAPADO dentro de uma pagina HTML: os bytes
    # trazem `&lt;DOCUMENT&gt;`, nao `<DOCUMENT>`. Sem desescapar, todo padrao
    # que procure `<TYPE>` casa zero vezes e o arquivamento se apresenta como
    # "nenhuma exibicao" - ausencia silenciosa, que e a falha que este projeto
    # mais tenta evitar. Foi assim que a primeira versao passou nos testes e
    # falhou contra a SEC: o fixture usava o tag cru que a origem nunca manda.
    texto = html.unescape(payload.decode("utf-8", errors="replace"))

    m = _ACCESSION_RE.search(texto)
    if not m:
        raise ValueError("indice sem ACCESSION NUMBER; layout mudou na origem")
    accession = m.group(1)

    mf = _FORM_RE.search(texto)
    form = mf.group(1).strip() if mf else ""

    arquivos: list[IndexedFile] = []
    for bloco in _DOCUMENT_RE.findall(texto):
        campos = {}
        for nome, padrao in _TAG_RE.items():
            achado = padrao.search(bloco)
            campos[nome] = achado.group(1).strip() if achado else ""
        if not campos["filename"] or not campos["type"]:
            continue
        try:
            sequencia = int(campos["sequence"]) if campos["sequence"] else None
        except ValueError:
            sequencia = None
        arquivos.append(
            IndexedFile(
                doc_type=campos["type"].upper(),
                filename=campos["filename"],
                sequence=sequencia,
                description=campos["description"],
            )
        )

    if not arquivos:
        raise ValueError("indice sem bloco <DOCUMENT>; layout mudou na origem")

    desconhecidos = sorted(
        {
            f.doc_type
            for f in arquivos
            if f.doc_type not in EXHIBIT_TYPE_TO_KIND and not _e_tecnico(f.doc_type)
        }
    )
    return FilingIndex(
        accession=accession,
        form=form,
        files=tuple(arquivos),
        unknown_types=tuple(desconhecidos),
    )


def _e_tecnico(doc_type: str) -> bool:
    return doc_type in TECHNICAL_TYPES or doc_type.startswith(_TAXONOMY_PREFIXES)


def exhibit_candidates(
    indice: FilingIndex,
    *,
    cik: str,
    published_at: date,
    reference_date: date | None = None,
) -> list[DocumentCandidate]:
    """Indice -> candidatos para as exibicoes que a tabela reconhece.

    `published_at` vem de FORA: e a data de entrega do arquivamento, que o
    cabecalho SGML tambem traz mas em formato proprio. Quem ja leu o indice de
    submissions tem a data canonica, e passar a mesma data para a capa e para a
    exibicao mantem as duas sob o mesmo corte `AS OF` - o contrario faria a
    carta e o 8-K que a anuncia aparecerem em dias diferentes.
    """
    saida: list[DocumentCandidate] = []
    for arquivo in indice.files:
        kind = EXHIBIT_TYPE_TO_KIND.get(arquivo.doc_type)
        if kind is None:
            continue
        saida.append(
            DocumentCandidate(
                dataset_id=DATASET,
                params=(
                    ("cik", str(cik).zfill(10)),
                    ("accession", indice.accession),
                    ("document", arquivo.filename),
                ),
                provider_id=PROVIDER,
                kind=kind,
                title=_titulo(indice.form, arquivo, published_at),
                language=LANGUAGE,
                published_at=published_at,
                published_at_basis=DateBasis.FILING_METADATA,
                reference_date=reference_date,
                reference_date_basis=(
                    DateBasis.FILING_METADATA if reference_date is not None else None
                ),
                resource_key=f"{indice.accession}/{arquivo.filename}",
                source_url=(
                    "https://www.sec.gov/Archives/edgar/data/"
                    f"{int(str(cik))}/{indice.accession.replace('-', '')}/"
                    f"{arquivo.filename}"
                ),
                # O rotulo do REGULADOR, nao o nosso: 'EX-99.1'. A forma que o
                # carrega ('8-K') fica no titulo, porque a mesma exibicao 99.1
                # aparece sob formas diferentes e a categoria de origem tem que
                # continuar sendo o que a origem declarou.
                origin_category=arquivo.doc_type,
            )
        )
    return saida


def _titulo(form: str, arquivo: IndexedFile, entregue: date) -> str:
    """Sem prosa inventada: tipo, forma que o carrega e data."""
    partes = [arquivo.doc_type]
    if form:
        partes.append(f"de {form}")
    partes.append(entregue.isoformat())
    return " · ".join(partes)

"""`sec.submissions` -> candidatos a documento. O analogo de `cvm_ipe.py`.

O JSON de submissions e o indice de arquivamentos de uma companhia: forma,
numero de accession, data de entrega e qual arquivo e o documento principal.
Ele nao traz conteudo - e por isso o que sai daqui e `DocumentCandidate`, e nao
`SourceDocument`.

Forma -> DocumentKind e uma TABELA DECLARADA
--------------------------------------------
Mesma disciplina de `Categoria -> DocumentKind` no lado da CVM, e pela mesma
razao: deduzir por semelhanca de rotulo ("tem 10 no nome, deve ser relatorio")
produziria classificacao que parece funcionar ate o dia em que nao funciona.
Forma desconhecida vira `OTHER` explicitamente, e o documento continua
armazenado e citavel, marcado como nao classificado.

O que este modulo NAO faz
-------------------------
Nao busca bytes, nao monta URL de download e nao interpreta o documento. A URL
que ele carrega e so procedencia; quem resolve endereco e o provider, a partir
de `params`. E a mesma fronteira que faz `sources/` nao abrir ZIP.

Paginacao
---------
A SEC divide o historico: os arquivamentos recentes vem em `filings.recent`, e
os antigos em arquivos separados listados em `filings.files`. `parse_submissions`
le `recent` e DECLARA quantos ficaram de fora, em vez de fingir que o historico
acabou ali - uma companhia com muitos arquivamentos tem anos inteiros fora do
`recent`, e um sistema que nao dissesse isso apresentaria "nao ha 10-K de 2015"
quando o certo e "o indice recente nao alcanca 2015".

`parse_submissions_page` le uma dessas paginas anteriores. Ela tem forma
DIFERENTE, e a diferenca importa: a pagina e um JSON PLANO - as colunas na
raiz, sem `filings.recent` - e nao carrega `cik` nem `name`. A identidade da
companhia tem que vir de fora, de quem pediu a pagina. Por isso as duas funcoes
existem em vez de uma que adivinha a forma: uma pagina que chegasse no lugar do
indice completo produziria uma companhia sem nome, e o nome so faltaria bem
depois, no titulo de um documento.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from pat.contracts.corpus import DateBasis, DocumentCandidate, DocumentKind

__all__ = [
    "FORM_TO_KIND",
    "SubmissionsIndex",
    "parse_submissions",
    "parse_submissions_page",
]

DATASET = "sec.filing_doc"
PROVIDER = "sec"
LANGUAGE = "en-US"

# Tabela DECLARADA. Cada linha e uma afirmacao de que aquela forma da SEC E
# aquele tipo de documento, e nao uma deducao pelo nome.
#
# As variantes com sufixo existem porque a SEC as trata como formas distintas:
# `10-K/A` e uma EMENDA ao 10-K, com conteudo proprio e data de entrega
# propria. Mapea-la para o mesmo tipo e correto; colapsar as duas na mesma
# entrada do catalogo nao seria - e o `document_id` (sha256) ja as separa.
FORM_TO_KIND: dict[str, DocumentKind] = {
    "10-K": DocumentKind.FILING,
    "10-K/A": DocumentKind.FILING,
    "10-Q": DocumentKind.FILING,
    "10-Q/A": DocumentKind.FILING,
    "20-F": DocumentKind.FILING,
    "40-F": DocumentKind.FILING,
    "8-K": DocumentKind.MATERIAL_FACT,
    "8-K/A": DocumentKind.MATERIAL_FACT,
    "6-K": DocumentKind.MATERIAL_FACT,
    "DEF 14A": DocumentKind.GOVERNANCE,
    "DEFA14A": DocumentKind.GOVERNANCE,
    "PRE 14A": DocumentKind.GOVERNANCE,
    "S-1": DocumentKind.FILING,
    "S-3": DocumentKind.FILING,
    "424B2": DocumentKind.FILING,
    "424B5": DocumentKind.FILING,
    "SC 13D": DocumentKind.SHAREHOLDER_NOTICE,
    "SC 13G": DocumentKind.SHAREHOLDER_NOTICE,
    "4": DocumentKind.SHAREHOLDER_NOTICE,
    "3": DocumentKind.SHAREHOLDER_NOTICE,
    "5": DocumentKind.SHAREHOLDER_NOTICE,
    "11-K": DocumentKind.GOVERNANCE,
    "ARS": DocumentKind.PERFORMANCE_REPORT,
}
"""Forma da SEC -> tipo de research. Ausencia vira `OTHER`, declaradamente.

`10-K` e `10-Q` sao `FILING` e nao `PERFORMANCE_REPORT`: o release de
resultados americano vem como EXIBICAO de um 8-K (EX-99.1), e chamar o
formulario anual de "relatorio de desempenho" faria as duas coisas colidirem no
mesmo filtro. Quem quer a discussao da administracao busca `FILING` e le a
secao; quem quer o release busca `MATERIAL_FACT`.
"""


@dataclass(frozen=True)
class SubmissionsIndex:
    """O que o indice trouxe, e o que ele deixou de fora.

    `older_files` nao e detalhe: e a diferenca entre "a companhia nao arquivou"
    e "o indice recente nao alcanca". As duas chegam como ausencia e pedem
    acoes opostas.
    """

    cik: str
    entity_name: str
    candidates: list[DocumentCandidate]
    older_files: tuple[str, ...] = ()
    skipped_no_document: int = 0
    """Arquivamentos sem documento principal declarado. Existem - alguns
    formularios sao so metadado -, e conta-los e mais honesto que descartar."""


def parse_submissions(
    payload: bytes,
    *,
    forms: tuple[str, ...] = (),
    since: date | None = None,
) -> SubmissionsIndex:
    """JSON de submissions -> candidatos, filtrados por forma e data.

    `forms` vazio significa TODAS as formas conhecidas e desconhecidas. O
    filtro existe porque uma companhia grande arquiva milhares de Form 4 por
    ano, e baixar todos para achar um 10-K seria gastar a paciencia da origem -
    a SEC pede uso comedido - sem ganhar informacao.
    """
    documento = json.loads(payload)
    cik = str(documento.get("cik", "")).zfill(10)
    nome = documento.get("name") or documento.get("entityName") or ""
    if not cik.strip("0") or not nome:
        raise ValueError("submissions sem cik ou name; layout mudou na origem")

    filings = documento.get("filings") or {}
    recentes = filings.get("recent") or {}
    antigos = tuple(
        str(f.get("name", ""))
        for f in (filings.get("files") or [])
        if f.get("name")
    )

    candidatos, sem_documento = _candidatos(recentes, cik, forms=forms, since=since)

    return SubmissionsIndex(
        cik=cik,
        entity_name=nome,
        candidates=candidatos,
        older_files=antigos,
        skipped_no_document=sem_documento,
    )


def parse_submissions_page(
    payload: bytes,
    *,
    cik: str,
    entity_name: str,
    forms: tuple[str, ...] = (),
    since: date | None = None,
) -> SubmissionsIndex:
    """Uma pagina ANTERIOR do historico -> candidatos. Mesmo contrato de saida.

    `cik` e `entity_name` sao exigidos porque a pagina nao os traz: ela e so as
    colunas. Passa-los explicitamente e o que impede a companhia de chegar sem
    nome no catalogo - o tipo de falta que so aparece tres camadas adiante.

    `older_files` sai vazio de proposito: uma pagina nao aponta para outras. Quem
    sabe quantas existem e o indice completo, e e la que a contagem vive.
    """
    dados = json.loads(payload)
    if "filings" in dados:
        raise ValueError(
            "isto e o indice completo, nao uma pagina; use parse_submissions"
        )
    if not isinstance(dados.get("accessionNumber"), list):
        raise ValueError("pagina de submissions sem colunas; layout mudou na origem")

    chave = str(cik).zfill(10)
    candidatos, sem_documento = _candidatos(dados, chave, forms=forms, since=since)
    return SubmissionsIndex(
        cik=chave,
        entity_name=entity_name,
        candidates=candidatos,
        older_files=(),
        skipped_no_document=sem_documento,
    )


def _candidatos(
    dados_brutos: dict,
    cik: str,
    *,
    forms: tuple[str, ...],
    since: date | None,
) -> tuple[list[DocumentCandidate], int]:
    """As colunas -> candidatos. Comum as duas formas do indice.

    Existe para que a pagina anterior e o indice recente produzam candidatos
    IDENTICOS em forma. Se a montagem fosse duplicada, um `DocumentKind` novo
    acrescentado num lado passaria a divergir do outro sem ninguem notar.
    """
    colunas = (
        "accessionNumber",
        "filingDate",
        "reportDate",
        "form",
        "primaryDocument",
        "primaryDocDescription",
    )
    dados = {c: dados_brutos.get(c) or [] for c in colunas}
    total = len(dados["accessionNumber"])

    candidatos: list[DocumentCandidate] = []
    sem_documento = 0
    formas = {f.upper() for f in forms}

    for i in range(total):
        forma = str(dados["form"][i] or "")
        if formas and forma.upper() not in formas:
            continue

        entregue = _data(dados["filingDate"][i])
        if entregue is None:
            continue
        if since is not None and entregue < since:
            continue

        principal = str(dados["primaryDocument"][i] or "")
        if not principal:
            sem_documento += 1
            continue

        accession = str(dados["accessionNumber"][i] or "")
        if not accession:
            sem_documento += 1
            continue

        candidatos.append(
            DocumentCandidate(
                dataset_id=DATASET,
                params=(
                    ("cik", cik),
                    ("accession", accession),
                    ("document", principal),
                ),
                provider_id=PROVIDER,
                kind=FORM_TO_KIND.get(forma, DocumentKind.OTHER),
                title=_titulo(forma, dados["primaryDocDescription"][i], entregue),
                language=LANGUAGE,
                # `filingDate` e quando o arquivamento se tornou publico - o
                # `knowledge_date` do texto. Metadado de protocolo, como a
                # `Data_Entrega` do IPE, e por isso a mesma base.
                published_at=entregue,
                published_at_basis=DateBasis.FILING_METADATA,
                # `reportDate` e o fim do periodo COBERTO quando existe. A SEC
                # o deixa vazio em varias formas, e vazio fica vazio: derivar
                # periodo do que nao foi publicado seria inferencia por formato.
                reference_date=_data(dados["reportDate"][i]),
                reference_date_basis=(
                    DateBasis.FILING_METADATA
                    if _data(dados["reportDate"][i]) is not None
                    else None
                ),
                resource_key=f"{accession}/{principal}",
                source_url=_url(cik, accession, principal),
                origin_category=forma,
            )
        )

    return candidatos, sem_documento


def _data(bruto) -> date | None:
    texto = str(bruto or "").strip()
    if len(texto) != 10:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def _titulo(forma: str, descricao, entregue: date) -> str:
    """Titulo sem texto inventado: forma, descricao da origem e data.

    Quando a SEC deixa a descricao vazia - e ela deixa com frequencia -, o
    titulo cai para forma e data. Nao ha geracao de prosa: so concatenacao do
    que a origem publicou.
    """
    partes = [forma or "(forma nao declarada)"]
    texto = str(descricao or "").strip()
    if texto and texto.upper() != forma.upper():
        partes.append(texto)
    partes.append(entregue.isoformat())
    return " · ".join(partes)


def _url(cik: str, accession: str, documento: str) -> str:
    """A URL publica do documento, SO para procedencia.

    Quem busca os bytes e o provider, a partir de `params`. Esta string existe
    para que a citacao final possa apontar para onde um humano confere.
    """
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession.replace('-', '')}/{documento}"
    )

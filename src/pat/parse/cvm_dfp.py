"""Parser dos ZIPs de DFP da CVM: bronze -> linhas silver.

Le bytes do bronze e nao toca a rede. Deterministico: o mesmo documento
produz sempre as mesmas linhas, com os mesmos ids.

Duas particularidades da fonte que o parser precisa respeitar
-------------------------------------------------------------
1. `DT_RECEB` (data em que a CVM recebeu o documento) so existe no arquivo
   indice, nao nos arquivos de detalhe. E ele que vira `knowledge_date`, e
   por isso o indice e lido primeiro e as linhas de detalhe sao unidas a ele
   por (CNPJ, DT_REFER, VERSAO). Linha sem indice correspondente nao vira
   fato: sem data de conhecimento nao existe fato bitemporal.

2. Contas patrimoniais (BPA, BPP) nao tem DT_INI_EXERC - sao saldo pontual,
   nao fluxo. O parser trata a ausencia da coluna como sinal, nao como erro.

Escopo: apenas DFP (anual). O ITR tem layout identico, mas seus valores sao
acumulados no ano; derivar um trimestre isolado exige des-acumulacao, que e
uma decisao de desenho propria. Ligar o ITR sem resolver isso deixaria uma
armadilha silenciosa.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from pat.contracts.silver import AccountLine

EXTRACTOR = "cvm_dfp"
EXTRACTOR_VERSION = "1.1.0"
"""1.1.0: passa a capturar COLUNA_DF (dimensao da DMPL). Sem ela, componentes
distintos do patrimonio liquido colapsavam na mesma chave logica e apareciam
como reapresentacoes falsas."""

ENCODING = "latin-1"
"""A CVM publica em ISO-8859-1."""

_MEMBER_RE = re.compile(
    r"^dfp_cia_aberta_(?P<statement>[A-Z]+(?:_[A-Z]{2})?)_(?P<scope>con|ind)_(?P<year>\d{4})\.csv$"
)
_INDEX_RE = re.compile(r"^dfp_cia_aberta_(?P<year>\d{4})\.csv$")

_KNOWN_STATEMENTS = frozenset({"DRE", "BPA", "BPP", "DFC_MI", "DFC_MD", "DVA", "DRA", "DMPL"})


class ParseError(RuntimeError):
    pass


@dataclass
class ParseStats:
    """Contagem do que entrou e do que foi descartado, e por que.

    Descarte silencioso e inaceitavel numa camada de dados que sustenta
    decisao. Todo motivo e contado e reportado ao fim do build.
    """

    lines_read: int = 0
    lines_emitted: int = 0
    skipped_no_index: int = 0
    skipped_ambiguous_index: int = 0
    skipped_bad_value: int = 0
    skipped_bad_date: int = 0
    skipped_bad_key: int = 0
    """Identificadores ilegiveis (CD_CVM, CNPJ, VERSAO). Contador proprio: um
    relatorio que chama isso de 'data invalida' manda quem for investigar
    olhar a coluna errada."""
    skipped_filtered_out: int = 0
    ambiguous_keys: int = 0
    members_parsed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int | list[str]]:
        return {
            "lines_read": self.lines_read,
            "lines_emitted": self.lines_emitted,
            "skipped_no_index": self.skipped_no_index,
            "skipped_ambiguous_index": self.skipped_ambiguous_index,
            "skipped_bad_value": self.skipped_bad_value,
            "skipped_bad_date": self.skipped_bad_date,
            "skipped_bad_key": self.skipped_bad_key,
            "skipped_filtered_out": self.skipped_filtered_out,
            "ambiguous_keys": self.ambiguous_keys,
            "members_parsed": self.members_parsed,
        }

    @property
    def skipped_total(self) -> int:
        """Linhas perdidas por qualidade de dado.

        `skipped_filtered_out` fica de fora de proposito: filtrar por empresa e
        selecao pedida pelo usuario, nao perda. Misturar os dois esconderia um
        problema real de extracao atras de um numero enorme e esperado.
        """
        return (
            self.skipped_no_index
            + self.skipped_ambiguous_index
            + self.skipped_bad_value
            + self.skipped_bad_date
            + self.skipped_bad_key
        )


@dataclass(frozen=True)
class DocumentIndexEntry:
    doc_id: str
    dt_receb: date
    link_doc: str | None


def strip_accents(text: str) -> str:
    """ULTIMO/PENULTIMO sem acento: chave estavel, imune a encoding."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw or raw in {"0000-00-00", "-"}:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _parse_decimal(raw: str) -> Decimal | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _silver_id(content_sha256: str, member: str, line_no: int) -> str:
    key = f"{content_sha256}|{member}|{line_no}|{EXTRACTOR_VERSION}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _read_csv(zf: zipfile.ZipFile, member: str) -> Iterator[tuple[int, dict[str, str]]]:
    """Devolve (numero_da_linha, linha). Linha 1 e o cabecalho."""
    with zf.open(member) as handle:
        text = io.TextIOWrapper(handle, encoding=ENCODING, newline="")
        reader = csv.DictReader(text, delimiter=";")
        for line_no, row in enumerate(reader, start=2):
            yield line_no, row


DocumentIndex = dict[tuple[str, date, int], DocumentIndexEntry]


def read_document_index(
    zf: zipfile.ZipFile, year: int
) -> tuple[DocumentIndex, set[tuple[str, date, int]]]:
    """Le o arquivo indice, que e a unica fonte de DT_RECEB.

    Chave (cnpj, dt_refer, versao). Um documento pode ter varias versoes; o
    indice lista todas, e a chave escolhe exatamente a que o arquivo de detalhe
    carrega.

    A chave *deveria* ser unica, mas nao e sempre: ha companhias que
    protocolaram dois documentos distintos ambos como VERSAO=1, com datas de
    recebimento diferentes (na DFP 2023, 1 em 892). Nesse caso e impossivel
    saber a qual documento as linhas de detalhe pertencem, e portanto qual e a
    data de conhecimento correta. A chave e marcada como ambigua e suas linhas
    nao viram fato: sem knowledge_date confiavel nao existe fato bitemporal.
    Escolher um dos documentos arbitrariamente produziria um numero com data
    errada, que e pior do que numero nenhum.

    Retorna (indice, chaves_ambiguas).
    """
    member = f"dfp_cia_aberta_{year}.csv"
    if member not in zf.namelist():
        raise ParseError(f"arquivo indice ausente no ZIP: {member}")

    index: DocumentIndex = {}
    ambiguous: set[tuple[str, date, int]] = set()

    for _line_no, row in _read_csv(zf, member):
        cnpj = _digits(row.get("CNPJ_CIA", ""))
        dt_refer = _parse_date(row.get("DT_REFER", ""))
        dt_receb = _parse_date(row.get("DT_RECEB", ""))
        raw_versao = (row.get("VERSAO") or "").strip()
        if not (cnpj and dt_refer and dt_receb and raw_versao.isdigit()):
            continue

        key = (cnpj, dt_refer, int(raw_versao))
        entry = DocumentIndexEntry(
            doc_id=(row.get("ID_DOC") or "").strip(),
            dt_receb=dt_receb,
            link_doc=(row.get("LINK_DOC") or "").strip() or None,
        )
        if (existing := index.get(key)) is not None and existing != entry:
            ambiguous.add(key)
            continue
        index[key] = entry

    for key in ambiguous:
        index.pop(key, None)
    return index, ambiguous


def parse_dfp_zip(
    content: bytes,
    *,
    year: int,
    content_sha256: str,
    retrieval_id: str,
    extraction_run_id: str,
    only_cod_cvm: frozenset[int] | None = None,
    statements: frozenset[str] | None = None,
) -> tuple[list[AccountLine], ParseStats]:
    """Converte um ZIP de DFP em linhas silver.

    `only_cod_cvm` materializa apenas as companhias pedidas - e o gancho para
    montar um projeto de pesquisa de uma empresa sem processar o mercado
    inteiro.
    """
    stats = ParseStats()
    lines: list[AccountLine] = []

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        index, ambiguous = read_document_index(zf, year)
        stats.ambiguous_keys = len(ambiguous)

        for member in sorted(zf.namelist()):
            match = _MEMBER_RE.match(member)
            if not match:
                continue
            statement = match.group("statement")
            if statement not in _KNOWN_STATEMENTS:
                continue
            if statements is not None and statement not in statements:
                continue

            consolidated = match.group("scope") == "con"
            stats.members_parsed.append(member)

            for line_no, row in _read_csv(zf, member):
                stats.lines_read += 1
                line = _build_line(
                    row,
                    member=member,
                    line_no=line_no,
                    statement=statement,
                    consolidated=consolidated,
                    index=index,
                    ambiguous=ambiguous,
                    content_sha256=content_sha256,
                    retrieval_id=retrieval_id,
                    extraction_run_id=extraction_run_id,
                    only_cod_cvm=only_cod_cvm,
                    stats=stats,
                )
                if line is not None:
                    lines.append(line)
                    stats.lines_emitted += 1

    return lines, stats


def _build_line(
    row: dict[str, str],
    *,
    member: str,
    line_no: int,
    statement: str,
    consolidated: bool,
    index: DocumentIndex,
    ambiguous: set[tuple[str, date, int]],
    content_sha256: str,
    retrieval_id: str,
    extraction_run_id: str,
    only_cod_cvm: frozenset[int] | None,
    stats: ParseStats,
) -> AccountLine | None:
    raw_cod = (row.get("CD_CVM") or "").strip()
    if not raw_cod.isdigit():
        stats.skipped_bad_key += 1
        return None
    cod_cvm = int(raw_cod)

    if only_cod_cvm is not None and cod_cvm not in only_cod_cvm:
        stats.skipped_filtered_out += 1
        return None

    cnpj = _digits(row.get("CNPJ_CIA", ""))
    dt_refer = _parse_date(row.get("DT_REFER", ""))
    dt_fim = _parse_date(row.get("DT_FIM_EXERC", ""))
    raw_versao = (row.get("VERSAO") or "").strip()

    if not (dt_refer and dt_fim):
        stats.skipped_bad_date += 1
        return None
    if not (cnpj and raw_versao.isdigit()):
        stats.skipped_bad_key += 1
        return None
    versao = int(raw_versao)

    key = (cnpj, dt_refer, versao)
    if key in ambiguous:
        # Dois documentos distintos com a mesma chave: a data de conhecimento
        # correta e indeterminavel. Ver read_document_index.
        stats.skipped_ambiguous_index += 1
        return None

    entry = index.get(key)
    if entry is None:
        # Sem DT_RECEB nao ha knowledge_date, e sem knowledge_date nao existe
        # fato bitemporal. Descartar e a unica opcao honesta.
        stats.skipped_no_index += 1
        return None

    value = _parse_decimal(row.get("VL_CONTA", ""))
    if value is None:
        stats.skipped_bad_value += 1
        return None

    st_fixa_raw = (row.get("ST_CONTA_FIXA") or "").strip().upper()
    st_conta_fixa = {"S": True, "N": False}.get(st_fixa_raw)

    return AccountLine(
        silver_id=_silver_id(content_sha256, member, line_no),
        content_sha256=content_sha256,
        retrieval_id=retrieval_id,
        source_member=member,
        source_line_no=line_no,
        cnpj=cnpj,
        cod_cvm=cod_cvm,
        denom_cia=(row.get("DENOM_CIA") or "").strip(),
        dt_refer=dt_refer,
        versao=versao,
        doc_id=entry.doc_id,
        dt_receb=entry.dt_receb,
        link_doc=entry.link_doc,
        statement=statement,
        consolidated=consolidated,
        grupo_dfp=(row.get("GRUPO_DFP") or "").strip() or None,
        ordem_exerc=strip_accents((row.get("ORDEM_EXERC") or "").strip().upper()),
        dt_ini_exerc=_parse_date(row.get("DT_INI_EXERC", "")),
        dt_fim_exerc=dt_fim,
        coluna_df=(row.get("COLUNA_DF") or "").strip(),
        cd_conta=(row.get("CD_CONTA") or "").strip(),
        ds_conta=(row.get("DS_CONTA") or "").strip(),
        vl_conta=value,
        st_conta_fixa=st_conta_fixa,
        moeda=(row.get("MOEDA") or "").strip(),
        escala_moeda=(row.get("ESCALA_MOEDA") or "").strip(),
        extractor=EXTRACTOR,
        extractor_version=EXTRACTOR_VERSION,
        extraction_run_id=extraction_run_id,
    )

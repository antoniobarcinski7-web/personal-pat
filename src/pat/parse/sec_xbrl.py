"""companyfacts e DERA -> linhas XBRL tipadas. Camada silver, nunca provider.

Duas entradas, e a diferenca entre elas e o que destrava o eixo SEGMENT:

    parse_companyfacts   JSON da SEC. Fatos consolidados, SEM dimensao.
    parse_dera_num       num.txt do dataset trimestral. COM a coluna `segments`.

O primeiro e a fonte natural das metricas: a propria SEC ja normalizou o valor
de cada elemento. O segundo e pesado e existe por uma razao so - ele e a UNICA
fonte estruturada de dado por segmento. Verificado antes de ser escrito:
`companyfacts` publica accn, end, filed, form, fp, frame, fy, start, val, e
nada mais. Nao ha dimensao nenhuma nele.

Deduplicacao, e por que ela e necessaria aqui
---------------------------------------------
A SEC repete o mesmo fato em varios arquivamentos: o FY2023 aparece no 10-K de
2024 e de novo no de 2025, como comparativo. Os dois sao verdadeiros e tem
`filed` diferentes - e exatamente uma reapresentacao no sentido do I1, e as
duas linhas ficam. O que NAO pode e a mesma (element, periodo, filed, accession)
entrar duas vezes, e e isso que `silver_id` impede.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from datetime import date
from decimal import Decimal, InvalidOperation

from pat.contracts.silver import XbrlFactLine

EXTRACTOR = "sec_xbrl"
EXTRACTOR_VERSION = "1.0.0"

__all__ = [
    "EXTRACTOR",
    "EXTRACTOR_VERSION",
    "parse_companyfacts",
    "parse_dera_num",
]

_TAXONOMIAS = ("us-gaap", "ifrs-full")
"""So as taxonomias de demonstracao financeira.

`dei` (entity information) e `invest` nao carregam grandeza contabil, e
ingeri-los encheria o silver de metadado que nenhuma metrica consulta."""


def _decimal(bruto) -> Decimal | None:
    try:
        return Decimal(str(bruto))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _silver_id(*partes: object) -> str:
    material = "|".join("" if p is None else str(p) for p in partes)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def parse_companyfacts(
    payload: bytes,
    *,
    content_sha256: str,
    retrieval_id: str,
    extraction_run_id: str,
    units: tuple[str, ...] = ("USD",),
) -> list[XbrlFactLine]:
    """JSON de companyfacts -> linhas tipadas, sem dimensao.

    `units` filtra na leitura: um `companyfacts` traz USD, shares, USD/shares e
    puros numeros, e so o primeiro sustenta metrica monetaria. O JSON inteiro
    continua no bronze, imutavel, e reprocessar com outro filtro nao depende de
    re-baixar.
    """
    documento = json.loads(payload)
    cik = str(documento.get("cik", "")).zfill(10)
    nome = documento.get("entityName") or ""
    if not cik.strip("0") or not nome:
        raise ValueError("companyfacts sem cik ou entityName; layout mudou na origem")

    linhas: list[XbrlFactLine] = []
    ordinal = 0
    fatos = documento.get("facts", {})
    for taxonomia in _TAXONOMIAS:
        for elemento, corpo in sorted(fatos.get(taxonomia, {}).items()):
            for unidade, ocorrencias in sorted((corpo.get("units") or {}).items()):
                if unidade not in units:
                    continue
                for ocorrencia in ocorrencias:
                    linha = _linha_companyfacts(
                        ocorrencia,
                        cik=cik,
                        nome=nome,
                        taxonomia=taxonomia,
                        elemento=elemento,
                        unidade=unidade,
                        ordinal=ordinal,
                        content_sha256=content_sha256,
                        retrieval_id=retrieval_id,
                        extraction_run_id=extraction_run_id,
                    )
                    ordinal += 1
                    if linha is not None:
                        linhas.append(linha)
    return linhas


def _linha_companyfacts(
    ocorrencia: dict,
    *,
    cik: str,
    nome: str,
    taxonomia: str,
    elemento: str,
    unidade: str,
    ordinal: int,
    content_sha256: str,
    retrieval_id: str,
    extraction_run_id: str,
) -> XbrlFactLine | None:
    valor = _decimal(ocorrencia.get("val"))
    fim = ocorrencia.get("end")
    entregue = ocorrencia.get("filed")
    accession = ocorrencia.get("accn")
    if valor is None or not fim or not entregue or not accession:
        # Fato sem valor, sem fim de periodo ou sem `filed` nao participa de
        # consulta AS OF. Descartar e mais honesto do que carimbar uma data.
        return None

    try:
        period_end = date.fromisoformat(fim)
        filed = date.fromisoformat(entregue)
        period_start = (
            date.fromisoformat(ocorrencia["start"]) if ocorrencia.get("start") else None
        )
    except ValueError:
        return None

    return XbrlFactLine(
        silver_id=_silver_id(
            content_sha256, taxonomia, elemento, unidade, fim, entregue, accession, ""
        ),
        content_sha256=content_sha256,
        retrieval_id=retrieval_id,
        source_member="companyfacts.json",
        source_line_no=ordinal,
        cik=cik,
        entity_name=nome,
        taxonomy=taxonomia,
        element=elemento,
        segments="",
        period_start=period_start,
        period_end=period_end,
        fiscal_year=ocorrencia.get("fy"),
        fiscal_period=ocorrencia.get("fp"),
        value=valor,
        unit=unidade,
        accession=accession,
        form=ocorrencia.get("form") or "",
        filed=filed,
        extractor=EXTRACTOR,
        extractor_version=EXTRACTOR_VERSION,
        extraction_run_id=extraction_run_id,
    )


def parse_dera_num(
    payload: bytes,
    *,
    cik: str,
    content_sha256: str,
    retrieval_id: str,
    extraction_run_id: str,
    only_dimensional: bool = True,
) -> list[XbrlFactLine]:
    """ZIP trimestral da DERA -> linhas COM dimensao, de uma companhia.

    `only_dimensional=True` por default, e nao por economia: o fato
    consolidado ja vem de `companyfacts`, ja normalizado pela propria SEC.
    Ingerir os dois produziria duas linhas para a mesma grandeza vindas de
    caminhos diferentes - duas fontes de verdade para o mesmo numero, que e
    exatamente o que este projeto nao faz.

    O que so existe aqui e o dado por segmento. E ele que justifica baixar
    130 MB.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as arquivo:
        membros = set(arquivo.namelist())
        if not {"sub.txt", "num.txt"} <= membros:
            raise ValueError(f"dataset DERA sem sub.txt/num.txt: {sorted(membros)}")
        submissoes = arquivo.read("sub.txt").decode("utf-8", "replace")
        numeros = arquivo.read("num.txt").decode("utf-8", "replace")

    alvo = str(cik).lstrip("0")
    entregas: dict[str, tuple[str, date, str]] = {}
    nome = ""
    for linha in csv.DictReader(io.StringIO(submissoes), delimiter="\t"):
        if (linha.get("cik") or "").lstrip("0") != alvo:
            continue
        try:
            filed = date.fromisoformat(_iso(linha.get("filed", "")))
        except ValueError:
            continue
        entregas[linha["adsh"]] = (linha.get("form", ""), filed, linha.get("name", ""))
        nome = nome or linha.get("name", "")

    if not entregas:
        return []

    linhas: list[XbrlFactLine] = []
    for ordinal, registro in enumerate(_dera_rows(numeros, set(entregas))):
        segmentos = (registro.get("segments") or "").strip()
        if only_dimensional and not segmentos:
            continue
        valor = _decimal(registro.get("value"))
        if valor is None:
            continue
        try:
            fim = date.fromisoformat(_iso(registro.get("ddate", "")))
        except ValueError:
            continue

        form, filed, razao = entregas[registro["adsh"]]
        # `qtrs` diz quantos trimestres o fato cobre: 0 e instantaneo (balanco),
        # 4 e o exercicio. O inicio e derivado disso, e nao adivinhado.
        try:
            trimestres = int(registro.get("qtrs") or 0)
        except ValueError:
            trimestres = 0
        inicio = _inicio(fim, trimestres)

        linhas.append(
            XbrlFactLine(
                silver_id=_silver_id(
                    content_sha256,
                    registro["adsh"],
                    registro.get("tag"),
                    registro.get("uom"),
                    registro.get("ddate"),
                    registro.get("qtrs"),
                    segmentos,
                ),
                content_sha256=content_sha256,
                retrieval_id=retrieval_id,
                source_member="num.txt",
                source_line_no=ordinal,
                cik=str(cik).zfill(10),
                entity_name=razao or nome,
                taxonomy="us-gaap",
                element=registro.get("tag") or "",
                segments=segmentos,
                period_start=inicio,
                period_end=fim,
                value=valor,
                unit=registro.get("uom") or "USD",
                accession=registro["adsh"],
                form=form,
                filed=filed,
                extractor=EXTRACTOR,
                extractor_version=EXTRACTOR_VERSION,
                extraction_run_id=extraction_run_id,
            )
        )
    return linhas


def _dera_rows(texto: str, adsh_alvo: set[str]) -> Iterator[dict]:
    leitor = csv.DictReader(io.StringIO(texto), delimiter="\t")
    faltando = {"adsh", "tag", "ddate", "value", "segments"} - set(leitor.fieldnames or ())
    if faltando:
        raise ValueError(f"num.txt mudou de layout; faltam colunas: {sorted(faltando)}")
    for registro in leitor:
        if registro.get("adsh") in adsh_alvo:
            yield registro


def _iso(bruto: str) -> str:
    """A DERA publica data como AAAAMMDD."""
    texto = (bruto or "").strip()
    if len(texto) == 8 and texto.isdigit():
        return f"{texto[:4]}-{texto[4:6]}-{texto[6:]}"
    return texto


def _inicio(fim: date, trimestres: int) -> date | None:
    """Inicio do periodo, derivado de `qtrs`. `None` para saldo instantaneo."""
    if trimestres <= 0:
        return None
    meses = trimestres * 3
    ano = fim.year - (meses // 12)
    mes = fim.month - (meses % 12)
    if mes <= 0:
        mes += 12
        ano -= 1
    dia = min(fim.day, 28)
    try:
        return date(ano, mes, dia).replace(day=1)
    except ValueError:  # pragma: no cover - defensivo
        return None

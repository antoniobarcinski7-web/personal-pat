"""Bytes de documento -> unidades citaveis. Deterministico e versionado.

O PDF continua sendo a fonte de verdade; o texto e representacao derivada.
Isso tem tres consequencias que este modulo existe para garantir:

1. **A extracao e reproduzivel.** Mesmos bytes + mesma `extraction_version`
   produzem exatamente as mesmas unidades, com os mesmos offsets e os mesmos
   `unit_id`. Nao ha aleatoriedade, nao ha relogio, nao ha modelo.

2. **A versao entra na identidade.** `extraction_version` embute a versao
   efetiva do pypdf. Um upgrade da biblioteca muda a versao, muda o
   `unit_id`, e portanto cria unidades novas *ao lado* das antigas em vez de
   redefinir o que uma citacao antiga queria dizer. E a regra de
   `extractor_version` da Fase 1, aplicada a texto.

3. **Falha tem nome.** Um PDF protegido, corrompido ou sem camada de texto
   produz `ExtractionFailure` com motivo nomeado - nunca uma lista vazia de
   unidades. Nao existe fallback para OCR, nem para uma segunda biblioteca,
   nem para heuristica: uma citacao vinda de reconhecimento optico e um texto
   que ninguem escreveu, e ela entraria no corpus indistinguivel de uma
   citacao real.

O algoritmo de blocos, e por que ele nao normaliza nada
-------------------------------------------------------
Uma pagina vira blocos separados por linha em branco. Cada bloco vira uma
unidade cujo texto e uma FATIA LITERAL do texto da pagina - `page_text[a:b]`,
sem reescrita, sem colapsar espaco, sem juntar linha. Normalizar tornaria a
citacao mais bonita e menos verbatim, e a conferencia
`reextrair -> fatiar -> comparar` deixaria de ser byte a byte.

Blocos muito longos sao divididos em fronteira de sentenca, de forma
deterministica, para que uma citacao caiba numa resposta. A divisao nunca
descarta texto: as fatias de um bloco cobrem o bloco inteiro.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime

import pypdf

from pat.contracts.corpus import (
    DocumentUnit,
    ExtractionFailure,
    ExtractionFailureReason,
    ExtractionOutcome,
    LocatorScheme,
    UnitLocator,
)
from pat.corpus.identity import unit_id

__all__ = [
    "EXTRACTION_VERSION",
    "MAX_UNIT_CHARS",
    "PDF_MEDIA_TYPE",
    "extract",
    "extract_pdf_page_texts",
    "sniff_media_type",
]

PDF_MEDIA_TYPE = "application/pdf"

_ALGORITHM_VERSION = "1"
"""Versao do algoritmo de blocos deste modulo.

Separada da versao da biblioteca de proposito: mudar o tamanho maximo de
unidade ou a regra de quebra muda o corpo das unidades sem que o pypdf tenha
mudado, e as duas coisas precisam aparecer na identidade."""

EXTRACTION_VERSION = f"pdf-blocks/v{_ALGORITHM_VERSION}+pypdf{pypdf.__version__}"
"""Identidade completa do extrator, embutida em todo `unit_id`.

Inclui a versao efetiva do pypdf em uso - nao a versao minima declarada no
`pyproject.toml`. Sao coisas diferentes: o lockfile diz o que foi instalado,
e e o que foi instalado que produziu aquele texto."""

MAX_UNIT_CHARS = 1200
MIN_TAIL_CHARS = 200
"""Ao quebrar um bloco, uma cauda menor que isto e mantida junto do trecho
anterior em vez de virar unidade propria. Evita citacao de tres palavras
pendurada, sem descartar texto."""

_BLANK_LINE = re.compile(r"\n[ \t\r\f\v]*\n")
_SENTENCE_END = re.compile(r"[.!?;:][ \n\)\]\"'”»]")


def sniff_media_type(payload: bytes) -> str | None:
    """Tipo real, lido dos bytes - nunca do header HTTP.

    Existe porque a CVM responde `Content-Type: text/html` para um PDF. Um
    sistema que acreditasse no header classificaria errado todo o corpus, e o
    erro apareceria como 'nao ha documentos', que se le como 'a empresa nao
    falou nada'.
    """
    if payload.startswith(b"%PDF"):
        return PDF_MEDIA_TYPE
    if payload.startswith(b"PK\x03\x04"):
        return "application/zip"
    head = payload[:1024].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return "text/html"
    return None


def extract(document_id: str, payload: bytes) -> ExtractionOutcome:
    """Bytes -> unidades, ou uma falha nomeada. Nunca as duas, nunca nenhuma."""
    media_type = sniff_media_type(payload)
    if media_type != PDF_MEDIA_TYPE:
        return _failed(
            document_id,
            ExtractionFailureReason.UNSUPPORTED_MEDIA_TYPE,
            f"tipo detectado dos bytes: {media_type or 'desconhecido'}; "
            f"o extrator da M5.1 le {PDF_MEDIA_TYPE}",
            remedy=(
                "Escrever um extrator para este tipo, com versao propria. "
                "Nao ha conversao automatica, de proposito."
            ),
        )

    try:
        pages = extract_pdf_page_texts(payload)
    except _ExtractionRefused as refusal:
        return _failed(document_id, refusal.reason, refusal.message, remedy=refusal.remedy)
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira motivo nomeado
        return _failed(
            document_id,
            ExtractionFailureReason.EXTRACTOR_ERROR,
            f"{type(exc).__name__}: {exc}",
            remedy="Documento fica registrado como nao extraido; o blob continua no bronze.",
        )

    units: list[DocumentUnit] = []
    ordinal = 0
    for page_number, page_text in enumerate(pages, start=1):
        for block_number, (start, end) in enumerate(_blocks(page_text)):
            text = page_text[start:end]
            locator = UnitLocator(
                scheme=LocatorScheme.PDF_PAGE,
                page=page_number,
                block=block_number,
                char_start=start,
                char_end=end,
            )
            units.append(
                DocumentUnit(
                    unit_id=unit_id(
                        document_id=document_id,
                        extraction_version=EXTRACTION_VERSION,
                        locator=locator,
                    ),
                    document_id=document_id,
                    ordinal=ordinal,
                    locator=locator,
                    text=text,
                    char_count=len(text),
                    extraction_version=EXTRACTION_VERSION,
                )
            )
            ordinal += 1

    if not units:
        return _failed(
            document_id,
            ExtractionFailureReason.NO_TEXT_LAYER,
            "o PDF abriu mas nao tem texto extraivel em nenhuma pagina "
            f"({len(pages)} paginas lidas)",
            remedy=(
                "Tipicamente um PDF digitalizado. NAO ha fallback para OCR: uma "
                "citacao vinda de reconhecimento optico e um texto que ninguem "
                "escreveu. O documento fica registrado como nao extraido."
            ),
        )

    return ExtractionOutcome(
        document_id=document_id,
        extraction_version=EXTRACTION_VERSION,
        units=tuple(units),
    )


def extract_pdf_page_texts(payload: bytes) -> tuple[str, ...]:
    """Texto por pagina, na ordem do documento.

    Isolada de `extract` porque e exatamente o que a verificacao de
    procedencia precisa re-rodar: reextrair, fatiar pelo locator e comparar
    com o texto guardado.
    """
    reader = pypdf.PdfReader(io.BytesIO(payload))
    if reader.is_encrypted:
        # Tentar `decrypt("")` resolveria parte dos casos e criaria um pior:
        # documento protegido lido como se nao fosse. Se o emissor protegeu,
        # o registro diz que ele protegeu.
        raise _ExtractionRefused(
            ExtractionFailureReason.ENCRYPTED,
            "PDF protegido por criptografia",
            remedy="Buscar a versao aberta na origem, se houver.",
        )
    return tuple(page.extract_text() or "" for page in reader.pages)


def _blocks(page_text: str) -> list[tuple[int, int]]:
    """Pagina -> intervalos [start, end) de blocos uteis, em ordem de leitura.

    Os intervalos indexam `page_text` diretamente: `page_text[start:end]` E o
    texto da unidade. Nenhum caractere e alterado no caminho.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for separator in _BLANK_LINE.finditer(page_text):
        spans.append((cursor, separator.start()))
        cursor = separator.end()
    spans.append((cursor, len(page_text)))

    out: list[tuple[int, int]] = []
    for start, end in spans:
        trimmed = _trim(page_text, start, end)
        if trimmed is None:
            continue
        out.extend(_split_long(page_text, *trimmed))
    return out


def _trim(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Encolhe o intervalo ate o primeiro e o ultimo caractere nao-branco."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if end > start else None


def _split_long(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Divide um bloco longo em fronteira de sentenca, sem perder caractere.

    As fatias devolvidas cobrem [start, end) por inteiro, a menos do espaco em
    branco aparado nas bordas - que nao e conteudo. Ordem preservada.
    """
    if end - start <= MAX_UNIT_CHARS:
        return [(start, end)]

    pieces: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > MAX_UNIT_CHARS:
        limit = cursor + MAX_UNIT_CHARS
        cut = _last_sentence_boundary(text, cursor, limit)
        if cut is None:
            cut = limit
        piece = _trim(text, cursor, cut)
        if piece is not None:
            pieces.append(piece)
        cursor = cut

    tail = _trim(text, cursor, end)
    if tail is not None:
        if pieces and (tail[1] - tail[0]) < MIN_TAIL_CHARS:
            # Cauda curta volta para o trecho anterior. O texto nao muda: o
            # intervalo anterior so passa a terminar onde a cauda terminava.
            previous_start, _ = pieces[-1]
            pieces[-1] = (previous_start, tail[1])
        else:
            pieces.append(tail)
    return pieces


def _last_sentence_boundary(text: str, start: int, limit: int) -> int | None:
    """Ultimo fim de sentenca em [start, limit), ou `None` se nao houver.

    Deterministico: sempre o ultimo, nunca o mais bonito.
    """
    best: int | None = None
    for match in _SENTENCE_END.finditer(text, start, limit):
        best = match.start() + 1
    if best is None or best <= start:
        return None
    return best


class _ExtractionRefused(Exception):
    def __init__(
        self, reason: ExtractionFailureReason, message: str, *, remedy: str | None = None
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.remedy = remedy


def _failed(
    document_id: str,
    reason: ExtractionFailureReason,
    message: str,
    *,
    remedy: str | None = None,
) -> ExtractionOutcome:
    return ExtractionOutcome(
        document_id=document_id,
        extraction_version=EXTRACTION_VERSION,
        failure=ExtractionFailure(
            document_id=document_id,
            reason=reason,
            message=message,
            extraction_version=EXTRACTION_VERSION,
            remedy=remedy,
            failed_at=datetime.now(UTC),
        ),
    )

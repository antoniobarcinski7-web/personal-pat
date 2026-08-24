"""Helpers da camada de corpus.

O helper central e `make_pdf`: monta um PDF minimo, valido e deterministico a
mao, sem biblioteca de escrita. Existe por uma razao de desenho, e nao por
economia - se os testes offline usassem PDFs gravados como fixture binaria,
nao daria para exercitar os casos que importam (documento sem camada de texto,
documento corrompido, mesma pagina com blocos diferentes) sem colecionar
arquivos que ninguem consegue revisar num diff.

Os bytes gerados aqui passam pelo `pypdf` de verdade. O par offline/rede e
deliberado, como na Fase 2: o offline prova que o extrator, o indice e o corte
point-in-time estao certos; o de rede (`-m network`) prova que o layout do IPE
e do RAD continua o que era.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pat.contracts.common import SourceTier
from pat.contracts.corpus import DateBasis, DocumentKind, SourceDocument

__all__ = ["make_document", "make_pdf", "make_pdf_without_text"]


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _build(objects: list[bytes]) -> bytes:
    """Monta o arquivo com a tabela xref correta.

    O `pypdf` reconstroi xref quebrada em silencio, entao uma tabela errada
    aqui passaria despercebida - e o teste estaria exercitando o caminho de
    recuperacao do parser em vez do caminho normal. Por isso ela e montada
    direito.
    """
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def make_pdf(pages: list[list[str]]) -> bytes:
    """PDF com uma lista de linhas por pagina. Deterministico.

    Cada linha vira um `Tj` numa posicao propria. Linha vazia produz uma linha
    em branco no texto extraido, que e como se criam blocos separados - e
    portanto como se testa a fronteira de unidade.
    """
    page_count = len(pages)
    font_number = 3 + 2 * page_count

    objects: list[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        (
            "<</Type/Pages/Kids["
            + " ".join(f"{3 + 2 * i} 0 R" for i in range(page_count))
            + f"]/Count {page_count}>>"
        ).encode(),
    ]

    for index, lines in enumerate(pages):
        page_number = 3 + 2 * index
        content_number = page_number + 1
        objects.append(
            (
                f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
                f"/Contents {content_number} 0 R"
                f"/Resources<</Font<</F1 {font_number} 0 R>>>>>>"
            ).encode()
        )
        stream_parts = ["BT /F1 12 Tf 72 720 Td 14 TL"]
        for line in lines:
            # Linha vazia sai como um unico espaco, e nao como string vazia: o
            # pypdf descarta `() Tj` e a linha em branco sumiria - justamente a
            # fronteira que separa um bloco do seguinte.
            stream_parts.append(f"({_escape(line)}) Tj T*" if line else "( ) Tj T*")
        stream_parts.append("ET")
        stream = "\n".join(stream_parts).encode("latin-1")
        objects.append(
            f"<</Length {len(stream)}>>\nstream\n".encode() + stream + b"\nendstream"
        )

    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    return _build(objects)


def make_pdf_without_text() -> bytes:
    """PDF valido, com pagina, e sem uma unica letra.

    E o caso do documento digitalizado. Tem que virar
    `ExtractionFailureReason.NO_TEXT_LAYER`, e nao uma lista vazia de unidades:
    'nao ha texto' e 'nao ha nada a dizer' sao coisas diferentes.
    """
    return _build(
        [
            b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>",
            b"<</Length 0>>\nstream\n\nendstream",
        ]
    )


def make_document(
    document_id: str,
    *,
    entity_id: str = "br:cnpj:33000167000101",
    kind: DocumentKind = DocumentKind.PERFORMANCE_REPORT,
    published_at: date,
    title: str = "Relatorio de Desempenho",
    byte_size: int = 1024,
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        entity_id=entity_id,
        kind=kind,
        title=title,
        language="pt-BR",
        source_tier=SourceTier.PRIMARY_OFFICIAL,
        published_at=published_at,
        published_at_basis=DateBasis.FILING_METADATA,
        media_type="application/pdf",
        byte_size=byte_size,
        provider_id="cvm",
        dataset_id="cvm.ipe_doc",
        resource_key=f"protocolo-{document_id[:8]}",
        source_url="https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?x=1",
        retrieval_id=f"ret-{document_id[:8]}",
        origin_category="Dados Economico-Financeiros",
        origin_version="1",
        first_seen_at=datetime(2025, 1, 1, tzinfo=UTC),
        first_seen_run_id="run-teste",
    )

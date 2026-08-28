"""O extrator: deterministico, versionado, e que falha com nome proprio."""

from __future__ import annotations

import pypdf

from pat.contracts.corpus import ExtractionFailureReason, LocatorScheme
from pat.corpus.extract import (
    EXTRACTION_VERSION,
    MAX_UNIT_CHARS,
    extract,
    extract_pdf_page_texts,
    sniff_media_type,
)
from tests.corpus.conftest import make_pdf, make_pdf_without_text

DOC = "a" * 64
OUTRO_DOC = "b" * 64


def test_a_versao_do_extrator_inclui_a_versao_efetiva_do_pypdf():
    """Regra 3 da decisao que autorizou a dependencia.

    Nao e a versao minima do `pyproject.toml` - e a que de fato produziu o
    texto. Sao coisas diferentes, e so a segunda explica um texto gravado.
    """
    assert pypdf.__version__ in EXTRACTION_VERSION
    assert EXTRACTION_VERSION.startswith("pdf-blocks/v")


def test_extracao_e_deterministica():
    pdf = make_pdf([["Primeira linha", "", "Segundo bloco de texto."]])
    primeira = extract(DOC, pdf)
    segunda = extract(DOC, pdf)

    assert [u.unit_id for u in primeira.units] == [u.unit_id for u in segunda.units]
    assert [u.text for u in primeira.units] == [u.text for u in segunda.units]
    assert [u.locator for u in primeira.units] == [u.locator for u in segunda.units]


def test_o_texto_da_unidade_e_fatia_literal_da_pagina():
    """A conferencia que torna uma citacao verificavel em vez de acreditavel.

    Reextrair, fatiar pelo locator e comparar tem que dar identico. Se o
    extrator normalizasse espaco em algum ponto, este teste quebraria - e e
    para isso que ele existe.
    """
    pdf = make_pdf(
        [
            ["Relatorio 3T24", "", "A receita caiu por causa do Brent."],
            ["Outra pagina", "", "Com outro bloco."],
        ]
    )
    outcome = extract(DOC, pdf)
    paginas = extract_pdf_page_texts(pdf)

    assert outcome.units
    for unit in outcome.units:
        pagina = paginas[unit.locator.page - 1]
        assert pagina[unit.locator.char_start : unit.locator.char_end] == unit.text


def test_unit_id_muda_com_a_versao_de_extracao():
    """Reprocessar com extrator novo cria unidade nova AO LADO da antiga.

    E a regra que a decisao 4 pediu por escrito: a versao faz parte da
    identidade, entao nada e substituido em silencio.
    """
    from pat.contracts.corpus import UnitLocator
    from pat.corpus.identity import unit_id

    locator = UnitLocator(
        scheme=LocatorScheme.PDF_PAGE, page=1, block=0, char_start=0, char_end=10
    )
    antiga = unit_id(document_id=DOC, extraction_version="pdf-blocks/v1+pypdf1.0.0", locator=locator)
    nova = unit_id(document_id=DOC, extraction_version="pdf-blocks/v1+pypdf2.0.0", locator=locator)
    assert antiga != nova


def test_unit_id_muda_com_o_documento():
    """O mesmo trecho em documentos diferentes e outra citacao.

    Uma reapresentacao repete paragrafos inteiros; as duas versoes precisam
    continuar distinguiveis.
    """
    pdf = make_pdf([["Texto identico nos dois documentos."]])
    um = extract(DOC, pdf)
    outro = extract(OUTRO_DOC, pdf)

    assert um.units[0].text == outro.units[0].text
    assert um.units[0].unit_id != outro.units[0].unit_id


def test_blocos_sao_separados_por_linha_em_branco():
    pdf = make_pdf([["Bloco um.", "", "Bloco dois.", "", "Bloco tres."]])
    outcome = extract(DOC, pdf)
    textos = [u.text for u in outcome.units]
    assert textos == ["Bloco um.", "Bloco dois.", "Bloco tres."]
    assert [u.locator.block for u in outcome.units] == [0, 1, 2]


def test_ordinal_segue_a_ordem_de_leitura_entre_paginas():
    pdf = make_pdf([["Pagina um a.", "", "Pagina um b."], ["Pagina dois."]])
    outcome = extract(DOC, pdf)
    assert [u.ordinal for u in outcome.units] == [0, 1, 2]
    assert [u.locator.page for u in outcome.units] == [1, 1, 2]


def test_bloco_longo_e_quebrado_sem_perder_texto():
    """A quebra e deterministica e NAO descarta caractere.

    Um extrator que truncasse produziria citacao incompleta que se apresenta
    como completa - a forma mais facil de tirar uma frase de contexto.
    """
    sentenca = "Esta e uma sentenca de tamanho conhecido para o teste. "
    linha = sentenca * 60
    pdf = make_pdf([[linha]])

    outcome = extract(DOC, pdf)
    assert len(outcome.units) > 1
    for unit in outcome.units:
        assert unit.char_count <= MAX_UNIT_CHARS + 200

    pagina = extract_pdf_page_texts(pdf)[0]
    coberto = "".join(
        pagina[u.locator.char_start : u.locator.char_end] for u in outcome.units
    )
    assert coberto.replace(" ", "").replace("\n", "") == pagina.replace(" ", "").replace(
        "\n", ""
    )


def test_pdf_sem_camada_de_texto_falha_com_nome():
    """Documento digitalizado nao vira lista vazia, e NAO cai para OCR."""
    outcome = extract(DOC, make_pdf_without_text())
    assert not outcome.units
    assert outcome.failure is not None
    assert outcome.failure.reason is ExtractionFailureReason.NO_TEXT_LAYER
    assert "OCR" in (outcome.failure.remedy or "")


def test_tipo_sem_extrator_falha_com_nome():
    """O invariante e "tipo sem extrator recusa com motivo", e nao "so PDF".

    Ate a N2 o HTML caia aqui; agora ele tem extrator proprio, e o teste passa
    a usar um tipo que de fato nao tem - um ZIP. O que nao pode mudar e a
    recusa NOMEADA: um tipo desconhecido que produzisse zero unidades em
    silencio se leria como "o documento nao diz nada".
    """
    outcome = extract(DOC, b"PK\x03\x04conteudo compactado")
    assert outcome.failure is not None
    assert outcome.failure.reason is ExtractionFailureReason.UNSUPPORTED_MEDIA_TYPE
    assert "application/zip" in outcome.failure.message


def test_pdf_corrompido_vira_erro_nomeado_e_nao_excecao():
    outcome = extract(DOC, b"%PDF-1.4\nlixo que nao e um pdf\n%%EOF")
    assert outcome.failure is not None
    assert outcome.failure.reason in {
        ExtractionFailureReason.EXTRACTOR_ERROR,
        ExtractionFailureReason.MALFORMED,
        ExtractionFailureReason.NO_TEXT_LAYER,
    }


def test_toda_falha_carrega_a_versao_do_extrator():
    """Sem isso nao da para saber se uma falha antiga ainda vale hoje."""
    outcome = extract(DOC, b"nao e pdf")
    assert outcome.failure is not None
    assert outcome.failure.extraction_version == EXTRACTION_VERSION


def test_o_tipo_e_lido_dos_bytes_e_nao_do_header():
    """A CVM responde `text/html` para um PDF.

    Um sistema que acreditasse no header classificaria errado o corpus
    inteiro, e o erro apareceria como 'nao ha documentos' - que se le como
    'a empresa nao falou nada'.
    """
    assert sniff_media_type(make_pdf([["x"]])) == "application/pdf"
    assert sniff_media_type(b"PK\x03\x04qualquer") == "application/zip"
    assert sniff_media_type(b"<!DOCTYPE html><p>oi") == "text/html"
    assert sniff_media_type(b"\x00\x01\x02") is None

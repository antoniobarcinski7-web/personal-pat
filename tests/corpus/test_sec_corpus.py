"""N2 - arquivamento da SEC no pipeline de corpus, sem caminho paralelo.

O teste que define o milestone e `test_os_dois_regimes_usam_o_mesmo_sync`: se
o lado americano tivesse um `sync` proprio, existiriam dois pipelines de
corpus, e o terceiro regime pediria um terceiro.

Os outros dois que importam:

- `test_o_texto_do_html_e_fatia_literal` - verbatim byte a byte continua
  valendo para HTML. Sem isso, a citacao de um 10-K seria parafrase com cara
  de citacao.
- `test_documento_sem_unidade_e_reprocessado` - documento cuja extracao falhou
  ANTES tem que ser reprocessado quando um extrator novo existe. Foi o achado
  da primeira sincronizacao real: os 10-K entraram como
  `unsupported_media_type` e ficariam permanentemente nao citaveis.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from pat.contracts.corpus import DocumentCandidate, DocumentKind, LocatorScheme
from pat.corpus.extract import (
    HTML_EXTRACTION_VERSION,
    XHTML_MEDIA_TYPE,
    extract,
    extract_html_text,
    sniff_media_type,
)
from pat.parse.sec_submissions import FORM_TO_KIND, parse_submissions

DOC = "a" * 64
CIK = "0001065280"

# A forma real de um 10-K da SEC: iXBRL, com declaracao XML e comentarios da
# ferramenta geradora antes da raiz.
IXBRL = (
    b"<?xml version='1.0' encoding='ASCII'?>\n"
    b"<!--XBRL Document Created with the Workiva Platform-->\n"
    b"<!--Copyright 2025 Workiva-->\n"
    b'<html xmlns="http://www.w3.org/1999/xhtml">\n'
    b"<head><style>.x{color:red}</style></head>\n"
    b"<body>\n"
    b"<div><h1>Item 1A. Risk Factors</h1></div>\n"
    b"<div><p>We face competition from a wide variety of &amp; entertainment "
    b"providers.</p></div>\n"
    b"<table><tr><td>Revenues</td><td>39,001</td></tr></table>\n"
    b"</body></html>"
)


def submissions(*, formas=("10-K", "4"), com_antigos: bool = True) -> bytes:
    recentes = {
        "accessionNumber": ["0001065280-25-000044", "0001065280-25-000010"],
        "filingDate": ["2025-01-27", "2025-02-03"],
        "reportDate": ["2024-12-31", ""],
        "form": list(formas),
        "primaryDocument": ["nflx-20241231.htm", "xslF345X05/primary.xml"],
        "primaryDocDescription": ["10-K", ""],
    }
    corpo = {
        "cik": int(CIK),
        "name": "NETFLIX INC",
        "filings": {
            "recent": recentes,
            "files": [{"name": "CIK0001065280-submissions-001.json"}] if com_antigos else [],
        },
    }
    return json.dumps(corpo).encode("utf-8")


# -- o criterio do milestone ------------------------------------------------


def test_os_dois_regimes_usam_o_mesmo_sync():
    """CVM e SEC produzem o MESMO tipo, e `sync_documents` nao ramifica.

    A prova e de forma, e nao de comportamento: as duas origens convergem em
    `DocumentCandidate` antes de entrar no pipeline.
    """
    from pat.parse.cvm_ipe import IpeCatalogEntry

    indice = parse_submissions(submissions(), forms=("10-K",))
    americano = indice.candidates[0]
    assert isinstance(americano, DocumentCandidate)

    brasileiro = IpeCatalogEntry(
        cnpj="47508411000156",
        denom_cia="CIA BRASILEIRA DE DISTRIBUICAO",
        cod_cvm=14826,
        categoria="Dados Econômico-Financeiros",
        kind=DocumentKind.PERFORMANCE_REPORT,
        delivered_at=date(2025, 2, 18),
        protocolo="700002",
        url="https://www.rad.cvm.gov.br/x",
    ).as_candidate()
    assert isinstance(brasileiro, DocumentCandidate)

    # Vocabularios diferentes, mesmo tipo: um carrega `10-K` e o outro
    # `Dados Econômico-Financeiros` em `origin_category`, e nenhum dos dois foi
    # traduzido para um rotulo "universal" que ninguem publicou.
    assert americano.origin_category == "10-K"
    assert brasileiro.origin_category == "Dados Econômico-Financeiros"
    assert americano.dataset_id != brasileiro.dataset_id


# -- descoberta de arquivamentos --------------------------------------------


def test_forma_desconhecida_vira_other_declaradamente():
    """Nao ha deducao por semelhanca de nome. O documento continua citavel."""
    indice = parse_submissions(submissions(formas=("10-K", "XX-NOVA")))
    tipos = {c.origin_category: c.kind for c in indice.candidates}
    assert tipos["10-K"] is FORM_TO_KIND["10-K"]
    assert tipos["XX-NOVA"] is DocumentKind.OTHER


def test_o_historico_que_ficou_de_fora_e_declarado():
    """A SEC pagina o historico, e "nao arquivou" e diferente de "nao alcanca".

    As duas chegam ao analista como ausencia e pedem acoes opostas.
    """
    indice = parse_submissions(submissions())
    assert indice.older_files == ("CIK0001065280-submissions-001.json",)

    sem = parse_submissions(submissions(com_antigos=False))
    assert sem.older_files == ()


def test_filtro_de_forma_e_de_data():
    todos = parse_submissions(submissions())
    assert len(todos.candidates) == 2

    so_anual = parse_submissions(submissions(), forms=("10-K",))
    assert len(so_anual.candidates) == 1
    assert so_anual.candidates[0].origin_category == "10-K"

    recentes = parse_submissions(submissions(), since=date(2025, 2, 1))
    assert len(recentes.candidates) == 1
    assert recentes.candidates[0].published_at == date(2025, 2, 3)


def test_report_date_vazio_nao_vira_data_inventada():
    """`reportDate` vazio fica vazio. Derivar periodo do que nao foi publicado
    seria inferencia por formato - o mesmo erro de casar conta por rotulo."""
    indice = parse_submissions(submissions())
    por_forma = {c.origin_category: c for c in indice.candidates}
    assert por_forma["10-K"].reference_date == date(2024, 12, 31)
    assert por_forma["4"].reference_date is None
    assert por_forma["4"].reference_date_basis is None


def test_layout_mudado_na_origem_levanta():
    with pytest.raises(ValueError, match="layout mudou na origem"):
        parse_submissions(json.dumps({"cik": 1065280}).encode())


# -- extracao de HTML -------------------------------------------------------


def test_ixbrl_e_reconhecido_como_xhtml():
    """O 10-K comeca com declaracao XML e comentarios, nao com `<html`.

    Um sniff que so olhasse o primeiro token classificaria todo arquivamento
    americano como desconhecido - foi o que aconteceu na primeira
    sincronizacao real, e os tres 10-K entraram como nao extraidos.
    """
    assert sniff_media_type(IXBRL) == XHTML_MEDIA_TYPE


def test_o_texto_do_html_e_fatia_literal():
    """Verbatim byte a byte, como no PDF. A conferencia e reextrair e fatiar."""
    resultado = extract(DOC, IXBRL)
    assert resultado.failure is None
    assert resultado.units

    fonte = extract_html_text(IXBRL)
    for unidade in resultado.units:
        fatia = fonte[unidade.locator.char_start : unidade.locator.char_end]
        assert fatia == unidade.text


def test_entidade_html_vira_o_caractere_que_ela_representa():
    """`&amp;` nao e uma citacao de "&amp;": e uma citacao de "&"."""
    resultado = extract(DOC, IXBRL)
    texto = " ".join(u.text for u in resultado.units)
    assert "&amp;" not in texto
    assert "of & entertainment" in texto


def test_script_e_style_nao_viram_citacao():
    resultado = extract(DOC, IXBRL)
    assert not any("color:red" in u.text for u in resultado.units)


def test_celula_de_tabela_fica_na_mesma_linha():
    """Uma tabela lida com uma celula por linha vira coluna de numeros sem
    rotulo, e a citacao perde o que a torna legivel."""
    resultado = extract(DOC, IXBRL)
    assert any("Revenues 39,001" in u.text for u in resultado.units)


def test_a_unidade_de_html_carrega_caminho_de_no():
    """`UnitLocator` exige `node_path` para HTML, e com razao: um endereco que
    fosse so offset nao diria a um humano onde conferir."""
    resultado = extract(DOC, IXBRL)
    for unidade in resultado.units:
        assert unidade.locator.scheme is LocatorScheme.HTML_NODE
        assert unidade.locator.node_path
    caminhos = [u.locator.node_path for u in resultado.units]
    assert any("h1[1]" in c for c in caminhos)


def test_a_versao_do_extrator_de_html_e_separada_da_do_pdf():
    """Uma versao unica faria o upgrade de um invalidar as unidades do outro."""
    from pat.corpus.extract import EXTRACTION_VERSION

    assert HTML_EXTRACTION_VERSION != EXTRACTION_VERSION
    assert HTML_EXTRACTION_VERSION.startswith("html-blocks/")
    resultado = extract(DOC, IXBRL)
    assert all(u.extraction_version == HTML_EXTRACTION_VERSION for u in resultado.units)


def test_html_sem_texto_falha_com_nome():
    """Zero unidade em silencio se leria como "o documento nao diz nada"."""
    from pat.contracts.corpus import ExtractionFailureReason

    resultado = extract(DOC, b"<html><head><style>a{}</style></head><body></body></html>")
    assert resultado.failure is not None
    assert resultado.failure.reason is ExtractionFailureReason.NO_TEXT_LAYER
    assert "OCR" in (resultado.failure.remedy or "")


def test_tag_de_fechamento_orfa_nao_derruba_o_documento():
    """HTML de arquivamento tem fechamento sem abertura com frequencia.

    Desempilhar o elemento errado embaralharia todos os caminhos seguintes, e
    um contador negativo descartaria o resto do documento em silencio.
    """
    quebrado = b"<html><body><p>antes</p></div></span><p>depois</p></body></html>"
    resultado = extract(DOC, quebrado)
    assert resultado.failure is None
    texto = " ".join(u.text for u in resultado.units)
    assert "antes" in texto and "depois" in texto


def test_a_extracao_e_reproduzivel():
    """Mesmos bytes, mesmas unidades, mesmos `unit_id`. Sem relogio, sem
    aleatoriedade, sem modelo."""
    primeira = extract(DOC, IXBRL)
    segunda = extract(DOC, IXBRL)
    assert [u.unit_id for u in primeira.units] == [u.unit_id for u in segunda.units]
    assert [u.text for u in primeira.units] == [u.text for u in segunda.units]


# -- reprocessamento --------------------------------------------------------


def test_documento_sem_unidade_e_reprocessado(tmp_path, monkeypatch):
    """Extracao que falhou ANTES tem que ser tentada de novo.

    Foi o achado da primeira sincronizacao real: os tres 10-K da Netflix
    entraram como `unsupported_media_type` porque o sniff nao reconhecia
    iXBRL. Com o sniff corrigido, uma segunda passada precisava extrai-los -
    e a versao anterior de `sync_documents` pulava todo documento ja conhecido,
    deixando-os permanentemente nao citaveis por causa de uma limitacao que ja
    tinha sido removida.

    Um documento ja conhecido E ja extraido continua sendo pulado: o
    reprocessamento e para quem nao tem unidade, e nao para todo mundo.
    """
    from datetime import UTC, datetime

    from pat.audit.run import new_run
    from pat.contracts.common import SourceTier
    from pat.contracts.documents import HttpMeta, Retrieval
    from pat.corpus import sync_documents
    from pat.ingest import IngestOutcome
    from pat.store import corpus as corpus_store
    from pat.store.bronze import BronzeStore
    from pat.store.catalog import Catalog
    from pat.store.db import connect, migrate
    from pat.sources.base import ResourceRef

    conn = connect(tmp_path / "w.duckdb")
    migrate(conn)
    bronze = BronzeStore(tmp_path / "bronze")
    catalog = Catalog(conn)
    run = new_run(command="teste")
    catalog.start_run(run)

    put = bronze.put(IXBRL, run_id=run.run_id, media_type=XHTML_MEDIA_TYPE, provenance={})
    document_id = put.document.content_sha256
    momento = datetime(2025, 6, 30, tzinfo=UTC)

    candidato = parse_submissions(submissions(), forms=("10-K",)).candidates[0]

    def _falso_ingest(dataset_id, params, **kwargs):
        return [
            IngestOutcome(
                ref=ResourceRef(
                    provider_id="sec",
                    dataset_id=dataset_id,
                    resource_key=candidato.resource_key,
                    url=candidato.source_url,
                ),
                document=put.document,
                retrieval=Retrieval(
                    retrieval_id="ret-1",
                    content_sha256=document_id,
                    provider_id="sec",
                    source_tier=SourceTier.PRIMARY_OFFICIAL,
                    dataset_id=dataset_id,
                    resource_key=candidato.resource_key,
                    url=candidato.source_url,
                    requested_at=momento,
                    retrieved_at=momento,
                    http=HttpMeta(status_code=200, content_type=XHTML_MEDIA_TYPE),
                    run_id=run.run_id,
                    provider_version="1.0.0",
                ),
                is_new_content=True,
            )
        ]

    monkeypatch.setattr("pat.corpus.ingest_dataset", _falso_ingest)

    comum = dict(
        entity_id="us:cik:0001065280",
        entries=[candidato],
        registry=None,
        bronze=bronze,
        catalog=catalog,
        run=run,
    )

    primeira = sync_documents(conn, **comum)
    assert primeira.documents_new == 1
    assert primeira.units_written > 0
    unidades = len(corpus_store.units_for_document(conn, document_id))
    assert unidades > 0

    # Segunda passada com o documento JA extraido: pula, sem reextrair.
    segunda = sync_documents(conn, **comum)
    assert segunda.skipped_existing == 1
    assert segunda.units_written == 0
    assert len(corpus_store.units_for_document(conn, document_id)) == unidades

    # Agora o caso que motivou a mudanca: documento conhecido e SEM unidade.
    conn.execute("DELETE FROM document_unit WHERE document_id = ?", [document_id])
    assert not corpus_store.units_for_document(conn, document_id)

    terceira = sync_documents(conn, **comum)
    assert terceira.documents_new == 0, "o documento ja existia; nao e novo"
    assert terceira.units_written == unidades, "mas as unidades voltam"
    conn.close()


# -- secao na unidade e na busca --------------------------------------------

SECOES = (
    b"<?xml version='1.0'?>\n"
    b'<html xmlns="http://www.w3.org/1999/xhtml"><body>\n'
    # Indice: numero e titulo em blocos separados, com pagina.
    b"<table><tr><td>Item 1.</td></tr><tr><td>Business</td></tr>"
    b"<tr><td>1</td></tr>"
    b"<tr><td>Item 1A.</td></tr><tr><td>Risk Factors</td></tr>"
    b"<tr><td>4</td></tr></table>\n"
    # Corpo: numero e titulo juntos.
    b"<div>Item 1.Business</div>"
    b"<div>" + b"Somos uma companhia de streaming. " * 40 + b"</div>"
    b"<div>Item 1A.Risk Factors</div>"
    b"<div>" + b"We face intense competition from many providers. " * 40 + b"</div>"
    b"</body></html>"
)


def _seccionar(texto: str):
    from pat.parse.sec_sections import split_sections

    return tuple(
        (s.char_start, s.char_end, s.path) for s in split_sections(texto, form="10-K")
    )


def test_a_unidade_carrega_a_secao_em_que_estava():
    resultado = extract(DOC, SECOES, sectioner=_seccionar)
    assert resultado.failure is None

    caminhos = {u.section_path for u in resultado.units}
    assert ("Item 1A", "Risk Factors") in caminhos
    assert ("Item 1", "Business") in caminhos


def test_o_indice_fica_sem_secao_em_vez_de_herdar_a_seguinte():
    """Rotular o sumario com a secao seguinte seria justamente o erro que a
    seccao existe para consertar."""
    resultado = extract(DOC, SECOES, sectioner=_seccionar)
    indice = [u for u in resultado.units if u.text.strip() in {"Item 1.", "Business", "1"}]
    assert indice, "o indice tem que estar entre as unidades - ele e citavel"
    assert all(u.section_path == () for u in indice)


def test_sem_seccionador_a_unidade_nasce_sem_secao():
    """Formato cuja estrutura ninguem declarou continua citavel, sem endereco
    de secao inventado."""
    resultado = extract(DOC, SECOES)
    assert resultado.failure is None
    assert all(u.section_path == () for u in resultado.units)


def test_a_versao_do_extrator_subiu_com_a_secao():
    """O conteudo das unidades mudou - nao o texto, mas o endereco que elas
    carregam -, e reprocessar cria unidades AO LADO das antigas."""
    assert HTML_EXTRACTION_VERSION.startswith("html-blocks/v2")


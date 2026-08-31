"""O indice de um arquivamento: tipo DECLARADO, nunca deduzido do nome.

O caso que motiva o modulo esta no teste
`test_a_carta_ao_acionista_e_achada_pelo_tipo_e_nao_pelo_nome`: os dois 8-K de
resultado da Netflix nomeiam o mesmo EX-99.1 de formas diferentes conforme o
agente que protocolou. Casar por padrao de nome acertaria num e erraria no
outro - o pior tipo de erro, o intermitente.
"""

from __future__ import annotations

from datetime import date

import pytest

from pat.contracts.corpus import DocumentKind
from pat.parse.sec_filing_index import (
    EXHIBIT_TYPE_TO_KIND,
    exhibit_candidates,
    parse_filing_index,
)
from pat.sources.base import SourceError
from pat.sources.public.sec import ENV_USER_AGENT, SECProvider

NETFLIX = "1065280"
ACCESSION = "0001065280-26-000033"


def cabecalho(*blocos: str, accession: str = ACCESSION, form: str = "8-K") -> bytes:
    """Um `-index-headers.html` minimo, ESCAPADO como a origem entrega.

    O escape nao e detalhe do fixture: a SEC serve o SGML dentro de uma pagina
    HTML, e os bytes trazem `&lt;DOCUMENT&gt;`. Um fixture com o tag cru deixa
    o parser passar no teste e devolver zero exibicao contra a origem real -
    aconteceu, e e por isso que o escape esta aqui e nao no parser do teste.
    """
    corpo = "\n".join(blocos)
    return (
        f"&lt;SEC-HEADER&gt;{accession}.hdr.sgml : 20260120\n"
        f"ACCESSION NUMBER:\t\t{accession}\n"
        f"CONFORMED SUBMISSION TYPE:\t{form}\n"
        f"FILED AS OF DATE:\t\t20260120\n"
        f"&lt;/SEC-HEADER&gt;\n{corpo}\n"
    ).encode("utf-8")


def documento(tipo: str, nome: str, sequencia: int, descricao: str = "") -> str:
    """Um bloco `<DOCUMENT>` escapado, com o link que a origem intercala.

    A ancora `<a href=...>` vem crua no meio do SGML escapado - a pagina e
    hibrida. Ela esta aqui porque um parser que assumisse SGML puro tropecaria
    nela.
    """
    return (
        f"&lt;DOCUMENT&gt;\n&lt;TYPE&gt;{tipo}\n&lt;SEQUENCE&gt;{sequencia}\n"
        f"&lt;FILENAME&gt;{nome}\n&lt;DESCRIPTION&gt;{descricao or tipo}\n"
        f"&lt;TEXT&gt;\n<a href=\"{nome}\">Document {sequencia} - file: {nome}</a><br>\n"
        f"&lt;/DOCUMENT&gt;"
    )


# -- O provider --------------------------------------------------------------


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv(ENV_USER_AGENT, "teste (teste@exemplo)")
    return SECProvider()


def test_a_url_do_indice_e_montada_e_nao_descoberta(provider):
    (ref,) = provider.resolve("sec.filing_index", cik=NETFLIX, accession=ACCESSION)
    assert ref.url == (
        "https://www.sec.gov/Archives/edgar/data/1065280/"
        "000106528026000033/0001065280-26-000033-index-headers.html"
    )
    assert ref.resource_key == ACCESSION


def test_o_accession_e_o_recurso_logico_do_indice(provider):
    """Como o proprio arquivamento, o indice dele e imutavel: a SEC nao
    reescreve accession. Por isso a chave e o accession puro, sem data."""
    (ref,) = provider.resolve("sec.filing_index", cik=NETFLIX, accession=ACCESSION)
    (outro,) = provider.resolve("sec.filing_index", cik="0001065280", accession=ACCESSION)
    assert ref.resource_key == outro.resource_key == ACCESSION


def test_accession_malformado_para_antes_da_rede(provider):
    with pytest.raises(SourceError, match="accession invalido"):
        provider.resolve("sec.filing_index", cik=NETFLIX, accession="26-33")


# -- O parser ----------------------------------------------------------------


def test_le_o_tipo_declarado_de_cada_arquivo():
    indice = parse_filing_index(
        cabecalho(
            documento("8-K", "nflx-20260120.htm", 1),
            documento("EX-99.1", "ex991_q425.htm", 2),
            documento("EX-101.SCH", "nflx-20260120.xsd", 3),
        )
    )
    assert indice.accession == ACCESSION
    assert indice.form == "8-K"
    assert [f.doc_type for f in indice.files] == ["8-K", "EX-99.1", "EX-101.SCH"]
    assert indice.files[1].filename == "ex991_q425.htm"
    assert indice.files[1].sequence == 2


def test_a_carta_ao_acionista_e_achada_pelo_tipo_e_nao_pelo_nome():
    """O nome do arquivo e escolha do arquivador e muda entre trimestres.

    Estes dois nomes sao reais, do mesmo emissor: `ex991_q425.htm` quando a
    Netflix protocola direto, `d65144dex991.htm` quando passa por agente. Um
    casamento por padrao de nome acertaria num e erraria no outro.
    """
    for nome in ("ex991_q425.htm", "d65144dex991.htm", "arquivo-sem-99-no-nome.htm"):
        indice = parse_filing_index(
            cabecalho(
                documento("8-K", "capa.htm", 1),
                documento("EX-99.1", nome, 2),
            )
        )
        candidatos = exhibit_candidates(
            indice, cik=NETFLIX, published_at=date(2026, 1, 20)
        )
        assert [dict(c.params)["document"] for c in candidatos] == [nome]


def test_a_exibicao_de_resultado_e_performance_report_e_a_capa_nao():
    """A capa 8-K e a carta sao dois documentos, com conteudos diferentes.

    A capa continua `MATERIAL_FACT`, vinda por `sec_submissions`; a carta e o
    release. Colapsar as duas faria quem busca o release achar a folha de rosto.
    """
    indice = parse_filing_index(
        cabecalho(
            documento("8-K", "capa.htm", 1),
            documento("EX-99.1", "carta.htm", 2),
        )
    )
    candidatos = exhibit_candidates(indice, cik=NETFLIX, published_at=date(2026, 1, 20))
    assert len(candidatos) == 1
    assert candidatos[0].kind is DocumentKind.PERFORMANCE_REPORT
    assert candidatos[0].origin_category == "EX-99.1"


def test_o_maquinario_do_xbrl_nao_vira_documento_citavel():
    indice = parse_filing_index(
        cabecalho(
            documento("EX-101.SCH", "nflx.xsd", 1),
            documento("EX-101.LAB", "nflx_lab.xml", 2),
            documento("GRAPHIC", "grafico.jpg", 3),
        )
    )
    assert exhibit_candidates(indice, cik=NETFLIX, published_at=date(2026, 1, 20)) == []
    # E, por ser decisao e nao lacuna, nao aparece como desconhecido.
    assert indice.unknown_types == ()


def test_tipo_desconhecido_e_contado_e_nomeado_em_vez_de_sumir():
    """"Nao ingerimos" e diferente de "nao existe", e as duas chegam como
    ausencia. Um EX-10.1 e uma lacuna que alguem pode querer fechar."""
    indice = parse_filing_index(
        cabecalho(
            documento("EX-99.1", "carta.htm", 1),
            documento("EX-10.1", "contrato.htm", 2),
        )
    )
    assert indice.unknown_types == ("EX-10.1",)
    assert len(exhibit_candidates(indice, cik=NETFLIX, published_at=date(2026, 1, 20))) == 1


def test_a_exibicao_herda_a_data_de_entrega_da_capa():
    """Sem isso a carta e o 8-K que a anuncia cairiam em dias diferentes, e um
    `AS OF` entre os dois mostraria um sem o outro."""
    indice = parse_filing_index(cabecalho(documento("EX-99.1", "carta.htm", 1)))
    (candidato,) = exhibit_candidates(
        indice,
        cik=NETFLIX,
        published_at=date(2026, 1, 20),
        reference_date=date(2025, 12, 31),
    )
    assert candidato.published_at == date(2026, 1, 20)
    assert candidato.reference_date == date(2025, 12, 31)
    assert candidato.resource_key == f"{ACCESSION}/carta.htm"


def test_indice_sem_bloco_de_documento_falha_em_vez_de_devolver_vazio():
    """Vazio silencioso se leria como 'este arquivamento nao tem exibicao'."""
    with pytest.raises(ValueError, match="layout mudou na origem"):
        parse_filing_index(cabecalho())
    with pytest.raises(ValueError, match="layout mudou na origem"):
        parse_filing_index(b"<html>pagina de erro</html>")


def test_a_tabela_de_exibicao_nao_tem_curinga():
    """Exibicao 99 e um balde: cabe consentimento de auditor e comunicado de
    imprensa. Um `EX-99.*` generico traria ruido chamado de release."""
    assert all(not chave.endswith("*") for chave in EXHIBIT_TYPE_TO_KIND)
    assert "EX-99" not in EXHIBIT_TYPE_TO_KIND


def test_o_sgml_vem_escapado_da_origem_e_o_parser_conta_com_isso():
    """A regressao que passou nos testes e falhou contra a SEC.

    A primeira versao deste parser procurava `<TYPE>` cru. Contra a origem ela
    casou zero vezes, e os 31 arquivamentos da Netflix se apresentaram como
    "nenhuma exibicao reconhecida" - ausencia silenciosa, indistinguivel de uma
    companhia que nao anexa carta nenhuma. O teste trava os bytes como a origem
    os manda.
    """
    cru = (
        b"ACCESSION NUMBER:\t\t0001065280-26-000033\n"
        b"CONFORMED SUBMISSION TYPE:\t8-K\n"
        b"&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;SEQUENCE&gt;2\n"
        b"&lt;FILENAME&gt;ex991_q425.htm\n&lt;DESCRIPTION&gt;EX-99.1\n"
        b"&lt;TEXT&gt;\n&lt;/DOCUMENT&gt;\n"
    )
    indice = parse_filing_index(cru)
    assert [f.doc_type for f in indice.files] == ["EX-99.1"]
    assert indice.files[0].filename == "ex991_q425.htm"

"""As paginas anteriores do historico: forma diferente, mesmo contrato.

O indice recente da SEC nao alcanca o passado - na Netflix ele comeca em
junho/2023, e o prospecto do IPO esta tres paginas atras. Estes testes travam a
diferenca de forma entre as duas coisas, que e o que faz uma funcao so ser
perigosa: a pagina e um JSON plano, sem `cik` e sem `name`.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from pat.contracts.corpus import DocumentKind
from pat.parse.sec_submissions import (
    parse_submissions,
    parse_submissions_page,
)
from pat.sources.base import SourceError
from pat.sources.public.sec import ENV_USER_AGENT, SECProvider

NETFLIX = "1065280"
NETFLIX_10 = "0001065280"
PAGINA = "CIK0001065280-submissions-003.json"


def colunas(*linhas: tuple[str, str, str, str, str]) -> dict:
    """As seis colunas que o parser le, na forma PLANA da pagina."""
    return {
        "accessionNumber": [x[0] for x in linhas],
        "filingDate": [x[1] for x in linhas],
        "reportDate": [x[2] for x in linhas],
        "form": [x[3] for x in linhas],
        "primaryDocument": [x[4] for x in linhas],
        "primaryDocDescription": ["" for _ in linhas],
    }


IPO = ("0000950168-03-001155", "2003-03-31", "2002-12-31", "10-K", "d10k.htm")
PROSPECTO = ("0000891618-02-002319", "2002-05-23", "", "424B4", "f81103b4e424b4.htm")


# -- O provider --------------------------------------------------------------


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv(ENV_USER_AGENT, "teste (teste@exemplo)")
    return SECProvider()


def test_a_pagina_e_buscada_pelo_nome_que_a_origem_declarou(provider):
    (ref,) = provider.resolve("sec.submissions_page", cik=NETFLIX, file=PAGINA)
    assert ref.url == f"https://data.sec.gov/submissions/{PAGINA}"
    assert ref.resource_key == PAGINA


def test_nome_de_pagina_forjado_nao_vira_url(provider):
    """O nome vem de JSON de terceiro. Cru numa URL, seria travessia."""
    for forjado in (
        "../../etc/passwd",
        "CIK0001065280-submissions-003.json/../../x",
        "qualquer.json",
    ):
        with pytest.raises(SourceError, match="nome de pagina invalido"):
            provider.resolve("sec.submissions_page", cik=NETFLIX, file=forjado)


def test_a_pagina_nao_e_imutavel_como_o_accession(provider):
    """A SEC repagina conforme a companhia arquiva: a pagina mais nova ganha
    linhas. A chave e o nome, e `pat changed` acusa - e deve."""
    (ref,) = provider.resolve("sec.submissions_page", cik=NETFLIX, file=PAGINA)
    assert ref.resource_key == PAGINA
    assert "-index" not in ref.url


# -- O parser ----------------------------------------------------------------


def test_a_pagina_e_plana_e_a_identidade_vem_de_fora():
    """Sem `cik` nem `name` nos bytes. Exigi-los como argumento e o que impede
    a companhia de chegar sem nome tres camadas adiante."""
    indice = parse_submissions_page(
        json.dumps(colunas(IPO)).encode("utf-8"),
        cik=NETFLIX,
        entity_name="NETFLIX INC",
    )
    assert indice.cik == NETFLIX_10
    assert indice.entity_name == "NETFLIX INC"
    assert indice.older_files == ()


def test_alcanca_o_arquivamento_do_ano_do_ipo():
    """O 10-K de FY2002, protocolado em 2003-03-31: o mais antigo da Netflix."""
    indice = parse_submissions_page(
        json.dumps(colunas(IPO, PROSPECTO)).encode("utf-8"),
        cik=NETFLIX,
        entity_name="NETFLIX INC",
    )
    por_forma = {c.origin_category: c for c in indice.candidates}
    assert por_forma["10-K"].published_at == date(2003, 3, 31)
    assert por_forma["10-K"].reference_date == date(2002, 12, 31)
    assert por_forma["10-K"].kind is DocumentKind.FILING
    assert por_forma["424B4"].published_at == date(2002, 5, 23)


def test_o_indice_completo_recusa_ser_lido_como_pagina():
    """As duas formas convivem na mesma origem. Uma funcao que adivinhasse a
    forma produziria companhia sem nome no dia em que errasse."""
    completo = json.dumps(
        {"cik": 1065280, "name": "NETFLIX INC", "filings": {"recent": colunas(IPO)}}
    ).encode("utf-8")
    with pytest.raises(ValueError, match="indice completo"):
        parse_submissions_page(completo, cik=NETFLIX, entity_name="NETFLIX INC")


def test_pagina_sem_colunas_falha_em_vez_de_devolver_vazio():
    with pytest.raises(ValueError, match="layout mudou na origem"):
        parse_submissions_page(b"{}", cik=NETFLIX, entity_name="NETFLIX INC")


def test_a_pagina_e_o_indice_recente_produzem_candidatos_iguais_em_forma():
    """A montagem e compartilhada de proposito: duplicada, um DocumentKind novo
    acrescentado num lado divergiria do outro sem ninguem notar."""
    completo = json.dumps(
        {"cik": 1065280, "name": "NETFLIX INC", "filings": {"recent": colunas(IPO)}}
    ).encode("utf-8")
    do_indice = parse_submissions(completo).candidates[0]
    da_pagina = parse_submissions_page(
        json.dumps(colunas(IPO)).encode("utf-8"),
        cik=NETFLIX,
        entity_name="NETFLIX INC",
    ).candidates[0]
    assert do_indice == da_pagina


def test_o_filtro_de_forma_e_de_data_vale_na_pagina_tambem():
    indice = parse_submissions_page(
        json.dumps(colunas(IPO, PROSPECTO)).encode("utf-8"),
        cik=NETFLIX,
        entity_name="NETFLIX INC",
        forms=("10-K",),
    )
    assert [c.origin_category for c in indice.candidates] == ["10-K"]

    recente = parse_submissions_page(
        json.dumps(colunas(IPO, PROSPECTO)).encode("utf-8"),
        cik=NETFLIX,
        entity_name="NETFLIX INC",
        since=date(2003, 1, 1),
    )
    assert [c.origin_category for c in recente.candidates] == ["10-K"]

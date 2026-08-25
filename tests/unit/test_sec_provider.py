"""O provider da SEC: construcao de URL e validacao de parametro, sem rede.

Segue a regra do projeto para provider novo - `resolve()` inteiro offline, e
so a verificacao de layout em `-m network`.
"""

from __future__ import annotations

import pytest

from pat.sources.base import SourceError
from pat.sources.public.sec import ENV_USER_AGENT, SECProvider

INTEL = "50863"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv(ENV_USER_AGENT, "teste (teste@exemplo)")
    return SECProvider()


# -- O User-Agent ------------------------------------------------------------


def test_sem_user_agent_o_provider_recusa_com_instrucao(monkeypatch):
    """A SEC devolve 403 para UA generico; falhar aqui e melhor do que la.

    E a mensagem tem que ensinar: um erro que so diz "403" manda o usuario
    procurar em documentacao de terceiro o que este projeto ja sabe.
    """
    monkeypatch.delenv(ENV_USER_AGENT, raising=False)
    with pytest.raises(SourceError, match="User-Agent identificado"):
        _ = SECProvider().user_agent


def test_o_contato_nao_e_embutido_no_codigo():
    """Commitar o e-mail de alguem publicaria um dado pessoal, e o enviaria a
    um terceiro toda vez que qualquer pessoa rodasse o projeto."""
    from pathlib import Path

    fonte = Path("src/pat/sources/public/sec.py").read_text(encoding="utf-8")
    assert "@gmail" not in fonte and "@hotmail" not in fonte
    # O unico e-mail que pode aparecer e o do exemplo da mensagem de erro.
    assert fonte.count("@") <= 6, "endereco de contato embutido no provider?"


def test_o_user_agent_pode_ser_injetado(monkeypatch):
    monkeypatch.delenv(ENV_USER_AGENT, raising=False)
    provider = SECProvider(user_agent="Fulano (fulano@exemplo)")
    assert "Fulano" in provider.user_agent


# -- CIK ---------------------------------------------------------------------


def test_cik_e_normalizado_para_dez_digitos(provider):
    """A SEC aceita as duas formas em lugares diferentes; normalizar impede o
    mesmo recurso logico de virar dois `resource_key` conforme quem digitou."""
    for entrada in ("50863", "0000050863", "CIK0000050863", "cik50863"):
        (ref,) = provider.resolve("sec.companyfacts", cik=entrada)
        assert ref.resource_key == "0000050863"
        assert "CIK0000050863.json" in ref.url


def test_cik_invalido_recusa(provider):
    for ruim in ("abc", "", "12a34"):
        with pytest.raises(SourceError):
            provider.resolve("sec.companyfacts", cik=ruim)


# -- Datasets ----------------------------------------------------------------


def test_submissions_e_companyfacts_sao_recursos_diferentes(provider):
    (submissions,) = provider.resolve("sec.submissions", cik=INTEL)
    (facts,) = provider.resolve("sec.companyfacts", cik=INTEL)
    assert submissions.url != facts.url
    assert "/submissions/" in submissions.url
    assert "/api/xbrl/companyfacts/" in facts.url


def test_dera_monta_a_url_do_trimestre(provider):
    (ref,) = provider.resolve("sec.financial_statements", quarter="2025q1")
    assert ref.url.endswith("/2025q1.zip")
    assert ref.resource_key == "2025q1"
    assert ref.media_type == "application/zip"


def test_trimestre_invalido_recusa(provider):
    for ruim in ("2025", "2025q5", "q1", "2025-01"):
        with pytest.raises(SourceError, match="trimestre invalido"):
            provider.resolve("sec.financial_statements", quarter=ruim)


def test_trimestre_anterior_ao_inicio_da_serie_recusa(provider):
    with pytest.raises(SourceError, match="nao publica"):
        provider.resolve("sec.financial_statements", quarter="2005q1")


def test_filing_doc_monta_o_caminho_do_archives(provider):
    (ref,) = provider.resolve(
        "sec.filing_doc",
        cik=INTEL,
        accession="0000050863-25-000009",
        document="intc-20241228.htm",
    )
    assert "/Archives/edgar/data/50863/000005086325000009/intc-20241228.htm" in ref.url
    assert ref.resource_key == "0000050863-25-000009/intc-20241228.htm"


def test_accession_malformado_recusa(provider):
    with pytest.raises(SourceError, match="accession invalido"):
        provider.resolve(
            "sec.filing_doc", cik=INTEL, accession="123", document="x.htm"
        )


def test_nome_de_documento_com_travessia_recusa(provider):
    """Um `..` no nome viraria leitura fora do diretorio do arquivamento."""
    for ruim in ("../x.htm", "a/b.htm"):
        with pytest.raises(SourceError, match="documento invalido"):
            provider.resolve(
                "sec.filing_doc",
                cik=INTEL,
                accession="0000050863-25-000009",
                document=ruim,
            )


def test_parametro_faltando_recusa(provider):
    with pytest.raises(SourceError, match="exige o parametro"):
        provider.resolve("sec.filing_doc", cik=INTEL, accession="0000050863-25-000009")


def test_parametro_desconhecido_recusa(provider):
    with pytest.raises(SourceError, match="desconhecidos"):
        provider.resolve("sec.companyfacts", cik=INTEL, year="2024")


# -- Disciplina da fonte -----------------------------------------------------


def test_a_sec_e_mais_devagar_que_a_cvm():
    """Pedir menos do que se pode e a postura certa com fonte publica."""
    from pat.sources.public.cvm import CVMProvider

    assert SECProvider.min_interval_s > 0
    assert SECProvider.tier is CVMProvider.tier  # as duas sao primary_official


def test_o_provider_nao_interpreta_conteudo():
    """L0 busca bytes. Nenhum parse de JSON, ZIP ou XBRL aqui dentro."""
    from pathlib import Path

    fonte = Path("src/pat/sources/public/sec.py").read_text(encoding="utf-8")
    for proibido in ("json.loads", "zipfile", "ElementTree", "BeautifulSoup"):
        assert proibido not in fonte, f"sec.py interpreta conteudo ({proibido})"


def test_o_registro_serve_os_datasets_da_sec():
    from pat.sources.registry import Registry

    registry = Registry()
    ids = {ds.dataset_id for _, ds in registry.datasets()}
    assert {
        "sec.submissions",
        "sec.companyfacts",
        "sec.financial_statements",
        "sec.filing_doc",
    } <= ids
    assert registry.for_dataset("sec.companyfacts").provider_id == "sec"

"""O corpus contra a CVM real. So roda com `-m network`.

O par offline/rede e o mesmo da Fase 2, e pela mesma razao. O offline prova
que o extrator, o indice e o corte point-in-time estao certos; este prova que
o LAYOUT da origem continua o que era - que o catalogo IPE ainda tem as
colunas que o parser espera, que o RAD ainda devolve PDF, e que os documentos
de resultado da Petrobras continuam sendo entregues sob as categorias que a
tabela de traducao declara.

Uma coluna renomeada na CVM tem que quebrar aqui, e nao aparecer meses depois
como um corpus que emagreceu sem ninguem notar.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from pat.contracts.corpus import DocumentKind
from pat.corpus.extract import extract, sniff_media_type
from pat.parse.cvm_ipe import parse_catalog
from pat.sources.public.cvm import CVMProvider

pytestmark = pytest.mark.network

COD_CVM_PETROBRAS = 9512
ANO = 2024


@pytest.fixture(scope="module")
def catalogo() -> bytes:
    provider = CVMProvider()
    (ref,) = provider.resolve("cvm.ipe", year=str(ANO))
    return provider.fetch(ref).content


def test_o_catalogo_ipe_ainda_tem_as_colunas_que_o_parser_espera(catalogo):
    """Se a CVM renomear uma coluna, `parse_catalog` levanta com a lista.

    Falhar aqui e o comportamento certo: um parser que tolerasse coluna
    ausente produziria entradas sem data de entrega, e documento sem
    `published_at` nao pode participar de um AS OF.
    """
    entradas = parse_catalog(catalogo, cod_cvm=COD_CVM_PETROBRAS)
    assert entradas, "nenhuma entrega da Petrobras no catalogo de 2024"
    assert all(e.delivered_at.year == ANO or e.delivered_at.year == ANO - 1 for e in entradas)


def test_a_petrobras_continua_entregando_relatorio_de_desempenho(catalogo):
    """A traducao Categoria -> DocumentKind ainda encontra o release.

    E o equivalente de `pat mapping-check` para o lado qualitativo: o binding
    entre o vocabulario da CVM e o conceito universal continua apontando para
    o documento certo? Se a Petrobras mudar a categoria sob a qual entrega o
    relatorio, isto quebra - e tem que quebrar.
    """
    entradas = parse_catalog(catalogo, cod_cvm=COD_CVM_PETROBRAS)
    desempenho = [e for e in entradas if e.kind is DocumentKind.PERFORMANCE_REPORT]
    assert desempenho, "nenhum PERFORMANCE_REPORT classificado em 2024"

    titulos = " | ".join(e.title for e in desempenho).lower()
    assert "desempenho" in titulos

    producao = [e for e in entradas if e.kind is DocumentKind.PRODUCTION_REPORT]
    assert producao, "nenhum PRODUCTION_REPORT: o refino por assunto parou de casar"


def test_um_documento_real_baixa_como_pdf_e_extrai(catalogo):
    """Ponta a ponta contra o RAD: bytes -> tipo -> unidades.

    O `Content-Type` que o RAD devolve e `text/html` mesmo para PDF - por isso
    o tipo e detectado dos bytes. Este teste e o que provaria o contrario, se
    algum dia mudasse.
    """
    entradas = parse_catalog(catalogo, cod_cvm=COD_CVM_PETROBRAS)
    alvo = next(
        e
        for e in sorted(entradas, key=lambda x: x.delivered_at)
        if e.kind is DocumentKind.PERFORMANCE_REPORT and "desempenho" in e.title.lower()
    )

    provider = CVMProvider()
    (ref,) = provider.resolve("cvm.ipe_doc", url=alvo.url, protocolo=alvo.protocolo)
    payload = provider.fetch(ref).content

    assert sniff_media_type(payload) == "application/pdf"

    import hashlib

    outcome = extract(hashlib.sha256(payload).hexdigest(), payload)
    assert outcome.failure is None, f"extracao falhou: {outcome.failure}"
    assert len(outcome.units) > 5

    # O texto e verbatim: reextrair e fatiar pelo locator da a mesma coisa.
    from pat.corpus.extract import extract_pdf_page_texts

    paginas = extract_pdf_page_texts(payload)
    for unit in outcome.units[:20]:
        pagina = paginas[unit.locator.page - 1]
        assert pagina[unit.locator.char_start : unit.locator.char_end] == unit.text


def test_o_provider_recusa_url_fora_do_dominio_da_cvm():
    """Sem rede, mas mora aqui porque e a mesma superficie.

    O provider so busca documento em `rad.cvm.gov.br`. A URL vem do catalogo
    da CVM; aceitar qualquer URL faria o corpus poder ser envenenado por uma
    linha de catalogo adulterada.
    """
    from pat.sources.base import SourceError

    provider = CVMProvider()
    with pytest.raises(SourceError, match="so busca documentos"):
        provider.resolve(
            "cvm.ipe_doc", url="https://exemplo.invalido/doc.pdf", protocolo="123"
        )


def test_o_link_de_download_do_catalogo_continua_apontando_para_o_rad(catalogo):
    entradas = parse_catalog(catalogo, cod_cvm=COD_CVM_PETROBRAS)
    assert all(e.url.startswith("https://www.rad.cvm.gov.br/") for e in entradas), (
        "a CVM mudou o host de download; o provider recusaria todas as URLs"
    )


def test_a_data_de_entrega_e_anterior_ou_igual_a_hoje(catalogo):
    """`published_at` e knowledge_date: entrega no futuro seria dado corrompido."""
    entradas = parse_catalog(catalogo, cod_cvm=COD_CVM_PETROBRAS)
    hoje = date.today()
    assert all(e.delivered_at <= hoje for e in entradas)


def test_o_host_do_rad_responde():
    """Canario separado: distingue 'a CVM mudou' de 'a CVM esta fora do ar'."""
    resposta = httpx.get(
        "https://www.rad.cvm.gov.br/ENET/frmConsultaExternaCVM.aspx",
        timeout=60,
        follow_redirects=True,
        headers={"user-agent": "personal-pat/0.1 (pesquisa pessoal)"},
    )
    assert resposta.status_code == 200

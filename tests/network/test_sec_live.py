"""O provider da SEC contra a origem real. So roda com `-m network`.

Mesmo par offline/rede da CVM: o offline prova que a URL e montada certo, este
prova que a SEC ainda responde o que respondia - e, principalmente, que os
campos que o sistema vai ler continuam existindo.

Precisa de `PAT_SEC_USER_AGENT`. Sem ele os testes sao PULADOS, e nao falham:
a ausencia de configuracao pessoal nao e regressao do codigo.
"""

from __future__ import annotations

import json
import os

import pytest

from pat.sources.public.sec import ENV_USER_AGENT, SECProvider

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not os.environ.get(ENV_USER_AGENT),
        reason=f"defina {ENV_USER_AGENT} com nome e contato para falar com a SEC",
    ),
]

INTEL_CIK = "50863"


@pytest.fixture(scope="module")
def provider():
    p = SECProvider()
    yield p
    p.close()


def test_a_sec_recusa_user_agent_generico():
    """O motivo de o provider exigir contato. Conferido, nao suposto."""
    import httpx

    resposta = httpx.get(
        f"https://data.sec.gov/submissions/CIK{INTEL_CIK.zfill(10)}.json",
        timeout=30,
        headers={"user-agent": "python-httpx/1.0"},
    )
    assert resposta.status_code == 403, (
        "a SEC passou a aceitar UA generico; a exigencia do provider pode ser "
        "revista, mas continua sendo boa pratica"
    )


def test_submissions_traz_os_campos_que_o_sistema_le(provider):
    (ref,) = provider.resolve("sec.submissions", cik=INTEL_CIK)
    payload = json.loads(provider.fetch(ref).content)

    assert payload["cik"] == "0000050863"
    assert "INTEL" in payload["name"].upper()
    assert "INTC" in payload["tickers"]

    recentes = payload["filings"]["recent"]
    for campo in ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument"):
        assert campo in recentes, f"a SEC deixou de publicar {campo}"

    assert "10-K" in set(recentes["form"]) or "10-Q" in set(recentes["form"])


def test_companyfacts_traz_filed_que_e_o_knowledge_date(provider):
    """`filed` e o eixo de conhecimento do lado americano.

    Sem ele nao ha consulta AS OF possivel na SEC, e o invariante I1 nao
    atravessaria a fronteira. Se a SEC parar de publicar este campo, isto tem
    que quebrar aqui.
    """
    (ref,) = provider.resolve("sec.companyfacts", cik=INTEL_CIK)
    payload = json.loads(provider.fetch(ref).content)

    assert "us-gaap" in payload["facts"]
    lucro_bruto = payload["facts"]["us-gaap"]["GrossProfit"]["units"]["USD"]
    assert lucro_bruto

    exemplo = lucro_bruto[-1]
    for campo in ("start", "end", "val", "form", "filed"):
        assert campo in exemplo, f"fato XBRL sem {campo}"


def test_os_elementos_que_a_intel_usa_continuam_existindo(provider):
    """O analogo de `pat mapping-check` para o regime americano.

    A Intel NAO usa `Revenues` nem `CostOfRevenue` - usa
    `RevenueFromContractWithCustomerExcludingAssessedTax` e
    `CostOfGoodsAndServicesSold`. E exatamente por isso que binding e afirmado
    e nunca deduzido: um sistema que casasse por semelhanca de nome escolheria
    o elemento errado com confianca.
    """
    (ref,) = provider.resolve("sec.companyfacts", cik=INTEL_CIK)
    usgaap = json.loads(provider.fetch(ref).content)["facts"]["us-gaap"]

    for elemento in (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "CostOfGoodsAndServicesSold",
        "GrossProfit",
        "OperatingExpenses",
        "OperatingIncomeLoss",
    ):
        assert elemento in usgaap, f"a Intel deixou de reportar {elemento}"


def test_o_exercicio_da_intel_nao_termina_em_31_de_dezembro(provider):
    """Ano fiscal de 52/53 semanas: o exercicio fecha no ultimo sabado.

    Registrado como teste porque e a suposicao mais facil de fazer sem
    perceber - e o mapeamento e a CLI nao podem assumir 12-31.
    """
    (ref,) = provider.resolve("sec.companyfacts", cik=INTEL_CIK)
    usgaap = json.loads(provider.fetch(ref).content)["facts"]["us-gaap"]
    anuais = [
        f
        for f in usgaap["GrossProfit"]["units"]["USD"]
        if f.get("form") == "10-K" and f.get("fp") == "FY" and f.get("frame")
    ]
    fins = {f["end"] for f in anuais}
    assert fins, "nenhum exercicio anual encontrado"
    assert any(not fim.endswith("-12-31") for fim in fins), (
        "todos os exercicios terminam em 31/12; a suposicao de calendario "
        "poderia passar despercebida"
    )


def test_o_dataset_dera_carrega_a_dimensao_de_segmento(provider):
    """A fonte que destrava o eixo SEGMENT, e a razao da decisao A.

    `companyfacts` nao tem dimensao; o dataset da DERA tem, na coluna
    `segments`. Este teste e o que prova que a promessa da M5.2 se sustenta -
    e ele baixa ~130 MB, entao so roda sob `-m network`.
    """
    import io
    import zipfile

    (ref,) = provider.resolve("sec.financial_statements", quarter="2025q1")
    conteudo = provider.fetch(ref).content
    with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo:
        assert {"sub.txt", "num.txt", "tag.txt"} <= set(arquivo.namelist())
        cabecalho = arquivo.read("num.txt").decode("utf-8", "replace").split("\n", 1)[0]

    colunas = cabecalho.strip().split("\t")
    assert "segments" in colunas, "a DERA deixou de publicar a coluna `segments`"
    assert "adsh" in colunas and "tag" in colunas and "ddate" in colunas

"""Os tres comandos da M5.1, de ponta a ponta, sem rede e sem modelo.

Monta um `PAT_HOME` temporario com um blob de verdade no bronze, extrai,
indexa e consulta. E o mesmo caminho que a empresa real percorre - o que muda
e so a origem dos bytes.
"""

from __future__ import annotations

from datetime import date

import pytest

from pat.cli import main
from pat.corpus.extract import extract
from pat.corpus.index import build_index
from pat.store import corpus as corpus_store
from pat.store.bronze import BronzeStore
from pat.store.db import connect, migrate
from tests.corpus.conftest import make_document, make_pdf

COD_CVM = 9512
ENTIDADE = "br:cnpj:33000167000101"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """PAT_HOME completo: bronze com o blob, warehouse com fato e corpus.

    O fato no gold existe porque `pat docs` e `pat evidence` resolvem a
    empresa por `cod_cvm` atraves do gold - o corpus qualitativo se pendura na
    mesma entidade do quantitativo, de proposito. Sem isso as duas metades do
    workspace poderiam divergir sobre quem e a empresa.
    """
    monkeypatch.setenv("PAT_HOME", str(tmp_path))
    (tmp_path / "bronze").mkdir(parents=True, exist_ok=True)

    bronze = BronzeStore(tmp_path / "bronze")
    pdf = make_pdf(
        [
            [
                "Relatorio de Desempenho 4T23",
                "",
                "A receita liquida caiu por causa da queda do preco do Brent.",
            ]
        ]
    )
    put = bronze.put(pdf, run_id="run-teste", media_type="application/pdf")
    document_id = put.document.content_sha256

    conn = connect(tmp_path / "warehouse.duckdb")
    migrate(conn)
    _semear_gold(conn)

    corpus_store.write_documents(
        conn,
        [make_document(document_id, published_at=date(2024, 3, 7), byte_size=len(pdf))],
    )
    outcome = extract(document_id, pdf)
    assert outcome.failure is None
    corpus_store.write_units(conn, outcome.units)
    build_index(conn)
    conn.close()
    return tmp_path, document_id


def _semear_gold(conn) -> None:
    conn.execute(
        """
        INSERT INTO gold_fact (
            fact_id, entity_id, cod_cvm, denom_cia, statement, consolidated,
            coluna_df, cd_conta, ds_conta, period_type, period_start, period_end,
            knowledge_date, value, unit, currency, ordem_exerc, source_doc_id,
            source_doc_version, silver_id, content_sha256, retrieval_id, locator,
            extractor, extractor_version, extraction_run_id
        ) VALUES (
            'fato-teste', ?, ?, 'PETROLEO BRASILEIRO S.A. PETROBRAS', 'DRE', TRUE,
            '', '3.01', 'Receita', 'year', DATE '2023-01-01', DATE '2023-12-31',
            DATE '2024-03-07', 1, 'BRL', 'BRL', 'ULTIMO', 'doc', 1,
            'silver', 'sha', 'ret', 'loc', 'ext', '1', 'run'
        )
        """,
        [ENTIDADE, COD_CVM],
    )


def test_docs_lista_o_acervo(home, capsys):
    assert main(["docs", "--cod-cvm", str(COD_CVM)]) == 0
    saida = capsys.readouterr().out
    assert "PETROBRAS" in saida
    assert "1 documento(s)" in saida
    assert "performance_report" in saida


def test_docs_com_as_of_esconde_o_que_ainda_nao_existia(home, capsys):
    assert main(["docs", "--cod-cvm", str(COD_CVM), "--as-of", "2024-01-01"]) == 0
    saida = capsys.readouterr().out
    assert "nenhum documento" in saida


def test_evidence_devolve_trecho_verbatim_com_procedencia(home, capsys):
    codigo = main(
        [
            "evidence",
            "--cod-cvm",
            str(COD_CVM),
            "--query",
            "brent receita",
            "--as-of",
            "2024-12-31",
        ]
    )
    assert codigo == 0
    saida = capsys.readouterr().out
    assert "queda do preco do Brent" in saida
    assert "unit " in saida and "doc " in saida
    assert "tier=primary_official" in saida
    assert "verbatim" in saida


def test_evidence_recusa_com_motivo_quando_o_as_of_e_anterior(home, capsys):
    codigo = main(
        [
            "evidence",
            "--cod-cvm",
            str(COD_CVM),
            "--query",
            "brent",
            "--as-of",
            "2024-01-01",
        ]
    )
    assert codigo == 1
    saida = capsys.readouterr().out
    assert "SEM EVIDENCIA" in saida
    assert "no_documents_as_of" in saida
    assert "posteriores" in saida


def test_evidence_nao_exige_credencial_de_modelo(home, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert (
        main(
            [
                "evidence",
                "--cod-cvm",
                str(COD_CVM),
                "--query",
                "brent",
                "--as-of",
                "2024-12-31",
            ]
        )
        == 0
    )


def test_provenance_unit_reconfere_contra_os_bytes(home, capsys):
    tmp_path, document_id = home
    conn = connect(tmp_path / "warehouse.duckdb", read_only=True)
    unidades = corpus_store.units_for_document(conn, document_id)
    conn.close()

    alvo = next(u for u in unidades if "Brent" in u.text)
    assert main(["provenance-unit", alvo.unit_id]) == 0

    saida = capsys.readouterr().out
    assert "hash confere        sim" in saida
    assert "texto reextraido    IDENTICO" in saida
    assert "verbatim" in saida
    # A cadeia inteira aparece: unidade -> documento -> blob.
    assert alvo.unit_id in saida
    assert document_id in saida


def test_provenance_unit_de_id_inexistente_falha_dizendo(home, capsys):
    assert main(["provenance-unit", "f" * 64]) == 1
    assert "desconhecido" in capsys.readouterr().err


def test_docs_de_empresa_sem_fato_instrui_em_vez_de_estourar(home, capsys):
    assert main(["docs", "--cod-cvm", "99999"]) == 1
    assert "pat build" in capsys.readouterr().err

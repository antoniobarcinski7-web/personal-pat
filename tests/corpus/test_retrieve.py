"""Recuperacao: point-in-time obrigatorio, ordem total, recusa nomeada.

O teste que da nome ao arquivo e
`test_a_mesma_consulta_com_as_of_diferente_devolve_conjuntos_diferentes` - o
criterio de conclusao da M5.1, e o analogo textual exato do criterio da Fase 1
(duas consultas `asof` divergindo por causa de uma reapresentacao).
"""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from pat.contracts.corpus import (
    DocumentKind,
    EvidenceQuery,
    EvidenceResult,
    EvidenceUnavailable,
    EvidenceUnavailableReason,
)
from pat.corpus.extract import extract
from pat.corpus.index import INDEX_VERSION, build_index, tokenize
from pat.corpus.retrieve import retrieve
from pat.store import corpus as corpus_store
from pat.store.db import migrate
from tests.corpus.conftest import make_document, make_pdf

ENTIDADE = "br:cnpj:33000167000101"

# Duas "publicacoes": uma em marco, outra em julho. A de julho e a que so
# aparece quando o `as_of` avanca - e ela e o corpo da prova point-in-time.
MARCO = date(2024, 3, 7)
JULHO = date(2024, 7, 29)


def _document_id(seed: str) -> str:
    return (seed * 64)[:64]


@pytest.fixture
def conn():
    connection = duckdb.connect(":memory:")
    migrate(connection)
    yield connection
    connection.close()


def _ingerir(connection, *, seed: str, published_at: date, paginas, **kwargs) -> str:
    document_id = _document_id(seed)
    pdf = make_pdf(paginas)
    corpus_store.write_documents(
        connection,
        [
            make_document(
                document_id,
                published_at=published_at,
                byte_size=len(pdf),
                **kwargs,
            )
        ],
    )
    outcome = extract(document_id, pdf)
    assert outcome.failure is None, outcome.failure
    corpus_store.write_units(connection, outcome.units)
    build_index(connection)
    return document_id


@pytest.fixture
def corpus(conn):
    _ingerir(
        conn,
        seed="a",
        published_at=MARCO,
        paginas=[["A receita caiu por causa da queda do preco do Brent."]],
        title="Desempenho 4T23",
    )
    _ingerir(
        conn,
        seed="b",
        published_at=JULHO,
        paginas=[["Atingimos recorde de producao no pre-sal, com Brent estavel."]],
        title="Producao 2T24",
        kind=DocumentKind.PRODUCTION_REPORT,
    )
    return conn


# -- O criterio de conclusao da M5.1 -----------------------------------------


def test_a_mesma_consulta_com_as_of_diferente_devolve_conjuntos_diferentes(corpus):
    """Existe um documento publicado entre A e B; os conjuntos divergem.

    Sem o segundo eixo de tempo, "o que a administracao disse sobre o Brent"
    responderia com comentario posterior ao ponto de analise - o vazamento
    mais facil de cometer, porque deixa a resposta MELHOR.
    """
    antes = retrieve(
        corpus,
        EvidenceQuery(entity_id=ENTIDADE, terms=("brent",), as_of=date(2024, 6, 30)),
    )
    depois = retrieve(
        corpus,
        EvidenceQuery(entity_id=ENTIDADE, terms=("brent",), as_of=date(2024, 12, 31)),
    )

    assert isinstance(antes, EvidenceResult)
    assert isinstance(depois, EvidenceResult)

    assert antes.documents_in_scope == 1
    assert depois.documents_in_scope == 2

    ids_antes = {h.quote.document_id for h in antes.hits}
    ids_depois = {h.quote.document_id for h in depois.hits}
    assert ids_antes < ids_depois
    assert _document_id("b") not in ids_antes
    assert _document_id("b") in ids_depois


def test_nenhuma_citacao_e_posterior_ao_as_of(corpus):
    for as_of in (date(2024, 6, 30), date(2024, 12, 31)):
        resultado = retrieve(
            corpus, EvidenceQuery(entity_id=ENTIDADE, terms=("brent",), as_of=as_of)
        )
        assert isinstance(resultado, EvidenceResult)
        for hit in resultado.hits:
            assert hit.quote.published_at <= as_of


def test_a_recuperacao_nao_toca_em_nenhum_modelo(corpus, monkeypatch):
    """M5.1 inteira roda sem credencial. E o criterio, nao um detalhe.

    Se a recuperacao nao presta sem modelo, ela nao vai prestar com um - vai
    so ficar dificil de perceber.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resultado = retrieve(
        corpus, EvidenceQuery(entity_id=ENTIDADE, terms=("brent",), as_of=date(2025, 1, 1))
    )
    assert isinstance(resultado, EvidenceResult)
    assert resultado.hits


# -- Verbatim ----------------------------------------------------------------


def test_a_citacao_e_byte_identica_a_unidade(corpus):
    resultado = retrieve(
        corpus, EvidenceQuery(entity_id=ENTIDADE, terms=("brent",), as_of=date(2025, 1, 1))
    )
    assert isinstance(resultado, EvidenceResult)
    for hit in resultado.hits:
        unidade = corpus_store.read_unit(corpus, hit.quote.unit_id)
        assert unidade is not None
        assert hit.quote.text == unidade.text


def test_a_citacao_carrega_a_base_da_data(corpus):
    """`published_at_basis` viaja ate a citacao, e nao para no documento."""
    resultado = retrieve(
        corpus, EvidenceQuery(entity_id=ENTIDADE, terms=("brent",), as_of=date(2025, 1, 1))
    )
    assert isinstance(resultado, EvidenceResult)
    assert all(h.quote.published_at_basis is not None for h in resultado.hits)


# -- Determinismo ------------------------------------------------------------


def test_a_ordem_e_estavel_entre_execucoes(corpus):
    consulta = EvidenceQuery(
        entity_id=ENTIDADE, terms=("brent", "producao"), as_of=date(2025, 1, 1)
    )
    primeira = retrieve(corpus, consulta)
    segunda = retrieve(corpus, consulta)
    assert isinstance(primeira, EvidenceResult)
    assert isinstance(segunda, EvidenceResult)
    assert [h.quote.unit_id for h in primeira.hits] == [
        h.quote.unit_id for h in segunda.hits
    ]
    assert [h.relevance for h in primeira.hits] == [h.relevance for h in segunda.hits]


def test_o_resultado_declara_a_versao_do_indice(corpus):
    resultado = retrieve(
        corpus, EvidenceQuery(entity_id=ENTIDADE, terms=("brent",), as_of=date(2025, 1, 1))
    )
    assert isinstance(resultado, EvidenceResult)
    assert resultado.index_version == INDEX_VERSION
    assert resultado.extraction_versions


# -- Recusas nomeadas --------------------------------------------------------


def test_empresa_sem_documento_recusa_com_motivo(conn):
    resultado = retrieve(
        conn, EvidenceQuery(entity_id="br:cnpj:0", terms=("brent",), as_of=date(2025, 1, 1))
    )
    assert isinstance(resultado, EvidenceUnavailable)
    assert resultado.reason is EvidenceUnavailableReason.NO_DOCUMENTS_FOR_ENTITY
    assert resultado.remedy


def test_todos_os_documentos_posteriores_ao_as_of_recusam_com_contagem(corpus):
    """A recusa DIZ quantos documentos existem mas sao posteriores.

    E a diferenca entre 'a empresa nao falou disso' e 'a empresa falou disso
    depois da data que voce pediu' - que e a diferenca entre nao ter tese e
    estar olhando o passado corretamente.
    """
    resultado = retrieve(
        corpus, EvidenceQuery(entity_id=ENTIDADE, terms=("brent",), as_of=date(2024, 1, 1))
    )
    assert isinstance(resultado, EvidenceUnavailable)
    assert resultado.reason is EvidenceUnavailableReason.NO_DOCUMENTS_AS_OF
    assert resultado.documents_excluded_by_as_of == 2


def test_termo_sem_casamento_recusa_em_vez_de_devolver_vazio(corpus):
    resultado = retrieve(
        corpus,
        EvidenceQuery(
            entity_id=ENTIDADE, terms=("criptomoeda",), as_of=date(2025, 1, 1)
        ),
    )
    assert isinstance(resultado, EvidenceUnavailable)
    assert resultado.reason is EvidenceUnavailableReason.NO_MATCH


def test_consulta_so_de_stopword_recusa_com_motivo_proprio(corpus):
    resultado = retrieve(
        corpus, EvidenceQuery(entity_id=ENTIDADE, terms=("de",), as_of=date(2025, 1, 1))
    )
    assert isinstance(resultado, EvidenceUnavailable)
    assert resultado.reason is EvidenceUnavailableReason.NO_MATCH
    assert "stopword" in resultado.message


def test_filtro_por_tipo_de_documento(corpus):
    resultado = retrieve(
        corpus,
        EvidenceQuery(
            entity_id=ENTIDADE,
            terms=("brent",),
            as_of=date(2025, 1, 1),
            kinds=(DocumentKind.PRODUCTION_REPORT,),
        ),
    )
    assert isinstance(resultado, EvidenceResult)
    assert all(h.quote.document_kind is DocumentKind.PRODUCTION_REPORT for h in resultado.hits)


# -- Tokenizador -------------------------------------------------------------


def test_acento_nao_impede_casamento():
    """Digitar sem acento e o caso normal numa linha de comando."""
    assert tokenize("produção") == tokenize("producao")


def test_a_negacao_sobrevive_ao_tokenizador():
    """"nao recorrente" e justamente o tipo de trecho que importa."""
    assert "nao" in tokenize("item nao recorrente")


def test_numeros_de_periodo_sao_termos_de_busca():
    tokens = tokenize("resultado do 3T24 em 2024")
    assert "3t24" in tokens
    assert "2024" in tokens

"""O3 - a mesa de ferramentas, nas duas jurisdicoes.

Os dois criterios de aceitacao sao `test_pergunta_a_empresa_br` e
`test_pergunta_a_empresa_us`: a MESMA mesa, sem ramo por pais, chega a fontes
diferentes porque a entidade decide o mapeamento, o mapeamento decide a
taxonomia e a taxonomia decide o resolver.

O terceiro criterio - nao existir resolucao entre jurisdicoes - e testado por
construcao, e nao por comportamento: `test_nenhum_metodo_aceita_entity_id`
verifica que a assinatura nao tem onde por outra empresa. Um teste de
comportamento so provaria que o caminho que eu lembrei de testar esta fechado.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.semantics import (
    MetricResult,
    MetricUnavailable,
    ReportingScope,
    UnavailableReason,
)
from pat.opportunity.company import profile_for
from pat.opportunity.tools import AccountLine, PatTools
from tests.opportunity.warehouses import (
    INTEL_ENTITY,
    INTEL_FY2023,
    INTEL_FY2024,
)
from tests.semantics import golden_gpa as gpa

AS_OF = date(2026, 6, 30)
GPA_FY2023 = date(2023, 12, 31)
GPA_FY2024 = date(2024, 12, 31)
MM = Decimal(1_000_000)


@pytest.fixture
def br_tools(br_warehouse):
    return PatTools(
        br_warehouse, company=profile_for(br_warehouse, gpa.ENTITY_ID), as_of=AS_OF
    )


@pytest.fixture
def us_tools(us_warehouse):
    return PatTools(
        us_warehouse, company=profile_for(us_warehouse, INTEL_ENTITY), as_of=AS_OF
    )


# -- criterios de aceitacao -------------------------------------------------


def test_pergunta_a_empresa_br(br_tools):
    assert br_tools.context.jurisdiction == "BR"
    assert br_tools.periods() == (GPA_FY2023, GPA_FY2024)

    resultado = br_tools.metric("ebitda@v1", period_end=GPA_FY2024)
    assert isinstance(resultado, MetricResult)
    assert resultado.currency == "BRL"
    assert resultado.entity_id == gpa.ENTITY_ID
    assert resultado.as_of == AS_OF
    assert resultado.scope is ReportingScope.CONSOLIDATED
    # A procedencia chega inteira: sem `inputs` e `fidelity`, a camada de cima
    # citaria um numero sem saber se ele e exato.
    assert resultado.inputs
    assert resultado.fidelity is not None
    assert resultado.mapping_id


def test_pergunta_a_empresa_us(us_tools):
    assert us_tools.context.jurisdiction == "US"
    assert us_tools.periods() == (INTEL_FY2023, INTEL_FY2024)

    resultado = us_tools.metric("ebitda@v1", period_end=INTEL_FY2024)
    assert isinstance(resultado, MetricResult)
    assert resultado.currency == "USD"
    # -11.678 de EBIT + 9.951 de depreciacao + 1.428 de amortizacao, em milhoes.
    assert resultado.value / MM == Decimal("-299")
    assert resultado.entity_id == INTEL_ENTITY


def test_a_mesma_metrica_nas_duas_jurisdicoes(br_tools, us_tools):
    """Nenhum ramo por pais em lugar nenhum do caminho.

    A entidade decide o mapeamento, o mapeamento decide a taxonomia, a
    taxonomia decide o resolver - e nada acima disso sabe qual foi.
    """
    br = br_tools.metric("receita_liquida@v1", period_end=GPA_FY2024)
    us = us_tools.metric("receita_liquida@v1", period_end=INTEL_FY2024)
    assert isinstance(br, MetricResult) and isinstance(us, MetricResult)
    assert br.currency == "BRL" and us.currency == "USD"
    assert br.metric == us.metric == "receita_liquida"


def test_nenhum_metodo_aceita_entity_id():
    """Resolucao entre jurisdicoes nao e proibida por convencao: nao ha onde
    escreve-la.

    Uma checagem `if jurisdiction` espalhada pelos metodos seria removida um
    dia por parecer redundante; uma assinatura que nao tem o parametro nao.
    """
    publicos = [
        (nome, metodo)
        for nome, metodo in inspect.getmembers(PatTools, inspect.isfunction)
        if not nome.startswith("_")
    ]
    assert publicos, "a introspecao nao achou metodo nenhum - o teste ficaria vazio"
    for nome, metodo in publicos:
        parametros = set(inspect.signature(metodo).parameters)
        assert "entity_id" not in parametros, (
            f"PatTools.{nome} aceita `entity_id`. A mesa e presa a uma empresa; "
            "aceitar outra abriria resolucao entre jurisdicoes."
        )
        assert "company" not in parametros or nome == "__init__"


def test_a_fonte_sai_da_jurisdicao_nunca_de_um_default(br_tools, us_tools):
    """Fontes diferentes, derivadas - e nao um literal de um pais so."""
    assert br_tools.context.source != us_tools.context.source
    assert br_tools.context.source and us_tools.context.source


# -- as_of e escopo ---------------------------------------------------------


def test_as_of_e_da_mesa_nao_da_chamada(br_tools):
    """Nenhum metodo de numero aceita `as_of`.

    Um agente que escolhesse `as_of` por pergunta compararia dois retratos do
    mundo dentro da mesma conclusao - e o erro apareceria como uma conclusao
    melhor.
    """
    for nome in ("metric", "series", "concept", "breakdown", "accounts", "evidence"):
        parametros = set(inspect.signature(getattr(PatTools, nome)).parameters)
        assert "as_of" not in parametros, f"PatTools.{nome} aceita as_of"


def test_as_of_anterior_ao_fato_esconde_o_fato(br_warehouse):
    """A mesa respeita o corte bitemporal, e a recusa e nomeada."""
    cedo = PatTools(
        br_warehouse,
        company=profile_for(br_warehouse, gpa.ENTITY_ID),
        as_of=date(2020, 1, 1),
    )
    resultado = cedo.metric("ebitda@v1", period_end=GPA_FY2024)
    assert isinstance(resultado, MetricUnavailable)
    assert resultado.reason is not None
    assert resultado.remedy or resultado.message


def test_as_of_nao_recua(br_tools):
    with pytest.raises(ValueError, match="as_of nao recua"):
        br_tools.advanced_to(date(2024, 1, 1))


def test_as_of_avanca_para_mesa_nova(br_tools):
    depois = br_tools.advanced_to(date(2026, 12, 31))
    assert depois.context.as_of == date(2026, 12, 31)
    assert br_tools.context.as_of == AS_OF, "a mesa original nao muda"


def test_escopo_diferente_e_mesa_diferente(br_tools):
    individual = br_tools.with_scope(ReportingScope.PARENT_ONLY)
    assert individual.context.scope is ReportingScope.PARENT_ONLY
    assert br_tools.context.scope is ReportingScope.CONSOLIDATED
    assert individual.context.entity_id == br_tools.context.entity_id


# -- series e recusas -------------------------------------------------------


def test_serie_nao_esconde_periodo_indisponivel(br_tools):
    """Uma serie sem buraco visivel faz uma tendencia parecer continua - o
    pior jeito de errar sobre crescimento."""
    serie = br_tools.series(
        "ebitda@v1", period_ends=(GPA_FY2023, date(2019, 12, 31), GPA_FY2024)
    )
    assert len(serie) == 3
    assert isinstance(serie[0], MetricResult)
    assert isinstance(serie[1], MetricUnavailable), "o periodo sem dado aparece"
    assert isinstance(serie[2], MetricResult)


def test_metrica_inexistente_levanta_em_vez_de_recusar(br_tools):
    """Metrica que nao existe NAO e um `MetricUnavailable`.

    A distincao e deliberada e vale a pena registrar: `MetricUnavailable`
    quer dizer "a metrica existe e o insumo faltou", que e um estado do
    MUNDO e o agente pode reportar. Nome que nao esta no registro e um estado
    do CODIGO - o agente inventou uma metrica -, e devolver uma recusa
    nomeada faria as duas coisas se lerem igual no relatorio. O agente evita
    isto consultando `capability()`, que lista o que de fato existe.
    """
    from pat.semantics.registry import RegistryError

    with pytest.raises(RegistryError, match="desconhecida"):
        br_tools.metric("ebitda_ajustado_divulgado@v9", period_end=GPA_FY2024)


def test_insumo_ausente_nunca_vira_zero(us_tools):
    """`MetricUnavailable`, com motivo nomeado - e ler `.value` nele LEVANTA.

    E a garantia que fecha o buraco: um `None` devolvido no lugar do numero
    seria somado como zero tres linhas adiante, e o total sairia menor sem
    nenhum erro no caminho.
    """
    from pat.contracts.semantics import MetricNotAvailableError

    resultado = us_tools.metric("ebitda@v1", period_end=date(2019, 12, 31))
    assert isinstance(resultado, MetricUnavailable)
    assert resultado.reason in set(UnavailableReason)
    with pytest.raises(MetricNotAvailableError):
        _ = resultado.value


# -- capacidade, contas, cobertura ------------------------------------------


def test_capacidade_lista_o_que_existe(br_tools):
    snapshot = br_tools.capability()
    refs = {m.ref for m in snapshot.metrics}
    assert "ebitda@v1" in refs
    # O que o agente NAO pode inventar: um nome plausivel que o motor nao tem.
    assert "ebitda_ajustado_divulgado@v9" not in refs
    assert gpa.ENTITY_ID in {e.entity_id for e in snapshot.entities}
    assert snapshot.concepts and snapshot.decompositions


def test_contas_saem_com_codigo_opaco(br_tools):
    linhas = br_tools.accounts(statement="DRE", period_end=GPA_FY2024)
    assert linhas and all(isinstance(x, AccountLine) for x in linhas)
    primeira = linhas[0]
    assert primeira.account_code and primeira.label
    assert primeira.fact_id
    # O rotulo sai para ser lido por uma pessoa, nunca para ser buscado.
    assert isinstance(primeira.label, str)


def test_cobertura_carrega_o_que_falta(us_tools):
    """Cobertura que so mostra o que existe mente sobre si mesma."""
    cobertura = us_tools.coverage()
    assert cobertura.facts > 0
    assert cobertura.state in ("draft", "ready")
    assert cobertura.documents == 0
    # A pendencia de corpus e nomeada, com remedio - e nao escondida.
    assert cobertura.gaps
    assert all(codigo and remedio for codigo, remedio in cobertura.gaps)


def test_cobertura_do_br_tem_documentos_zerados_mas_fatos(br_tools):
    cobertura = br_tools.coverage()
    assert cobertura.facts > 0
    assert cobertura.period_ends == (GPA_FY2023, GPA_FY2024)
    assert "ebitda@v1" in cobertura.metrics_available


# -- procedencia ------------------------------------------------------------


def test_procedencia_chega_ate_os_bytes(br_tools):
    resultado = br_tools.metric("receita_liquida@v1", period_end=GPA_FY2024)
    assert isinstance(resultado, MetricResult)
    fact_id = next(i.fact_id for i in resultado.inputs if i.fact_id)
    procedencia = br_tools.provenance(fact_id)
    assert procedencia is not None
    assert procedencia.content_sha256
    assert procedencia.url


def test_procedencia_de_fato_inexistente_e_none(br_tools):
    assert br_tools.provenance("f" * 64) is None


# -- corpus ------------------------------------------------------------------


def test_busca_sem_corpus_e_recusa_nomeada_nao_lista_vazia(br_tools):
    """"Nao encontrei nada" e ambiguo entre tres situacoes que pedem acoes
    diferentes."""
    from pat.contracts.corpus import EvidenceUnavailable

    resultado = br_tools.evidence(("margem", "bruta"))
    assert isinstance(resultado, EvidenceUnavailable)
    assert resultado.reason is not None
    assert resultado.remedy

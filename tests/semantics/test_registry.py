"""Registro de metricas: o grafo tem que quebrar no import, nao na consulta."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pat.contracts.semantics import (
    ConsistencyCheck,
    Dimension,
    MetricDefinition,
    MetricKind,
    MetricRef,
    PeriodKind,
)
from pat.semantics import concepts
from pat.semantics.registry import (
    CheckEval,
    Inputs,
    Metric,
    MetricRegistry,
    RegistryError,
    default_registry,
)


def _definition(name: str, *, deps: tuple[MetricRef, ...] = (), **overrides) -> MetricDefinition:
    base = dict(
        name=name,
        version="v1",
        kind=MetricKind.CALCULATED,
        dimension=Dimension.MONEY,
        period_kind=PeriodKind.FLOW,
        requires_concepts=() if deps else (concepts.REVENUE_NET,),
        requires_metrics=deps,
        definition="d",
        rationale="r",
    )
    return MetricDefinition(**(base | overrides))


def _metric(name: str, **kwargs) -> Metric:
    return Metric(definition=_definition(name, **kwargs), compute=lambda inputs: Decimal(0))


def test_dependencia_nao_registrada_quebra_a_validacao():
    """Dependencia e pinada por versao; apontar para versao que nao existe e
    erro de programacao, e tem que aparecer subindo o programa."""
    registry = MetricRegistry()
    registry.register(_metric("a", deps=(MetricRef(name="b", version="v1"),)))

    with pytest.raises(RegistryError, match="b@v1"):
        registry.validate()


def test_ciclo_entre_metricas_e_detectado():
    registry = MetricRegistry()
    registry.register(_metric("a", deps=(MetricRef(name="b", version="v1"),)))
    registry.register(_metric("b", deps=(MetricRef(name="a", version="v1"),)))

    with pytest.raises(RegistryError, match="ciclo"):
        registry.validate()


def test_metrica_registrada_duas_vezes_e_recusada():
    registry = MetricRegistry()
    registry.register(_metric("a"))
    with pytest.raises(RegistryError, match="ja registrada"):
        registry.register(_metric("a"))


def test_conceito_fora_do_catalogo_e_recusado_no_registro():
    registry = MetricRegistry()
    definition = _definition("a", requires_concepts=("nao_existe",))
    with pytest.raises(RegistryError, match="nao_existe"):
        registry.register(Metric(definition=definition, compute=lambda i: Decimal(0)))


def test_check_declarado_sem_funcao_e_recusado():
    """Check que nunca roda e pior que check nenhum: da a impressao de que
    alguem esta conferindo."""
    registry = MetricRegistry()
    definition = _definition(
        "a",
        checks=(ConsistencyCheck(check_id="x", description="d", tolerance_abs=Decimal(0)),),
    )
    with pytest.raises(RegistryError, match="sem funcao"):
        registry.register(Metric(definition=definition, compute=lambda i: Decimal(0)))


def test_funcao_de_check_nao_declarada_e_recusada():
    registry = MetricRegistry()
    with pytest.raises(RegistryError, match="nao declarada"):
        registry.register(
            Metric(
                definition=_definition("a"),
                compute=lambda i: Decimal(0),
                check_fns={"fantasma": lambda i, v, s: CheckEval(passed=True)},
            )
        )


# -- o registro real ---------------------------------------------------------


def test_registro_embutido_e_valido():
    registry = default_registry()
    nomes = {str(m.ref) for m in registry.all()}
    assert nomes == {
        # DRE
        "receita_liquida@v1",
        "ebit@v1",
        "d_and_a@v1",
        "ebitda@v1",
        "margem_ebitda@v1",
        "lucro_liquido@v1",
        # Balanco
        "caixa_e_equivalentes@v1",
        "divida_bruta@v1",
        "divida_bruta_com_arrendamento@v1",
        "divida_liquida@v1",
        # Fluxo de caixa
        "fluxo_de_caixa_operacional@v1",
        "capex@v1",
        "fcf@v1",
        # Patrimonio e retorno ao acionista
        "patrimonio_liquido@v1",
        "dividendos_pagos@v1",
        "recompras@v1",
        "retorno_ao_acionista@v1",
        # Retorno sobre o capital
        "roe@v1",
        "aliquota_efetiva@v1",
        "capital_investido@v1",
        "roic@v1",
    }


def test_toda_metrica_explica_a_definicao_e_a_escolha():
    """`rationale` nao e enfeite: e onde fica registrado por que a definicao e
    esta e nao a alternativa. Sem ele, a proxima pessoa 'corrige' a metrica."""
    for metric in default_registry().all():
        assert len(metric.definition.definition.strip()) > 20
        assert len(metric.definition.rationale.strip()) > 40


def test_ebitda_depende_de_ebit_e_d_and_a_pinados():
    registry = default_registry()
    deps = {str(d) for d in registry.get("ebitda@v1").definition.requires_metrics}
    assert deps == {"ebit@v1", "d_and_a@v1"}


def test_inputs_recusam_conceito_nao_declarado():
    inputs = Inputs(concepts={}, metrics={})
    with pytest.raises(KeyError, match="requires_concepts"):
        inputs.concept(concepts.REVENUE_NET)
    assert inputs.optional(concepts.REVENUE_NET) is None

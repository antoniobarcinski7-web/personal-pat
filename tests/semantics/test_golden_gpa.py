"""Golden tests: numeros reais do GPA, conferidos a mao, offline.

Cada teste responde a uma das seis perguntas que a Fase 2 se propos a
garantir por metrica:

    1. os insumos sao os fatos esperados
    2. o calculo e deterministico
    3. o resultado bate com o valor conferido a mao
    4. a procedencia sobrevive ate o byte de origem
    5. insumo ausente falha explicitamente
    6. a versao da metrica fica registrada
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.semantics import (
    Fidelity,
    MetricKind,
    MetricNotAvailableError,
    MetricUnavailable,
    ReportingScope,
    UnavailableReason,
)
from tests.semantics import golden_gpa as gpa

CONSOLIDADO = ReportingScope.CONSOLIDATED


@pytest.fixture
def engine(make_engine):
    return make_engine(gpa.zips())


def _ok(engine, metric: str, period_end: date, as_of: date):
    result = engine.compute(
        metric, entity_id=gpa.ENTITY_ID, period_end=period_end, scope=CONSOLIDADO, as_of=as_of
    )
    assert not isinstance(result, MetricUnavailable), getattr(result, "message", "")
    return result


# -- 3. o valor bate com o conferido a mao -----------------------------------


@pytest.mark.parametrize(("key", "expected"), sorted(gpa.EXPECTED.items(), key=lambda kv: str(kv[0])))
def test_valor_bate_com_o_conferido_a_mao(engine, key, expected):
    metric, period_end, as_of = key
    result = _ok(engine, metric, period_end, as_of)
    assert result.value == expected
    assert result.currency == "BRL"


@pytest.mark.parametrize(("key", "expected"), sorted(gpa.EXPECTED_MARGEM.items(), key=str))
def test_margem_bate_com_a_conferida_a_mao(engine, key, expected):
    period_end, as_of = key
    result = _ok(engine, "margem_ebitda@v1", period_end, as_of)
    assert result.value.quantize(Decimal("0.000001")) == expected
    # Fracao, nao percentual - e sem moeda, por nao ser grandeza monetaria.
    assert result.value < 1
    assert result.currency is None


# -- 1. os insumos sao os fatos esperados ------------------------------------


def test_receita_usa_exatamente_a_linha_de_receita(engine):
    result = _ok(engine, "receita_liquida@v1", gpa.FY2023, date(2024, 6, 30))

    (ref,) = result.inputs
    assert ref.role == "revenue_net"
    assert ref.address == "cvm.plano_padronizado[cd_conta=3.01,statement=DRE]"
    assert ref.sign_applied == 1
    assert ref.is_metric is False


def test_ebitda_soma_ebit_e_d_and_a_e_nada_mais(engine):
    result = _ok(engine, "ebitda@v1", gpa.FY2023, date(2024, 6, 30))

    assert {ref.role for ref in result.inputs} == {"ebit@v1", "d_and_a@v1"}
    assert all(ref.is_metric for ref in result.inputs)
    assert sum(ref.value for ref in result.inputs) == result.value


def test_d_and_a_do_gpa_vem_do_fluxo_de_caixa_e_nao_da_dva(engine):
    """O override da empresa e o ponto do desenho: a familia so aproxima."""
    result = _ok(engine, "d_and_a@v1", gpa.FY2023, date(2024, 6, 30))

    (ref,) = result.inputs
    assert ref.address == "cvm.plano_padronizado[cd_conta=6.01.01.04,statement=DFC_MI]"
    assert ref.sign_applied == 1
    assert result.fidelity is Fidelity.EXACT
    # A DVA teria dado 1.133 MM, nao 1.136 MM.
    assert result.value == Decimal("1136000000")


def test_check_expoe_a_divergencia_entre_d_and_a_do_dfc_e_da_dva(engine):
    result = _ok(engine, "d_and_a@v1", gpa.FY2023, date(2024, 6, 30))

    (check,) = [c for c in result.checks if c.check_id == "proxima_da_retida"]
    assert check.status == "pass"
    assert check.observed == Decimal("1136000000")
    assert check.expected == Decimal("1133000000")


def test_ebit_fecha_com_a_cascata_da_dre(engine):
    result = _ok(engine, "ebit@v1", gpa.FY2023, date(2024, 6, 30))

    (check,) = result.checks
    assert (check.check_id, check.status) == ("fecha_com_a_dre", "pass")
    assert check.expected == Decimal("677000000")  # 4.817 - 4.140


# -- eixo bitemporal, herdado da Fase 1 --------------------------------------


def test_reapresentacao_muda_a_metrica_conforme_a_data_de_conhecimento(engine):
    """O caso que motiva o sistema inteiro, agora um andar acima.

    A mesma metrica, do mesmo periodo, tem dois valores certos - um por data
    em que era verdade. Uma camada semantica que achatasse isso apagaria a
    Fase 1 inteira.
    """
    antes = _ok(engine, "ebitda@v1", gpa.FY2023, date(2024, 6, 30))
    depois = _ok(engine, "ebitda@v1", gpa.FY2023, date(2025, 6, 30))

    assert antes.value == Decimal("1813000000")
    assert depois.value == Decimal("1796000000")
    assert antes.knowledge_date == gpa.KD_2024
    assert depois.knowledge_date == gpa.KD_2025
    assert depois.value - antes.value == Decimal("-17000000")


def test_antes_da_divulgacao_a_metrica_nao_existe(engine):
    """Nao devolve zero, nao devolve o numero que so existiria depois."""
    result = engine.compute(
        "ebitda@v1",
        entity_id=gpa.ENTITY_ID,
        period_end=gpa.FY2023,
        scope=CONSOLIDADO,
        as_of=date(2024, 1, 1),
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.DEPENDENCY_UNAVAILABLE


@pytest.mark.parametrize("key", sorted(gpa.KNOWLEDGE_DATES, key=str))
def test_knowledge_date_e_o_maximo_dos_insumos(engine, key):
    period_end, as_of = key
    result = _ok(engine, "ebitda@v1", period_end, as_of)
    assert result.knowledge_date == gpa.KNOWLEDGE_DATES[key]
    assert result.knowledge_date <= as_of


# -- 2. determinismo ---------------------------------------------------------


def test_calculo_e_deterministico(engine):
    valores = {
        _ok(engine, "margem_ebitda@v1", gpa.FY2023, date(2024, 6, 30)).value for _ in range(5)
    }
    assert len(valores) == 1


# -- 4. procedencia ate o byte -----------------------------------------------


def test_procedencia_vai_da_metrica_ate_os_bytes(engine):
    """I2, um andar acima: da metrica ao blob, com hash reconferido.

    EBITDA nao cita fato nenhum diretamente - cita `ebit@v1` e `d_and_a@v1`.
    A cadeia so vale se der para descer ate as folhas e, de cada folha, chegar
    a um documento que ainda existe em disco e ainda bate com o hash.
    """
    from pat.query.asof import AsOf

    result = _ok(engine, "ebitda@v1", gpa.FY2023, date(2024, 6, 30))

    folhas = _folhas(engine, result)
    assert len(folhas) == 2, "esperado um fato por metrica folha (EBIT e D&A)"

    asof = AsOf(engine.test_conn)
    for fact_id in folhas:
        prov = asof.provenance(fact_id)
        assert prov is not None, f"fato sem procedencia: {fact_id}"
        assert prov.locator.endswith(("cd_conta=3.05", "cd_conta=6.01.01.04"))
        assert prov.dataset_id == "cvm.dfp"
        # Os bytes ainda estao la e ainda batem com o hash registrado.
        assert asof.verify_provenance(fact_id, engine.test_bronze)


def _folhas(engine, result) -> list[str]:
    """fact_ids na base do grafo, recompondo pelas dependencias."""
    out = [ref.fact_id for ref in result.inputs if ref.fact_id]
    for ref in result.inputs:
        if ref.is_metric:
            child = engine.compute(
                ref.role,
                entity_id=result.entity_id,
                period_end=result.period_end,
                scope=result.scope,
                as_of=result.as_of,
            )
            out.extend(_folhas(engine, child))
    return out


# -- 5. insumo ausente falha explicitamente ----------------------------------


def test_insumo_ausente_nao_vira_zero(make_engine):
    """Sem a linha de D&A, EBITDA fica indisponivel - e diz qual conceito faltou.

    A alternativa silenciosa (tratar ausencia como zero) devolveria um EBITDA
    igual ao EBIT, plausivel e errado.
    """
    zips = gpa.zips()
    sem_dfc = _rebuild_sem_dfc()
    engine = make_engine({2023: sem_dfc, 2024: zips[2024]})

    result = engine.compute(
        "ebitda@v1",
        entity_id=gpa.ENTITY_ID,
        period_end=gpa.FY2023,
        scope=CONSOLIDADO,
        as_of=date(2024, 6, 30),
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.DEPENDENCY_UNAVAILABLE
    assert result.concept_id == "d_and_a_pnl"
    assert "6.01.01.04" in " ".join(result.tried)

    with pytest.raises(MetricNotAvailableError):
        _ = result.value


def _rebuild_sem_dfc() -> bytes:
    from tests.conftest import DocumentSpec, ZipSpec, build_dfp_zip

    return build_dfp_zip(
        ZipSpec(
            year=2023,
            documents=[
                DocumentSpec("2023-12-31", 1, "2024-02-21", "700001", cnpj=gpa.CNPJ,
                             cod_cvm=gpa.COD_CVM, denom=gpa.DENOM)
            ],
            dre_con=gpa._DRE_2023,
            flow_members={("DVA", "con"): gpa._DVA_2023},
        )
    )


def test_escopo_sem_dados_nao_cai_para_o_outro_escopo(engine):
    """O fixture so tem consolidado. Individual precisa faltar, nao herdar."""
    result = engine.compute(
        "receita_liquida@v1",
        entity_id=gpa.ENTITY_ID,
        period_end=gpa.FY2023,
        scope=ReportingScope.PARENT_ONLY,
        as_of=date(2024, 6, 30),
    )
    assert isinstance(result, MetricUnavailable)
    assert result.reason is UnavailableReason.MISSING_FACT_AS_OF


# -- 6. a versao e o regime ficam registrados --------------------------------


def test_resultado_registra_versao_mapeamento_e_regime(engine):
    result = _ok(engine, "ebitda@v1", gpa.FY2023, date(2024, 6, 30))

    assert (result.metric, result.metric_version) == ("ebitda", "v1")
    assert result.kind is MetricKind.CALCULATED
    assert result.mapping_id == "br:cnpj:47508411000156"
    assert result.mapping_confirmed is True
    assert len(result.mapping_sha256) == 64
    assert result.framework == "ifrs_cpc_br"
    assert result.jurisdiction == "BR"
    assert result.scope is CONSOLIDADO
    assert result.local_ids == (("cod_cvm", "14826"),)


def test_hash_do_mapeamento_cobre_a_cadeia_herdada(engine, tmp_path, make_engine):
    """Editar a familia muda o hash mesmo sem tocar no arquivo da empresa.

    Sem isso, dois numeros diferentes ficariam sem explicacao: mesma metrica,
    mesma data, mesmo mapeamento aparente.
    """
    import shutil

    from tests.semantics.conftest import MAPPINGS_DIR

    destino = tmp_path / "mappings"
    shutil.copytree(MAPPINGS_DIR, destino)
    familia = destino / "br" / "cvm_padronizado_nao_financeiro.toml"
    familia.write_bytes(familia.read_bytes() + b"\n# comentario novo\n")

    original = _ok(engine, "ebitda@v1", gpa.FY2023, date(2024, 6, 30))
    editado = make_engine(gpa.zips(), mappings_dir=destino).compute(
        "ebitda@v1",
        entity_id=gpa.ENTITY_ID,
        period_end=gpa.FY2023,
        scope=CONSOLIDADO,
        as_of=date(2024, 6, 30),
    )

    assert editado.value == original.value
    assert editado.mapping_sha256 != original.mapping_sha256

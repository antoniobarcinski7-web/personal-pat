"""Golden tests da Petrobras: a segunda empresa com mapeamento proprio.

O golden do GPA prova que o motor calcula certo. Este prova duas coisas que
so aparecem quando existe mais de uma empresa mapeada:

    1. o mapeamento por empresa generaliza - a mesma familia, um override
       diferente, e o codigo de conta do DFC e outro (6.01.01.04 aqui e no
       GPA por coincidencia, mas 6.01.01.06 na Vale e 6.01.01.02 na WEG);
    2. o override pode estar certo *antes* da fonte se corrigir. Ver o
       cabecalho de `golden_petrobras.py`: a DVA da DFP 2023 discordava do DFC
       em 12,9%, e foi a DVA que a companhia reapresentou.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.semantics import Fidelity, MetricUnavailable, ReportingScope
from tests.semantics import golden_petrobras as pb

CONSOLIDADO = ReportingScope.CONSOLIDATED


@pytest.fixture
def engine(make_engine):
    return make_engine(pb.zips())


def _ok(engine, metric: str, period_end: date, as_of: date):
    result = engine.compute(
        metric, entity_id=pb.ENTITY_ID, period_end=period_end, scope=CONSOLIDADO, as_of=as_of
    )
    assert not isinstance(result, MetricUnavailable), getattr(result, "message", "")
    return result


# -- o valor bate com o conferido a mao --------------------------------------


@pytest.mark.parametrize(("key", "expected"), sorted(pb.EXPECTED.items(), key=lambda kv: str(kv[0])))
def test_valor_bate_com_o_conferido_a_mao(engine, key, expected):
    metric, period_end, as_of = key
    result = _ok(engine, metric, period_end, as_of)
    assert result.value == expected
    assert result.currency == "BRL"


@pytest.mark.parametrize(("key", "expected"), sorted(pb.EXPECTED_MARGEM.items(), key=str))
def test_margem_bate_com_a_conferida_a_mao(engine, key, expected):
    period_end, as_of = key
    result = _ok(engine, "margem_ebitda@v1", period_end, as_of)
    assert result.value.quantize(Decimal("0.000001")) == expected
    assert result.currency is None


@pytest.mark.parametrize(("key", "expected"), sorted(pb.KNOWLEDGE_DATES.items(), key=str))
def test_knowledge_date_e_o_maximo_dos_insumos(engine, key, expected):
    period_end, as_of = key
    assert _ok(engine, "ebitda@v1", period_end, as_of).knowledge_date == expected


# -- o mapeamento proprio esta em uso ----------------------------------------


def test_d_and_a_vem_do_fluxo_de_caixa_e_nao_da_dva(engine):
    """A prova de que o override da Petrobras esta em uso, e nao a familia."""
    result = _ok(engine, "d_and_a@v1", pb.FY2023, date(2024, 6, 30))

    (ref,) = result.inputs
    assert ref.address == f"cvm.plano_padronizado[cd_conta={pb.DFC_DA},statement=DFC_MI]"
    assert ref.sign_applied == 1
    assert result.fidelity is Fidelity.EXACT
    assert result.mapping_confirmed is True
    # A DVA teria dado 76.020 MM naquela data, nao 66.204 MM.
    assert result.value == Decimal("66204000") * pb.MIL


def test_fidelidade_exata_sobe_ate_a_margem(engine):
    """Com d_and_a exata, nada na cadeia degrada a fidelidade - o inverso do
    que acontece com uma empresa que caiu na familia default."""
    for metric in ("d_and_a@v1", "ebitda@v1", "margem_ebitda@v1"):
        result = _ok(engine, metric, pb.FY2024, date(2025, 6, 30))
        assert result.fidelity is Fidelity.EXACT, metric
        assert result.mapping_confirmed is True, metric


def test_cadeia_de_mapeamento_e_propria_e_nao_a_do_gpa(engine):
    """Empresas diferentes tem hashes de cadeia diferentes, mesmo herdando a
    mesma familia. Sem isso, dois resultados de empresas distintas seriam
    indistinguiveis na procedencia."""
    from tests.semantics import golden_gpa as gpa

    result = _ok(engine, "ebitda@v1", pb.FY2024, date(2025, 6, 30))
    assert result.mapping_id == "br:cnpj:33000167000101"
    assert result.mapping_id != gpa.ENTITY_ID
    assert len(result.mapping_sha256) == 64


# -- o caso que motiva o override -------------------------------------------


def test_check_expoe_a_divergencia_entre_dfc_e_dva_e_depois_a_reconciliacao(engine):
    """O check nao esconde o desacordo, e tambem nao o transforma em recusa.

    Em 2024-06-30 as duas demonstracoes da propria companhia discordavam em
    12,9%: o numero sai, marcado. Depois da reapresentacao, o check volta a
    passar sozinho - sem que nada no mapeamento tenha sido tocado.
    """
    antes = _ok(engine, "d_and_a@v1", pb.FY2023, date(2024, 6, 30))
    (check_antes,) = [c for c in antes.checks if c.check_id == "proxima_da_retida"]
    assert check_antes.status == "fail"
    assert check_antes.observed == Decimal("66204000") * pb.MIL
    assert check_antes.expected == pb.DVA_RETIDA[(pb.FY2023, date(2024, 6, 30))]

    depois = _ok(engine, "d_and_a@v1", pb.FY2023, date(2025, 6, 30))
    (check_depois,) = [c for c in depois.checks if c.check_id == "proxima_da_retida"]
    assert check_depois.status == "pass"
    assert check_depois.observed == check_depois.expected


def test_reapresentacao_da_dva_nao_move_a_metrica_da_empresa_mapeada(engine):
    """O outro lado do teste do GPA.

    La, a reapresentacao muda o numero e a camada semantica tem que refletir
    isso. Aqui, o que foi reapresentado foi justamente a linha que o
    mapeamento proprio NAO usa - entao o EBITDA tem que ficar parado, com a
    data de conhecimento andando. Um sistema que puxasse o valor de qualquer
    linha 'parecida' se mexeria aqui, e estaria errado.
    """
    antes = _ok(engine, "ebitda@v1", pb.FY2023, date(2024, 6, 30))
    depois = _ok(engine, "ebitda@v1", pb.FY2023, date(2025, 6, 30))

    assert antes.value == depois.value == Decimal("255546000") * pb.MIL
    assert antes.knowledge_date == pb.KD_2024
    assert depois.knowledge_date == pb.KD_2025


def test_familia_default_daria_outro_numero_na_mesma_data(engine, make_engine, tmp_path):
    """Quanto custaria nao ter escrito o mapeamento: 9,8 bilhoes em FY2023.

    Monta o mesmo warehouse com um diretorio de mapeamentos que so tem a
    familia, e compara. E a medida do valor do arquivo da empresa - e a razao
    de `mapping_confirmed` bloquear publicacao por default.
    """
    import shutil

    from tests.semantics.conftest import MAPPINGS_DIR

    so_familia = tmp_path / "so_familia"
    so_familia.mkdir()
    (so_familia / "br").mkdir()
    shutil.copy(
        MAPPINGS_DIR / "br" / "cvm_padronizado_nao_financeiro.toml",
        so_familia / "br" / "cvm_padronizado_nao_financeiro.toml",
    )

    sem_mapa = make_engine(pb.zips(), mappings_dir=so_familia)
    pela_familia = sem_mapa.compute(
        "ebitda@v1", entity_id=pb.ENTITY_ID, period_end=pb.FY2023,
        scope=CONSOLIDADO, as_of=date(2024, 6, 30),
    )
    pelo_mapa = _ok(engine, "ebitda@v1", pb.FY2023, date(2024, 6, 30))

    assert pela_familia.value == Decimal("265362000") * pb.MIL  # 189.342 + 76.020
    assert pelo_mapa.value == Decimal("255546000") * pb.MIL     # 189.342 + 66.204
    assert pela_familia.value - pelo_mapa.value == Decimal("9816000") * pb.MIL

    assert pela_familia.fidelity is Fidelity.APPROXIMATE
    assert pela_familia.mapping_confirmed is False
    assert pelo_mapa.fidelity is Fidelity.EXACT
    assert pelo_mapa.mapping_confirmed is True


# -- eixo temporal -----------------------------------------------------------


def test_antes_da_divulgacao_a_metrica_nao_existe(engine):
    """Nao devolve zero, nao devolve o numero que so existiria depois."""
    result = engine.compute(
        "ebitda@v1", entity_id=pb.ENTITY_ID, period_end=pb.FY2024,
        scope=CONSOLIDADO, as_of=date(2025, 1, 15),
    )
    assert isinstance(result, MetricUnavailable)


def test_calculo_e_deterministico(engine):
    a = _ok(engine, "ebitda@v1", pb.FY2024, date(2025, 6, 30))
    b = _ok(engine, "ebitda@v1", pb.FY2024, date(2025, 6, 30))
    assert a.value == b.value
    assert a.mapping_sha256 == b.mapping_sha256
    assert a.knowledge_date == b.knowledge_date

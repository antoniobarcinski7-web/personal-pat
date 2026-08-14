"""Catalogo de conceitos, adapter da CVM e carregamento de mapeamentos."""

from __future__ import annotations

import pytest

from pat.contracts.semantics import Fidelity, StatementKind, TaxonomyId
from pat.semantics import concepts
from pat.semantics.frameworks import AdapterError
from pat.semantics.frameworks.cvm_dfp.adapter import CvmPlanoPadronizadoAdapter, parts_of
from pat.semantics.loader import MappingError, MappingSet, load_dir, parse_mapping
from tests.semantics.conftest import MAPPINGS_DIR

ADAPTER = CvmPlanoPadronizadoAdapter()


# -- catalogo de conceitos ---------------------------------------------------


def test_catalogo_nao_menciona_plano_de_contas():
    """O teste que mantem o catalogo universal.

    Se um dia alguem escrever 'conta 3.01' na definicao de um conceito, a
    separacao conceito/endereco morreu e ninguem percebeu.
    """
    proibidos = ("3.01", "cd_conta", "CVM", "us-gaap", "DRE", "XBRL")
    for concept in concepts.CATALOG.values():
        texto = " ".join(
            (concept.definition, concept.label_en, concept.sign_convention, *concept.boundary_notes)
        )
        for termo in proibidos:
            assert termo not in texto, f"{concept.concept_id} cita {termo!r}"


def test_todo_conceito_declara_convencao_de_sinal():
    for concept in concepts.CATALOG.values():
        assert concept.sign_convention.strip()


def test_d_and_a_do_resultado_e_da_retencao_sao_conceitos_distintos():
    """Nao sao sinonimos, e o catalogo precisa dizer isso explicitamente."""
    pnl = concepts.get(concepts.D_AND_A_PNL)
    retida = concepts.get(concepts.D_AND_A_RETAINED)

    assert pnl.concept_id != retida.concept_id
    assert pnl.definition != retida.definition
    assert any("d_and_a_retained" in nota for nota in pnl.boundary_notes)


def test_conceito_desconhecido_levanta_em_vez_de_devolver_nada():
    with pytest.raises(concepts.UnknownConceptError, match="revenue_gross"):
        concepts.get("revenue_gross")


# -- adapter da CVM ----------------------------------------------------------


def test_endereco_da_cvm_e_normalizado_e_ordenado():
    addr = ADAPTER.parse_address({"statement": "dre", "cd_conta": "3.01"})

    assert addr.taxonomy is TaxonomyId.CVM_PLANO_PADRONIZADO
    assert addr.statement_kind is StatementKind.INCOME
    assert addr.address == (("cd_conta", "3.01"), ("statement", "DRE"))
    assert parts_of(addr) == ("DRE", "3.01", "")


def test_dva_e_reconhecida_como_demonstracao_do_valor_adicionado():
    addr = ADAPTER.parse_address({"statement": "DVA", "cd_conta": "7.04.01"})
    assert addr.statement_kind is StatementKind.VALUE_ADDED


def test_campo_desconhecido_no_endereco_falha_no_load():
    """Erro de digitacao tem que aparecer aqui, e nao tres camadas adiante
    como conceito nao resolvido - que parece problema de dado."""
    with pytest.raises(AdapterError, match="desconhecidos"):
        ADAPTER.parse_address({"statement": "DRE", "cd_contas": "3.01"})


def test_conta_mal_formada_falha_no_load():
    with pytest.raises(AdapterError, match="cd_conta invalido"):
        ADAPTER.parse_address({"statement": "DRE", "cd_conta": "3,01"})


def test_demonstracao_desconhecida_falha_no_load():
    with pytest.raises(AdapterError, match="desconhecida"):
        ADAPTER.parse_address({"statement": "DFP", "cd_conta": "3.01"})


def test_coluna_df_fora_da_dmpl_e_recusada():
    with pytest.raises(AdapterError, match="coluna_df"):
        ADAPTER.parse_address({"statement": "DRE", "cd_conta": "3.01", "coluna_df": "Capital"})


# -- carregamento de mapeamentos ---------------------------------------------

_MINIMO = """
mapping_id = "t/base"
mapping_version = "v1"
framework = "ifrs_cpc_br"
taxonomy = "cvm.plano_padronizado"
jurisdiction = "BR"
source = "cvm.dfp"

[[binding]]
concept_id = "revenue_net"
fidelity = "exact"
equivalence_basis = "conferido"
[[binding.line]]
statement = "DRE"
cd_conta = "3.01"
sign = 1
"""


def test_mapeamento_carrega_com_hash_dos_proprios_bytes():
    mapping = parse_mapping(_MINIMO.encode(), where="t")
    assert len(mapping.source_sha256) == 64
    assert mapping.binding_for("revenue_net").fidelity is Fidelity.EXACT


def test_hash_muda_quando_o_arquivo_muda():
    a = parse_mapping(_MINIMO.encode(), where="t")
    b = parse_mapping((_MINIMO + "\n# nota\n").encode(), where="t")
    assert a.source_sha256 != b.source_sha256


def test_binding_para_conceito_fora_do_catalogo_e_recusado():
    """O catalogo e a autoridade. Mapeamento nao inventa conceito."""
    ruim = _MINIMO.replace('concept_id = "revenue_net"', 'concept_id = "receita_bruta"')
    with pytest.raises(MappingError, match="inexistente"):
        parse_mapping(ruim.encode(), where="t")


def test_campo_obrigatorio_ausente_e_recusado():
    ruim = _MINIMO.replace('jurisdiction = "BR"\n', "")
    with pytest.raises(MappingError, match="jurisdiction"):
        parse_mapping(ruim.encode(), where="t")


def test_parent_inexistente_e_recusado():
    filho = _MINIMO.replace('mapping_id = "t/base"', 'mapping_id = "t/filho"\nparent = "t/nao-existe"')
    with pytest.raises(MappingError, match="parent inexistente"):
        MappingSet((parse_mapping(filho.encode(), where="t"),))


def test_ciclo_de_heranca_e_detectado_no_load():
    a = _MINIMO.replace('mapping_id = "t/base"', 'mapping_id = "t/a"\nparent = "t/b"')
    b = _MINIMO.replace('mapping_id = "t/base"', 'mapping_id = "t/b"\nparent = "t/a"')
    with pytest.raises(MappingError, match="ciclo"):
        MappingSet((parse_mapping(a.encode(), where="a"), parse_mapping(b.encode(), where="b")))


def test_duas_empresas_com_o_mesmo_entity_id_e_recusado():
    a = _MINIMO.replace('mapping_id = "t/base"', 'mapping_id = "t/a"\nentity_id = "br:cnpj:1"')
    b = _MINIMO.replace('mapping_id = "t/base"', 'mapping_id = "t/b"\nentity_id = "br:cnpj:1"')
    with pytest.raises(MappingError, match="duas empresas"):
        MappingSet((parse_mapping(a.encode(), where="a"), parse_mapping(b.encode(), where="b")))


# -- os mapeamentos versionados no repositorio -------------------------------


def test_mapeamentos_do_repositorio_carregam():
    mappings = load_dir(MAPPINGS_DIR)
    assert len(mappings) >= 2

    familia = mappings.by_id("cvm.plano_padronizado/nao_financeiro")
    assert familia.is_default_for_source
    assert familia.entity_id is None

    gpa = mappings.by_id("br:cnpj:47508411000156")
    assert gpa.parent == familia.mapping_id


def test_empresa_mapeada_sobrescreve_a_familia_e_a_cadeia_e_confirmada():
    chain = load_dir(MAPPINGS_DIR).resolve("br:cnpj:47508411000156", source="cvm.dfp")

    assert chain.confirmed is True
    binding, owner = chain.binding_for(concepts.D_AND_A_PNL)
    assert owner.mapping_id == "br:cnpj:47508411000156"
    assert binding.fidelity is Fidelity.EXACT

    # O que a empresa nao sobrescreve continua vindo da familia.
    _, dono_receita = chain.binding_for(concepts.REVENUE_NET)
    assert dono_receita.mapping_id == "cvm.plano_padronizado/nao_financeiro"


def test_empresa_sem_mapeamento_cai_na_familia_e_a_cadeia_nao_e_confirmada():
    chain = load_dir(MAPPINGS_DIR).resolve("br:cnpj:99999999999999", source="cvm.dfp")

    assert chain.confirmed is False
    binding, _ = chain.binding_for(concepts.D_AND_A_PNL)
    assert binding.fidelity is Fidelity.APPROXIMATE
    assert binding.divergence_note


def test_fonte_sem_familia_default_nao_resolve():
    """Sem default nem mapeamento proprio, a resposta e 'nao sei' - nao um chute."""
    assert load_dir(MAPPINGS_DIR).resolve("br:cnpj:1", source="sec.edgar") is None


def test_toda_aproximacao_do_repositorio_explica_como_diverge():
    for mapping in load_dir(MAPPINGS_DIR).all():
        for binding in mapping.bindings:
            if binding.fidelity is not Fidelity.EXACT:
                assert binding.divergence_note.strip(), (
                    f"{mapping.mapping_id}/{binding.concept_id} aproxima sem dizer como"
                )

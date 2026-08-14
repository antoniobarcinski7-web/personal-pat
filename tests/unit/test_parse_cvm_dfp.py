"""Parser de DFP: bytes do bronze -> linhas silver.

O que estes testes protegem, em ordem de importancia:

1. `DT_RECEB` vem do arquivo indice e vira a data de conhecimento. Linha sem
   indice correspondente nao pode virar fato.
2. Descarte nunca e silencioso: todo motivo tem contador.
3. Os ids sao deterministicos e derivados do conteudo, nao do relogio.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from tests.conftest import CNPJ_DIGITS, COD_CVM, DocumentSpec, LineSpec, ZipSpec, build_dfp_zip

from pat.parse import cvm_dfp
from pat.parse.cvm_dfp import EXTRACTOR, EXTRACTOR_VERSION, ParseError, parse_dfp_zip

SHA_A = "a" * 64
SHA_B = "b" * 64


def _parse(content: bytes, year: int, *, sha: str = SHA_A, **kwargs):
    return parse_dfp_zip(
        content,
        year=year,
        content_sha256=sha,
        retrieval_id="ret-1",
        extraction_run_id="run-1",
        **kwargs,
    )


def _simple_zip(**overrides) -> bytes:
    spec = ZipSpec(
        year=2023,
        documents=[DocumentSpec("2023-12-31", 1, "2024-02-20", "111111")],
        dre_con=[LineSpec("3.01", "1000.0000000000", "2023-12-31", "2023-01-01")],
    )
    for key, value in overrides.items():
        setattr(spec, key, value)
    return build_dfp_zip(spec)


# -- caminho feliz ---------------------------------------------------------


def test_linha_de_dre_carrega_o_dt_receb_do_indice():
    """A data de conhecimento so existe no indice; a linha de detalhe nao a tem."""
    lines, stats = _parse(_simple_zip(), 2023)

    (line,) = lines
    assert line.cd_conta == "3.01"
    assert line.vl_conta == Decimal("1000.0000000000")
    assert line.dt_receb == date(2024, 2, 20)  # veio do indice
    assert line.doc_id == "111111"
    assert line.link_doc == "http://rad.cvm.gov.br/doc/111111"
    assert line.dt_ini_exerc == date(2023, 1, 1)
    assert line.dt_fim_exerc == date(2023, 12, 31)
    assert line.statement == "DRE"
    assert line.consolidated is True
    assert line.cnpj == CNPJ_DIGITS
    assert line.cod_cvm == int(COD_CVM)
    assert stats.lines_read == 1
    assert stats.lines_emitted == 1
    assert stats.skipped_total == 0


def test_escala_e_moeda_ficam_cruas_no_silver():
    """Aplicar a escala e interpretacao, e interpretacao pertence ao gold.
    Enquanto o valor permanece cru, a linha silver continua conferivel
    contra o CSV original."""
    (line,), _ = _parse(_simple_zip(), 2023)

    assert line.escala_moeda == "MIL"
    assert line.moeda == "REAL"
    assert line.vl_conta == Decimal("1000.0000000000")  # nao multiplicado


def test_acento_de_ordem_exerc_e_removido():
    """ULTIMO/PENULTIMO e chave logica: com acento ela fica refem do encoding."""
    zip_bytes = _simple_zip(
        dre_con=[
            LineSpec("3.01", "1000.00", "2023-12-31", "2023-01-01", "ÚLTIMO"),
            LineSpec("3.01", "800.00", "2022-12-31", "2022-01-01", "PENÚLTIMO"),
        ]
    )
    lines, _ = _parse(zip_bytes, 2023)

    assert sorted(line.ordem_exerc for line in lines) == ["PENULTIMO", "ULTIMO"]


def test_conta_patrimonial_nao_tem_data_inicial():
    """BPA/BPP sao saldo pontual: a ausencia da coluna e sinal, nao erro."""
    zip_bytes = build_dfp_zip(
        ZipSpec(
            year=2023,
            documents=[DocumentSpec("2023-12-31", 1, "2024-02-20", "111111")],
            bpa_con=[LineSpec("1", "5000.00", "2023-12-31", ds_conta="Ativo Total")],
        )
    )
    lines, stats = _parse(zip_bytes, 2023)

    (line,) = lines
    assert line.statement == "BPA"
    assert line.dt_ini_exerc is None
    assert line.dt_fim_exerc == date(2023, 12, 31)
    assert stats.skipped_total == 0


def test_dmpl_carrega_coluna_df():
    """Sem COLUNA_DF, componentes distintos do PL colapsam na mesma chave
    logica e viram reapresentacoes falsas. Foi a razao do extractor 1.1.0."""
    zip_bytes = build_dfp_zip(
        ZipSpec(
            year=2023,
            documents=[DocumentSpec("2023-12-31", 1, "2024-02-20", "111111")],
            dmpl_con=[
                LineSpec("5.01", "100.00", "2023-12-31", "2023-01-01", coluna_df="Capital Social"),
                LineSpec("5.01", "200.00", "2023-12-31", "2023-01-01", coluna_df="Reservas de Lucro"),
            ],
        )
    )
    lines, _ = _parse(zip_bytes, 2023)

    assert {line.coluna_df for line in lines} == {"Capital Social", "Reservas de Lucro"}
    # A coluna entra no locator: duas linhas do mesmo cd_conta continuam
    # enderecaveis de forma distinta.
    assert all(":coluna=" in line.locator for line in lines)


def test_locator_endereca_a_linha_dentro_do_documento():
    (line,), _ = _parse(_simple_zip(), 2023)

    assert line.source_member == "dfp_cia_aberta_DRE_con_2023.csv"
    assert line.source_line_no == 2  # linha 1 e o cabecalho
    assert line.locator == "dfp_cia_aberta_DRE_con_2023.csv#L2:cd_conta=3.01"


# -- descartes contados ----------------------------------------------------


def test_linha_sem_indice_nao_vira_fato():
    """Sem DT_RECEB nao ha knowledge_date, e sem knowledge_date nao ha fato
    bitemporal. Descartar e a unica opcao honesta - mas nunca em silencio."""
    zip_bytes = _simple_zip(
        dre_con=[
            LineSpec("3.01", "1000.00", "2023-12-31", "2023-01-01"),
            # DT_REFER de um documento que nao existe no indice:
            LineSpec("3.01", "999.00", "2022-12-31", "2022-01-01", dt_refer="2022-12-31"),
        ]
    )
    lines, stats = _parse(zip_bytes, 2023)

    assert len(lines) == 1
    assert stats.skipped_no_index == 1
    assert stats.skipped_total == 1


def test_indice_ambiguo_descarta_as_linhas_afetadas():
    """Dois documentos distintos com a mesma chave: a data de conhecimento e
    indeterminavel. Escolher um deles produziria um numero com data errada,
    que e pior do que numero nenhum."""
    zip_bytes = _simple_zip(
        documents=[
            DocumentSpec("2023-12-31", 1, "2024-02-20", "111111"),
            DocumentSpec("2023-12-31", 1, "2024-03-15", "222222"),  # mesma chave
        ]
    )
    lines, stats = _parse(zip_bytes, 2023)

    assert lines == []
    assert stats.ambiguous_keys == 1
    assert stats.skipped_ambiguous_index == 1


def test_versoes_diferentes_do_mesmo_documento_nao_sao_ambiguas():
    """VERSAO faz parte da chave: uma republicacao e um documento proprio."""
    zip_bytes = _simple_zip(
        documents=[
            DocumentSpec("2023-12-31", 1, "2024-02-20", "111111"),
            DocumentSpec("2023-12-31", 2, "2024-05-10", "111112"),
        ],
        dre_con=[
            LineSpec("3.01", "1000.00", "2023-12-31", "2023-01-01", versao=1),
            LineSpec("3.01", "1100.00", "2023-12-31", "2023-01-01", versao=2),
        ],
    )
    lines, stats = _parse(zip_bytes, 2023)

    assert stats.ambiguous_keys == 0
    assert {(line.versao, line.dt_receb) for line in lines} == {
        (1, date(2024, 2, 20)),
        (2, date(2024, 5, 10)),
    }


def test_valor_ilegivel_e_contado_e_descartado():
    zip_bytes = _simple_zip(
        dre_con=[
            LineSpec("3.01", "1000.00", "2023-12-31", "2023-01-01"),
            LineSpec("3.02", "", "2023-12-31", "2023-01-01"),
            LineSpec("3.03", "n/d", "2023-12-31", "2023-01-01"),
        ]
    )
    lines, stats = _parse(zip_bytes, 2023)

    assert [line.cd_conta for line in lines] == ["3.01"]
    assert stats.skipped_bad_value == 2


def test_data_ilegivel_e_identificador_ilegivel_sao_contados_separadamente():
    """Cada motivo de descarte tem contador proprio: um relatorio que chama
    'CD_CVM invalido' de 'data invalida' manda quem investigar olhar a coluna
    errada."""
    zip_bytes = _simple_zip(
        dre_con=[
            LineSpec("3.01", "1000.00", "2023-12-31", "2023-01-01"),
            LineSpec("3.02", "10.00", "0000-00-00", "2023-01-01"),  # data ilegivel
            LineSpec("3.03", "20.00", "2023-12-31", "2023-01-01", cod_cvm="ABC"),
        ]
    )
    lines, stats = _parse(zip_bytes, 2023)

    assert [line.cd_conta for line in lines] == ["3.01"]
    assert stats.skipped_bad_date == 1
    assert stats.skipped_bad_key == 1
    assert stats.skipped_total == 2


def test_zip_sem_arquivo_indice_falha_alto():
    """Sem indice o ZIP inteiro e inutil para fins bitemporais: falhar e
    melhor do que emitir linhas sem data de conhecimento."""
    zip_bytes = build_dfp_zip(ZipSpec(year=2023, documents=[]))

    with pytest.raises(ParseError, match="indice ausente"):
        _parse(zip_bytes, 2024)  # ano errado => membro indice inexistente


# -- selecao pedida pelo usuario nao e perda -------------------------------


def test_filtro_por_empresa_nao_conta_como_perda():
    """Misturar filtro com perda esconderia um problema real de extracao
    atras de um numero enorme e esperado."""
    zip_bytes = _simple_zip(
        dre_con=[
            LineSpec("3.01", "1000.00", "2023-12-31", "2023-01-01"),
            LineSpec("3.01", "7.00", "2023-12-31", "2023-01-01", cod_cvm="088888"),
        ]
    )
    lines, stats = _parse(zip_bytes, 2023, only_cod_cvm=frozenset({int(COD_CVM)}))

    assert len(lines) == 1
    assert stats.skipped_filtered_out == 1
    assert stats.skipped_total == 0  # filtro nao entra na conta de perdas


def test_filtro_por_demonstracao():
    zip_bytes = build_dfp_zip(
        ZipSpec(
            year=2023,
            documents=[DocumentSpec("2023-12-31", 1, "2024-02-20", "111111")],
            dre_con=[LineSpec("3.01", "1000.00", "2023-12-31", "2023-01-01")],
            bpa_con=[LineSpec("1", "5000.00", "2023-12-31")],
        )
    )
    lines, stats = _parse(zip_bytes, 2023, statements=frozenset({"BPA"}))

    assert [line.statement for line in lines] == ["BPA"]
    assert stats.members_parsed == ["dfp_cia_aberta_BPA_con_2023.csv"]


# -- determinismo (I3) ------------------------------------------------------


def test_reparse_do_mesmo_documento_produz_ids_identicos():
    zip_bytes = _simple_zip()
    first, _ = _parse(zip_bytes, 2023)
    second, _ = _parse(zip_bytes, 2023)

    assert [line.silver_id for line in first] == [line.silver_id for line in second]


def test_conteudo_diferente_produz_ids_diferentes():
    """As duas versoes de um ano reapresentado coexistem: se os ids
    colidissem, uma sobrescreveria a outra."""
    zip_bytes = _simple_zip()
    (a,), _ = _parse(zip_bytes, 2023, sha=SHA_A)
    (b,), _ = _parse(zip_bytes, 2023, sha=SHA_B)

    assert a.silver_id != b.silver_id


def test_novo_extrator_produz_ids_novos(monkeypatch):
    """Reprocessar com extrator novo cria fatos novos ao lado dos antigos,
    nunca por cima deles (I3)."""
    zip_bytes = _simple_zip()
    (antes,), _ = _parse(zip_bytes, 2023)

    monkeypatch.setattr(cvm_dfp, "EXTRACTOR_VERSION", "9.9.9")
    (depois,), _ = _parse(zip_bytes, 2023)

    assert antes.silver_id != depois.silver_id
    assert antes.extractor_version == EXTRACTOR_VERSION
    assert depois.extractor_version == "9.9.9"


def test_linhagem_e_gravada_em_toda_linha():
    (line,), _ = _parse(_simple_zip(), 2023)

    assert line.content_sha256 == SHA_A
    assert line.retrieval_id == "ret-1"
    assert line.extraction_run_id == "run-1"
    assert line.extractor == EXTRACTOR

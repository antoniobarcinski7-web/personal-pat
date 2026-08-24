"""A forma dos contratos de corpus, e o que ela torna inexprimivel.

O teste central deste arquivo e `test_uma_citacao_nao_tem_onde_por_um_numero`,
que e o analogo exato de
`test_a_gramatica_de_plano_nao_tem_onde_por_um_numero` da Fase 3, com a mesma
tecnica: leitura por AST do arquivo de contrato, e nao instrospeccao do objeto
em memoria. A diferenca importa - anotacao lida do fonte pega o campo que
alguem acrescentou, mesmo que nenhum teste o exercite.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from pat.contracts.common import SourceTier
from pat.contracts.corpus import (
    DateBasis,
    DocumentKind,
    DocumentUnit,
    EvidenceHit,
    EvidenceQuery,
    EvidenceResult,
    ExtractionFailure,
    ExtractionFailureReason,
    ExtractionOutcome,
    LocatorScheme,
    QuoteClaim,
    UnitLocator,
)

CONTRATO = Path(__file__).resolve().parents[2] / "src" / "pat" / "contracts" / "corpus.py"

SHA_A = "a" * 64
SHA_B = "b" * 64


def _locator(**kwargs) -> UnitLocator:
    base = dict(scheme=LocatorScheme.PDF_PAGE, page=1, block=0, char_start=0, char_end=10)
    base.update(kwargs)
    return UnitLocator(**base)


def _quote(**kwargs) -> QuoteClaim:
    base = dict(
        unit_id=SHA_A,
        document_id=SHA_B,
        text="A receita caiu.",
        document_kind=DocumentKind.PERFORMANCE_REPORT,
        published_at=date(2024, 11, 7),
        published_at_basis=DateBasis.FILING_METADATA,
        source_tier=SourceTier.PRIMARY_OFFICIAL,
        locator=_locator(char_end=15),
        title="Relatorio de Desempenho 3T24",
    )
    base.update(kwargs)
    return QuoteClaim(**base)


# -- A propriedade central ---------------------------------------------------


def _classe(nome: str) -> ast.ClassDef:
    arvore = ast.parse(CONTRATO.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.ClassDef) and no.name == nome:
            return no
    raise AssertionError(f"classe {nome} nao encontrada em {CONTRATO}")


def test_uma_citacao_nao_tem_onde_por_um_numero():
    """`QuoteClaim` nao pode ganhar campo capaz de carregar quantidade.

    E o portao contra a lavagem de numero do emissor: um release diz "receita
    de R$ 511,9 bilhoes", e esse algarismo pode sair no relatorio DENTRO da
    citacao - mas nao pode alimentar conta nenhuma. Sem `Decimal`, sem `value`,
    sem `unit` e sem `currency`, nao ha por onde ele ser lido como grandeza.

    Quem quiser afrouxar isso vai ter que acrescentar um campo num arquivo que
    so tem contrato, e o diff vai aparecer.
    """
    proibidos = {"Decimal", "float", "int"}
    proibidos_por_nome = {"value", "amount", "unit", "currency", "magnitude", "scale"}

    for no in _classe("QuoteClaim").body:
        if not isinstance(no, ast.AnnAssign) or not isinstance(no.target, ast.Name):
            continue
        campo = no.target.id
        anotacao = ast.unparse(no.annotation)
        assert campo not in proibidos_por_nome, (
            f"QuoteClaim.{campo} tem nome de quantidade. Numero de documento e "
            "citacao, nunca insumo."
        )
        for tipo in proibidos:
            assert tipo not in anotacao, (
                f"QuoteClaim.{campo}: {anotacao} carrega {tipo}. Uma citacao com "
                "campo numerico vira insumo de calculo na primeira vez que "
                "alguem tiver pressa."
            )


def test_o_texto_de_uma_citacao_e_o_texto_da_unidade():
    """Verbatim e byte a byte, e o contrato nao normaliza nada no caminho."""
    texto = "  A receita caiu 12%.\n  Segundo a administracao,\n"
    unit = DocumentUnit(
        unit_id=SHA_A,
        document_id=SHA_B,
        ordinal=0,
        locator=_locator(char_start=5, char_end=5 + len(texto)),
        text=texto,
        char_count=len(texto),
        extraction_version="pdf-blocks/v1+pypdf9.9.9",
    )
    quote = _quote(text=unit.text, locator=unit.locator)
    assert quote.text == unit.text
    assert quote.text is not None and quote.text.startswith("  ")


# -- Consistencia interna ----------------------------------------------------


def test_locator_precisa_cobrir_exatamente_o_texto():
    with pytest.raises(ValidationError, match="locator cobre"):
        DocumentUnit(
            unit_id=SHA_A,
            document_id=SHA_B,
            ordinal=0,
            locator=_locator(char_start=0, char_end=5),
            text="dez caracteres",
            char_count=len("dez caracteres"),
            extraction_version="v1",
        )


def test_char_count_precisa_bater_com_o_texto():
    with pytest.raises(ValidationError, match="char_count"):
        DocumentUnit(
            unit_id=SHA_A,
            document_id=SHA_B,
            ordinal=0,
            locator=_locator(char_end=10),
            text="dez caract",
            char_count=99,
            extraction_version="v1",
        )


def test_locator_de_pdf_exige_pagina_e_bloco():
    with pytest.raises(ValidationError, match="exige `page` e `block`"):
        UnitLocator(scheme=LocatorScheme.PDF_PAGE, char_start=0, char_end=5)


def test_intervalo_vazio_nao_e_endereco():
    with pytest.raises(ValidationError, match="intervalo vazio"):
        _locator(char_start=10, char_end=10)


def test_extracao_devolve_unidades_ou_falha_nunca_as_duas():
    falha = ExtractionFailure(
        document_id=SHA_B,
        reason=ExtractionFailureReason.NO_TEXT_LAYER,
        message="sem camada de texto",
        extraction_version="v1",
        failed_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    unidade = DocumentUnit(
        unit_id=SHA_A,
        document_id=SHA_B,
        ordinal=0,
        locator=_locator(char_end=5),
        text="texto",
        char_count=5,
        extraction_version="v1",
    )

    with pytest.raises(ValidationError, match="unidades OU"):
        ExtractionOutcome(
            document_id=SHA_B, extraction_version="v1", units=(unidade,), failure=falha
        )

    with pytest.raises(ValidationError, match="unidades OU"):
        # O caso que motiva a regra: documento sem unidade e sem falha e uma
        # ausencia que se le como "nao ha nada a dizer".
        ExtractionOutcome(document_id=SHA_B, extraction_version="v1")


def test_data_sem_base_declarada_nao_existe():
    from pat.contracts.corpus import SourceDocument

    with pytest.raises(ValidationError, match="andam juntos"):
        SourceDocument(
            document_id=SHA_A,
            entity_id="br:cnpj:1",
            kind=DocumentKind.PERFORMANCE_REPORT,
            title="t",
            language="pt-BR",
            source_tier=SourceTier.PRIMARY_OFFICIAL,
            published_at=date(2024, 1, 1),
            published_at_basis=DateBasis.FILING_METADATA,
            reference_date=date(2023, 12, 31),
            media_type="application/pdf",
            byte_size=1,
            provider_id="cvm",
            dataset_id="cvm.ipe_doc",
            resource_key="p",
            source_url="u",
            retrieval_id="r",
            first_seen_at=datetime(2025, 1, 1, tzinfo=UTC),
            first_seen_run_id="run",
        )


# -- O corte point-in-time, no contrato --------------------------------------


def test_consulta_nao_aceita_janela_posterior_ao_as_of():
    with pytest.raises(ValidationError, match="posterior ao as_of"):
        EvidenceQuery(
            entity_id="br:cnpj:1",
            terms=("brent",),
            as_of=date(2024, 6, 30),
            published_to=date(2024, 12, 31),
        )


def test_as_of_nao_tem_default():
    """Consulta sem `as_of` nem compila. A mesma regra de `Engine.compute`."""
    with pytest.raises(ValidationError):
        EvidenceQuery(entity_id="br:cnpj:1", terms=("brent",))


def test_resultado_recusa_citacao_posterior_ao_as_of():
    """O ultimo portao: mesmo que a consulta passasse, o resultado nao monta.

    Duas barreiras para o mesmo vazamento, de proposito. A do `EvidenceQuery`
    protege contra a janela pedida errada; esta protege contra um bug de SQL -
    que e por onde o vazamento realmente aconteceria.
    """
    hit = EvidenceHit(
        rank=1,
        relevance=Decimal("1.5"),
        matched_terms=("brent",),
        quote=_quote(published_at=date(2025, 3, 1)),
    )
    with pytest.raises(ValidationError, match="vazamento point-in-time"):
        EvidenceResult(
            query_id=SHA_A,
            entity_id="br:cnpj:1",
            as_of=date(2024, 12, 31),
            hits=(hit,),
            index_version="lexical/v1",
            documents_in_scope=1,
            units_in_scope=1,
            retrieved_at=datetime(2025, 6, 1, tzinfo=UTC),
        )


def test_rank_fora_de_ordem_e_erro():
    hit = EvidenceHit(
        rank=7, relevance=Decimal("1"), matched_terms=("brent",), quote=_quote()
    )
    with pytest.raises(ValidationError, match="rank fora de ordem"):
        EvidenceResult(
            query_id=SHA_A,
            entity_id="br:cnpj:1",
            as_of=date(2025, 1, 1),
            hits=(hit,),
            index_version="lexical/v1",
            documents_in_scope=1,
            units_in_scope=1,
            retrieved_at=datetime(2025, 6, 1, tzinfo=UTC),
        )


def test_termos_repetidos_sao_erro_de_identidade():
    """Consulta e enderecada por conteudo; repeticao mudaria o hash sem mudar
    o significado."""
    with pytest.raises(ValidationError, match="repetidos"):
        EvidenceQuery(
            entity_id="br:cnpj:1", terms=("brent", "brent"), as_of=date(2025, 1, 1)
        )

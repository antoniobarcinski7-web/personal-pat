"""O4 - a camada de documento, e a honestidade sobre o que falta.

O teste que define este milestone e
`test_o_provider_americano_recusa_com_motivo_em_vez_de_lista_vazia`. Todos os
outros poderiam passar com um adapter que devolvesse `()` quando nao sabe
fazer nada - e um sistema assim diria ao analista que a companhia americana
nao comenta as proprias margens, quando o que acontece e que o PAT nao sabe
ler os arquivamentos dela.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pat.contracts.corpus import DocumentKind, ExtractionFailure, ExtractionFailureReason
from pat.contracts.opportunity.documents import (
    ProviderCapability,
    ProviderProfile,
    ProviderUnavailable,
    ProviderUnavailableReason,
)
from pat.opportunity.company import profile_for
from pat.opportunity.documents import DocumentProvider, inventory, stored_documents
from pat.opportunity.providers import (
    BrazilDocumentProvider,
    USDocumentProvider,
    provider_for,
)
from pat.store.corpus import write_documents, write_failure
from tests.corpus.conftest import make_document
from tests.opportunity.warehouses import INTEL_ENTITY
from tests.semantics import golden_gpa as gpa

AS_OF = date(2025, 6, 30)


# -- o criterio que define a O4 ---------------------------------------------


def test_o_provider_americano_recusa_com_motivo_em_vez_de_lista_vazia(us_warehouse):
    """Lista vazia seria uma afirmacao sobre o MUNDO. O fato e sobre o SISTEMA.

    As duas coisas chegam ao agente como a mesma ausencia e pedem acoes
    opostas: uma entra na tese como achado, a outra e uma lacuna do PAT.
    """
    provider = provider_for("US")
    perfil = profile_for(us_warehouse, INTEL_ENTITY)

    resposta = provider.discover(
        us_warehouse,
        company=perfil,
        published_from=date(2020, 1, 1),
        published_to=AS_OF,
    )

    assert isinstance(resposta, ProviderUnavailable)
    assert resposta.capability is ProviderCapability.DISCOVER
    assert resposta.reason is ProviderUnavailableReason.NOT_IMPLEMENTED
    # O remedio nomeia o trabalho que falta, e nao apenas que ele falta.
    assert "parse" in resposta.remedy.lower()
    assert resposta.message


def test_o_provider_americano_para_na_extracao_e_diz_onde(us_warehouse):
    """A lacuna e no formato, e nao na busca. A diferenca diz a quem for
    terminar o trabalho por onde comecar."""
    provider = USDocumentProvider()
    resposta = provider.fetch(
        us_warehouse,
        company=profile_for(us_warehouse, INTEL_ENTITY),
        documents=(),
    )
    assert isinstance(resposta, ProviderUnavailable)
    assert resposta.capability is ProviderCapability.EXTRACT
    assert resposta.reason is ProviderUnavailableReason.UNSUPPORTED_MEDIA
    # E o remedio recusa explicitamente o atalho.
    assert "ocr" in resposta.remedy.lower()


def test_o_perfil_nao_pode_ficar_calado_sobre_uma_capacidade():
    """Silencio sobre uma etapa faz o agente descobrir a lacuna tarde, depois
    de ja ter prometido a pesquisa."""
    with pytest.raises(ValueError, match="nao diz nada sobre"):
        ProviderProfile(
            provider_id="incompleto",
            jurisdiction="XX",
            capabilities=(ProviderCapability.FETCH,),
        )


def test_o_perfil_nao_pode_declarar_e_negar_a_mesma_coisa():
    with pytest.raises(ValueError, match="declara e nega"):
        ProviderProfile(
            provider_id="contraditorio",
            jurisdiction="XX",
            capabilities=tuple(ProviderCapability),
            missing=(
                ProviderUnavailable(
                    provider_id="contraditorio",
                    jurisdiction="XX",
                    capability=ProviderCapability.EXTRACT,
                    reason=ProviderUnavailableReason.NOT_IMPLEMENTED,
                    message="m",
                    remedy="r",
                ),
            ),
        )


def test_remedio_e_obrigatorio_em_toda_lacuna():
    """Sem remedio, o registro manda o agente parar sem dizer a uma pessoa por
    onde continuar - e a lacuna sobrevive a cada leitura do relatorio."""
    with pytest.raises(ValueError):
        ProviderUnavailable(
            provider_id="p",
            jurisdiction="XX",
            capability=ProviderCapability.DISCOVER,
            reason=ProviderUnavailableReason.NOT_IMPLEMENTED,
            message="nao da",
            remedy="",
        )


# -- perfis declarados ------------------------------------------------------


def test_os_dois_providers_satisfazem_o_protocolo():
    assert isinstance(BrazilDocumentProvider(), DocumentProvider)
    assert isinstance(USDocumentProvider(), DocumentProvider)


def test_o_provider_brasileiro_declara_as_tres_capacidades():
    perfil = provider_for("BR").profile
    assert set(perfil.capabilities) == set(ProviderCapability)
    assert perfil.missing == ()
    assert DocumentKind.PERFORMANCE_REPORT in perfil.kinds_supported


def test_o_provider_americano_declara_o_que_tem_e_o_que_falta():
    perfil = provider_for("US").profile
    assert perfil.can(ProviderCapability.FETCH)
    assert not perfil.can(ProviderCapability.DISCOVER)
    assert not perfil.can(ProviderCapability.EXTRACT)
    assert perfil.why_not(ProviderCapability.DISCOVER).remedy
    assert perfil.why_not(ProviderCapability.EXTRACT).remedy
    assert perfil.why_not(ProviderCapability.FETCH) is None


def test_nao_existe_provider_default():
    """Um default faria a terceira jurisdicao ser atendida pelo adapter da
    primeira, e o erro sairia como documento do pais errado no inventario."""
    with pytest.raises(KeyError, match="nenhum provider"):
        provider_for("DE")


def test_a_jurisdicao_e_normalizada_mas_nao_adivinhada():
    assert provider_for("br").jurisdiction == "BR"
    with pytest.raises(KeyError):
        provider_for("BRA")


# -- inventario -------------------------------------------------------------


@pytest.fixture
def corpus_conn(br_warehouse):
    """Tres documentos: um extraido, um que falhou, um posterior ao `as_of`."""
    from pat.corpus.index import build_index
    from pat.store.corpus import write_units

    from tests.corpus.conftest import make_pdf
    from pat.corpus.extract import extract

    docs = [
        make_document("a" * 64, entity_id=gpa.ENTITY_ID, published_at=date(2024, 3, 1)),
        make_document(
            "b" * 64,
            entity_id=gpa.ENTITY_ID,
            published_at=date(2024, 6, 1),
            kind=DocumentKind.MATERIAL_FACT,
            title="Fato relevante",
        ),
        make_document(
            "c" * 64,
            entity_id=gpa.ENTITY_ID,
            published_at=date(2025, 12, 1),
            title="Depois do as_of",
        ),
    ]
    write_documents(br_warehouse, docs)

    extraido = extract("a" * 64, make_pdf([["A margem bruta subiu no trimestre."]]))
    write_units(br_warehouse, extraido.units)

    write_failure(
        br_warehouse,
        ExtractionFailure(
            document_id="b" * 64,
            reason=ExtractionFailureReason.NO_TEXT_LAYER,
            message="pdf sem camada de texto",
            extraction_version="1.0.0",
            failed_at=datetime(2025, 1, 2, tzinfo=UTC),
        ),
    )
    build_index(br_warehouse)
    return br_warehouse


def test_o_inventario_corta_por_as_of(corpus_conn):
    """Listar documento posterior ao `as_of` faria o agente planejar uma
    citacao que a busca depois recusaria, e a explicacao pareceria um bug."""
    documentos = stored_documents(corpus_conn, entity_id=gpa.ENTITY_ID, as_of=AS_OF)
    titulos = {d.title for d in documentos}
    assert "Depois do as_of" not in titulos
    assert len(documentos) == 2


def test_o_documento_que_falhou_continua_no_inventario_marcado(corpus_conn):
    """Some-lo faria a contagem parecer menor e correta, quando ela esta menor
    e errada: os bytes estao la, e o que falta e o sistema saber ler."""
    inv = inventory(
        corpus_conn,
        company=profile_for(corpus_conn, gpa.ENTITY_ID),
        as_of=AS_OF,
        provider=provider_for("BR"),
    )

    assert len(inv.documents) == 2
    assert len(inv.citable) == 1
    assert len(inv.failed) == 1
    falho = inv.failed[0]
    assert falho.extraction_failed
    assert falho.failure_reason == ExtractionFailureReason.NO_TEXT_LAYER.value
    assert not falho.is_citable
    assert falho.document_id == "b" * 64, "o documento continua identificavel"


def test_o_inventario_carrega_o_perfil_do_provider(corpus_conn):
    """Quem le "2 documentos" precisa saber, na mesma estrutura, se sao 2 de 2
    ou 2 de um regime cuja descoberta nao existe."""
    inv = inventory(
        corpus_conn,
        company=profile_for(corpus_conn, gpa.ENTITY_ID),
        as_of=AS_OF,
        provider=provider_for("BR"),
    )
    assert inv.provider is not None
    assert inv.provider.can(ProviderCapability.DISCOVER)


def test_o_inventario_americano_mostra_zero_com_a_lacuna_junto(us_warehouse):
    """Zero documentos e a lacuna, lado a lado. Um sem o outro engana."""
    inv = inventory(
        us_warehouse,
        company=profile_for(us_warehouse, INTEL_ENTITY),
        as_of=AS_OF,
        provider=provider_for("US"),
    )
    assert inv.documents == ()
    assert inv.provider.missing, "o zero vem acompanhado do motivo"
    assert {m.capability for m in inv.provider.missing} == {
        ProviderCapability.DISCOVER,
        ProviderCapability.EXTRACT,
    }


def test_o_inventario_preserva_a_procedencia_exigida(corpus_conn):
    """Fonte, identidade, tipo, data, URL, hash, versao de extracao,
    jurisdicao - todos os campos que o milestone exige preservados."""
    documentos = stored_documents(corpus_conn, entity_id=gpa.ENTITY_ID, as_of=AS_OF)
    citavel = next(d for d in documentos if d.is_citable)
    assert citavel.document_id == "a" * 64, "o hash dos bytes E a identidade"
    assert citavel.provider_id and citavel.dataset_id
    assert citavel.kind is DocumentKind.PERFORMANCE_REPORT
    assert citavel.published_at == date(2024, 3, 1)
    assert citavel.source_url.startswith("https://")
    assert citavel.byte_size > 0
    assert citavel.jurisdiction == "BR"
    assert citavel.extraction_versions, "a versao de extracao viaja junto"
    assert citavel.units > 0


def test_por_kind_e_janela_de_publicacao(corpus_conn):
    inv = inventory(
        corpus_conn, company=profile_for(corpus_conn, gpa.ENTITY_ID), as_of=AS_OF
    )
    assert dict(inv.by_kind) == {
        DocumentKind.MATERIAL_FACT: 1,
        DocumentKind.PERFORMANCE_REPORT: 1,
    }
    assert inv.published_range == (date(2024, 3, 1), date(2024, 6, 1))


# -- descoberta brasileira --------------------------------------------------


def test_a_descoberta_br_sem_catalogo_e_recusa_nomeada(br_warehouse):
    """Nao e lista vazia: e `CATALOG_MISSING`, com o comando no remedio."""
    from pat.store.bronze import BronzeStore

    resposta = provider_for("BR").discover(
        br_warehouse,
        company=profile_for(br_warehouse, gpa.ENTITY_ID),
        published_from=date(2024, 1, 1),
        published_to=date(2024, 12, 31),
        bronze=BronzeStore(__import__("pathlib").Path("/tmp/bronze-inexistente-o4")),
    )
    assert isinstance(resposta, ProviderUnavailable)
    assert resposta.reason is ProviderUnavailableReason.CATALOG_MISSING
    assert "2024" in resposta.remedy


def test_documento_descoberto_nao_e_documento_citavel():
    """`DiscoveredDocument` nao tem `document_id`, e nao e esquecimento.

    O hash so existe depois dos bytes. Tratar um descoberto como citavel
    deixaria o agente prometer uma citacao que nao existe.
    """
    from pat.contracts.opportunity.documents import DiscoveredDocument

    campos = set(DiscoveredDocument.model_fields)
    assert "document_id" not in campos
    assert "units" not in campos
    assert "external_id" in campos

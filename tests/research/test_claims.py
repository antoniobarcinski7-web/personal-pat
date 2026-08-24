"""O grafo de afirmacoes e o critic mecanico.

O teste que da nome ao arquivo e
`test_um_calculo_nao_pode_se_apoiar_numa_citacao`: o portao contra a lavagem
de numero do emissor, conferido na CONSTRUCAO do grafo - antes do critic,
antes do escritor, antes de qualquer prompt.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from pat.canonical import sha256_of
from pat.contracts.claims import (
    ClaimGraph,
    ClaimKind,
    ClaimNode,
    EvidenceStrength,
    MechanicalFinding,
    Severity,
)
from pat.contracts.program import ProgramResult
from pat.research.critic import review

AS_OF = date(2025, 6, 30)


def _id(rotulo: str) -> str:
    return sha256_of({"rotulo": rotulo})


def _fato(rotulo: str = "f1") -> ClaimNode:
    return ClaimNode(
        claim_id=_id(rotulo),
        kind=ClaimKind.FACT,
        text="receita liquida consolidada, FY2024, AS OF 2025-06-30",
        token="{{s:receita}}",
        result_id="1" * 64,
    )


def _citacao(rotulo: str = "q1", texto: str = "A receita caiu por causa do Brent.") -> ClaimNode:
    return ClaimNode(
        claim_id=_id(rotulo),
        kind=ClaimKind.QUOTE,
        text=texto,
        unit_id="2" * 64,
    )


def _leitura(supports: tuple[str, ...], rotulo: str = "i1") -> ClaimNode:
    return ClaimNode(
        claim_id=_id(rotulo),
        kind=ClaimKind.INFERENCE,
        text="a queda veio principalmente de preco",
        supports=supports,
        strength=EvidenceStrength.ATTRIBUTED,
    )


def _resultado_vazio() -> ProgramResult:
    return ProgramResult(
        program_id="a" * 64,
        question_id="b" * 64,
        as_of=AS_OF,
        executed_at=datetime(2025, 6, 30, tzinfo=UTC),
        capability_sha256="c" * 64,
    )


# ---------------------------------------------------------------------------
# O portao contra a lavagem de numero do emissor
# ---------------------------------------------------------------------------


def test_um_calculo_nao_pode_se_apoiar_numa_citacao():
    """A barreira mais importante do sistema, na construcao do grafo.

    Um release diz "receita de R$ 511,9 bilhoes". Esse algarismo pode sair no
    relatorio DENTRO da citacao. O que ele nao pode e alimentar uma conta - e
    um relatorio que tentasse isso nao chega a existir.
    """
    citacao = _citacao()
    calculo = ClaimNode(
        claim_id=_id("c1"),
        kind=ClaimKind.CALCULATION,
        text="variacao da receita",
        token="{{s:delta}}",
        result_id="3" * 64,
        supports=(citacao.claim_id,),
    )
    with pytest.raises(ValidationError, match="nunca insumo de conta"):
        ClaimGraph(nodes=(citacao, calculo))


def test_o_critic_tambem_confere_a_lavagem_para_grafo_vindo_de_fora():
    """A mesma regra, no ponto onde a resposta sai.

    Um grafo pode chegar montado por outro caminho - desserializado de um
    arquivo, por exemplo - e a barreira mais importante do sistema merece ser
    conferida nos dois lugares.
    """
    citacao = _citacao()
    calculo = ClaimNode.model_construct(
        claim_id=_id("c1"),
        kind=ClaimKind.CALCULATION,
        text="variacao",
        token="{{s:d}}",
        result_id="3" * 64,
        supports=(citacao.claim_id,),
        unit_id=None,
        strength=None,
        falsified_by=(),
    )
    grafo = ClaimGraph.model_construct(graph_version="v1", nodes=(citacao, calculo))

    relatorio = review(grafo, result=_resultado_vazio())
    codigos = {f.code for f in relatorio.hard}
    assert MechanicalFinding.ISSUER_NUMBER_LAUNDERED in codigos
    assert relatorio.blocks


# ---------------------------------------------------------------------------
# A estrutura do grafo
# ---------------------------------------------------------------------------


def test_toda_leitura_alcanca_um_no_ancorado():
    """Uma torre de leituras sem nada embaixo e recusada, nao avisada."""
    solta = ClaimNode(
        claim_id=_id("i1"),
        kind=ClaimKind.INFERENCE,
        text="a margem melhorou",
        supports=(_id("i2"),),
        strength=EvidenceStrength.SUGGESTED,
    )
    outra = ClaimNode(
        claim_id=_id("i2"),
        kind=ClaimKind.INFERENCE,
        text="o custo caiu",
        supports=(_id("i3"),),
        strength=EvidenceStrength.SUGGESTED,
    )
    terceira = ClaimNode(
        claim_id=_id("i3"),
        kind=ClaimKind.INFERENCE,
        text="a operacao melhorou",
        supports=(_id("i1"),),
        strength=EvidenceStrength.SUGGESTED,
    )
    with pytest.raises(ValidationError, match="ciclo|nao alcanca"):
        ClaimGraph(nodes=(solta, outra, terceira))


def test_ciclo_e_recusado():
    a = ClaimNode(
        claim_id=_id("a"),
        kind=ClaimKind.INFERENCE,
        text="a",
        supports=(_id("b"),),
        strength=EvidenceStrength.SUGGESTED,
    )
    b = ClaimNode(
        claim_id=_id("b"),
        kind=ClaimKind.INFERENCE,
        text="b",
        supports=(_id("a"),),
        strength=EvidenceStrength.SUGGESTED,
    )
    with pytest.raises(ValidationError, match="ciclo"):
        ClaimGraph(nodes=(a, b))


def test_suporte_que_nao_existe_e_recusado():
    with pytest.raises(ValidationError, match="nao existe"):
        ClaimGraph(nodes=(_leitura((_id("fantasma"),)),))


def test_uma_leitura_sobre_uma_leitura_e_valida_se_a_base_ancora():
    """Cadeia de raciocinio e legitima - o que nao pode e ela flutuar."""
    fato = _fato()
    primeira = _leitura((fato.claim_id,), "i1")
    segunda = ClaimNode(
        claim_id=_id("i2"),
        kind=ClaimKind.INFERENCE,
        text="portanto a tendencia e de compressao",
        supports=(primeira.claim_id,),
        strength=EvidenceStrength.SUGGESTED,
    )
    grafo = ClaimGraph(nodes=(fato, primeira, segunda))
    assert len(grafo.of_kind(ClaimKind.INFERENCE)) == 2


# ---------------------------------------------------------------------------
# As especies, e o que cada uma exige
# ---------------------------------------------------------------------------


def test_conclusao_sem_falsificador_nao_e_construivel():
    """Conclusao que nao diz o que a derrubaria e opiniao, nao research."""
    with pytest.raises(ValidationError, match="falsified_by"):
        ClaimNode(
            claim_id=_id("k"),
            kind=ClaimKind.CONCLUSION,
            text="a tese continua de pe",
            supports=(_id("f1"),),
        )


def test_inferencia_sem_suporte_nao_e_construivel():
    with pytest.raises(ValidationError, match="supports"):
        ClaimNode(
            claim_id=_id("i"),
            kind=ClaimKind.INFERENCE,
            text="a margem caiu por alavancagem operacional",
            strength=EvidenceStrength.SUGGESTED,
        )


def test_inferencia_nao_tem_onde_por_um_valor():
    """A separacao que impede "a margem caiu por alavancagem" de ficar
    indistinguivel de "a margem foi 3,89%"."""
    with pytest.raises(ValidationError, match="nao leva token"):
        ClaimNode(
            claim_id=_id("i"),
            kind=ClaimKind.INFERENCE,
            text="leitura",
            supports=(_id("f1"),),
            strength=EvidenceStrength.SUGGESTED,
            token="{{s:x}}",
        )


def test_citacao_sem_endereco_nao_e_construivel():
    with pytest.raises(ValidationError, match="unit_id"):
        ClaimNode(claim_id=_id("q"), kind=ClaimKind.QUOTE, text="um trecho")


def test_a_forca_e_enum_e_nunca_numero():
    """Um escore 0-1 seria um numero produzido por modelo - e o pior tipo,
    porque pareceria medido."""
    assert set(EvidenceStrength) == {
        EvidenceStrength.QUANTIFIED,
        EvidenceStrength.ATTRIBUTED,
        EvidenceStrength.SUGGESTED,
    }
    anotacao = ClaimNode.model_fields["strength"].annotation
    assert "Decimal" not in str(anotacao) and "float" not in str(anotacao)


# ---------------------------------------------------------------------------
# O critic mecanico
# ---------------------------------------------------------------------------


def test_citacao_que_nao_bate_com_a_unidade_bloqueia():
    """Citacao parafraseada nao e citacao."""
    citacao = _citacao(texto="A receita caiu bastante por causa do Brent.")
    grafo = ClaimGraph(nodes=(citacao,))
    relatorio = review(
        grafo,
        result=_resultado_vazio(),
        unit_texts={"2" * 64: "A receita caiu por causa do Brent."},
    )
    assert MechanicalFinding.QUOTE_NOT_VERBATIM in {f.code for f in relatorio.hard}
    assert relatorio.blocks


def test_citacao_identica_passa():
    texto = "A receita caiu por causa do Brent."
    grafo = ClaimGraph(nodes=(_citacao(texto=texto),))
    relatorio = review(grafo, result=_resultado_vazio(), unit_texts={"2" * 64: texto})
    assert not relatorio.blocks


def test_digito_na_prosa_fora_de_citacao_bloqueia():
    grafo = ClaimGraph(nodes=(_fato(),))
    relatorio = review(
        grafo,
        result=_resultado_vazio(),
        prose_blocks=(("", "A receita caiu 12% no periodo."),),
        values={"{{s:receita}}": "R$ 490.8 bn"},
    )
    assert MechanicalFinding.DIGIT_OUTSIDE_QUOTE in {f.code for f in relatorio.hard}


def test_digito_dentro_de_bloco_de_citacao_e_permitido():
    """A excecao TIPADA da Fase 5: algarismo pode, dentro de citacao verbatim.

    E o que permite ao relatorio dizer que a companhia falou em "queda de 18%
    do Brent" sem que esse numero vire insumo de nada.
    """
    texto = "queda de 18% do preco do Brent"
    citacao = _citacao(texto=texto)
    grafo = ClaimGraph(nodes=(citacao,))
    relatorio = review(
        grafo,
        result=_resultado_vazio(),
        prose_blocks=((citacao.claim_id, texto),),
        unit_texts={"2" * 64: texto},
    )
    assert MechanicalFinding.DIGIT_OUTSIDE_QUOTE not in {f.code for f in relatorio.findings}
    assert not relatorio.blocks


def test_token_permitido_nao_conta_como_digito():
    grafo = ClaimGraph(nodes=(_fato(),))
    relatorio = review(
        grafo,
        result=_resultado_vazio(),
        prose_blocks=(("", "A receita foi de {{s:receita}} no exercicio."),),
        values={"{{s:receita}}": "R$ 490.8 bn"},
    )
    assert not relatorio.blocks


def test_token_desconhecido_bloqueia():
    """Nao ha o que substituir, e a frase sairia com a chave literal."""
    grafo = ClaimGraph(nodes=(_fato(),))
    relatorio = review(
        grafo,
        result=_resultado_vazio(),
        prose_blocks=(("", "A margem foi {{s:inexistente}}."),),
        values={"{{s:receita}}": "R$ 490.8 bn"},
    )
    assert MechanicalFinding.UNKNOWN_TOKEN in {f.code for f in relatorio.hard}


def test_achado_leve_acompanha_mas_nao_bloqueia():
    """Um relatorio que carrega a ressalva e mais util do que um limpo por
    reescrita."""
    grafo = ClaimGraph(nodes=(_fato(),))
    relatorio = review(
        grafo,
        result=_resultado_vazio(),
        warnings=("d1: a decomposicao nao fecha; residual de R$ 7.2 bn nao explicado",),
    )
    assert relatorio.soft
    assert not relatorio.blocks
    assert relatorio.soft[0].severity is Severity.SOFT


def test_o_critic_nao_escreve_prosa_e_nao_corrige():
    """Taxonomia fechada, sem texto livre de correcao.

    O critic aponta; ele nao reescreve e nao rechama o escritor. Sem loop
    `writer -> critic -> writer`, que e como um portao vira amostrador.
    """
    import inspect

    from pat.research import critic

    fonte = inspect.getsource(critic)
    assert "write_report" not in fonte
    assert "llm" not in fonte.replace("llm.complete", "")
    for finding in MechanicalFinding:
        assert finding.value.islower()

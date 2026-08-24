"""A decomposicao: as partes somam o todo, e o que sobra tem nome.

O teste central e `test_as_partes_somam_o_todo_sempre` - a invariante que o
contrato torna inviolavel. Os demais cobrem a matriz de recusa: membro
ausente, membro novo, membro encerrado, escopo incompativel, periodo
incompativel, residual nao-zero e mapeamento errado.

Os testes rodam contra um `FactResolver` de mentira, e nao contra o warehouse,
pela mesma razao dos golden tests da Fase 2: aqui se prova a ARITMETICA e as
recusas. Que os enderecos ainda apontem para as linhas certas na origem e
assunto de `-m network`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pat.contracts.common import PeriodType
from pat.contracts.decomposition import (
    BreakdownAxis,
    Contribution,
    DecompositionFailureReason,
    DecompositionResult,
    DecompositionUnavailable,
)
from pat.contracts.semantics import (
    Fidelity,
    ReportingScope,
    UnavailableReason,
)
from pat.research.decompose import decompose
from pat.semantics import concepts, decompositions
from pat.semantics.engine import Engine
from pat.semantics.loader import load_dir
from pat.semantics.registry import default_registry
from pat.semantics.resolver import ResolutionFailure, ResolvedFact

FY23 = date(2023, 12, 31)
FY24 = date(2024, 12, 31)
Q1 = date(2024, 3, 31)
AS_OF = date(2025, 6, 30)
ENTIDADE = "br:cnpj:33000167000101"

MM = Decimal(1_000_000)


class ResolverFalso:
    """Devolve o que o teste mandar, por (endereco, periodo).

    Endereco ausente vira `ResolutionFailure`, que e como o motor representa
    "a companhia nao publicou esta linha" - e e assim que se testa membro novo
    e membro encerrado sem precisar de um warehouse.
    """

    def __init__(self, valores: dict[tuple[str, date], Decimal], *, currency: str = "BRL") -> None:
        self.valores = valores
        self.currency = currency
        self.currency_overrides: dict[tuple[str, date], str] = {}
        self.period_types: dict[date, PeriodType] = {}

    def resolve(
        self, *, entity_id, address, period_end, period_kind, scope, as_of
    ):
        chave = (address.as_str(), period_end)
        if chave not in self.valores:
            return ResolutionFailure(
                reason=UnavailableReason.MISSING_FACT_AS_OF,
                message=f"nenhum fato para {address.as_str()} em {period_end} AS OF {as_of}",
            )
        return ResolvedFact(
            value=self.valores[chave],
            currency=self.currency_overrides.get(chave, self.currency),
            period_type=self.period_types.get(period_end, PeriodType.YEAR),
            period_start=date(period_end.year, 1, 1),
            period_end=period_end,
            knowledge_date=date(period_end.year + 1, 3, 1),
            fact_id=f"fato-{address.as_str()}-{period_end}",
            locator=address.as_str(),
        )


def _bindings() -> dict[str, tuple[str, int]]:
    """(endereco, sinal) efetivos de cada conceito na cadeia real da Petrobras.

    Lidos do mapeamento em vez de escritos a mao: assim o teste continua
    valido se o binding mudar, e quebra se o conceito deixar de estar ligado.

    O SINAL importa tanto quanto o endereco. A CVM publica custo e despesa
    como numeros negativos, e o binding os multiplica por -1 para chegar a
    convencao do conceito ("positivo = despesa"). O resolver de mentira aqui
    devolve valores CRUS, como a fonte os publica, justamente para que o
    caminho do sinal seja exercitado - um teste que alimentasse valores ja
    convertidos passaria com o binding trocado.
    """
    chain = load_dir().resolve(ENTIDADE, source="cvm")
    assert chain is not None, "a Petrobras precisa de mapeamento para este teste"
    saida = {}
    for concept_id in (
        concepts.REVENUE_NET,
        concepts.COGS,
        concepts.GROSS_PROFIT,
        concepts.OPERATING_EXPENSES_NET,
        concepts.EBIT_REPORTED,
    ):
        found = chain.binding_for(concept_id)
        assert found is not None, f"{concept_id} sem binding"
        binding, _ = found
        assert len(binding.lines) == 1, f"{concept_id} tem mais de uma linha"
        linha = binding.lines[0]
        saida[concept_id] = (linha.address.as_str(), linha.sign)
    return saida


BINDINGS = _bindings()
ENDERECOS = {cid: endereco for cid, (endereco, _) in BINDINGS.items()}


def _engine(resolver) -> Engine:
    chain_source = load_dir()
    taxonomy = chain_source.resolve(ENTIDADE, source="cvm").head.taxonomy
    return Engine(
        registry=default_registry(),
        mappings=chain_source,
        resolvers={taxonomy: resolver},
        source="cvm",
        pat_version="teste",
    )


def _valores(**por_conceito: tuple[Decimal, Decimal]) -> dict[tuple[str, date], Decimal]:
    """{conceito: (valor_fy23, valor_fy24)} -> {(endereco, periodo): valor CRU}.

    Recebe valores na convencao do CONCEITO e grava na convencao da FONTE,
    aplicando o inverso do sinal do binding. O motor reaplica o sinal e o
    ciclo fecha - o que faz o teste falhar se o sinal de um binding mudar.
    """
    saida: dict[tuple[str, date], Decimal] = {}
    for concept_id, (de, para) in por_conceito.items():
        endereco, sinal = BINDINGS[concept_id]
        if de is not None:
            saida[(endereco, FY23)] = de * sinal
        if para is not None:
            saida[(endereco, FY24)] = para * sinal
    return saida


def _petrobras_real() -> dict[tuple[str, date], Decimal]:
    """Os numeros publicados da Petrobras, FY2023 e FY2024, em reais."""
    return _valores(
        **{
            concepts.REVENUE_NET: (Decimal("511994") * MM, Decimal("490829") * MM),
            concepts.COGS: (Decimal("242061") * MM, Decimal("244367") * MM),
            concepts.GROSS_PROFIT: (Decimal("269933") * MM, Decimal("246462") * MM),
            concepts.OPERATING_EXPENSES_NET: (Decimal("80591") * MM, Decimal("109261") * MM),
            concepts.EBIT_REPORTED: (Decimal("189342") * MM, Decimal("137201") * MM),
        }
    )


def _decompor(resolver, ref="ebit_by_line@v1", **kwargs):
    parametros = dict(
        entity_id=ENTIDADE,
        period_from=FY23,
        period_to=FY24,
        scope=ReportingScope.CONSOLIDATED,
        as_of=AS_OF,
    )
    parametros.update(kwargs)
    return decompose(_engine(resolver), ref, **parametros)


# -- A invariante ------------------------------------------------------------


def test_as_partes_somam_o_todo_sempre():
    """`contribuicoes + residual == target_delta`, exatamente.

    Nao aproximadamente, nao dentro de tolerancia: exatamente, em `Decimal`.
    A tolerancia decide se a decomposicao FECHA, nao se ela pode existir.
    """
    resultado = _decompor(ResolverFalso(_petrobras_real()))
    assert isinstance(resultado, DecompositionResult)

    soma = sum((c.contribution for c in resultado.contributions), Decimal(0))
    assert soma + resultado.residual == resultado.target_delta


def test_os_numeros_reais_da_petrobras_fecham():
    """FY2023 -> FY2024: EBIT cai 52,1 bi, e a identidade fecha exata."""
    resultado = _decompor(ResolverFalso(_petrobras_real()))
    assert isinstance(resultado, DecompositionResult)

    assert resultado.target_from == Decimal("189342") * MM
    assert resultado.target_to == Decimal("137201") * MM
    assert resultado.target_delta == Decimal("-52141") * MM
    assert resultado.residual == 0
    assert resultado.closes is True
    assert resultado.fidelity is Fidelity.EXACT

    por_membro = {c.member_id: c for c in resultado.contributions}
    # Despesa que SOBE contribui NEGATIVAMENTE: o sinal da identidade ja esta
    # aplicado, e e por isso que `contribution` existe separado de `delta`.
    opex = por_membro[concepts.OPERATING_EXPENSES_NET]
    assert opex.delta == Decimal("28670") * MM
    assert opex.contribution == Decimal("-28670") * MM
    assert por_membro[concepts.REVENUE_NET].contribution == Decimal("-21165") * MM
    assert por_membro[concepts.COGS].contribution == Decimal("-2306") * MM


def test_a_maior_contribuicao_vem_primeiro_e_o_desempate_e_total():
    resultado = _decompor(ResolverFalso(_petrobras_real()))
    assert isinstance(resultado, DecompositionResult)
    ordenadas = resultado.ranked()
    magnitudes = [abs(c.contribution) for c in ordenadas]
    assert magnitudes == sorted(magnitudes, reverse=True)
    assert ordenadas[0].member_id == concepts.OPERATING_EXPENSES_NET
    # Duas execucoes da mesma decomposicao apresentam a mesma ordem.
    assert [c.member_id for c in ordenadas] == [c.member_id for c in resultado.ranked()]


# -- Residual nao-zero: um ACHADO, nao um detalhe ----------------------------


def test_residual_nao_zero_aparece_e_nao_e_distribuido():
    """Termos que nao fecham produzem residual visivel, nunca rateio.

    Distribuir o residual proporcionalmente faria a conta fechar sem que ela
    feche, e o resultado teria a aparencia exata de uma analise correta.
    """
    valores = _petrobras_real()
    # Mexe so no alvo: o EBIT deixa de bater com a soma das partes.
    valores[(ENDERECOS[concepts.EBIT_REPORTED], FY24)] = Decimal("130000") * MM * BINDINGS[concepts.EBIT_REPORTED][1]

    resultado = _decompor(ResolverFalso(valores))
    assert isinstance(resultado, DecompositionResult)

    assert resultado.residual == Decimal("-7201") * MM
    assert resultado.closes is False
    # As contribuicoes NAO foram tocadas para absorver a diferenca.
    por_membro = {c.member_id: c for c in resultado.contributions}
    assert por_membro[concepts.REVENUE_NET].contribution == Decimal("-21165") * MM
    # E a igualdade continua exata.
    soma = sum((c.contribution for c in resultado.contributions), Decimal(0))
    assert soma + resultado.residual == resultado.target_delta


def test_residual_dentro_da_tolerancia_fecha_mas_continua_visivel():
    valores = _petrobras_real()
    endereco, sinal = BINDINGS[concepts.EBIT_REPORTED]
    valores[(endereco, FY24)] = valores[(endereco, FY24)] + Decimal(500) * sinal

    resultado = _decompor(ResolverFalso(valores))
    assert isinstance(resultado, DecompositionResult)
    assert resultado.closes is True
    assert resultado.residual == Decimal(500)  # visivel mesmo fechando


# -- Membro ausente, novo, encerrado -----------------------------------------


def test_membro_ausente_nos_dois_periodos_recusa():
    valores = _petrobras_real()
    for periodo in (FY23, FY24):
        del valores[(ENDERECOS[concepts.COGS], periodo)]

    resultado = _decompor(ResolverFalso(valores))
    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.MEMBER_UNAVAILABLE
    assert resultado.member_id == concepts.COGS


def test_membro_novo_recusa_em_vez_de_assumir_zero_antes():
    """Componente que aparece so no periodo final.

    Zero implicito atribuiria o valor inteiro como contribuicao - um driver
    fabricado, com cara de medido.
    """
    valores = _petrobras_real()
    del valores[(ENDERECOS[concepts.COGS], FY23)]

    resultado = _decompor(ResolverFalso(valores))
    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.MEMBER_ONLY_IN_ONE_PERIOD
    assert resultado.member_id == concepts.COGS
    assert "fabricado" in resultado.message


def test_membro_encerrado_recusa_em_vez_de_assumir_zero_depois():
    valores = _petrobras_real()
    del valores[(ENDERECOS[concepts.COGS], FY24)]

    resultado = _decompor(ResolverFalso(valores))
    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.MEMBER_ONLY_IN_ONE_PERIOD
    assert str(FY24) in resultado.message


def test_alvo_ausente_recusa_com_motivo_proprio():
    valores = _petrobras_real()
    del valores[(ENDERECOS[concepts.EBIT_REPORTED], FY24)]

    resultado = _decompor(ResolverFalso(valores))
    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.TARGET_UNAVAILABLE


# -- Periodo e escopo --------------------------------------------------------


def test_periodo_invertido_recusa():
    resultado = _decompor(ResolverFalso(_petrobras_real()), period_from=FY24, period_to=FY23)
    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.PERIOD_ORDER


def test_periodo_igual_recusa():
    resultado = _decompor(ResolverFalso(_petrobras_real()), period_from=FY24, period_to=FY24)
    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.PERIOD_ORDER


def test_trimestre_contra_exercicio_recusa():
    """A variacao existiria, e nao significaria nada."""
    valores = _petrobras_real()
    for concept_id in (
        concepts.REVENUE_NET,
        concepts.COGS,
        concepts.OPERATING_EXPENSES_NET,
        concepts.EBIT_REPORTED,
    ):
        valores[(ENDERECOS[concept_id], Q1)] = Decimal("1000") * MM

    resolver = ResolverFalso(valores)
    resolver.period_types[Q1] = PeriodType.QUARTER
    resultado = _decompor(resolver, period_from=Q1, period_to=FY24)

    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.PERIOD_KIND_MISMATCH


def test_escopo_viaja_ate_o_resolver_e_ate_o_resultado():
    """Escopo nao e default e nao e misturado: ele desce inalterado.

    O par consolidado/individual e a classe de erro que mais parece certa -
    um numero no escopo errado tem a ordem de grandeza plausivel.
    """
    resultado = _decompor(
        ResolverFalso(_petrobras_real()), scope=ReportingScope.PARENT_ONLY
    )
    assert isinstance(resultado, DecompositionResult)
    assert resultado.scope is ReportingScope.PARENT_ONLY


def test_moeda_diferente_entre_periodos_para_o_calculo():
    """Moeda NUNCA e convertida implicitamente."""
    valores = _petrobras_real()
    resolver = ResolverFalso(valores)
    resolver.currency_overrides[(ENDERECOS[concepts.EBIT_REPORTED], FY24)] = "USD"

    resultado = _decompor(resolver)
    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.CURRENCY_MISMATCH


# -- Mapeamento errado -------------------------------------------------------


def test_mapeamento_que_pega_a_linha_errada_aparece_como_residual():
    """O sintoma de um binding errado e o residual, e ele nao e silenciado.

    E o valor pratico do residual: ele transforma um erro de mapeamento -
    que de outra forma sairia como um numero plausivel - em algo que se ve na
    tela.
    """
    valores = _petrobras_real()
    # Custo apontando para a linha errada: valor de outra grandeza.
    valores[(ENDERECOS[concepts.COGS], FY24)] = Decimal("50000") * MM * BINDINGS[concepts.COGS][1]

    resultado = _decompor(ResolverFalso(valores))
    assert isinstance(resultado, DecompositionResult)
    assert resultado.closes is False
    assert abs(resultado.residual) > Decimal("100000") * MM


def test_a_decomposicao_carrega_o_sha_da_cadeia_de_mapeamento():
    """Editar a familia muda o resultado sem tocar no arquivo da empresa."""
    resultado = _decompor(ResolverFalso(_petrobras_real()))
    assert isinstance(resultado, DecompositionResult)
    assert len(resultado.mapping_sha256) == 64


# -- Eixos sem fonte ---------------------------------------------------------


def test_eixo_sem_fonte_estruturada_recusa_com_nome():
    """SEGMENT nao tem fonte na CVM, e a recusa DIZ isso.

    Distinto de 'a empresa nao reporta': e o sistema que nao tem por onde ler.
    As duas coisas pedem acoes diferentes de quem esta pesquisando.
    """
    from pat.contracts.decomposition import BreakdownAxis as Eixo
    from pat.semantics.decompositions import DecompositionDefinition, Term

    ficticia = DecompositionDefinition(
        decomposition_id="receita_por_segmento",
        version="v1",
        axis=Eixo.SEGMENT,
        target_concept=concepts.REVENUE_NET,
        target_label="Receita liquida",
        terms=(Term(concepts.REVENUE_NET, 1, "E&P"),),
        definition="receita por segmento operacional",
        rationale="teste",
        tolerance_abs=Decimal(1000),
    )
    decompositions.CATALOG[ficticia.ref] = ficticia
    try:
        resultado = _decompor(ResolverFalso(_petrobras_real()), ref=ficticia.ref)
    finally:
        del decompositions.CATALOG[ficticia.ref]

    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.NO_BREAKDOWN_SOURCE
    assert resultado.axis is BreakdownAxis.SEGMENT
    assert "PDF" in resultado.message


def test_decomposicao_desconhecida_lista_as_que_existem():
    resultado = _decompor(ResolverFalso(_petrobras_real()), ref="nao_existe@v1")
    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.UNKNOWN_DECOMPOSITION
    assert "ebit_by_line@v1" in resultado.message


def test_referencia_sem_versao_nao_resolve_para_a_mais_recente():
    """'A mais recente' faria uma analise antiga mudar de significado."""
    resultado = _decompor(ResolverFalso(_petrobras_real()), ref="ebit_by_line")
    assert isinstance(resultado, DecompositionUnavailable)
    assert resultado.reason is DecompositionFailureReason.UNKNOWN_DECOMPOSITION


# -- Fracoes -----------------------------------------------------------------


def test_variacao_zero_nao_produz_fracao_e_sim_ausencia():
    """Divisao por zero vira `None`, nunca 0%, nunca 100%, nunca infinito.

    Um total que nao mudou pode ter partes que se cancelaram exatamente - que
    e a coisa mais interessante que poderia ter acontecido. Imprimir "0%"
    sugeriria que as partes tambem nao mudaram.
    """
    valores = _valores(
        **{
            concepts.REVENUE_NET: (Decimal("100") * MM, Decimal("120") * MM),
            concepts.COGS: (Decimal("40") * MM, Decimal("60") * MM),
            concepts.OPERATING_EXPENSES_NET: (Decimal("10") * MM, Decimal("10") * MM),
            concepts.EBIT_REPORTED: (Decimal("50") * MM, Decimal("50") * MM),
        }
    )
    resultado = _decompor(ResolverFalso(valores))
    assert isinstance(resultado, DecompositionResult)

    assert resultado.target_delta == 0
    assert resultado.residual_share is None
    assert all(c.share is None for c in resultado.contributions)
    # E as partes se cancelaram, o que continua visivel.
    assert resultado.contributions[0].contribution == Decimal("20") * MM
    assert resultado.contributions[1].contribution == Decimal("-20") * MM


def test_residual_zero_nao_sai_como_menos_zero():
    resultado = _decompor(ResolverFalso(_petrobras_real()))
    assert isinstance(resultado, DecompositionResult)
    assert resultado.residual_share == 0
    assert not str(resultado.residual_share).startswith("-")


# -- Contrato ----------------------------------------------------------------


def test_contribuicao_com_sinal_incoerente_nao_e_construivel():
    with pytest.raises(ValueError, match="contribuicao"):
        Contribution(
            member_id="x",
            member_label="X",
            sign=-1,
            value_from=Decimal(10),
            value_to=Decimal(30),
            delta=Decimal(20),
            contribution=Decimal(20),  # deveria ser -20
            fidelity=Fidelity.EXACT,
        )


def test_resultado_cujas_partes_nao_somam_o_todo_nao_e_construivel():
    """O portao final: nao ha como montar uma decomposicao que nao fecha.

    Se esta validacao cair, uma decomposicao poderia explicar 94% da variacao
    e se apresentar como completa, com 6% escondido num arredondamento que
    ninguem foi conferir.
    """
    contribuicao = Contribution(
        member_id="x",
        member_label="X",
        sign=1,
        value_from=Decimal(10),
        value_to=Decimal(30),
        delta=Decimal(20),
        contribution=Decimal(20),
        fidelity=Fidelity.EXACT,
    )
    with pytest.raises(ValueError, match="as partes nao somam o todo"):
        DecompositionResult(
            decomposition_id="x",
            decomposition_version="v1",
            axis=BreakdownAxis.COMPONENT,
            target_id="alvo",
            target_label="Alvo",
            entity_id=ENTIDADE,
            scope=ReportingScope.CONSOLIDATED,
            period_type=PeriodType.YEAR,
            period_from=FY23,
            period_to=FY24,
            as_of=AS_OF,
            target_from=Decimal(0),
            target_to=Decimal(100),
            target_delta=Decimal(100),
            contributions=(contribuicao,),
            residual=Decimal(0),  # deveria ser 80
            closes=True,
            tolerance_abs=Decimal(1000),
            currency="BRL",
            fidelity=Fidelity.EXACT,
            knowledge_date=FY24,
            mapping_sha256="a" * 64,
        )


def test_a_definicao_declara_a_tolerancia_e_nao_a_chamada():
    """Tolerancia e da identidade, revisavel. Nao e parametro de chamada.

    Se fosse parametro, quem estivesse com pressa afrouxaria ate fechar.
    """
    import inspect

    assinatura = inspect.signature(decompose)
    assert "tolerance" not in assinatura.parameters
    assert "tolerance_abs" not in assinatura.parameters
    for definition in decompositions.all_definitions():
        assert definition.tolerance_abs > 0

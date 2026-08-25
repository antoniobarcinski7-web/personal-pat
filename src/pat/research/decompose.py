"""Executa uma decomposicao: variacao de um total -> contribuicoes + residual.

Deterministico de ponta a ponta. Nenhum modelo participa, e nao deve passar a
participar: atribuir a variacao de um numero a suas partes e aritmetica sobre
fatos com linhagem, e um modelo no meio disso so poderia piorar a
procedencia.

O que este modulo NAO faz, e por que
-------------------------------------
Nao distribui o residual entre os membros, nao estima contribuicao por
regressao, nao normaliza para somar 100%, e nao esconde membro pequeno em
"outros". Cada uma dessas seria uma forma de fazer a conta fechar sem que ela
feche - e o resultado teria exatamente a aparencia de uma analise correta.

Sobre o residual
----------------
Ele e calculado por diferenca, e nao aferido:

    residual = target_delta - soma(contribuicoes)

Com isso a igualdade do contrato vale por construcao, e o residual carrega
sozinho tudo que a identidade nao explica - arredondamento da origem,
reclassificacao entre linhas, ou um mapeamento que pegou a linha errada. Um
residual grande e um ACHADO, e por isso ele viaja ate a tela em vez de virar
um `assert`.

Membro presente em um so periodo
--------------------------------
Recusa nomeada (`MEMBER_ONLY_IN_ONE_PERIOD`), nunca zero. Uma companhia que
deixou de reportar um componente nao contribuiu com "menos o valor inteiro do
ano passado" - ela mudou de apresentacao, e isso e informacao diferente. As
duas se pareceriam identicas no grafico.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext

from pat.contracts.decomposition import (
    BreakdownAxis,
    Contribution,
    DecompositionFailureReason,
    DecompositionResult,
    DecompositionUnavailable,
)
from pat.contracts.semantics import (
    ConceptUnavailable,
    Fidelity,
    ReportingScope,
    weakest,
)
from pat.semantics import decompositions
from pat.semantics.decompositions import DecompositionDefinition
from pat.semantics.engine import Engine

__all__ = ["SHARE_PRECISION", "decompose"]

SHARE_PRECISION = 10
"""Precisao fixa para as fracoes de participacao.

Fixa, e nao herdada do contexto ambiente, pela mesma razao do escore do
indice: um processo que tivesse mexido no contexto decimal por outro motivo
produziria outras fracoes, e o resultado deixaria de ser reproduzivel sem que
nada visivel tivesse mudado. As fracoes sao apresentacao - a igualdade que
importa (`contribuicoes + residual == delta`) e exata e nao passa por aqui."""


def decompose(
    engine: Engine,
    ref: str,
    *,
    entity_id: str,
    period_from: date,
    period_to: date,
    scope: ReportingScope,
    as_of: date,
) -> DecompositionResult | DecompositionUnavailable:
    """Abre a variacao de um alvo entre dois periodos.

    `scope` e `as_of` sao obrigatorios pelas razoes de sempre: um numero no
    escopo errado parece certo, e nao existe consulta que devolva "o valor"
    sem dizer segundo quando.
    """
    definition = decompositions.get(ref)
    if definition is None:
        conhecidas = ", ".join(d.ref for d in decompositions.all_definitions())
        return DecompositionUnavailable(
            reason=DecompositionFailureReason.UNKNOWN_DECOMPOSITION,
            message=f"decomposicao {ref!r} nao existe. Disponiveis: {conhecidas}",
            decomposition_id=ref,
            entity_id=entity_id,
            remedy="Use 'nome@versao'. Sem versao nao resolve para a mais recente, de proposito.",
        )

    if definition.axis is not BreakdownAxis.COMPONENT:
        return _decompose_by_axis(
            engine,
            definition,
            entity_id=entity_id,
            period_from=period_from,
            period_to=period_to,
            scope=scope,
            as_of=as_of,
        )

    if period_from >= period_to:
        return _fail(
            definition,
            DecompositionFailureReason.PERIOD_ORDER,
            f"periodo inicial {period_from} nao e anterior a {period_to}",
            entity_id=entity_id,
            period_from=period_from,
            period_to=period_to,
            as_of=as_of,
            remedy="Uma decomposicao abre uma VARIACAO; ela precisa de dois periodos distintos.",
        )

    alvo = _pair(
        engine,
        definition.target_concept,
        entity_id=entity_id,
        period_from=period_from,
        period_to=period_to,
        scope=scope,
        as_of=as_of,
    )
    if isinstance(alvo, DecompositionUnavailable):
        return _retag(alvo, definition, DecompositionFailureReason.TARGET_UNAVAILABLE)

    de_alvo, para_alvo = alvo

    if de_alvo.period_type != para_alvo.period_type:
        return _fail(
            definition,
            DecompositionFailureReason.PERIOD_KIND_MISMATCH,
            f"{period_from} e {de_alvo.period_type} e {period_to} e "
            f"{para_alvo.period_type}: a variacao existiria e nao significaria nada",
            entity_id=entity_id,
            period_from=period_from,
            period_to=period_to,
            as_of=as_of,
            remedy="Compare periodos do mesmo tipo (exercicio com exercicio).",
        )

    if de_alvo.currency != para_alvo.currency:
        return _mixed_currency(
            definition, entity_id, period_from, period_to, as_of, de_alvo.currency, para_alvo.currency
        )

    contribuicoes: list[Contribution] = []
    fidelidades: list[Fidelity] = [de_alvo.fidelity, para_alvo.fidelity]
    moedas = {de_alvo.currency, para_alvo.currency}

    for termo in definition.terms:
        par = _pair(
            engine,
            termo.concept_id,
            entity_id=entity_id,
            period_from=period_from,
            period_to=period_to,
            scope=scope,
            as_of=as_of,
        )
        if isinstance(par, DecompositionUnavailable):
            return _retag(par, definition, par.reason)
        de, para = par

        moedas.update({de.currency, para.currency})
        if len(moedas) > 1:
            return _mixed_currency(
                definition, entity_id, period_from, period_to, as_of, *sorted(str(m) for m in moedas)[:2]
            )

        delta = para.value - de.value
        contribuicoes.append(
            Contribution(
                member_id=termo.concept_id,
                member_label=termo.label,
                sign=termo.sign,
                value_from=de.value,
                value_to=para.value,
                delta=delta,
                contribution=termo.sign * delta,
                fidelity=weakest([de.fidelity, para.fidelity]),
                inputs=tuple(de.inputs) + tuple(para.inputs),
            )
        )
        fidelidades.extend([de.fidelity, para.fidelity])

    target_delta = para_alvo.value - de_alvo.value
    soma = sum((c.contribution for c in contribuicoes), Decimal(0))
    residual = target_delta - soma

    chain = engine.mapping_chain_for(entity_id)
    if chain is None:  # pragma: no cover - `_pair` ja teria recusado
        return _fail(
            definition,
            DecompositionFailureReason.TARGET_UNAVAILABLE,
            f"nenhum mapeamento cobre {entity_id}",
            entity_id=entity_id,
            as_of=as_of,
        )

    with localcontext() as contexto:
        contexto.prec = SHARE_PRECISION
        shares = [(c, _share(c.contribution, target_delta)) for c in contribuicoes]
        residual_share = _share(residual, target_delta)
        delta_pct = (target_delta / abs(de_alvo.value)) if de_alvo.value != 0 else None

    return DecompositionResult(
        decomposition_id=definition.decomposition_id,
        decomposition_version=definition.version,
        axis=definition.axis,
        target_id=definition.target_concept,
        target_label=definition.target_label,
        entity_id=entity_id,
        scope=scope,
        period_type=de_alvo.period_type,
        period_from=period_from,
        period_to=period_to,
        as_of=as_of,
        target_from=de_alvo.value,
        target_to=para_alvo.value,
        target_delta=target_delta,
        target_delta_pct=delta_pct,
        contributions=tuple(
            contribuicao.model_copy(update={"share": share}) for contribuicao, share in shares
        ),
        residual=residual,
        residual_share=residual_share,
        closes=abs(residual) <= definition.tolerance_abs,
        tolerance_abs=definition.tolerance_abs,
        currency=str(de_alvo.currency),
        fidelity=weakest(fidelidades),
        knowledge_date=max(de_alvo.knowledge_date, para_alvo.knowledge_date),
        mapping_sha256=chain.sha256,
    )


def _decompose_by_axis(
    engine: Engine,
    definition: DecompositionDefinition,
    *,
    entity_id: str,
    period_from: date,
    period_to: date,
    scope: ReportingScope,
    as_of: date,
) -> DecompositionResult | DecompositionUnavailable:
    """Abre a variacao por MEMBRO dimensional declarado.

    Os membros vem do mapeamento da empresa, e nunca da fonte. A fonte publica
    os membros que o emissor usa, mas nao publica a hierarquia entre eles -
    roll-up e folha aparecem lado a lado. Somar tudo que a fonte tem contaria
    segmentos duas vezes, e o total pareceria plausivel.

    Sem membro declarado, `NO_BREAKDOWN_SOURCE`. E o estado da CVM inteira: a
    fonte nao publica a dimensao, entao nao ha o que declarar.
    """
    chain = engine.mapping_chain_for(entity_id)
    membros = [
        m
        for m in (chain.head.segments if chain else ())
        if m.axis == definition.member_axis or m.is_elimination
    ]
    if not membros:
        return _no_source(definition, entity_id, as_of)

    if period_from >= period_to:
        return _fail(
            definition,
            DecompositionFailureReason.PERIOD_ORDER,
            f"periodo inicial {period_from} nao e anterior a {period_to}",
            entity_id=entity_id,
            period_from=period_from,
            period_to=period_to,
            as_of=as_of,
        )

    alvo = _pair(
        engine,
        definition.target_concept,
        entity_id=entity_id,
        period_from=period_from,
        period_to=period_to,
        scope=scope,
        as_of=as_of,
    )
    if isinstance(alvo, DecompositionUnavailable):
        return _retag(alvo, definition, DecompositionFailureReason.TARGET_UNAVAILABLE)
    de_alvo, para_alvo = alvo

    if de_alvo.currency != para_alvo.currency:
        return _mixed_currency(
            definition, entity_id, period_from, period_to, as_of,
            de_alvo.currency, para_alvo.currency,
        )

    contribuicoes: list[Contribution] = []
    fidelidades: list[Fidelity] = [de_alvo.fidelity, para_alvo.fidelity]

    for membro in membros:
        de = engine.resolve_member(
            definition.target_concept, membro,
            entity_id=entity_id, period_end=period_from, scope=scope, as_of=as_of,
        )
        para = engine.resolve_member(
            definition.target_concept, membro,
            entity_id=entity_id, period_end=period_to, scope=scope, as_of=as_of,
        )
        faltou_de = isinstance(de, ConceptUnavailable)
        faltou_para = isinstance(para, ConceptUnavailable)

        if faltou_de and faltou_para:
            return DecompositionUnavailable(
                reason=DecompositionFailureReason.MEMBER_UNAVAILABLE,
                message=f"membro {membro.member_id!r} indisponivel nos dois periodos: {de.message}",
                decomposition_id=definition.ref,
                axis=definition.axis,
                member_id=membro.member_id,
                entity_id=entity_id,
                period_from=period_from,
                period_to=period_to,
                as_of=as_of,
            )
        if faltou_de or faltou_para:
            ausente = period_from if faltou_de else period_to
            return DecompositionUnavailable(
                reason=DecompositionFailureReason.MEMBER_ONLY_IN_ONE_PERIOD,
                message=(
                    f"membro {membro.member_id!r} nao existe em {ausente}. Segmento novo "
                    "ou encerrado: tratar a ausencia como zero atribuiria o valor "
                    "inteiro do outro periodo como contribuicao."
                ),
                decomposition_id=definition.ref,
                axis=definition.axis,
                member_id=membro.member_id,
                entity_id=entity_id,
                period_from=period_from,
                period_to=period_to,
                as_of=as_of,
            )

        delta = para.value - de.value
        contribuicoes.append(
            Contribution(
                member_id=membro.member_id,
                member_label=membro.label,
                sign=1,
                value_from=de.value,
                value_to=para.value,
                delta=delta,
                contribution=delta,
                fidelity=weakest([de.fidelity, para.fidelity]),
                inputs=tuple(de.inputs) + tuple(para.inputs),
            )
        )
        fidelidades.extend([de.fidelity, para.fidelity])

    target_delta = para_alvo.value - de_alvo.value
    soma = sum((c.contribution for c in contribuicoes), Decimal(0))
    residual = target_delta - soma

    with localcontext() as contexto:
        contexto.prec = SHARE_PRECISION
        shares = [(c, _share(c.contribution, target_delta)) for c in contribuicoes]
        residual_share = _share(residual, target_delta)
        delta_pct = (target_delta / abs(de_alvo.value)) if de_alvo.value != 0 else None

    return DecompositionResult(
        decomposition_id=definition.decomposition_id,
        decomposition_version=definition.version,
        axis=definition.axis,
        target_id=definition.target_concept,
        target_label=definition.target_label,
        entity_id=entity_id,
        scope=scope,
        period_type=de_alvo.period_type,
        period_from=period_from,
        period_to=period_to,
        as_of=as_of,
        target_from=de_alvo.value,
        target_to=para_alvo.value,
        target_delta=target_delta,
        target_delta_pct=delta_pct,
        contributions=tuple(
            c.model_copy(update={"share": share}) for c, share in shares
        ),
        residual=residual,
        residual_share=residual_share,
        closes=abs(residual) <= definition.tolerance_abs,
        tolerance_abs=definition.tolerance_abs,
        currency=str(de_alvo.currency),
        fidelity=weakest(fidelidades),
        knowledge_date=max(de_alvo.knowledge_date, para_alvo.knowledge_date),
        mapping_sha256=chain.sha256,
    )


def _share(parte: Decimal, total: Decimal) -> Decimal | None:
    """Fracao da variacao, ou `None` quando nao ha variacao para repartir.

    Divisao por zero NAO vira infinito, nem 0%, nem 100%: vira ausencia
    declarada. Um total que nao mudou nao tem como ser repartido, e imprimir
    "0%" ali sugeriria que as partes tambem nao mudaram - quando elas podem
    ter se cancelado exatamente, que e a coisa mais interessante que poderia
    ter acontecido.

    O zero e normalizado para positivo porque `Decimal(0) / Decimal(-1)` e
    `-0`, que sai na tela como "-0.0%" e faz o leitor procurar um sinal que
    nao existe.
    """
    if total == 0:
        return None
    resultado = parte / total
    return Decimal(0) if resultado == 0 else resultado


# ---------------------------------------------------------------------------
# Resolucao dos dois periodos
# ---------------------------------------------------------------------------


def _pair(
    engine: Engine,
    concept_id: str,
    *,
    entity_id: str,
    period_from: date,
    period_to: date,
    scope: ReportingScope,
    as_of: date,
):
    """Resolve um conceito nos dois periodos, ou recusa nomeando o que faltou.

    Um membro presente so num dos lados vira `MEMBER_ONLY_IN_ONE_PERIOD`, e
    nao zero do outro lado - a distincao entre 'a companhia parou de reportar'
    e 'a companhia reportou zero' e a diferenca entre uma mudanca de
    apresentacao e um driver real.
    """
    de = engine.resolve_concept(
        concept_id,
        entity_id=entity_id,
        period_end=period_from,
        scope=scope,
        as_of=as_of,
    )
    para = engine.resolve_concept(
        concept_id,
        entity_id=entity_id,
        period_end=period_to,
        scope=scope,
        as_of=as_of,
    )

    faltou_de = isinstance(de, ConceptUnavailable)
    faltou_para = isinstance(para, ConceptUnavailable)

    if faltou_de and faltou_para:
        return DecompositionUnavailable(
            reason=DecompositionFailureReason.MEMBER_UNAVAILABLE,
            message=f"conceito {concept_id!r} indisponivel nos dois periodos: {de.message}",
            member_id=concept_id,
            entity_id=entity_id,
            period_from=period_from,
            period_to=period_to,
            as_of=as_of,
            remedy=de.remedy,
        )
    if faltou_de or faltou_para:
        ausente = period_from if faltou_de else period_to
        presente = period_to if faltou_de else period_from
        indisponivel = de if faltou_de else para
        return DecompositionUnavailable(
            reason=DecompositionFailureReason.MEMBER_ONLY_IN_ONE_PERIOD,
            message=(
                f"conceito {concept_id!r} existe em {presente} e nao em {ausente} "
                f"({indisponivel.message}). Tratar a ausencia como zero atribuiria o "
                "valor inteiro do outro periodo como contribuicao - um driver "
                "fabricado, com cara de medido."
            ),
            member_id=concept_id,
            entity_id=entity_id,
            period_from=period_from,
            period_to=period_to,
            as_of=as_of,
            remedy=(
                "Se a companhia mudou de apresentacao, a comparacao precisa de outra "
                "decomposicao - nao de um zero."
            ),
        )
    return de, para


# ---------------------------------------------------------------------------
# Recusas
# ---------------------------------------------------------------------------


def _no_source(
    definition: DecompositionDefinition, entity_id: str, as_of: date
) -> DecompositionUnavailable:
    return DecompositionUnavailable(
        reason=DecompositionFailureReason.NO_BREAKDOWN_SOURCE,
        message=(
            f"o eixo {definition.axis} nao tem fonte estruturada neste regime. O plano "
            "padronizado da CVM nao publica esta dimensao, e a nota explicativa que a "
            "contem e PDF."
        ),
        decomposition_id=definition.ref,
        axis=definition.axis,
        entity_id=entity_id,
        as_of=as_of,
        remedy=(
            "Nao ha como derivar isto do que esta no gold hoje. Extrair de PDF exigiria "
            "reconstruir a tabela a partir de texto achatado, que e deducao por formato - "
            "o mesmo erro de casar conta por rotulo. Precisa de fonte estruturada."
        ),
    )


def _mixed_currency(
    definition: DecompositionDefinition,
    entity_id: str,
    period_from: date,
    period_to: date,
    as_of: date,
    primeira: str,
    segunda: str,
) -> DecompositionUnavailable:
    return _fail(
        definition,
        DecompositionFailureReason.CURRENCY_MISMATCH,
        f"insumos em moedas diferentes ({primeira} e {segunda}); moeda nunca e "
        "convertida implicitamente",
        entity_id=entity_id,
        period_from=period_from,
        period_to=period_to,
        as_of=as_of,
    )


def _fail(
    definition: DecompositionDefinition,
    reason: DecompositionFailureReason,
    message: str,
    **kwargs,
) -> DecompositionUnavailable:
    return DecompositionUnavailable(
        reason=reason,
        message=message,
        decomposition_id=definition.ref,
        axis=definition.axis,
        **kwargs,
    )


def _retag(
    unavailable: DecompositionUnavailable,
    definition: DecompositionDefinition,
    reason: DecompositionFailureReason,
) -> DecompositionUnavailable:
    """Carimba a recusa com a decomposicao que a provocou, sem perder o motivo."""
    return unavailable.model_copy(
        update={
            "reason": reason,
            "decomposition_id": definition.ref,
            "axis": definition.axis,
        }
    )

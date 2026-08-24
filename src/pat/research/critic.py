"""O critic mecanico: metade do critic NAO e um modelo.

Este modulo confere o que da para conferir por maquina, com taxonomia fechada.
Ele nao escreve prosa, nao corrige, nao reescreve e nao chama o escritor de
novo.

Sem loop
--------
Nao ha `writer -> critic -> writer`. Pela mesma razao que nao ha retentativa de
planejador: "criticar ate passar" e um amostrador que uma hora aprova algo
errado-mas-aprovado, e a procedencia passa a sub-reportar o que aconteceu.
Achado duro BLOQUEIA; achado leve ACOMPANHA a resposta, visivel. Um relatorio
que carrega a ressalva e mais util do que um relatorio limpo por reescrita.

O que e duro e o que e leve
---------------------------
Duro e o que torna a resposta ERRADA ou nao-auditavel: citacao que nao bate com
os bytes, digito fora de citacao, evidencia posterior ao `as_of`, afirmacao sem
suporte, numero de emissor usado como insumo. Leve e o que a torna INCOMPLETA
mas ainda verdadeira: fidelidade aproximada nao mencionada, resultado calculado
que ninguem citou.

A distincao nao e de gosto. Ela responde "esta resposta pode sair?", e a
resposta certa para um numero que nao resolve ate o byte e nao.
"""

from __future__ import annotations

import re

from pat.contracts.claims import (
    ClaimGraph,
    ClaimKind,
    CriticFinding,
    CriticReport,
    MechanicalFinding,
    Severity,
)
from pat.contracts.program import ProgramResult

__all__ = ["DIGIT", "review"]

DIGIT = re.compile(r"\d")

_TOKEN = re.compile(r"\{\{[sp]:[a-z0-9_]+\}\}")


def review(
    graph: ClaimGraph,
    *,
    result: ProgramResult,
    prose_blocks: tuple[tuple[str, str], ...] = (),
    values: dict[str, str] | None = None,
    unit_texts: dict[str, str] | None = None,
    warnings: tuple[str, ...] = (),
) -> CriticReport:
    """Confere o grafo e a prosa contra o que foi de fato executado.

    `prose_blocks` sao pares (claim_id_ou_vazio, texto) na ordem do relatorio.
    `unit_texts` mapeia `unit_id` para o texto GRAVADO da unidade - e o que
    permite conferir que uma citacao e verbatim sem reabrir o PDF.
    """
    valores = values or {}
    textos = unit_texts or {}
    achados: list[CriticFinding] = []

    achados.extend(_check_quotes(graph, textos))
    achados.extend(_check_point_in_time(graph, result))
    achados.extend(_check_laundering(graph))
    achados.extend(_check_support(graph))
    achados.extend(_check_prose(prose_blocks, valores))
    achados.extend(_check_disclosure(graph, warnings))
    achados.extend(_check_orphans(graph, result))

    return CriticReport(findings=tuple(achados), checked_claims=len(graph.nodes))


# ---------------------------------------------------------------------------
# Duros
# ---------------------------------------------------------------------------


def _check_quotes(graph: ClaimGraph, unit_texts: dict[str, str]) -> list[CriticFinding]:
    """Toda citacao e byte-identica a unidade que ela declara citar.

    Nao "essencialmente igual", nao normalizada, nao aparada. Citacao
    parafraseada nao e citacao, e uma que ninguem confere e so uma frase entre
    aspas.
    """
    achados = []
    for no in graph.of_kind(ClaimKind.QUOTE):
        gravado = unit_texts.get(no.unit_id or "")
        if gravado is None:
            achados.append(
                CriticFinding(
                    code=MechanicalFinding.QUOTE_NOT_VERBATIM,
                    severity=Severity.HARD,
                    message=(
                        f"a citacao aponta para a unidade {(no.unit_id or '')[:12]}, que "
                        "nao esta entre as unidades recuperadas nesta execucao"
                    ),
                    claim_id=no.claim_id,
                    remedy="Cite so trechos que a execucao recuperou.",
                )
            )
            continue
        if no.text != gravado:
            achados.append(
                CriticFinding(
                    code=MechanicalFinding.QUOTE_NOT_VERBATIM,
                    severity=Severity.HARD,
                    message=(
                        f"o texto citado difere da unidade {(no.unit_id or '')[:12]}: "
                        f"{len(no.text)} caracteres citados contra {len(gravado)} gravados"
                    ),
                    claim_id=no.claim_id,
                    remedy="Citacao e byte a byte. Nao apare, nao normalize, nao resuma.",
                )
            )
    return achados


def _check_point_in_time(graph: ClaimGraph, result: ProgramResult) -> list[CriticFinding]:
    """Nenhuma citacao vem de documento publicado depois do `as_of`.

    A terceira barreira contra o mesmo vazamento - depois do contrato da
    consulta e do contrato do resultado. Sao tres porque este e o erro que
    deixa a resposta MELHOR, e portanto o que ninguem percebe.
    """
    publicacao = {
        hit.quote.unit_id: hit.quote.published_at
        for outcome in result.evidence
        if outcome.result is not None
        for hit in outcome.result.hits
    }
    achados = []
    for no in graph.of_kind(ClaimKind.QUOTE):
        quando = publicacao.get(no.unit_id or "")
        if quando is not None and quando > result.as_of:
            achados.append(
                CriticFinding(
                    code=MechanicalFinding.EVIDENCE_AFTER_AS_OF,
                    severity=Severity.HARD,
                    message=(
                        f"documento publicado em {quando}, depois do as_of "
                        f"{result.as_of}"
                    ),
                    claim_id=no.claim_id,
                )
            )
    return achados


def _check_laundering(graph: ClaimGraph) -> list[CriticFinding]:
    """Numero de emissor nao vira insumo de conta.

    O contrato do grafo ja recusa `CALCULATION -> QUOTE` na construcao. Esta
    checagem existe porque um grafo pode chegar aqui montado por outro caminho
    - desserializado de um arquivo, por exemplo - e a barreira mais importante
    do sistema merece ser conferida onde a resposta sai, e nao so onde ela e
    montada.
    """
    por_id = {no.claim_id: no for no in graph.nodes}
    achados = []
    for no in graph.of_kind(ClaimKind.CALCULATION):
        for suporte in no.supports:
            alvo = por_id.get(suporte)
            if alvo is not None and alvo.kind is ClaimKind.QUOTE:
                achados.append(
                    CriticFinding(
                        code=MechanicalFinding.ISSUER_NUMBER_LAUNDERED,
                        severity=Severity.HARD,
                        message=(
                            f"o calculo {no.claim_id[:12]} se apoia na citacao "
                            f"{suporte[:12]}. Numero lido de documento e citacao, "
                            "nunca insumo de conta."
                        ),
                        claim_id=no.claim_id,
                    )
                )
    return achados


def _check_support(graph: ClaimGraph) -> list[CriticFinding]:
    """Toda leitura alcanca um no ancorado, e toda conclusao tem falsificador.

    O contrato ja garante as duas coisas. Repetir aqui e barato e cobre o
    grafo que chegou pronto de fora.
    """
    por_id = {no.claim_id: no for no in graph.nodes}
    achados = []
    for no in graph.nodes:
        if no.kind not in (ClaimKind.INFERENCE, ClaimKind.CONCLUSION):
            continue
        if not _alcanca_ancora(no, por_id):
            achados.append(
                CriticFinding(
                    code=MechanicalFinding.UNSUPPORTED_CLAIM,
                    severity=Severity.HARD,
                    message=f"{no.kind} sem caminho ate fato, calculo ou citacao",
                    claim_id=no.claim_id,
                )
            )
        if no.kind is ClaimKind.CONCLUSION and not no.falsified_by:
            achados.append(
                CriticFinding(
                    code=MechanicalFinding.CONCLUSION_WITHOUT_FALSIFIER,
                    severity=Severity.HARD,
                    message="conclusao sem o que a derrubaria",
                    claim_id=no.claim_id,
                )
            )
    return achados


def _alcanca_ancora(no, por_id) -> bool:
    vistos: set[str] = set()
    pilha = list(no.supports)
    while pilha:
        atual = pilha.pop()
        if atual in vistos or atual not in por_id:
            continue
        vistos.add(atual)
        alvo = por_id[atual]
        if alvo.is_grounded:
            return True
        pilha.extend(alvo.supports)
    return False


def _check_prose(
    prose_blocks: tuple[tuple[str, str], ...], values: dict[str, str]
) -> list[CriticFinding]:
    """A regra do digito, estendida com a excecao TIPADA da Fase 5.

    Digito e permitido dentro de bloco de citacao, e so la. Fora dele, um
    algarismo so pode chegar por substituicao de token - e um token que nao
    existe na tabela e recusado, porque nao ha o que substituir e a frase sairia
    com a chave literal.
    """
    achados = []
    for claim_id, texto in prose_blocks:
        if claim_id:
            continue  # bloco de citacao: conferido por `_check_quotes`
        sem_tokens = _TOKEN.sub("", texto)
        if DIGIT.search(sem_tokens):
            achados.append(
                CriticFinding(
                    code=MechanicalFinding.DIGIT_OUTSIDE_QUOTE,
                    severity=Severity.HARD,
                    message=(
                        "prosa com algarismo fora de citacao e fora de token: "
                        f"{sem_tokens.strip()[:120]!r}"
                    ),
                    remedy="Use o token do resultado; o sistema substitui depois.",
                )
            )
        for token in _TOKEN.findall(texto):
            if token not in values:
                achados.append(
                    CriticFinding(
                        code=MechanicalFinding.UNKNOWN_TOKEN,
                        severity=Severity.HARD,
                        message=f"token {token} nao existe na tabela de substituicao",
                        remedy="Use apenas os tokens listados; nao componha token novo.",
                    )
                )
    return achados


# ---------------------------------------------------------------------------
# Leves
# ---------------------------------------------------------------------------


def _check_disclosure(graph: ClaimGraph, warnings: tuple[str, ...]) -> list[CriticFinding]:
    """Fidelidade aproximada e residual material tem que estar ditos.

    Leve, e nao duro, porque o numero continua certo: o que falta e a ressalva.
    Bloquear seria tratar "incompleto" como "errado", e o usuario perderia uma
    resposta correta por causa de uma frase ausente.
    """
    achados = []
    dito = " ".join(no.text for no in graph.nodes).lower()
    for aviso in warnings:
        if "nao fecha" in aviso and "residual" not in dito and "explicad" not in dito:
            achados.append(
                CriticFinding(
                    code=MechanicalFinding.FIDELITY_UNDISCLOSED,
                    severity=Severity.SOFT,
                    message=f"a decomposicao nao fecha e o relatorio nao diz: {aviso}",
                    remedy="Mencione a parcela nao explicada.",
                )
            )
        if "binding" in aviso and "aproximad" not in dito and "fidelidade" not in dito:
            achados.append(
                CriticFinding(
                    code=MechanicalFinding.FIDELITY_UNDISCLOSED,
                    severity=Severity.SOFT,
                    message=f"resultado aproximado apresentado sem ressalva: {aviso}",
                    remedy="Diga que a metrica veio de binding aproximado.",
                )
            )
    return achados


def _check_orphans(graph: ClaimGraph, result: ProgramResult) -> list[CriticFinding]:
    """Resultado calculado que ninguem citou.

    Leve: nao torna a resposta errada, mas costuma indicar que a investigacao
    apurou algo e a redacao ignorou - inclusive quando o ignorado e o que
    contradiz a tese.
    """
    citados = {
        no.result_id for no in graph.nodes if no.result_id is not None
    } | {
        suporte for no in graph.nodes for suporte in no.supports
    }
    achados = []
    for computation in result.computations:
        claim = next(
            (no for no in graph.nodes if no.result_id == computation.result_id), None
        )
        if claim is None:
            continue
        usado = claim.claim_id in citados or any(
            claim.claim_id in no.supports for no in graph.nodes
        )
        if not usado:
            achados.append(
                CriticFinding(
                    code=MechanicalFinding.ORPHAN_RESULT,
                    severity=Severity.SOFT,
                    message=(
                        f"o passo {computation.step_id} foi calculado e nenhuma leitura "
                        "se apoia nele"
                    ),
                    claim_id=claim.claim_id,
                )
            )
    return achados

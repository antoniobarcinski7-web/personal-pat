"""Executa um `ResearchProgram`. Deterministico, sem modelo nenhum.

Mesma disciplina da M5.1 e da Fase 3: o caminho sem LLM roda inteiro e e o
default. Um programa lido de arquivo produz numeros, decomposicoes e citacoes
sem que nenhuma credencial exista no ambiente - e e isso que prova que o resto
nao depende do modelo.

As tres pontas, e a ordem entre elas
------------------------------------
    compute          metricas e derivacoes     (Fase 3, intacta)
    decompositions   de onde a variacao veio   (M5.2)
    evidence         o que foi dito            (M5.1)

A ordem importa por uma razao de desenho, e nao de implementacao: a
decomposicao roda ANTES da busca de evidencia porque e ela que diz o que vale
procurar. Um sistema que buscasse primeiro estaria escolhendo a citacao e
depois procurando o numero que a sustenta - que e como se produz uma narrativa
convincente e errada.

Falha parcial nao aborta
------------------------
Um pedido que falha vira recusa NOMEADA e o programa continua. Isso e
deliberado: numa investigacao, saber que a decomposicao fechou mas o corpus
nao tem nada sobre o assunto e um resultado util, e abortar tudo por causa de
uma busca vazia jogaria fora o que ja foi apurado. O que nao pode e a falha
sumir - toda recusa entra em `ProgramResult` com motivo.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb

from pat.contracts.corpus import EvidenceQuery, EvidenceUnavailable
from pat.contracts.decomposition import DecompositionUnavailable
from pat.contracts.program import (
    DecompositionOutcome,
    EvidenceOutcome,
    ProgramResult,
    ResearchProgram,
    ResultShape,
)
from pat.contracts.research import (
    ComputationFailure,
    ComputationResult,
    ResearchQuestion,
)
from pat.corpus.index import INDEX_VERSION
from pat.corpus.retrieve import retrieve
from pat.research.decompose import decompose
from pat.semantics.engine import Engine
from pat.research.shape import (
    shape_of_computation,
    shape_of_computation_failure,
    shape_of_decomposition,
    shape_of_evidence,
)

__all__ = ["run_program"]


def run_program(
    conn: duckdb.DuckDBPyConnection,
    program: ResearchProgram,
    *,
    engine: Engine,
    question: ResearchQuestion,
    program_id: str,
    capability_sha256: str,
) -> ProgramResult:
    """Programa certificado -> resultados. Nenhuma chamada de modelo.

    O motor chega por INJECAO, e nao e construido aqui. E a mesma regra de
    `execute_plan`: nenhum modulo de L3 escolhe sozinho contra que warehouse,
    que mapeamento ou que fonte o sistema fala - quem monta essa ligacao e a
    raiz de composicao. `conn` continua entrando porque o corpus mora no mesmo
    banco e a busca de evidencia le dali.

    `program_id` e `capability_sha256` tambem chegam prontos: quem executa nao
    calcula a propria identidade nem o retrato do sistema.
    """
    computations: tuple[ComputationResult, ...] = ()
    computation_failures: tuple[ComputationFailure, ...] = ()
    shapes: list[ResultShape] = []

    if program.compute is not None and program.compute.steps:
        computations, computation_failures = _run_compute(
            program, question=question, engine=engine
        )
        shapes.extend(shape_of_computation(result) for result in computations)
        shapes.extend(shape_of_computation_failure(failure) for failure in computation_failures)

    # Decomposicao ANTES da evidencia: e ela que diz o que vale procurar.
    decomposition_outcomes: list[DecompositionOutcome] = []
    for pedido in program.decompositions:
        resultado = decompose(
            engine,
            pedido.decomposition,
            entity_id=pedido.entity_id,
            period_from=pedido.period_from,
            period_to=pedido.period_to,
            scope=program.scope,
            as_of=program.as_of,
        )
        outcome = (
            DecompositionOutcome(request_id=pedido.request_id, unavailable=resultado)
            if isinstance(resultado, DecompositionUnavailable)
            else DecompositionOutcome(request_id=pedido.request_id, result=resultado)
        )
        decomposition_outcomes.append(outcome)
        shapes.append(shape_of_decomposition(outcome))

    evidence_outcomes: list[EvidenceOutcome] = []
    for pedido in program.evidence:
        resultado = _retrieve(conn, program, pedido)
        outcome = (
            EvidenceOutcome(request_id=pedido.request_id, unavailable=resultado)
            if isinstance(resultado, EvidenceUnavailable)
            else EvidenceOutcome(request_id=pedido.request_id, result=resultado)
        )
        evidence_outcomes.append(outcome)
        shapes.append(shape_of_evidence(outcome))

    return ProgramResult(
        program_id=program_id,
        question_id=program.question_id,
        as_of=program.as_of,
        executed_at=datetime.now(UTC),
        computations=computations,
        computation_failures=computation_failures,
        decompositions=tuple(decomposition_outcomes),
        evidence=tuple(evidence_outcomes),
        shapes=tuple(shapes),
        capability_sha256=capability_sha256,
        # `None` quando nao houve busca: gravar a versao de um indice que nao
        # foi consultado sugeriria que ele participou do resultado.
        index_version=INDEX_VERSION if program.evidence else None,
    )


def _run_compute(
    program: ResearchProgram,
    *,
    question: ResearchQuestion,
    engine: Engine,
) -> tuple[tuple[ComputationResult, ...], tuple[ComputationFailure, ...]]:
    """Delega ao executor da Fase 3, sem reimplementar nada.

    O plano ja foi validado junto do programa; aqui ele so e certificado e
    executado pelo mesmo caminho de sempre. Uma segunda implementacao da
    execucao de plano divergiria da primeira, normalmente na direcao de a mais
    permissiva deixar passar o que a outra recusaria.
    """
    from pat.research.execute import execute_plan
    from pat.research.validate import certify

    validated = certify(program.compute, question, violations=(), issues=())
    outcome = execute_plan(validated, engine=engine)
    return outcome.results, outcome.failures


def _retrieve(
    conn: duckdb.DuckDBPyConnection, program: ResearchProgram, pedido
) -> object:
    """Monta a consulta com o `as_of` DO PROGRAMA e busca.

    O pedido nao tem `as_of` proprio, e por isso nao ha como uma parte da
    resposta olhar mais longe que a outra. A consulta e montada aqui, num
    lugar so.
    """
    try:
        query = EvidenceQuery(
            entity_id=pedido.entity_id,
            terms=pedido.terms,
            as_of=program.as_of,
            kinds=pedido.kinds,
            published_from=pedido.published_from,
            published_to=pedido.published_to,
            limit=pedido.limit,
        )
    except ValueError as exc:
        from pat.contracts.corpus import EvidenceUnavailableReason

        return EvidenceUnavailable(
            reason=EvidenceUnavailableReason.NO_MATCH,
            message=f"pedido {pedido.request_id} nao vira consulta valida: {exc}",
            entity_id=pedido.entity_id,
            as_of=program.as_of,
        )
    return retrieve(conn, query)

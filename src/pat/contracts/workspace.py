"""O Company Research Workspace (Fase 5, M5.5): uma empresa, e o que se sabe
sobre ela.

Nada neste modulo importa implementacao.

O que um workspace e
--------------------
O objeto que responde "posso pesquisar esta empresa a fundo?" - e responde com
criterios OBJETIVOS, nao com uma impressao. Ele junta as duas metades:

    quantitativo   periodos no gold, mapeamento conferido, metricas resolviveis
    qualitativo    documentos indexados, e o que NAO foi extraido

`READY` nao e um adjetivo: e a conjuncao de requisitos conferiveis, cada um com
nome proprio. Um workspace que se declarasse pronto por sentimento seria a
mesma classe de erro que um numero aproximado que se apresenta como exato.

Por que a cobertura carrega o que FALTA
---------------------------------------
`missing_concepts` e `extraction_failures` sao campos de primeira classe, e nao
notas de rodape. Cobertura que so mostra o que existe mente sobre si mesma - e
a mentira aparece como ausencia de evidencia, que e exatamente o que um analista
leria como evidencia de ausencia.

O hash do workspace
-------------------
`workspace_sha256` cobre a cadeia de mapeamento, o conjunto de documentos, a
versao do indice e as versoes de metrica. Ele entra em toda resposta pela mesma
razao que `mapping_sha256` entra num `MetricResult`: e o que distingue "a
resposta mudou" de "os dados mudaram".
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import AwareDatetime, Frozen, Sha256


class WorkspaceState(StrEnum):
    DRAFT = "draft"
    """Existe, e incompleto. Responde o que der, e diz o que falta."""

    READY = "ready"
    """Todos os requisitos objetivos satisfeitos. So aqui uma sintese sobre a
    empresa e defensavel."""


class ReadinessCode(StrEnum):
    """Por que um workspace ainda nao esta pronto. Conjunto fechado.

    Cada codigo tem um remedio de uma linha, e e por isso que eles sao
    especificos: "faltam dados" nao diz a ninguem o que fazer na segunda-feira.
    """

    NO_ENTITY = "no_entity"
    NO_FACTS = "no_facts"
    TOO_FEW_PERIODS = "too_few_periods"
    """Menos de dois periodos: sem dois, nao ha variacao, e sem variacao nao ha
    pergunta causal a fazer."""

    NO_CONFIRMED_MAPPING = "no_confirmed_mapping"
    """Caiu na familia default. Explorar assim e razoavel; declarar a empresa
    pronta para pesquisa profunda, nao."""

    MISSING_DECOMPOSITION_CONCEPTS = "missing_decomposition_concepts"
    NO_DOCUMENTS = "no_documents"
    NO_UNITS_INDEXED = "no_units_indexed"


class ReadinessGap(Frozen):
    code: ReadinessCode
    message: str = Field(min_length=1)
    remedy: str = Field(min_length=1)
    detail: tuple[str, ...] = ()


class QuantitativeCoverage(Frozen):
    facts: int = Field(ge=0)
    period_ends: tuple[date, ...] = ()
    scopes: tuple[str, ...] = ()
    mapping_id: str | None = None
    mapping_sha256: Sha256 | None = None
    mapping_confirmed: bool = False
    weakest_fidelity: str | None = None
    metrics_available: tuple[str, ...] = ()
    decompositions_available: tuple[str, ...] = ()
    missing_concepts: tuple[str, ...] = ()
    """Conceitos que alguma decomposicao registrada exige e o mapeamento nao
    liga. Campo de primeira classe: e a lista de trabalho de quem for tornar a
    empresa pesquisavel."""


class QualitativeCoverage(Frozen):
    documents: int = Field(ge=0)
    kinds: tuple[tuple[str, int], ...] = ()
    published_from: date | None = None
    published_to: date | None = None
    units_indexed: int = Field(default=0, ge=0)
    index_version: str | None = None
    extraction_failures: tuple[tuple[str, str], ...] = ()
    """(document_id, motivo). Sempre listadas. Um corpus que esconde o que nao
    conseguiu ler mente sobre a propria cobertura."""


class CompanyWorkspace(Frozen):
    """Uma empresa, com o que se sabe e o que falta saber."""

    workspace_version: Literal["v1"] = "v1"
    entity_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    cod_cvm: int | None = None
    jurisdiction: str = Field(min_length=2)

    state: WorkspaceState
    gaps: tuple[ReadinessGap, ...] = ()

    quantitative: QuantitativeCoverage
    qualitative: QualitativeCoverage

    workspace_sha256: Sha256
    built_at: AwareDatetime
    as_of: date | None = None

    @model_validator(mode="after")
    def _check(self) -> "CompanyWorkspace":
        if self.state is WorkspaceState.READY and self.gaps:
            raise ValueError(
                f"{self.entity_id}: READY com {len(self.gaps)} pendencia(s). "
                "Prontidao e a conjuncao dos requisitos, e nao um adjetivo - um "
                "workspace nao pode se declarar pronto e listar o que falta."
            )
        if self.state is WorkspaceState.DRAFT and not self.gaps:
            raise ValueError(
                f"{self.entity_id}: DRAFT sem nenhuma pendencia declarada. Se nada "
                "falta, o estado e READY; se algo falta, tem que ter nome."
            )
        return self

    @property
    def is_ready(self) -> bool:
        return self.state is WorkspaceState.READY

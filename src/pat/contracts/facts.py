"""O contrato central da camada analitica: um fato numerico bitemporal.

Nao ha implementacao de gold na Fase 0. O contrato existe desde ja porque e
o alvo para o qual as camadas de ingestao e parsing trabalham, e porque a
decisao bitemporal precisa estar fixada antes de qualquer extracao.

Os dois eixos de tempo
----------------------
`period_end`     - a que periodo o numero se refere (ex. 4T24).
`knowledge_date` - quando o numero se tornou publicamente conhecido.

Consultas sao sempre feitas AS OF uma data de conhecimento. Sem esse segundo
eixo, qualquer backtest usa numeros reapresentados e mente sobre o passado.

Um mesmo (entity_id, metric, period_end) pode ter varios fatos com
knowledge_dates diferentes. Eles nao se sobrescrevem: o valor original e a
reapresentacao coexistem, e a diferenca entre eles e informacao.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field, model_validator

from pat.contracts.common import Frozen, PeriodType
from pat.contracts.lineage import Lineage


class Fact(Frozen):
    fact_id: str

    entity_id: str
    metric: str = Field(description="Nome canonico registrado em `semantics`")
    metric_version: str = Field(
        description="Definicao da metrica muda => nova versao. Analises antigas "
        "continuam reproduzindo com a versao com que foram feitas."
    )

    value: Decimal = Field(description="Decimal, nunca float: dinheiro nao e binario")
    unit: str = Field(description="Ex. 'BRL', 'BRL_thousand', 'ratio', 'percent', 'shares'")
    currency: str | None = Field(default=None, description="ISO 4217, quando aplicavel")

    period_type: PeriodType
    period_start: date | None = Field(default=None, description="Nulo se period_type=INSTANT")
    period_end: date

    knowledge_date: date = Field(
        description="Data em que o valor se tornou publico. Limitada por cima "
        "pelo retrieved_at do documento de origem."
    )

    is_restatement: bool = Field(
        default=False,
        description="True quando existe fato anterior para a mesma chave com "
        "knowledge_date menor e valor diferente.",
    )
    consolidated: bool | None = Field(
        default=None, description="Consolidado vs. individual (CVM: CONSOLIDADO/INDIVIDUAL)"
    )

    lineage: Lineage

    @model_validator(mode="after")
    def _check_period(self) -> "Fact":
        if self.period_type is PeriodType.INSTANT:
            if self.period_start is not None:
                raise ValueError("period_start deve ser nulo quando period_type=INSTANT")
        elif self.period_start is None:
            raise ValueError(f"period_start obrigatorio para period_type={self.period_type}")
        elif self.period_start > self.period_end:
            raise ValueError("period_start posterior a period_end")

        if self.knowledge_date < self.period_end:
            raise ValueError(
                "knowledge_date anterior ao fim do periodo: um numero nao pode "
                "ser conhecido antes de existir"
            )
        return self

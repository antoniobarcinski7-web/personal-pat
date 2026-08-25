"""`FactResolver` da DFP da CVM.

Este arquivo e a fronteira. Acima dele so existem conceitos e metricas
universais; abaixo, `cd_conta`, `cod_cvm` e ORDEM_EXERC. E o unico modulo da
Fase 2 que importa `pat.query.asof`.

Duas traducoes acontecem aqui, e so aqui:

    ReportingScope.CONSOLIDATED -> consolidated=True   (a "DF Consolidada")
    ReportingScope.PARENT_ONLY  -> consolidated=False  (a "DF Individual")

    PeriodKind.FLOW  -> espera PeriodType.YEAR     (a DFP e anual por construcao)
    PeriodKind.STOCK -> espera PeriodType.INSTANT  (contas patrimoniais)

A verificacao de `period_type` nao e redundante com a demonstracao escolhida:
um mapeamento que apontasse um conceito de fluxo para uma conta do balanco
compilaria, resolveria e devolveria um saldo pontual como se fosse fluxo
anual. Aqui isso vira WRONG_PERIOD_TYPE em vez de numero errado.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    LineAddress,
    PeriodKind,
    ReportingScope,
    TaxonomyId,
    UnavailableReason,
)
from pat.query.asof import AsOf
from pat.semantics.frameworks.cvm_dfp.adapter import parts_of
from pat.semantics.resolver import ResolutionFailure, ResolvedFact

_EXPECTED_PERIOD_TYPE = {
    PeriodKind.FLOW: PeriodType.YEAR,
    PeriodKind.STOCK: PeriodType.INSTANT,
}


class CvmDfpResolver:
    taxonomy: ClassVar[TaxonomyId] = TaxonomyId.CVM_PLANO_PADRONIZADO

    def __init__(self, asof: AsOf) -> None:
        self._asof = asof
        self._entities: dict[str, object] = {}

    def _entity(self, entity_id: str):
        if entity_id not in self._entities:
            self._entities[entity_id] = self._asof.entity(entity_id)
        return self._entities[entity_id]

    def _has_any_fact(self, entity_id: str) -> bool:
        return bool(
            self._asof.conn.execute(
                "SELECT 1 FROM gold_fact WHERE entity_id = ? LIMIT 1", [entity_id]
            ).fetchone()
        )

    def entity_display(self, entity_id: str) -> tuple[str | None, tuple[tuple[str, str], ...]]:
        """Nome e identificadores LOCAIS, que e o que este adapter pode dizer.

        Continua devolvendo `cod_cvm` porque este e o adapter da CVM - aqui
        identificador de regime e o assunto. O que mudou na M5.6 e a origem:
        ele vem da tabela `entity`, e nao de uma coluna do fato.
        """
        ref = self._entity(entity_id)
        if ref is None:
            return None, ()
        locais = tuple(
            (linha[0], linha[1])
            for linha in self._asof.conn.execute(
                "SELECT scheme, local_id FROM entity WHERE entity_id = ? ORDER BY scheme",
                [entity_id],
            ).fetchall()
        )
        return ref.display_name, locais

    def resolve(
        self,
        *,
        entity_id: str,
        address: LineAddress,
        period_end: date,
        period_kind: PeriodKind,
        scope: ReportingScope,
        as_of: date,
    ) -> ResolvedFact | ResolutionFailure:
        # O nome e BEST-EFFORT e nao participa da resolucao. Fazer a busca de
        # um fato depender da tabela de nomes seria acoplar dado a rotulo: o
        # fato existe tendo ou nao alguem registrado como a empresa se chama.
        ref = self._entity(entity_id)

        statement, cd_conta, coluna_df = parts_of(address)

        view = self._asof.value(
            entity_id=entity_id,
            cd_conta=cd_conta,
            period_end=period_end,
            as_of=as_of,
            statement=statement,
            consolidated=scope is ReportingScope.CONSOLIDATED,
            coluna_df=coluna_df,
        )
        if view is None:
            escopo = "consolidado" if scope is ReportingScope.CONSOLIDATED else "individual"
            # Duas ausencias diferentes, e a mensagem diz qual. Ate a M5.6 as
            # duas saiam iguais, e "a empresa nao existe" se confundia com
            # "esta linha nao foi publicada neste periodo" - remedios opostos.
            if not self._has_any_fact(entity_id):
                return ResolutionFailure(
                    UnavailableReason.UNKNOWN_ENTITY,
                    f"nenhum fato no gold para entity_id={entity_id}. "
                    "Rode `pat build cvm.dfp` para esta companhia.",
                )
            nome = ref.display_name if ref else entity_id
            return ResolutionFailure(
                UnavailableReason.MISSING_FACT_AS_OF,
                f"nada conhecido em {as_of} para {statement}/{cd_conta} "
                f"({escopo}) de {nome} no exercicio findo em {period_end}",
            )

        expected = _EXPECTED_PERIOD_TYPE[period_kind]
        if view.period_type != expected:
            return ResolutionFailure(
                UnavailableReason.WRONG_PERIOD_TYPE,
                f"{statement}/{cd_conta} e {view.period_type}, mas o conceito exige "
                f"{expected}: o mapeamento ligou um conceito de "
                f"{'fluxo' if period_kind is PeriodKind.FLOW else 'saldo'} a uma conta "
                f"do tipo errado",
            )

        return ResolvedFact(
            value=view.value,
            currency=view.currency or view.unit,
            period_type=PeriodType(view.period_type),
            period_start=view.period_start,
            period_end=view.period_end,
            knowledge_date=view.knowledge_date,
            fact_id=view.fact_id,
            locator=view.locator,
            label_as_reported=view.ds_conta,
        )

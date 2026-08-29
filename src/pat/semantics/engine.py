"""O motor: resolve conceitos, executa o grafo, monta o resultado.

Nao importa `pat.query`, `pat.store` nem nada sob `frameworks/`. Fala com o
mundo por dois objetos injetados: um conjunto de `FactResolver` e um
`MappingSet`. E o que torna o layout "Brasil primeiro, nao Brasil apenas"
verdadeiro em codigo, e nao so em prosa.

Invariantes que o motor mantem
------------------------------
- `entity_id`, `scope`, `period_end` e `as_of` sao fixados na chamada raiz e
  descem inalterados por todo o grafo. Nao existe aresta onde possam variar,
  logo nao existe metrica que misture escopo ou data de conhecimento.
- `knowledge_date` do resultado e o maximo dos insumos: uma metrica so e
  conhecida quando o seu ultimo ingrediente ficou publico.
- `fidelity` do resultado e a mais fraca da cadeia inteira.
- Insumo ausente nunca vira zero. Vira `MetricUnavailable` com motivo.
- Moeda nunca e convertida. Moedas diferentes entre insumos param o calculo.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from datetime import date
from decimal import Decimal

from pat.contracts.common import PeriodType
from pat.contracts.semantics import (
    CheckOutcome,
    ConceptUnavailable,
    Dimension,
    Fidelity,
    InputRef,
    MetricRef,
    MetricResult,
    MetricUnavailable,
    ReportingScope,
    TaxonomyId,
    UnavailableReason,
    weakest,
)
from pat.semantics import concepts
from pat.semantics.loader import MappingChain, MappingSet
from pat.semantics.registry import (
    ComputationUnavailable,
    ConceptValue,
    Inputs,
    MetricRegistry,
)
from pat.semantics.resolver import FactResolver, ResolutionFailure


class _ConceptFailure:
    __slots__ = ("reason", "message", "concept_id", "tried", "remedy")

    def __init__(
        self,
        reason: UnavailableReason,
        message: str,
        concept_id: str,
        tried: tuple[str, ...] = (),
        remedy: str | None = None,
    ) -> None:
        self.reason = reason
        self.message = message
        self.concept_id = concept_id
        self.tried = tried
        self.remedy = remedy


class Engine:
    def __init__(
        self,
        *,
        resolvers: MappingABC[TaxonomyId, FactResolver],
        mappings: MappingSet,
        registry: MetricRegistry,
        source: str,
        pat_version: str,
        git_sha: str | None = None,
    ) -> None:
        self._resolvers = dict(resolvers)
        self._mappings = mappings
        self._registry = registry
        self._source = source
        self._pat_version = pat_version
        self._git_sha = git_sha

    # -- API ----------------------------------------------------------------

    def resolver_for(self, taxonomy: TaxonomyId) -> FactResolver | None:
        """Resolver instalado para uma taxonomia. Usado pela conferencia de
        mapeamento, que precisa falar com a fonte sem passar por metrica."""
        return self._resolvers.get(taxonomy)

    def compute(
        self,
        metric: str | MetricRef,
        *,
        entity_id: str,
        period_end: date,
        scope: ReportingScope,
        as_of: date,
    ) -> MetricResult | MetricUnavailable:
        """Calcula uma metrica. `scope` e obrigatorio de proposito: um numero
        no escopo errado parece certo, e essa e a pior classe de erro aqui."""
        ref = MetricRef.parse(metric) if isinstance(metric, str) else metric
        cache: dict[str, MetricResult | MetricUnavailable] = {}
        return self._compute(
            ref, entity_id=entity_id, period_end=period_end, scope=scope, as_of=as_of, cache=cache
        )

    def resolve_concept(
        self,
        concept_id: str,
        *,
        entity_id: str,
        period_end: date,
        scope: ReportingScope,
        as_of: date,
    ) -> "ConceptValue | ConceptUnavailable":
        """Resolve UM conceito, sem passar por metrica.

        Existe para a decomposicao da Fase 5, que precisa dos termos de uma
        identidade contabil - e nao do resultado dela. `compute` nao serve:
        `gross_profit` e `cogs` sao conceitos, nao metricas registradas, e
        promove-los a metrica so para poder le-los criaria cinco definicoes
        cujo unico proposito seria contornar a falta desta porta.

        A resolucao em si e a mesma de `_compute` - a mesma cadeia de
        mapeamento, o mesmo resolver, o mesmo `as_of`. Duas implementacoes
        divergiriam, e a mais permissiva deixaria passar o que a outra
        recusaria.
        """
        chain = self._mappings.resolve(entity_id, source=self._source)
        if chain is None:
            return ConceptUnavailable(
                reason=UnavailableReason.NO_MAPPING,
                message=f"nenhum mapeamento cobre {entity_id} na fonte {self._source}",
                concept_id=concept_id,
                remedy=f"escreva src/pat/semantics/mappings/ para {entity_id}",
            )

        resolver = self._resolvers.get(chain.head.taxonomy)
        if resolver is None:
            return ConceptUnavailable(
                reason=UnavailableReason.NO_MAPPING,
                message=f"nenhum resolver registrado para a taxonomia {chain.head.taxonomy}",
                concept_id=concept_id,
            )

        outcome = self._resolve_concept(
            concept_id,
            chain=chain,
            resolver=resolver,
            entity_id=entity_id,
            period_end=period_end,
            scope=scope,
            as_of=as_of,
        )
        if isinstance(outcome, _ConceptFailure):
            return ConceptUnavailable(
                reason=outcome.reason,
                message=outcome.message,
                concept_id=outcome.concept_id,
                tried=outcome.tried,
                remedy=outcome.remedy,
            )
        return outcome

    def resolve_member(
        self,
        concept_id: str,
        member,
        *,
        entity_id: str,
        period_end: date,
        scope: ReportingScope,
        as_of: date,
    ) -> "ConceptValue | ConceptUnavailable":
        """Resolve um conceito RESTRITO a um membro dimensional declarado.

        Reusa o binding do conceito e acrescenta a dimensao ao endereco. Isso
        preserva a separacao que a Fase 2 estabeleceu: o CONCEITO continua
        universal, o ENDERECO continua opaco fora do adapter, e o MEMBRO e mais
        um componente do endereco - nao um eixo novo de significado.

        Nao ha abstracao dimensional nova aqui, e nao deveria haver: `member` e
        um par (eixo, membro) declarado pelo mapeamento da empresa, e o adapter
        do regime ja sabe montar um endereco com dimensao.
        """
        chain = self._mappings.resolve(entity_id, source=self._source)
        if chain is None:
            return ConceptUnavailable(
                reason=UnavailableReason.NO_MAPPING,
                message=f"nenhum mapeamento cobre {entity_id} na fonte {self._source}",
                concept_id=concept_id,
            )
        found = chain.binding_for(concept_id)
        if found is None:
            return ConceptUnavailable(
                reason=UnavailableReason.MISSING_CONCEPT,
                message=f"o mapeamento nao liga {concept_id!r} a nenhuma linha",
                concept_id=concept_id,
            )
        binding, _owner = found
        resolver = self._resolvers.get(chain.head.taxonomy)
        if resolver is None:
            return ConceptUnavailable(
                reason=UnavailableReason.NO_MAPPING,
                message=f"nenhum resolver para a taxonomia {chain.head.taxonomy}",
                concept_id=concept_id,
            )

        total = Decimal(0)
        refs: list[InputRef] = []
        moedas: set[str] = set()
        conhecidas: list[date] = []
        tipos: set[PeriodType] = set()
        inicios: set[date | None] = set()

        chave = member.source_key or f"{member.axis}={member.member_id};"
        for line in binding.lines:
            endereco = line.address
            outcome = resolver.resolve(
                entity_id=entity_id,
                address=endereco,
                period_end=period_end,
                period_kind=concepts.get(concept_id).period_kind,
                scope=scope,
                as_of=as_of,
                member=chave,
            )
            if isinstance(outcome, ResolutionFailure):
                return ConceptUnavailable(
                    reason=outcome.reason,
                    message=f"membro {member.member_id}: {outcome.message}",
                    concept_id=concept_id,
                    tried=(f"{endereco.as_str()} [{chave}]",),
                )
            total += Decimal(line.sign) * outcome.value
            moedas.add(outcome.currency)
            conhecidas.append(outcome.knowledge_date)
            tipos.add(outcome.period_type)
            inicios.add(outcome.period_start)
            refs.append(
                InputRef(
                    role=f"{concept_id}[{member.member_id}]",
                    value=outcome.value,
                    address=f"{endereco.as_str()} [{chave}]",
                    fact_id=outcome.fact_id,
                    knowledge_date=outcome.knowledge_date,
                    sign_applied=line.sign,
                    is_metric=False,
                    fidelity=binding.fidelity,
                )
            )

        if len(moedas) > 1:
            return ConceptUnavailable(
                reason=UnavailableReason.MIXED_CURRENCY,
                message=f"membro {member.member_id} em moedas diferentes: {sorted(moedas)}",
                concept_id=concept_id,
            )

        return ConceptValue(
            concept_id=concept_id,
            value=total,
            currency=next(iter(moedas)),
            fidelity=binding.fidelity,
            period_type=next(iter(tipos)),
            period_start=next(iter(inicios)),
            knowledge_date=max(conhecidas),
            inputs=tuple(refs),
        )

    def mapping_chain_for(self, entity_id: str):
        """A cadeia de mapeamento efetiva. A decomposicao precisa do sha256
        dela pelo mesmo motivo que `MetricResult` precisa: editar a familia
        muda o resultado sem tocar no arquivo da empresa."""
        return self._mappings.resolve(entity_id, source=self._source)

    # -- interno ------------------------------------------------------------

    def _unavailable(
        self,
        ref: MetricRef,
        *,
        reason: UnavailableReason,
        message: str,
        entity_id: str,
        period_end: date,
        scope: ReportingScope,
        as_of: date,
        concept_id: str | None = None,
        tried: tuple[str, ...] = (),
        remedy: str | None = None,
    ) -> MetricUnavailable:
        return MetricUnavailable(
            metric=ref.name,
            metric_version=ref.version,
            reason=reason,
            message=message,
            entity_id=entity_id,
            period_end=period_end,
            as_of=as_of,
            scope=scope,
            concept_id=concept_id,
            tried=tried,
            remedy=remedy,
        )

    def _compute(
        self,
        ref: MetricRef,
        *,
        entity_id: str,
        period_end: date,
        scope: ReportingScope,
        as_of: date,
        cache: dict[str, MetricResult | MetricUnavailable],
    ) -> MetricResult | MetricUnavailable:
        key = str(ref)
        if key in cache:
            return cache[key]

        metric = self._registry.get(ref)
        definition = metric.definition
        fail = lambda **kw: self._unavailable(  # noqa: E731 - fecha sobre a chave da consulta
            ref, entity_id=entity_id, period_end=period_end, scope=scope, as_of=as_of, **kw
        )

        chain = self._mappings.resolve(entity_id, source=self._source)
        if chain is None:
            result = fail(
                reason=UnavailableReason.NO_MAPPING,
                message=(
                    f"nenhum mapeamento cobre {entity_id} na fonte {self._source}. "
                    "Bancos e seguradoras caem aqui: o plano de contas deles e outro."
                ),
                remedy=f"escreva src/pat/semantics/mappings/ para {entity_id}",
            )
            cache[key] = result
            return result

        resolver = self._resolvers.get(chain.head.taxonomy)
        if resolver is None:
            result = fail(
                reason=UnavailableReason.NO_MAPPING,
                message=f"nenhum resolver registrado para a taxonomia {chain.head.taxonomy}",
            )
            cache[key] = result
            return result

        # -- dependencias de metrica, pinadas por versao --------------------
        dep_results: dict[str, MetricResult] = {}
        for dep in definition.requires_metrics:
            dep_result = self._compute(
                dep,
                entity_id=entity_id,
                period_end=period_end,
                scope=scope,
                as_of=as_of,
                cache=cache,
            )
            if isinstance(dep_result, MetricUnavailable):
                result = fail(
                    reason=UnavailableReason.DEPENDENCY_UNAVAILABLE,
                    message=f"depende de {dep}, que esta indisponivel: {dep_result.message}",
                    concept_id=dep_result.concept_id,
                    tried=dep_result.tried,
                    remedy=dep_result.remedy,
                )
                cache[key] = result
                return result
            dep_results[str(dep)] = dep_result

        # -- conceitos obrigatorios ----------------------------------------
        concept_values: dict[str, ConceptValue] = {}
        for concept_id in definition.requires_concepts:
            resolved = self._resolve_concept(
                concept_id,
                chain=chain,
                resolver=resolver,
                entity_id=entity_id,
                period_end=period_end,
                scope=scope,
                as_of=as_of,
            )
            if isinstance(resolved, _ConceptFailure):
                result = fail(
                    reason=resolved.reason,
                    message=resolved.message,
                    concept_id=resolved.concept_id,
                    tried=resolved.tried,
                    remedy=resolved.remedy,
                )
                cache[key] = result
                return result
            concept_values[concept_id] = resolved

        # -- conceitos opcionais: so alimentam checks -----------------------
        for concept_id in definition.optional_concepts:
            resolved = self._resolve_concept(
                concept_id,
                chain=chain,
                resolver=resolver,
                entity_id=entity_id,
                period_end=period_end,
                scope=scope,
                as_of=as_of,
            )
            if not isinstance(resolved, _ConceptFailure):
                concept_values[concept_id] = resolved

        inputs = Inputs(concepts=concept_values, metrics=dep_results)

        # -- moeda: nunca converter ----------------------------------------
        currencies = {
            cv.currency
            for cid, cv in concept_values.items()
            if cid in definition.requires_concepts and cv.currency
        } | {r.currency for r in dep_results.values() if r.currency}
        if len(currencies) > 1:
            result = fail(
                reason=UnavailableReason.MIXED_CURRENCY,
                message=(
                    f"insumos em moedas diferentes: {sorted(currencies)}. Conversao "
                    "cambial nao acontece aqui, e nao deve acontecer implicitamente "
                    "em lugar nenhum."
                ),
            )
            cache[key] = result
            return result

        # -- periodo --------------------------------------------------------
        period_types = {
            cv.period_type for cid, cv in concept_values.items() if cid in definition.requires_concepts
        } | {r.period_type for r in dep_results.values()}
        period_starts = {
            cv.period_start
            for cid, cv in concept_values.items()
            if cid in definition.requires_concepts
        } | {r.period_start for r in dep_results.values()}
        if len(period_types) > 1 and not definition.mixes_period_kinds:
            result = fail(
                reason=UnavailableReason.WRONG_PERIOD_TYPE,
                message=f"insumos com tipos de periodo diferentes: {sorted(period_types)}",
            )
            cache[key] = result
            return result
        if len(period_types) > 1:
            # Mistura DECLARADA: o resultado herda o periodo do insumo de
            # fluxo, porque a metrica descreve um periodo e nao uma data. Um
            # ROE carimbado como INSTANT diria que o lucro do exercicio
            # aconteceu num dia.
            fluxos = {t for t in period_types if t is not PeriodType.INSTANT}
            period_types = fluxos or period_types
            period_starts = {
                inicio for inicio in period_starts if inicio is not None
            } or period_starts

        # -- calculo --------------------------------------------------------
        try:
            value = metric.compute(inputs)
        except ComputationUnavailable as exc:
            result = fail(reason=exc.reason, message=exc.message)
            cache[key] = result
            return result

        # -- checks ---------------------------------------------------------
        outcomes: list[CheckOutcome] = []
        for spec in definition.checks:
            evaluation = metric.check_fns[spec.check_id](inputs, value, spec)
            if evaluation is None:
                outcomes.append(CheckOutcome(check_id=spec.check_id, status="skipped_missing"))
                continue
            outcomes.append(
                CheckOutcome(
                    check_id=spec.check_id,
                    status="pass" if evaluation.passed else "fail",
                    observed=evaluation.observed,
                    expected=evaluation.expected,
                    tolerance_abs=spec.tolerance_abs,
                )
            )

        # -- montagem -------------------------------------------------------
        input_refs: list[InputRef] = []
        fidelities: list[Fidelity] = []
        knowledge_dates: list[date] = []

        for concept_id in definition.requires_concepts:
            cv = concept_values[concept_id]
            input_refs.extend(cv.inputs)
            fidelities.append(cv.fidelity)
            knowledge_dates.append(cv.knowledge_date)
        for dep_key, dep_result in dep_results.items():
            input_refs.append(
                InputRef(
                    role=dep_key,
                    is_metric=True,
                    value=dep_result.value,
                    fidelity=dep_result.fidelity,
                    currency=dep_result.currency,
                    knowledge_date=dep_result.knowledge_date,
                )
            )
            fidelities.append(dep_result.fidelity)
            knowledge_dates.append(dep_result.knowledge_date)

        display_name, local_ids = resolver.entity_display(entity_id)
        currency = currencies.pop() if currencies else None
        period_type = period_types.pop()
        period_start = period_starts.pop() if len(period_starts) == 1 else None

        result = MetricResult(
            metric=definition.name,
            metric_version=definition.version,
            kind=definition.kind,
            entity_id=entity_id,
            local_ids=local_ids,
            display_name=display_name,
            scope=scope,
            period_type=PeriodType(period_type),
            period_start=period_start,
            period_end=period_end,
            as_of=as_of,
            knowledge_date=max(knowledge_dates),
            value=value,
            dimension=definition.dimension,
            currency=currency if definition.dimension is Dimension.MONEY else None,
            fidelity=weakest(tuple(fidelities)),
            inputs=tuple(input_refs),
            checks=tuple(outcomes),
            mapping_id=chain.head.mapping_id,
            mapping_version=chain.head.mapping_version,
            mapping_sha256=chain.sha256,
            mapping_confirmed=chain.confirmed,
            framework=chain.head.framework,
            jurisdiction=chain.head.jurisdiction,
            pat_version=self._pat_version,
            git_sha=self._git_sha,
        )
        cache[key] = result
        return result

    def _resolve_concept(
        self,
        concept_id: str,
        *,
        chain: MappingChain,
        resolver: FactResolver,
        entity_id: str,
        period_end: date,
        scope: ReportingScope,
        as_of: date,
    ) -> ConceptValue | _ConceptFailure:
        concept = concepts.get(concept_id)
        found = chain.binding_for(concept_id)
        if found is None:
            return _ConceptFailure(
                UnavailableReason.MISSING_CONCEPT,
                f"o mapeamento {chain.head.mapping_id} nao liga o conceito "
                f"{concept_id!r} a nenhuma linha",
                concept_id,
                remedy=f"adicione um [[binding]] de {concept_id} ao mapeamento",
            )
        binding, _owner = found

        total = Decimal(0)
        refs: list[InputRef] = []
        currencies: set[str] = set()
        knowledge_dates: list[date] = []
        period_types: set[PeriodType] = set()
        period_starts: set[date | None] = set()
        tried: list[str] = []

        for line in binding.lines:
            tried.append(line.address.as_str())
            outcome = resolver.resolve(
                entity_id=entity_id,
                address=line.address,
                period_end=period_end,
                period_kind=concept.period_kind,
                scope=scope,
                as_of=as_of,
            )
            if isinstance(outcome, ResolutionFailure):
                return _ConceptFailure(
                    outcome.reason,
                    f"conceito {concept_id!r}: {outcome.message}",
                    concept_id,
                    tuple(tried),
                )

            total += Decimal(line.sign) * outcome.value
            currencies.add(outcome.currency)
            knowledge_dates.append(outcome.knowledge_date)
            period_types.add(outcome.period_type)
            period_starts.add(outcome.period_start)
            refs.append(
                InputRef(
                    role=concept_id,
                    is_metric=False,
                    value=outcome.value,
                    fidelity=binding.fidelity,
                    fact_id=outcome.fact_id,
                    address=line.address.as_str(),
                    sign_applied=line.sign,
                    currency=outcome.currency,
                    knowledge_date=outcome.knowledge_date,
                    locator=outcome.locator,
                )
            )

        if len(currencies) > 1:
            return _ConceptFailure(
                UnavailableReason.MIXED_CURRENCY,
                f"conceito {concept_id!r} soma linhas em moedas diferentes: {sorted(currencies)}",
                concept_id,
                tuple(tried),
            )

        return ConceptValue(
            concept_id=concept_id,
            value=total,
            currency=currencies.pop() if currencies else None,
            fidelity=binding.fidelity,
            period_type=period_types.pop(),
            period_start=period_starts.pop() if len(period_starts) == 1 else None,
            knowledge_date=max(knowledge_dates),
            inputs=tuple(refs),
        )

"""O3 - a mesa de ferramentas: tudo que o agente pode perguntar ao PAT.

Uma so ideia estrutural, e ela e a razao do modulo existir:

    **A mesa e presa a UMA entidade, escolhida na construcao.**

Nenhum metodo aceita `entity_id`. Nao ha como pedir um numero de outra
empresa sem construir outra mesa, e construir outra mesa exige passar por
`profile_for`, que le a jurisdicao do catalogo. Resolucao entre jurisdicoes
nao e proibida por convencao: e impossivel de expressar. Foi a unica forma que
encontrei de garantir o requisito sem espalhar uma checagem `if jurisdiction`
por dez metodos - checagem que alguem removeria um dia por parecer redundante.

`as_of` e `scope` tambem sao da mesa, e nao de cada chamada. Um agente que
pudesse escolher `as_of` por pergunta acabaria comparando dois retratos do
mundo dentro da mesma conclusao - e o erro apareceria como uma conclusao
melhor, que e a classe de erro que nunca se investiga.

O que a mesa NAO faz
-------------------
Nao calcula, nao arredonda, nao converte, nao agrega e nao interpreta. Ela
devolve os proprios contratos do PAT - `MetricResult`, `DecompositionResult`,
`EvidenceResult` - inteiros, com procedencia, fidelidade e `as_of` intactos.
Um envelope que resumisse "o valor" perderia o `mapping_sha256` e a fidelidade
no caminho, e a camada de cima passaria a citar um numero sem saber que ele
era aproximado.

Recusa tambem e resposta. `MetricUnavailable` e `EvidenceUnavailable` sao
devolvidos como estao, com motivo nomeado, enderecos tentados e remedio. Nao
existe `allow_missing`, aqui tampouco.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb

from pat.contracts.corpus import DocumentKind, EvidenceQuery
from pat.contracts.opportunity import CompanyProfile, CoverageSnapshot
from pat.contracts.semantics import ReportingScope

__all__ = ["AccountLine", "PatTools", "ToolContext"]

DEFAULT_LIMIT = 5
"""Poucos trechos por consulta. A janela de contexto de um modelo e finita, e
uma busca que devolve trinta trechos faz o agente escolher por posicao em vez
de por relevancia."""


@dataclass(frozen=True)
class ToolContext:
    """O escopo fixo de toda consulta desta mesa.

    Existe como valor para poder ser gravado junto de um achado: saber que um
    numero saiu com `as_of` de junho e escopo consolidado e parte do numero.
    """

    entity_id: str
    jurisdiction: str
    as_of: date
    scope: ReportingScope
    source: str


@dataclass(frozen=True)
class AccountLine:
    """Uma linha do plano de contas efetivo da companhia.

    `account_code` e OPACO nesta camada. Ele e o endereco da linha na taxonomia
    do regime, e serve para um humano escolher qual linha e qual conceito ao
    escrever um mapeamento. O Opportunity nunca o interpreta, nunca casa por
    rotulo e nunca o usa para buscar - `pat accounts` existe para uma pessoa
    escolher, e nao deve virar inferencia.
    """

    account_code: str
    label: str
    value: Decimal
    currency: str | None
    knowledge_date: date
    fact_id: str


class PatTools:
    """A mesa. Uma empresa, um `as_of`, um escopo.

    O motor semantico e construido uma vez e reusado: `build_engine` carrega os
    mapeamentos do disco, e refazer isso a cada pergunta faria uma conversa de
    vinte turnos reler os TOML vinte vezes.
    """

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        *,
        company: CompanyProfile,
        as_of: date,
        scope: ReportingScope = ReportingScope.CONSOLIDATED,
        source: str | None = None,
    ) -> None:
        from pat.research.workspace import SOURCE_BY_JURISDICTION
        from pat.semantics import build_engine

        self._conn = conn
        self._company = company
        # A fonte sai da jurisdicao que a entidade declara, nunca de um default
        # global: um default aqui seria um literal de um pais so, e a segunda
        # jurisdicao falharia apontando para o lugar errado.
        self._source = source or SOURCE_BY_JURISDICTION.get(
            company.jurisdiction, SOURCE_BY_JURISDICTION["BR"]
        )
        self._context = ToolContext(
            entity_id=company.entity_id,
            jurisdiction=company.jurisdiction,
            as_of=as_of,
            scope=scope,
            source=self._source,
        )
        self._engine = build_engine(conn, source=self._source)

    # -- identidade e escopo -------------------------------------------------

    @property
    def context(self) -> ToolContext:
        return self._context

    @property
    def company(self) -> CompanyProfile:
        return self._company

    def with_scope(self, scope: ReportingScope) -> "PatTools":
        """Outra mesa, mesmo escopo de empresa e `as_of`, escopo contabil
        diferente.

        Mesa nova em vez de parametro por chamada: comparar individual com
        consolidado e legitimo, e misturar os dois dentro de uma serie e o erro
        que `scope` obrigatorio existe para impedir. Duas mesas tornam a
        comparacao visivel no codigo que a faz.
        """
        return PatTools(
            self._conn,
            company=self._company,
            as_of=self._context.as_of,
            scope=scope,
            source=self._source,
        )

    def advanced_to(self, as_of: date) -> "PatTools":
        """Mesa nova num `as_of` posterior. Nunca anterior."""
        if as_of < self._context.as_of:
            raise ValueError(
                f"as_of nao recua: {self._context.as_of} -> {as_of}. Uma conclusao "
                "tirada sob o as_of maior citaria documento que a mesa nova nao ve."
            )
        return PatTools(
            self._conn,
            company=self._company,
            as_of=as_of,
            scope=self._context.scope,
            source=self._source,
        )

    # -- cobertura e capacidade ---------------------------------------------

    def coverage(self) -> CoverageSnapshot:
        """O que o PAT tem sobre esta empresa, resumido para o diario.

        Deriva do `CompanyWorkspace` da Fase 5 - nao reimplementa a checagem.
        Duas implementacoes de "esta pronto?" divergiriam, e a mais permissiva
        seria a que o agente consultaria.
        """
        from pat.research.workspace import build_workspace

        w = build_workspace(
            self._conn,
            entity_id=self._context.entity_id,
            display_name=self._company.display_name,
            jurisdiction=self._company.jurisdiction,
            source=self._source,
            as_of=self._context.as_of,
        )
        return CoverageSnapshot(
            workspace_sha256=w.workspace_sha256,
            state=str(w.state.value),
            as_of=w.as_of,
            facts=w.quantitative.facts,
            period_ends=w.quantitative.period_ends,
            metrics_available=w.quantitative.metrics_available,
            missing_concepts=w.quantitative.missing_concepts,
            documents=w.qualitative.documents,
            units_indexed=w.qualitative.units_indexed,
            extraction_failures=len(w.qualitative.extraction_failures),
            gaps=tuple((g.code.value, g.remedy) for g in w.gaps),
            refreshed_at=w.built_at if w.built_at else datetime.now(UTC),
        )

    def capability(self):
        """O que da para perguntar: conceitos, metricas, decomposicoes, corpus.

        E o mesmo `CapabilitySnapshot` que o planejador da Fase 3 recebe. O
        agente do Opportunity nao pode conhecer uma metrica que o motor nao
        registra - inventar `ebitda_ajustado@v1` porque soa plausivel e
        exatamente o que este snapshot impede.
        """
        from pat.research.capability import build_snapshot

        return build_snapshot(
            self._conn, as_of=self._context.as_of, source=self._source
        )

    def periods(self) -> tuple[date, ...]:
        """Periodos com fato conhecido em `as_of`, do mais antigo ao mais novo."""
        from pat.query.asof import AsOf

        cobertura = AsOf(self._conn).coverage(
            self._context.entity_id, as_of=self._context.as_of
        )
        return tuple(cobertura.period_ends) if cobertura else ()

    # -- numeros -------------------------------------------------------------

    def periods_of_type(self, period_type: str) -> tuple[date, ...]:
        """Datas-base de um tipo de periodo so, sob o `as_of` da mesa.

        `periods()` mistura anual, trimestral e instantaneo. Quem pede uma
        metrica anual em toda data coberta recebe uma recusa por trimestre -
        recusa correta para uma pergunta errada.
        """
        from pat.query.asof import AsOf

        return AsOf(self._conn).period_ends_of_type(
            self._context.entity_id,
            period_type=period_type,
            as_of=self._context.as_of,
        )

    def metric(self, ref: str, *, period_end: date):
        """Uma metrica num periodo. `MetricResult` ou `MetricUnavailable`.

        Devolvido inteiro, com `inputs`, `fidelity`, `mapping_id` e `checks`.
        Resumir aqui perderia justamente o que distingue um numero exato de um
        aproximado, e o aproximado e o que precisa aparecer marcado.
        """
        return self._engine.compute(
            ref,
            entity_id=self._context.entity_id,
            period_end=period_end,
            scope=self._context.scope,
            as_of=self._context.as_of,
        )

    def series(self, ref: str, *, period_ends: tuple[date, ...]) -> tuple:
        """A mesma metrica em varios periodos, na ordem pedida.

        Nao filtra as recusas. Uma serie que descartasse os periodos
        indisponiveis teria buracos invisiveis, e uma tendencia calculada sobre
        ela pareceria continua - o pior jeito de errar sobre crescimento.
        """
        return tuple(self.metric(ref, period_end=p) for p in period_ends)

    def concept(self, concept_id: str, *, period_end: date):
        """Um conceito, sem passar por metrica."""
        return self._engine.resolve_concept(
            concept_id,
            entity_id=self._context.entity_id,
            period_end=period_end,
            scope=self._context.scope,
            as_of=self._context.as_of,
        )

    def breakdown(self, ref: str, *, period_from: date, period_to: date):
        """Abre a variacao de um alvo entre dois periodos.

        `DecompositionResult` ou `DecompositionUnavailable` - com o residual,
        que e a parte que uma decomposicao desonesta esconde.
        """
        from pat.research.decompose import decompose

        return decompose(
            self._engine,
            ref,
            entity_id=self._context.entity_id,
            period_from=period_from,
            period_to=period_to,
            scope=self._context.scope,
            as_of=self._context.as_of,
        )

    def accounts(self, *, statement: str, period_end: date) -> tuple[AccountLine, ...]:
        """O plano de contas efetivo da companhia num periodo.

        Para um humano escolher a linha ao escrever mapeamento. Nao e
        inferencia e nao deve virar uma: nada nesta camada casa conta por
        rotulo, e `label` sai daqui para ser lido, nunca para ser buscado.
        """
        from pat.query.asof import AsOf

        linhas = AsOf(self._conn).accounts(
            entity_id=self._context.entity_id,
            statement=statement,
            period_end=period_end,
            as_of=self._context.as_of,
            consolidated=self._context.scope is ReportingScope.CONSOLIDATED,
        )
        return tuple(
            AccountLine(
                account_code=v.cd_conta,
                label=v.ds_conta,
                value=v.value,
                currency=v.currency,
                knowledge_date=v.knowledge_date,
                fact_id=v.fact_id,
            )
            for v in linhas
        )

    # -- texto ---------------------------------------------------------------

    def sections(self) -> tuple[str, ...]:
        """As secoes que o corpus desta empresa declara.

        Existe para que quem planeja um passo de evidencia ESCOLHA a secao em
        vez de adivinhar o nome dela. E a mesma razao de `metrics_available`:
        um raciocinador que inventa o endereco recebe uma recusa correta e
        conclui que nao ha texto, quando ha - so nao com o nome que ele
        chutou.
        """
        from pat.corpus.retrieve import sections_for

        return sections_for(self._conn, entity_id=self._context.entity_id, as_of=self._context.as_of)

    def evidence(
        self,
        terms: tuple[str, ...],
        *,
        kinds: tuple[DocumentKind, ...] = (),
        published_from: date | None = None,
        published_to: date | None = None,
        sections: tuple[str, ...] = (),
        limit: int = DEFAULT_LIMIT,
    ):
        """Busca no corpus. `EvidenceResult` ou `EvidenceUnavailable`.

        `as_of` vem da mesa e o corte esta no SQL do `retrieve`, nao aqui:
        citar documento posterior ao `as_of` e o vazamento mais facil de
        cometer, porque deixa a resposta melhor.
        """
        from pat.corpus.retrieve import retrieve

        return retrieve(
            self._conn,
            EvidenceQuery(
                entity_id=self._context.entity_id,
                terms=terms,
                as_of=self._context.as_of,
                kinds=kinds,
                published_from=published_from,
                published_to=published_to,
                sections=sections,
                limit=limit,
            ),
        )

    # -- procedencia ---------------------------------------------------------

    def provenance(self, fact_id: str):
        """Cadeia de um fato ate os bytes de origem. `None` se o fato nao
        existe."""
        from pat.query.asof import AsOf

        return AsOf(self._conn).provenance(fact_id)

    def verify_quote(self, unit_id: str, bronze):
        """Reextrai, fatia e compara byte a byte. `None` se a unidade sumiu.

        A conferencia de verbatim nao e detalhe de auditoria: e o que distingue
        uma citacao de uma parafrase que se apresenta como citacao.
        """
        from pat.corpus import verify_unit
        from pat.store.corpus import read_document, read_unit

        unidade = read_unit(self._conn, unit_id)
        if unidade is None:
            return None
        documento = read_document(self._conn, unidade.document_id)
        if documento is None:
            return None
        return verify_unit(unidade, documento, bronze)

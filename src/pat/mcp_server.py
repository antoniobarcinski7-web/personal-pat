"""O PAT como ferramenta do Claude, em vez de programa que alguem dirige.

Raiz de composicao, como `cli_opportunity.py` - e pelo mesmo motivo mora FORA
de `pat/opportunity/`: e aqui que `cod_cvm` e `cik` podem existir, porque e
aqui que um humano (ou um modelo falando com um humano) usa o identificador que
conhece.

A inversao que este modulo faz
------------------------------
Na CLI, o PAT e o programa e o modelo e um detalhe interno. Aqui e o contrario:
a conversa acontece no Claude, e o PAT e a ferramenta que ele chama quando
precisa de um numero. Isso resolve as duas metades que nenhum dos dois tinha
sozinho:

- Um modelo lendo os PDFs conversa muito bem e erra numero sem avisar. O caso
  que motiva o projeto inteiro esta no `CLAUDE.md`: o GPA publicou a receita de
  2023 e REAPRESENTOU outro valor um ano depois. Quem le "o PDF" pega um dos
  dois com confianca e nunca menciona que o outro existe.
- O PAT acerta o numero e nao conversa.

Aqui o numero vem do motor deterministico, com `as_of` e procedencia, e a
conversa vem de quem sabe conversar.

O que NAO muda por estar num servidor MCP
------------------------------------------
1. **Nenhuma ferramenta calcula.** Elas leem o que o motor produziu. Nao ha
   ferramenta que receba dois valores e devolva a soma - pedir isso ao modelo
   e o modo de falha que o projeto existe para evitar, e oferece-lo numa
   ferramenta seria o mesmo erro com carimbo de API.
2. **`as_of` e obrigatorio em tudo que le fato ou texto.** Sem default de
   "hoje": um default silencioso faria a mesma pergunta responder coisas
   diferentes em dias diferentes, e ninguem saberia por que.
3. **Insumo ausente vira recusa NOMEADA**, com motivo e remedio, e nunca uma
   lista vazia. O modelo do outro lado precisa da diferenca entre "a empresa
   nao reporta isso" e "eu nao ingeri o dado" - as duas pedem acoes opostas.

Sobre os numeros que saem daqui
-------------------------------
Cada valor sai DUAS vezes: `valor_exato` (todos os digitos, string) e
`valor` (legivel, "US$ 45,18 bi"). O legivel existe porque
`45183036000.0000000000` esconde a ordem de grandeza, que e a primeira coisa
que alguem le. O exato existe porque o legivel e arredondado, e um numero
arredondado que volta ao sistema e por onde uma aproximacao entra num calculo
sem ninguem notar.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from pat.config import resolve_paths
from pat.contracts.semantics import Dimension, ReportingScope
from pat.display import format_money, format_number, format_percent, format_ratio

__all__ = ["build_server", "main"]

SERVER_NAME = "pat"

INSTRUCTIONS = """\
O PAT responde sobre companhias abertas a partir de arquivamentos oficiais.

Como usar bem:

1. NUNCA calcule um numero a partir dos que estas ferramentas devolvem. Nao
   some, nao subtraia, nao converta moeda e nao anualize. Se voce precisa de
   uma grandeza derivada, procure-a em `metricas_disponiveis` e peca ao motor.
   Se ela nao existe, diga que nao existe.

2. Toda consulta precisa de `as_of` - a data ate a qual o conhecimento e
   considerado. E o que impede citar um documento publicado depois da data que
   se esta analisando. Na duvida, use a data de hoje e diga qual usou.

3. Quando uma ferramenta devolver `disponivel: false`, ela diz o MOTIVO e o
   REMEDIO. Repasse os dois. "Nao encontrei" sem motivo e a resposta que faz
   alguem concluir que a empresa nao reporta algo que ela reporta.

4. `fidelidade` diferente de `exact` significa que o numero saiu de uma
   aproximacao declarada, e vem com `nota_de_divergencia`. Mencione isso ao
   citar o valor - e o que separa um numero de uma estimativa.

5. Citacao de texto e verbatim e tem `unit_id`. Cite o trecho como esta, com o
   documento e a data. Nao parafraseie um numero que aparece dentro de um
   trecho como se fosse um numero calculado: e o que a companhia DISSE.
"""


# ---------------------------------------------------------------------------
# Conexao
# ---------------------------------------------------------------------------


def _warehouse_path() -> Path:
    """O warehouse a abrir. `PAT_HOME` manda; o default e o mesmo da CLI.

    Um servidor que apontasse para um caminho proprio responderia sobre um
    banco diferente daquele que `pat build` acabou de atualizar, e a divergencia
    apareceria como "o Claude nao ve o que eu acabei de ingerir".
    """
    return resolve_paths(os.environ.get("PAT_HOME")).ensure().warehouse


def _conn():
    from pat.store.db import connect

    return connect(_warehouse_path(), read_only=True)


def _as_of(texto: str) -> date:
    return date.fromisoformat(texto)


# ---------------------------------------------------------------------------
# Traducao de resultado -> dicionario legivel
# ---------------------------------------------------------------------------


def _valor_legivel(valor: Decimal, dimension: Dimension, currency: str | None) -> str:
    """O mesmo numero, escrito para um humano.

    A escolha sai da `dimension` do registro, e nao do nome da metrica: uma
    metrica nova de razao formata certo sem tocar aqui.
    """
    if dimension is Dimension.MONEY and currency:
        return format_money(valor, currency)
    if dimension is Dimension.RATIO:
        return format_ratio(valor)
    if dimension is Dimension.PERCENT:
        return format_percent(valor)
    return format_number(valor)


def _periodos_de(tools: Any, ref: str) -> tuple[date, ...]:
    """As datas-base em que faz sentido pedir esta metrica.

    A escolha sai do REGISTRO - `period_kind` -, e nao de uma lista de nomes:
    metrica de fluxo resolve em exercicio e metrica de saldo em data de
    balanco. Pedir uma anual em toda data coberta devolveria uma recusa
    `WRONG_PERIOD_TYPE` por trimestre - recusas corretas, pergunta errada.
    """
    from pat.contracts.semantics import PeriodKind
    from pat.semantics.registry import default_registry

    try:
        definicao = default_registry().get(ref).definition
    except Exception:  # noqa: BLE001 - metrica desconhecida cai no anual
        return tools.periods_of_type("year")
    if definicao.period_kind is PeriodKind.STOCK:
        return tools.periods_of_type("instant")
    return tools.periods_of_type("year")


def _resultado(r: Any) -> dict:
    """`MetricResult` ou `MetricUnavailable` -> dicionario.

    A recusa nao vira excecao: do outro lado ha um modelo montando uma
    resposta, e uma excecao viraria "a ferramenta falhou" - que se le como
    problema tecnico, quando a informacao e sobre a EMPRESA ou sobre a
    cobertura. O motivo nomeado e o conteudo, nao o erro.
    """
    if hasattr(r, "reason"):
        return {
            "disponivel": False,
            "metrica": f"{r.metric}@{r.metric_version}",
            "period_end": r.period_end.isoformat(),
            "motivo": r.reason.value,
            "explicacao": r.message,
            "conceito_faltante": r.concept_id,
            "remedio": getattr(r, "remedy", None),
        }

    return {
        "disponivel": True,
        "metrica": f"{r.metric}@{r.metric_version}",
        "valor": _valor_legivel(r.value, r.dimension, r.currency),
        "valor_exato": str(r.value),
        "moeda": r.currency,
        "period_end": r.period_end.isoformat(),
        "period_start": r.period_start.isoformat() if r.period_start else None,
        "tipo_de_periodo": r.period_type.value,
        "as_of": r.as_of.isoformat(),
        "knowledge_date": r.knowledge_date.isoformat(),
        "fidelidade": r.fidelity.value,
        "escopo": r.scope.value,
        # `mapping_confirmed=False` quer dizer que o numero saiu da familia
        # default, sem ninguem ter conferido o mapeamento DESTA empresa. Sai
        # sempre, porque e a diferenca entre um numero conferido e um plausivel.
        "mapeamento_conferido": r.mapping_confirmed,
        "id_do_resultado": f"{r.metric}@{r.metric_version}|{r.entity_id}|"
        f"{r.period_end.isoformat()}|{r.scope.value}|{r.as_of.isoformat()}",
    }


# ---------------------------------------------------------------------------
# O servidor
# ---------------------------------------------------------------------------


def build_server() -> MCPServer:
    """Monta o servidor. Separado de `main` para poder ser testado sem stdio."""
    server = MCPServer(name=SERVER_NAME, instructions=INSTRUCTIONS)

    @server.tool()
    def listar_empresas() -> list[dict]:
        """Companhias que existem no warehouse, com identificador e jurisdicao.

        Comece por aqui: o resto das ferramentas pede `entity_id`, e adivinhar
        um identificador nao funciona nem deve funcionar.
        """
        conn = _conn()
        try:
            linhas = conn.execute(
                """
                SELECT e.entity_id, e.display_name, e.jurisdiction,
                       (SELECT COUNT(*) FROM gold_fact f WHERE f.entity_id = e.entity_id),
                       (SELECT COUNT(*) FROM source_document d WHERE d.entity_id = e.entity_id)
                FROM entity e ORDER BY e.display_name
                """
            ).fetchall()
            return [
                {
                    "entity_id": r[0],
                    "nome": r[1],
                    "jurisdicao": r[2],
                    "fatos": r[3],
                    "documentos": r[4],
                }
                for r in linhas
            ]
        finally:
            conn.close()

    @server.tool()
    def cobertura(entity_id: str, as_of: str) -> dict:
        """O que o PAT sabe sobre esta empresa nesta data.

        Periodos cobertos, metricas calculaveis, conceitos que faltam e quantos
        documentos ha. Consulte antes de pedir uma metrica: pedir o que nao
        existe devolve recusa, e a recusa esta certa mas gasta uma volta.
        """
        from pat.query.asof import AsOf

        conn = _conn()
        try:
            asof = AsOf(conn)
            corte = _as_of(as_of)
            cob = asof.coverage(entity_id, as_of=corte)
            if cob is None:
                return {
                    "disponivel": False,
                    "motivo": "sem_fatos",
                    "explicacao": f"nenhum fato de {entity_id} conhecido em {as_of}",
                    "remedio": "Rode `pat build` para esta empresa, ou confira o entity_id "
                    "em `listar_empresas`.",
                }
            from pat.opportunity import PatTools, profile_for

            perfil = profile_for(conn, entity_id)
            tools = PatTools(conn, company=perfil, as_of=corte)
            instantaneo = tools.coverage()
            return {
                "disponivel": True,
                "empresa": perfil.display_name,
                "as_of": as_of,
                "fatos": instantaneo.facts,
                "periodos_anuais": [d.isoformat() for d in tools.periods_of_type("year")],
                "periodos_de_saldo": [
                    d.isoformat() for d in tools.periods_of_type("instant")
                ],
                "metricas_calculaveis": list(instantaneo.metrics_available),
                "conceitos_ausentes": list(instantaneo.missing_concepts),
                "documentos": instantaneo.documents,
                "trechos_indexados": instantaneo.units_indexed,
                "secoes_do_corpus": list(tools.sections()),
            }
        finally:
            conn.close()

    @server.tool()
    def metricas_disponiveis() -> list[dict]:
        """O catalogo de metricas que o motor sabe calcular, com a definicao.

        A definicao importa: `ebit@v1` inclui equivalencia patrimonial, e
        `divida_bruta@v1` exclui arrendamento. Duas decisoes que mudam o numero
        e que estao escritas aqui, nao subentendidas.
        """
        from pat.semantics.registry import default_registry

        return [
            {
                "ref": str(m.definition.ref),
                "dimensao": m.definition.dimension.value,
                "tipo_de_periodo": m.definition.period_kind.value,
                "definicao": m.definition.definition,
                "por_que_assim": m.definition.rationale,
            }
            for m in default_registry().all()
        ]

    @server.tool()
    def metrica(entity_id: str, ref: str, period_end: str, as_of: str) -> dict:
        """Uma metrica num periodo. Ex.: ref="receita_liquida@v1".

        Devolve o valor legivel e o exato, mais fidelidade, escopo e a data em
        que o insumo passou a ser conhecido.
        """
        from pat.opportunity import PatTools, profile_for

        conn = _conn()
        try:
            perfil = profile_for(conn, entity_id)
            tools = PatTools(conn, company=perfil, as_of=_as_of(as_of))
            return _resultado(tools.metric(ref, period_end=_as_of(period_end)))
        finally:
            conn.close()

    @server.tool()
    def serie(entity_id: str, ref: str, as_of: str) -> dict:
        """A metrica em TODOS os periodos cobertos, na ordem.

        Prefira isto a varias chamadas de `metrica`: uma serie mostra reversao
        de tendencia, e comparar so as pontas descreve `-565, +660, -436` como
        "subiu" - aritmeticamente certo e uma frase errada.

        As recusas NAO sao filtradas. Uma serie sem os periodos indisponiveis
        teria buracos invisiveis, e uma tendencia lida sobre ela pareceria
        continua.
        """
        from pat.opportunity import PatTools, profile_for

        conn = _conn()
        try:
            perfil = profile_for(conn, entity_id)
            corte = _as_of(as_of)
            tools = PatTools(conn, company=perfil, as_of=corte)
            periodos = _periodos_de(tools, ref)
            pontos = [_resultado(r) for r in tools.series(ref, period_ends=periodos)]
            return {
                "metrica": ref,
                "empresa": perfil.display_name,
                "as_of": as_of,
                "pontos": pontos,
                "calculados": sum(1 for p in pontos if p["disponivel"]),
                "recusados": sum(1 for p in pontos if not p["disponivel"]),
            }
        finally:
            conn.close()

    @server.tool()
    def buscar_evidencia(
        entity_id: str,
        termos: list[str],
        as_of: str,
        secoes: list[str] | None = None,
        limite: int = 5,
    ) -> dict:
        """Trechos VERBATIM dos arquivamentos, com documento, data e `unit_id`.

        Use `secoes` apenas com valores que vieram de `cobertura`
        (`secoes_do_corpus`) - o caminho casa pelo endereco declarado no
        formulario, nao pelo titulo. Sem `secoes`, a busca cobre o documento
        inteiro.

        O texto e fatia literal do documento. Cite como esta.
        """
        from pat.opportunity import PatTools, profile_for

        conn = _conn()
        try:
            perfil = profile_for(conn, entity_id)
            tools = PatTools(conn, company=perfil, as_of=_as_of(as_of))
            r = tools.evidence(
                tuple(termos), sections=tuple(secoes or ()), limit=limite
            )
            if hasattr(r, "reason"):
                return {
                    "disponivel": False,
                    "motivo": r.reason.value,
                    "explicacao": r.message,
                    "remedio": r.remedy,
                    "documentos_no_escopo": r.documents_in_scope,
                    "documentos_posteriores_ao_as_of": r.documents_excluded_by_as_of,
                }
            return {
                "disponivel": True,
                "trechos": [
                    {
                        "texto": h.quote.text,
                        "unit_id": h.quote.unit_id,
                        "documento": h.quote.title,
                        "publicado_em": h.quote.published_at.isoformat(),
                        # A BASE da data vai junto: `RETRIEVED_AT_FALLBACK`
                        # quer dizer que a data foi inferida com erro para o
                        # lado seguro, e nao lida do documento.
                        "base_da_data": h.quote.published_at_basis.value,
                        "termos_casados": list(h.matched_terms),
                    }
                    for h in r.hits
                ],
                "documentos_no_escopo": r.documents_in_scope,
            }
        finally:
            conn.close()

    @server.tool()
    def plano_de_contas(
        entity_id: str, statement: str, period_end: str, as_of: str
    ) -> list[dict]:
        """As linhas que a companhia de fato reportou numa demonstracao.

        Existe para um HUMANO escolher a linha quando um mapeamento precisa ser
        criado. Nao e para casar conta por rotulo: rotulo muda, e um sistema que
        buscasse por ele resolveria calado para outra coisa no dia em que a
        companhia renomeasse uma linha.
        """
        from pat.opportunity import PatTools, profile_for

        conn = _conn()
        try:
            perfil = profile_for(conn, entity_id)
            tools = PatTools(conn, company=perfil, as_of=_as_of(as_of))
            linhas = tools.accounts(statement=statement, period_end=_as_of(period_end))
            return [
                {
                    "endereco": a.address,
                    "rotulo": a.label,
                    "valor": format_money(a.value, a.currency) if a.currency else str(a.value),
                    "valor_exato": str(a.value),
                }
                for a in linhas
            ]
        finally:
            conn.close()

    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()

# M5.6 — SEC/EDGAR, US GAAP e o desbloqueio do eixo SEGMENT

Plano de execução do último milestone da Fase 5. Escrito antes da
implementação, para que o estado da fase não dependa de nenhuma sessão
específica: quem retomar isto daqui a um mês tem o que precisa aqui e nos
commits.

## Estado ao entrar

| | escopo | commit |
|---|---|---|
| M5.1 | corpus point-in-time, evidência citável | `39dbcbd` |
| M5.2 | decomposição quantitativa (eixo COMPONENT) | `e43803f` |
| — | decisão A: SEGMENT bloqueado até haver fonte estruturada | `8282bfe` |
| M5.3 | ResearchProgram, planner em dois estágios | `7f8858e` |
| M5.4 | grafo de claims, writer, critic mecânico | `db67f0c` |
| M5.5 | critic de modelo, Company Workspace | `52f8d8e` |

Suíte: 914 offline, 18 de rede. Branch `phase5`, worktree
`.claude/worktrees/phase5`. Nada não-commitado.

## Por que este é o maior milestone da fase

Não é uma feature: é uma jurisdição inteira. A Fase 2 já provou que a camada
semântica aguenta — `tests/semantics/test_second_framework.py` roda as métricas
reais contra um regime fictício em dólar — então o risco é de implementação, não
de desenho. Mas são quatro frentes, e as três primeiras são independentes.

## Ordem de execução

### 1. Provider SEC/EDGAR

`sources/public/sec.py`, com a mesma disciplina do `CVMProvider`: busca bytes,
não interpreta.

Datasets previstos:

| dataset | recurso |
|---|---|
| `sec.submissions` | histórico de arquivamentos de um CIK |
| `sec.companyfacts` | fatos XBRL consolidados de um CIK |
| `sec.filing_doc` | um documento de um arquivamento (10-K, 10-Q, 20-F) |

Pontos de atenção, todos verificáveis antes de escrever código:

- A SEC **exige** `User-Agent` identificado com contato; requisição sem ele é
  bloqueada. O `PublicSourceProvider` já tem o campo, mas o valor atual é
  genérico e precisa ser adequado.
- Rate limit da SEC é mais estrito que o da CVM (10 req/s declarado, na prática
  convém menos). `min_interval_s` próprio.
- `companyfacts` é JSON, não ZIP — o parser é outro, e é da camada silver.
- A SEC **não reescreve** arquivamentos como a CVM reescreve o ZIP anual: uma
  reapresentação é um arquivamento novo (10-K/A). Isso muda o significado de
  `resource_key`, e a diferença precisa estar escrita no módulo — o padrão da
  CVM não vale aqui.

### 2. Regime `us-gaap`

- `semantics/frameworks/us_gaap/` — adapter de taxonomia e resolver, espelhando
  `cvm_dfp/`. Continua valendo que **só o resolver importa `pat.query`**.
- `LineAddress` para elemento XBRL (`us-gaap:Revenues` + contexto). O contrato de
  `LineAddress` já é opaco fora do adapter; conferir se ele acomoda o par
  elemento/contexto sem mudança, e se não, essa é a primeira decisão a registrar.
- Nenhum conceito novo deve ser necessário: `revenue_net`, `cogs`,
  `gross_profit`, `operating_expenses_net`, `ebit_reported` são universais por
  construção. Se algum precisar de conceito novo, isso é um achado sobre o
  desenho e merece nota, não um `if` no adapter.

### 3. Entidade multi-jurisdição — **provável ponto de parada**

Hoje `entity_id` é derivado do CNPJ (`br:cnpj:…`) e a CLI resolve empresa por
`--cod-cvm`. Para a Intel isso não serve.

O caminho aditivo é um registro de entidade com identificadores por jurisdição
(CIK, ticker, CNPJ, cod_cvm), com `entity_id` continuando opaco. Se o caminho se
mostrar aditivo, seguir; se exigir quebrar a assinatura de `AsOf.entity_by_cod_cvm`
ou o formato de `entity_id` já gravado no gold, **parar e chamar** — é mudança de
contrato público sobre dados já persistidos.

Superfícies afetadas: `cli.py` (`--cod-cvm` em ~8 comandos), `workspace.py`,
`capability.py`, `query/asof.py`.

### 4. Eixo SEGMENT, via XBRL

É o que a decisão A destravou, e é a parte mais barata das quatro — o executor de
decomposição já é agnóstico de eixo por construção.

- XBRL traz `us-gaap:StatementBusinessSegmentsAxis` com membros **tipados**: é a
  fonte estruturada que a CVM não tem, e a razão registrada em
  `docs/phase5_proposal.md` para não extrair segmento de PDF.
- Membros continuam **mapeados por declaração**, na disciplina de
  `equivalence_basis`: "Client Computing Group" é o endereço do segmento na
  taxonomia da Intel, não um conceito universal.
- `BreakdownAxis.SEGMENT` deixa de recusar com `NO_BREAKDOWN_SOURCE` **apenas no
  regime que tem a fonte**. No regime da CVM ele continua bloqueado, e isso não é
  um caso a consertar depois: é o comportamento correto.

### 5. Mapeamento da Intel

Um TOML com `equivalence_basis` por binding. Trabalho humano por natureza, como
sempre — `pat accounts` é a ferramenta, e não deve virar inferência.

## Critério de conclusão

```
pat company --cik 50863   → workspace da Intel, DRAFT ou READY, com as
                            mesmas seis condições objetivas
pat decompose revenue_by_segment@v1 --entity intel --from ... --to ...
                          → variação de receita aberta por segmento, com
                            residual explícito
```

E o invariante que atravessa a fase inteira continua válido: nenhum número de
documento vira insumo de cálculo, nenhuma citação sai sem resolver até o byte.

## O que NÃO fazer aqui

- Não generalizar para "todas as empresas americanas". Primeiro a Intel funciona
  ponta a ponta; cobertura é outro problema, e é o problema que a Fase 5 existe
  para *não* priorizar.
- Não usar XBRL para reabrir a discussão de extrair segmento de PDF no regime
  brasileiro. A decisão A é sobre a fonte, não sobre a vontade.

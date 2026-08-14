# personal-pat

Personal Investment Research Agent — Equity Research brasileiro (depois EUA e macro).

Inspirado na arquitetura pública do PAT (Bridgewater / AIA Labs): pipeline modular
que espelha o fluxo de um analista, com estágios inspecionáveis e o LLM produzindo
*código*, nunca números.

**Estado atual: Fase 2.** Espinha dorsal de dados (bronze → silver → gold,
consulta `AS OF`) mais a camada semântica: conceitos financeiros universais,
mapeamentos por regime contábil e métricas canônicas versionadas. Não há agente
ainda — isso é deliberado (ver "Fases").

---

## Os três invariantes

O sistema inteiro existe para tornar estas três propriedades difíceis de violar.

### I1 — Point-in-time por construção
Todo fato carrega dois eixos de tempo: `period_end` (a que período se refere) e
`knowledge_date` (quando se tornou público). Consultas são sempre `AS OF` uma data
de conhecimento.

Não é teórico: a CVM **reescreve** `dfp_cia_aberta_2024.zip` quando uma companhia
reapresenta demonstrações. Mesma URL, bytes diferentes. Sem o segundo eixo, todo
backtest usa números reapresentados e mente sobre o passado.

### I2 — Toda célula tem linhagem até o byte de origem
Qualquer número resolve, por um identificador, para: documento fonte (SHA-256),
localização dentro dele, versão do extrator e run de ingestão. Nada de números órfãos.

### I3 — Reprodutibilidade determinística
Bronze imutável (reprocessar nunca depende de re-baixar), dependências travadas em
`uv.lock` com hash por wheel, timestamps sempre timezone-aware, e todo run gravando
`git_sha` + versões de `pat` e Python no catálogo.

---

## Arquitetura

```
L5  INTERFACE     CLI · relatório versionado
L4  AGENTE        planner → retriever → coder → charter → writer → critic
                  ↑ só emite CÓDIGO e TEXTO, nunca números
L3  EXECUÇÃO      sandbox determinístico, DuckDB read-only
L2  SEMÂNTICA     conceito universal → endereço de taxonomia → métrica versionada
L1  DADOS         bronze (raw imutável) → prata (tipado) → ouro (point-in-time)
L0  FONTES        CVM · B3 · BCB · IBGE · RI · (vendor licenciado, futuro)
```

Duas fronteiras são inegociáveis:

- **L4 nunca toca dados diretamente.** Escreve código executado em sandbox contra
  DuckDB montado read-only. Não existe caminho pelo qual um LLM escreva no storage.
- **L0 nunca interpreta.** Providers baixam bytes e registram procedência; parsing é
  L1. É o que permite reprocessar anos de documentos com um parser corrigido, sem
  re-baixar e sem depender de o servidor externo responder hoje o que respondeu ontem.

### Abstração de fontes

```
SourceProvider (ABC)
├── PublicSourceProvider        HTTP público, sem credencial
│   ├── CVMProvider             ✅ Fase 0
│   ├── B3Provider              pendente
│   ├── BCBProvider             pendente
│   ├── IBGEProvider            pendente
│   └── CompanyIRProvider       pendente
└── LicensedSourceProvider      ponto de extensão; não implementado
```

A lógica de pesquisa nunca conhece o provider. Plugar Bloomberg / LSEG / FactSet /
Capital IQ depois é adicionar uma subclasse, não reformar o núcleo. Todo retrieval
grava `source_tier`, o que permite auditar depois quanto de uma conclusão dependeu
de fonte fraca ou licenciada.

---

## Modelo de dados do bronze

Duas noções deliberadamente separadas:

| | |
|---|---|
| **`RawDocument`** | um conteúdo único, identificado pelo SHA-256 dos bytes. Imutável, armazenado uma vez. |
| **`Retrieval`** | uma *observação*: em tal instante, tal URL de tal provider devolveu tal conteúdo. |

Muitos retrievals podem apontar para o mesmo documento. **Dois retrievals do mesmo
recurso lógico apontando para hashes diferentes são a evidência primária de uma
reapresentação** — e ambas as versões continuam em disco.

`pat history cvm.dfp 2024` mostra exatamente isso.

---

## A camada semântica (L2)

O problema que ela resolve: `3.01` não é "receita líquida". É onde a receita
líquida aparece **no plano de contas da CVM**. Confundir as duas coisas é o que
torna um sistema de research permanentemente brasileiro.

Por isso três eixos ficam separados:

```
CONCEITO     revenue_net, d_and_a_pnl, capex…       universal, sem jurisdição
    ▲
    │ binding — afirmação humana de equivalência semântica
    │
ENDEREÇO     onde a ideia aparece numa TAXONOMIA
             BR: cvm.plano_padronizado  → DRE/3.01
             US: us-gaap.xbrl           → us-gaap:Revenue… (não implementado)
    ▲
REGIME       framework · jurisdição · fonte  — metadado do MAPEAMENTO
```

`ebitda@v1 = ebit@v1 + d_and_a@v1` é escrita uma vez e vale em qualquer regime.
Os módulos em `semantics/definitions/` **não podem** mencionar `cd_conta` — há
um teste que falha se mencionarem, e outro que roda as mesmas métricas contra um
regime fictício em dólar para provar que a promessa é real.

### Nada de sinônimo automático

"Net Revenue", "Revenue" e "Sales" nunca são equiparados por semelhança de
rótulo. Um `[[binding]]` só existe com `equivalence_basis` preenchido — a
justificativa de *por que* aquela linha é aquele conceito. Rótulos são gravados
(`label_as_reported`) e conferidos por `pat mapping-check`, mas jamais usados
para busca: uma conta renomeada tem que quebrar o teste, não resolver em
silêncio para outra coisa.

### Fidelidade, e por que ela viaja

Nem todo regime publica todo conceito com a mesma limpeza. `d_and_a_pnl` — a
depreciação que passou pelo resultado — tem código estável no fluxo de caixa de
**cada empresa**, mas não no plano padronizado. Então:

| | binding | fidelidade |
|---|---|---|
| família default | DVA 7.04.01 (D&A retida do valor adicionado) | `approximate` |
| mapeamento do GPA | DFC_MI 6.01.01.04 (reversão de D&A) | `exact` |

A aproximação é legítima e útil; escondê-la não seria. `fidelity` sobe pelo
grafo até o `MetricResult` — um EBITDA montado sobre binding aproximado **diz
isso**, em todo relatório. No GPA a diferença é de 0,3% em FY2023 e de 46% em
FY2022 (operações descontinuadas): exatamente o erro intermitente que passa
despercebido quando o sistema trata as duas grandezas como sinônimas. Por isso
`d_and_a_pnl` e `d_and_a_retained` são conceitos **distintos**.

### Fluxo por projeto

Um arquivo TOML por empresa, herdando da família e sobrescrevendo só o que
diverge. O mapeamento do GPA tem **um** binding.

```bash
pat concepts                       # que conceitos existem, e o que significam
pat accounts --cod-cvm 14826 --statement DFC_MI \
    --period-end 2023-12-31 --as-of 2025-06-30   # o que a empresa publicou
# escolha a linha, escreva o binding com equivalence_basis, e então:
pat mapping-check --cod-cvm 14826 --period-end 2023-12-31 --as-of 2025-06-30
```

`pat accounts` é ferramenta para um humano escolher a linha. Não há inferência
automática em lugar nenhum do caminho.

### Métricas da Fase 2

`receita_liquida@v1` · `ebit@v1` · `d_and_a@v1` · `ebitda@v1` ·
`margem_ebitda@v1`

Decisões contábeis tomadas explicitamente, e registradas no `rationale` de cada
definição:

- **`ebit@v1` inclui equivalência patrimonial**, por ser o resultado
  operacional *como reportado*. Não é detalhe: no GPA FY2023 o EBIT de
  R$ 677 MM contém R$ 768 MM de equivalência — sem ela seria −R$ 91 MM. Um EBIT
  ex-equivalência é outra métrica, não uma correção desta.
- **`ebitda@v1` não é EBITDA ajustado divulgado.** Esse vem do RI, tier
  `issuer`, não é derivável da DFP, e jamais serve de fallback.
- **Versões são pinadas.** `ebitda@v1` depende de `ebit@v1`, nunca do "ebit
  atual". Publicar `ebit@v2` deixa `ebitda@v1` bit a bit idêntica.

```bash
pat metric ebitda@v1 --cod-cvm 14826 --period-end 2023-12-31 --as-of 2024-06-30
pat metric ebitda@v1 --cod-cvm 14826 --period-end 2023-12-31 --as-of 2025-06-30
```

As duas devolvem números diferentes — R$ 1.813 MM e R$ 1.796 MM — porque a
reapresentação do GPA atravessa a camada semântica intacta. Cada resultado traz
`knowledge_date`, `fidelity`, hash da cadeia de mapeamento e o `fact_id` de cada
insumo, que resolve até o blob no bronze.

### Quando não dá para calcular

Nunca zero, nunca parcial, nunca `None` que se lê como zero. `MetricUnavailable`
carrega motivo nomeado (`missing_concept`, `missing_fact_as_of`, `no_mapping`,
`mixed_currency`, `scope_not_available`, `division_by_zero`…), o conceito que
faltou, os endereços tentados e o que fazer a respeito. Moeda **nunca** é
convertida implicitamente.

---

## Uso

```bash
uv sync --locked --extra dev    # instala exatamente o que está em uv.lock
export PAT_HOME=data

pat init                             # diretórios + esquema
pat sources                          # providers e datasets disponíveis
pat fetch cvm.cad_cia_aberta         # cadastro de companhias
pat fetch cvm.dfp --year 2020-2024   # demonstrações anuais
pat fetch cvm.itr --year 2024        # trimestrais
pat status                           # volume e recursos alterados
pat history cvm.dfp 2024             # versões de conteúdo de um recurso
pat changed                          # recursos que mudaram na origem
pat verify                           # reconfere o hash de cada blob
pat runs                             # execuções recentes
```

Re-buscar é barato e não duplica armazenamento — e é necessário: é assim que
reapresentações são detectadas. Rode `pat fetch` periodicamente sobre anos já
ingeridos.

### Do bronze ao fato consultável

`build` não usa rede: lê bytes já armazenados. Quando um recurso tem mais de uma
versão de conteúdo no bronze, **todas** são processadas — cada uma traz seu próprio
`DT_RECEB`, então as duas coexistem no gold com datas de conhecimento diferentes.

```bash
pat build cvm.dfp --year 2020-2024            # bronze → silver → gold
pat build cvm.dfp --year 2024 --cod-cvm 9512  # só uma companhia

# valor de receita líquida da Petrobras conhecido em duas datas diferentes
pat asof --cod-cvm 9512 --conta 3.01 --period-end 2023-12-31 --as-of 2024-06-30
pat asof --cod-cvm 9512 --conta 3.01 --period-end 2023-12-31 --as-of 2025-06-30

pat fact-history --cod-cvm 9512 --conta 3.01 --period-end 2023-12-31
pat restatements --min-pct 5      # o que mudou entre versões, e quanto
pat restatements --consolidated   # só o escopo consolidado (default: os dois)
pat provenance <fact_id>          # do número até o byte: blob, membro, linha
```

Se as duas consultas `asof` acima devolvem valores diferentes, houve
reapresentação — e os dois números continuam consultáveis, cada um pela data em
que era verdade. É esse o critério de conclusão da Fase 1.

### Testes

```bash
uv run pytest              # suite padrão (sem rede) — 243 testes
uv run pytest -m network   # contra a CVM real — 11 testes
```

`uv lock --check` verifica que o lockfile ainda reflete o `pyproject.toml`.
Nunca instale com dependências resolvidas na hora: `--locked` é o que mantém I3.

---

## Layout

```
src/pat/
├── contracts/       schemas Pydantic — sem dependências internas, tudo depende daqui
│   ├── common.py      SourceTier, Sha256, AwareDatetime, Frozen
│   ├── entities.py    Company (CNPJ, cod_cvm, tickers)
│   ├── documents.py   RawDocument, Retrieval, ResourceRef, HttpMeta
│   ├── lineage.py     Lineage, Run
│   ├── silver.py      AccountLine — uma linha de CSV da CVM, tipada
│   ├── facts.py       Fact bitemporal
│   └── semantics.py   Concept, LineAddress, ConceptBinding, Mapping, MetricResult
├── sources/         L0 — busca bytes com procedência, nunca interpreta
│   ├── base.py        SourceProvider, PublicSourceProvider, LicensedSourceProvider
│   ├── registry.py    resolve dataset_id → provider
│   └── public/cvm.py  DFP, ITR, cadastro
├── parse/           L1 — bytes → linhas tipadas
│   └── cvm_dfp.py     abre o ZIP da DFP, tipa cada CSV, registra o que descartou
├── store/           L1 — bronze imutável + catálogo + silver + gold
│   ├── bronze.py      content-addressed, atômico, somente-leitura, verificável
│   ├── catalog.py     runs, documentos, retrievals, detecção de mudança
│   ├── silver.py      persiste AccountLine; idempotente por silver_id
│   ├── gold.py        escala, tipo de período, validação → Fact. Append-only.
│   └── db.py          conexão e esquema
├── semantics/       L2 — conceitos universais, mapeamentos por regime, métricas
│   ├── concepts.py    catálogo universal; não menciona plano de contas
│   ├── definitions/   uma métrica por módulo, versionada; proibido importar frameworks/
│   ├── mappings/br/   dados TOML: família CVM + um arquivo por empresa
│   ├── loader.py      TOML → Mapping, herança, sha256 da cadeia
│   ├── registry.py    registro explícito + validação do DAG no import
│   ├── engine.py      executa o grafo contra um FactResolver (porta)
│   ├── resolver.py    Protocol FactResolver — a fronteira com a fonte
│   ├── check.py       os bindings ainda resolvem na origem?
│   └── frameworks/cvm_dfp/   único lugar que conhece cd_conta e cod_cvm
├── query/asof.py    única porta de leitura do gold; toda consulta exige AS OF
├── audit/run.py     manifesto de execução (versão, git sha, timestamps)
├── ingest.py        orquestração: resolve → fetch → put → record
├── build.py         orquestração: bronze → silver → gold, sem rede
└── cli.py

data/                gitignored
├── bronze/blobs/    imutável. NUNCA editar, NUNCA sobrescrever.
├── bronze/meta/     sidecar JSON com procedência de cada blob
└── warehouse.duckdb catálogo + silver + gold (derivado; reconstruível)
```

O bronze é a fonte de verdade; o DuckDB é derivado. Se o banco for perdido, pode ser
reconstruído a partir dos sidecars. O inverso não é verdade.

---

## Fases

| | Escopo | Critério de conclusão | |
|---|---|---|---|
| **0** | contratos, bronze, provider CVM, catálogo | todo byte rastreável à origem | ✅ |
| **1** | silver + gold bitemporal + `query AS OF` | consulta prova diferença antes/depois de uma reapresentação | ✅ |
| **2** | semantics: conceitos universais, mapeamentos por regime, métricas versionadas | golden tests batendo com demonstrações conferidas à mão | ✅ |
| **3** | sandbox + coder + charter | pergunta → código → número correto, sem LLM fazendo conta | |
| **4** | planner, writer, critic, pipeline | relatório com toda afirmação citando `fact_id` | |
| **5** | B3 (preços, proventos), BCB, IBGE | | |
| **6** | SEC EDGAR (EUA) — reusa `contracts`, novo provider | | |

**Fases 0–2 antes de qualquer linha de agente.** Um agente sobre dados não confiáveis
produz erros eloquentes e confiantes — o pior resultado possível para research.

## Riscos conhecidos

1. **Reapresentações da CVM** — endereçado por I1 + bronze content-addressed.
2. **Planos de conta heterogêneos entre empresas** — endereçado por
   `semantics/mappings/`: um TOML por empresa, herdando da família e
   sobrescrevendo só o que diverge. Continua sendo a maior fonte de esforço
   não-automatizável do projeto; não há atalho, e de propósito — a alternativa
   seria inferir equivalência por rótulo.
3. **Ajustes não-padronizados** (EBITDA ajustado, não-recorrentes) — por isso métricas
   são versionadas e explícitas, nunca inferidas pelo LLM. EBITDA ajustado
   divulgado é conceito e fonte separados, jamais fallback de `ebitda@v1`.

6. **Aproximação invisível** — endereçado por `fidelity` no binding, que sobe
   até o `MetricResult`. Um número aproximado que se apresenta como exato é pior
   que número nenhum.
4. **Eventos societários** (splits, bonificações, incorporações) — tratamento próprio
   antes de qualquer série de preços. Fase 5.
5. **Deriva de modelo** — mitigado por cache de LLM endereçado por conteúdo e model id
   no manifesto de run. Fase 4.

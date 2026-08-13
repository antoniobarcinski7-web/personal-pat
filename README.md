# personal-pat

Personal Investment Research Agent — Equity Research brasileiro (depois EUA e macro).

Inspirado na arquitetura pública do PAT (Bridgewater / AIA Labs): pipeline modular
que espelha o fluxo de um analista, com estágios inspecionáveis e o LLM produzindo
*código*, nunca números.

**Estado atual: Fase 1.** Espinha dorsal de dados, mais o pipeline
bronze → silver → gold e consulta `AS OF`. Não há agente nem métricas canônicas
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
L2  SEMÂNTICA     métricas canônicas versionadas (ebitda@v2, roic@v1…)
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
uv run pytest              # suite padrão (sem rede)
uv run pytest -m network   # contra a CVM real
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
│   └── facts.py       Fact bitemporal
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
| **2** | semantics: ~15 métricas versionadas | golden tests batendo com demonstrações conferidas à mão | |
| **3** | sandbox + coder + charter | pergunta → código → número correto, sem LLM fazendo conta | |
| **4** | planner, writer, critic, pipeline | relatório com toda afirmação citando `fact_id` | |
| **5** | B3 (preços, proventos), BCB, IBGE | | |
| **6** | SEC EDGAR (EUA) — reusa `contracts`, novo provider | | |

**Fases 0–2 antes de qualquer linha de agente.** Um agente sobre dados não confiáveis
produz erros eloquentes e confiantes — o pior resultado possível para research.

## Riscos conhecidos

1. **Reapresentações da CVM** — endereçado por I1 + bronze content-addressed.
2. **Planos de conta heterogêneos entre empresas** — exigirá trabalho manual por
   empresa em `semantics/aliases`. Maior fonte de esforço não-automatizável do
   projeto; não há atalho.
3. **Ajustes não-padronizados** (EBITDA ajustado, não-recorrentes) — por isso métricas
   são versionadas e explícitas, nunca inferidas pelo LLM.
4. **Eventos societários** (splits, bonificações, incorporações) — tratamento próprio
   antes de qualquer série de preços. Fase 5.
5. **Deriva de modelo** — mitigado por cache de LLM endereçado por conteúdo e model id
   no manifesto de run. Fase 4.

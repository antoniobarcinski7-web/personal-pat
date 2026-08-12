# personal-pat

Personal Investment Research Agent — Equity Research brasileiro (depois EUA e macro).

Inspirado na arquitetura pública do PAT (Bridgewater / AIA Labs): pipeline modular
que espelha o fluxo de um analista, com estágios inspecionáveis e o LLM produzindo
*código*, nunca números.

**Estado atual: Fase 0.** Espinha dorsal de dados. Não há agente, parsing ou
métricas ainda — isso é deliberado (ver "Fases").

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
Bronze imutável (reprocessar nunca depende de re-baixar), dependências pinadas,
timestamps sempre timezone-aware, todo run registrado com versão de código.

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
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
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

### Testes

```bash
.venv/bin/pytest              # suite padrão (sem rede)
.venv/bin/pytest -m network   # contra a CVM real
```

---

## Layout

```
src/pat/
├── contracts/       schemas Pydantic — sem dependências internas, tudo depende daqui
│   ├── common.py      SourceTier, Sha256, AwareDatetime, Frozen
│   ├── entities.py    Company (CNPJ, cod_cvm, tickers)
│   ├── documents.py   RawDocument, Retrieval, ResourceRef, HttpMeta
│   ├── lineage.py     Lineage, Run
│   └── facts.py       Fact bitemporal  ← alvo da Fase 1
├── sources/         L0 — busca bytes com procedência, nunca interpreta
│   ├── base.py        SourceProvider, PublicSourceProvider, LicensedSourceProvider
│   ├── registry.py    resolve dataset_id → provider
│   └── public/cvm.py  DFP, ITR, cadastro
├── store/           L1 — bronze imutável + catálogo DuckDB
│   ├── bronze.py      content-addressed, atômico, somente-leitura, verificável
│   ├── catalog.py     runs, documentos, retrievals, detecção de mudança
│   └── db.py          conexão e esquema
├── audit/run.py     manifesto de execução (versão, git sha, timestamps)
├── ingest.py        orquestração: resolve → fetch → put → record
└── cli.py

data/                gitignored
├── bronze/blobs/    imutável. NUNCA editar, NUNCA sobrescrever.
├── bronze/meta/     sidecar JSON com procedência de cada blob
└── warehouse.duckdb catálogo (derivado; reconstruível a partir do bronze)
```

O bronze é a fonte de verdade; o DuckDB é derivado. Se o banco for perdido, pode ser
reconstruído a partir dos sidecars. O inverso não é verdade.

---

## Fases

| | Escopo | Critério de conclusão | |
|---|---|---|---|
| **0** | contratos, bronze, provider CVM, catálogo | todo byte rastreável à origem | ✅ |
| **1** | silver + gold bitemporal + `query AS OF` | consulta prova diferença antes/depois de uma reapresentação | |
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

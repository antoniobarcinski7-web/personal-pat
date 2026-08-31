# personal-pat

Personal Investment Research Agent — Equity Research brasileiro (depois EUA e macro).

Inspirado na arquitetura pública do PAT (Bridgewater / AIA Labs): pipeline modular
que espelha o fluxo de um analista, com estágios inspecionáveis e o LLM produzindo
*código*, nunca números.

**Estado atual: Fase 3, Milestone 1.** Espinha dorsal de dados (bronze → silver
→ gold, consulta `AS OF`), camada semântica (conceitos universais, mapeamentos
por regime, métricas versionadas) e o núcleo determinístico da camada de
pesquisa: plano declarativo → validação → resolução → execução → renderização →
resposta com citações, tudo auditável e sem uma linha de código de LLM. O
planejador e o escritor entram nos Milestones 2 e 3.

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
L4  LINGUAGEM     planner → (plano) · writer → (prosa com tokens)
                  ↑ só emite PLANO e TEXTO, nunca números
L3  PESQUISA      plano → validador → resolver → executor → renderer → resposta
                  determinístico, DuckDB read-only, sem código gerado
L2  SEMÂNTICA     conceito universal → endereço de taxonomia → métrica versionada
L1  DADOS         bronze (raw imutável) → prata (tipado) → ouro (point-in-time)
L0  FONTES        CVM · B3 · BCB · IBGE · RI · (vendor licenciado, futuro)
```

O caminho completo de uma pergunta:

```
Question → Planner → ResearchPlan → Validator → Resolver → Executor → Renderer → Writer → Answer
             (LLM)                                                                  (LLM)
```

Duas fronteiras são inegociáveis:

- **L4 nunca toca dados diretamente.** O planejador recebe um *capability
  snapshot* — o que existe, nunca quanto vale — e devolve um plano declarativo.
  O escritor recebe tokens (`{{s:margin_fy2024}}`) e seus rótulos, nunca os
  valores. Não existe caminho pelo qual um LLM escreva no storage, leia um fato
  ou copie um número que não lhe foi mostrado.
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

### Pesquisa (L3) — o caminho determinístico

O pipeline inteiro roda **sem LLM**, e continua sendo o default. Um plano
declarativo em JSON entra no lugar do planejador, o que é a forma de provar que
o resto não depende dele: nenhum número do sistema precisa de um modelo para
existir.

```bash
pat capability              # o que o sistema sabe executar, e o hash disso
pat capability --json       # os bytes canônicos, como o planejador os verá

pat ask --plan-file tests/research/plans/gpa_margin_fy24_fy23.json --dry-run
pat ask --plan-file tests/research/plans/gpa_margin_fy24_fy23.json

pat runs --research                  # corridas de pesquisa registradas
pat runs --research <manifest_id>    # uma corrida específica
```

O `capability snapshot` diz **o que existe** — conceitos, métricas, mapeamentos,
empresas, períodos cobertos, derivações disponíveis — e nunca **quanto vale**:
não há um único `Decimal` nele, e há teste que falha se aparecer. É a única coisa
que o planejador vê.

### Pesquisa (L3) — o caminho com modelo

São **duas invocações**, de propósito: o plano sai em disco, um humano lê o que
o modelo escolheu, e só então alguma coisa executa. Encadear os dois faria a
primeira execução de um plano acontecer antes de qualquer chance de revisá-lo.

```bash
export ANTHROPIC_API_KEY=...

pat plan "Qual foi a margem EBITDA do GPA em 2024?" --as-of 2025-06-30 --out p.json
pat ask --plan-file p.json --writer
```

O LLM entra em exatamente dois pontos, e em nenhum deles produz um número:

- **Planejador** — pergunta → `ResearchPlan`. A gramática de plano não tem onde
  escrever um valor; não existe campo para isso.
- **Escritor** — resultados → prosa. Ele não *recebe* os números: o prompt leva
  rótulo e token (`{{s:margin_fy2024}}`), nunca o valor. A substituição é
  determinística e acontece depois. Não dá para copiar um número que nunca foi
  mostrado.

Sem `--writer`, `pat ask` não chama modelo nenhum. Os dois comandos falham
dizendo que falta `ANTHROPIC_API_KEY` em vez de cair para um modo degradado, e
prosa recusada pela regra do dígito **não** vira prosa determinística em
silêncio — apresentar um texto que ninguém pediu como se fosse o pedido é pior
do que falhar.

Cada chamada fica gravada em `llm_call` com o papel (`planner`/`writer`), o
hash do prompt e da resposta, e o `client_fingerprint`. A do planejador nasce
órfã (planejar não executa nada); a do escritor nasce ligada ao manifesto.

`ask` valida o plano antes de executá-lo, e o executor só aceita plano
certificado — não há caminho que aceite um `ResearchPlan` cru. Toda saída
carrega citação por token, e cada citação resolve até o `fact_id` e o byte no
bronze:

```
RESPOSTA
  FY2023: 10.09%; FY2024: 3.89%; variacao: -6.20%.

CITACOES
  {{s:margin_fy2023}}   10.09%   67d834df61b6
    margem_ebitda@v1, consolidado, exercicio findo em 2023-12-31, AS OF 2025-06-30
```

Cada execução grava um manifesto em `research_run`: plano, pergunta, hash do
capability, métricas, mapeamentos, fatos-folha, versão do `pat` e `git_sha`.
Uma corrida cujo resultado não pôde ser calculado **também** é registrada — a
auditoria de por que não houve resposta vale tanto quanto a de por que houve.

### Conversa (L4) — a interface local

```bash
export ANTHROPIC_API_KEY=...
pat serve                      # http://127.0.0.1:8765
```

O chat é uma **casca** sobre o caminho acima, e não um caminho novo. Cada
mensagem é uma execução completa de `pat plan` + `pat ask --writer` em processo:
duas chamadas de modelo, um manifesto em `research_run`, duas linhas em
`llm_call`. Nada é reaproveitado entre turnos.

O histórico da conversa entra em **um lugar só** — o prompt do planejador — e
carrega apenas estrutura: que empresas, que métricas, que períodos e que escopo
foram planejados antes, e se o turno foi recusado. `ConversationContext` não tem
campo capaz de carregar um `Decimal`, do mesmo jeito que `MetricStep` não tem.
É isso que faz "todo número vem do motor, recalculado neste turno" ser uma
propriedade da forma do tipo, e não uma instrução de prompt.

O follow-up funciona por interpretação, nunca por herança de limite:

```
você  Compare o EBITDA de Petrobras, Vale e WEG em FY2024.
PAT   … R$ 272.00 bn … R$ 81.00 bn … R$ 6.70 bn        [Ver plano] [Ver fontes]
você  E a margem EBITDA?                    → mesmas empresas, mesmo período
você  E como isso mudou desde 2023?         → mesmas empresas, período ACRESCENTADO
você  Qual teve a maior queda?              → recusa nomeada
```

A última recusa é o comportamento correto: `min` e `max` devolvem o valor
extremo, nunca *qual* insumo o produziu, e responder exigiria um `argmin` que
não existe. Devolver o menor `delta_pct` sem dizer de quem ele é seria um número
certo para uma pergunta que ninguém fez.

Os `pinned_*` da UI continuam sendo o que o **usuário** fixou. Contexto de
conversa não vira pino — herdar `pinned_periods=(2024,)` faria "desde 2023" ser
recusado com `PIN_CONTRADICTED_PERIOD`.

Todo turno é auditável pelas mesmas portas de sempre:

```bash
pat runs --research <manifest_id>              # a corrida que produziu a resposta
curl localhost:8765/api/turn/<sessao>/<n>/plan > p.json
pat ask --plan-file p.json                     # reproduz os números, SEM modelo
```

O servidor é `http.server` da biblioteca padrão, escuta só em `127.0.0.1`, não
tem autenticação e não deve ser exposto. O log das sessões fica em `data/chat/`
e é conveniência de UI: apagá-lo não perde nada auditável, porque a auditoria
mora em `research_run` e `llm_call`.

### Testes

```bash
uv run pytest              # suite padrão (sem rede, sem LLM) — 626 testes
uv run pytest tests/research  # só a camada de pesquisa — 383
uv run pytest -m network   # contra a CVM real — 11 testes
uv run pytest -m llm       # contra a API real — 13 testes (gasta token)
```

Os dois marcadores estão desligados no `addopts` por razões diferentes:
`network` depende de terceiro, `llm` gasta credencial. Nenhum dos dois roda por
acidente, e há teste conferindo que continua assim
(`test_layering_research.py::test_a_suite_padrao_desliga_os_marcadores_que_custam`).

Se `pat` falhar com `ModuleNotFoundError: No module named 'pat'` logo depois de
um `uv sync` que terminou sem erro, cheque `ls -lO .venv/lib/python*/site-packages/*.pth`:
o CPython 3.13+ **ignora `.pth` marcado como oculto** (um arquivo escondido não
deve injetar `sys.path` em silêncio), e o `uv` marca os `.pth` do editable
install exatamente assim no macOS. `chflags -R nohidden .venv` resolve.

O que a nota anterior não dizia, e que importa na prática: **o `uv run` volta a
marcar o arquivo como oculto**, então o `chflags` não é definitivo — todo `uv
run` desfaz o conserto do `.venv/bin/pat` seguinte. Duas saídas que funcionam
sempre:

```bash
PYTHONPATH=src .venv/bin/pat ...          # ignora o .pth de vez
find .venv -name '*.pth' -exec chflags nohidden {} \;   # depois de cada uv run
```

Os testes não pegam isso porque o pytest injeta `pythonpath = ["src"]` por
conta própria — é por isso que `uv run pytest` passa com o entry point quebrado.

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
│   ├── semantics.py   Concept, LineAddress, ConceptBinding, Mapping, MetricResult
│   └── research.py    ResearchPlan, PlanStep, ComputationResult, Claim, manifesto
│                      ← nenhum passo de plano aceita Decimal: número literal é
│                        inexprimível na gramática, não apenas proibido
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
│   ├── research.py    persiste o manifesto de pesquisa. Quem calcula não grava.
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
├── research/        L3 — plano declarativo → resultado auditável. Sem LLM ainda.
│   ├── canonical.py   JSON canônico + sha256: uma identidade, uma implementação
│   ├── capability.py  o que o sistema sabe fazer; jamais um valor financeiro
│   ├── validate.py    validação pura: sem banco, sem relógio, sem rede
│   ├── resolve.py     o que só o warehouse sabe responder
│   ├── derive.py      as 7 derivações fechadas e suas condições de recusa
│   ├── execute.py     único módulo que segura um Engine; só aceita plano certificado
│   ├── render.py      único lugar que formata número para exibição
│   ├── answer.py      montagem de claims, substituição de token, regra do dígito
│   └── manifest.py    o que foi executado, com que versões e sob que hashes
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
| **3** | camada de pesquisa controlada: plano declarativo, validador, executor determinístico, renderer, manifesto | `pat ask` produz o número certo, com citação até o byte, sem LLM no caminho do cálculo | ✅ |
| **4** | planner e writer atrás de um Protocol; cache e procedência de modelo | relatório com toda afirmação citando `fact_id`, e nenhum dígito escrito pelo modelo | ✅ |
| **5** | B3 (preços, proventos), BCB, IBGE | | |
| **6** | SEC EDGAR (EUA) — reusa `contracts`, novo provider | | |

A Fase 3 abandonou o desenho original de *sandbox + coder*: sem código gerado,
não há sandbox a proteger. O modelo escolhe **o que** perguntar, dentro de uma
gramática fechada onde um número literal é inexprimível; o cálculo continua sendo
o motor da Fase 2. É uma superfície de ataque menor e uma garantia mais forte —
não "o LLM foi instruído a não calcular", e sim "não há onde ele escreveria um
número". Os três milestones estão fechados: M1 (núcleo determinístico), M2
(planner, porta de LLM, cache e procedência) e M3 (writer), detalhados em
`docs/phase3_proposal.md`.

Nota de numeração: o que a tabela chamava de Fase 4 foi entregue **dentro** da
Fase 3, como M2 e M3 — o planner e o writer nasceram atrás do mesmo Protocol
previsto ali. A linha 4 fica marcada como concluída em vez de renumerada,
porque o critério dela é o que os testes hoje verificam. As Fases 5 e 6
continuam intocadas.

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

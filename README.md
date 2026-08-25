# personal-pat

Personal Investment Research Agent — Equity Research brasileiro (depois EUA e macro).

Inspirado na arquitetura pública do PAT (Bridgewater / AIA Labs): pipeline modular
que espelha o fluxo de um analista, com estágios inspecionáveis e o LLM produzindo
*código*, nunca números.

**Estado atual: Fase 5 concluida (M5.1–M5.6).** Espinha dorsal de dados (bronze →
silver → gold, consulta `AS OF`), camada semântica (conceitos universais,
mapeamentos por regime, métricas versionadas), camada de pesquisa completa
(plano declarativo → validação → resolução → execução → renderização → resposta
com citações, com planner e writer atrás de um Protocol), o primeiro corpus
qualitativo — documentos da CVM extraídos, indexados e citáveis verbatim, com
`published_at ≤ as_of` garantido por construção e nenhum modelo no caminho da
evidência — a decomposição quantitativa, que abre a variação de um total nas
partes que a produziram com residual explícito, o programa de pesquisa, que
junta as duas metades num artefato revisável antes de executar, o grafo de
afirmações com critic mecânico, em que nenhuma frase existe sem procedência
tipada, o Company Workspace, que só se declara pronto quando seis requisitos
objetivos estão satisfeitos, e a **segunda jurisdição**: SEC/EDGAR, regime
us-gaap e decomposição por segmento operacional.

O objetivo da Fase 5 é o **Company Research Workspace**: uma empresa por vez,
combinando evidência quantitativa e qualitativa para responder não só "quanto
foi" mas "por quê".

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
│   ├── SECProvider             ✅ Fase 5 (M5.6)
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
             US: us-gaap.xbrl           → us-gaap:GrossProfit (✅ M5.6)
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

---

## Segunda jurisdição: SEC e us-gaap — Fase 5, M5.6

```bash
export PAT_SEC_USER_AGENT="Seu Nome (seu@email)"   # a SEC exige contato; 403 sem ele
pat company --cik 50863
pat decompose revenue_by_segment@v1 --cik 50863 --from 2023-12-31 --to 2024-12-31 --as-of 2026-06-30
```

**Nenhum conceito, métrica ou decomposição mudou para o regime americano
existir.** `ebitda@v1` foi escrita pensando na CVM e roda sobre a Intel sem uma
linha de alteração — era o que `test_second_framework.py` prometia com um regime
fictício desde a Fase 2.

| | Intel FY2023 | Intel FY2024 |
|---|---|---|
| receita_liquida@v1 | 54.228 MM USD | 53.101 MM USD |
| ebit@v1 | 93 MM USD | −11.678 MM USD |
| d_and_a@v1 | 9.602 MM USD | 11.379 MM USD |
| **ebitda@v1** | **9.695 MM USD** | **−299 MM USD** |

No mesmo motor, na mesma execução: Petrobras FY2024 em 204.234 MM BRL, regime
`ifrs_cpc_br/BR`.

### A entidade é universal; o identificador é local

`gold_fact` guarda **só** `entity_id`, opaco. CNPJ, `cod_cvm`, CIK e ticker vivem
em `entity`, uma linha por regime. Uma jurisdição nova não pede coluna nova —
pede uma linha. `--cod-cvm` continua existindo por compatibilidade, mas para numa
porta única e vira `entity_id` ali.

Três ausências que antes se confundiam agora são distinguíveis: entidade
inexistente, entidade sem fatos, e linha não publicada. Cada uma pede uma ação
diferente.

### Por que `equivalence_basis` existe

A Intel **não usa** `Revenues`. **Não usa** `CostOfRevenue`. E **não publica**
`DepreciationDepletionAndAmortization` — reporta D&A em dois elementos separados.
Um sistema que casasse elemento por semelhança de nome concluiria que a Intel não
divulga receita, ou acharia algo parecido de outra grandeza e devolveria um
número errado com confiança.

Não há família us-gaap, de propósito: a CVM padroniza o plano de contas, o
us-gaap não padroniza a escolha de elemento. Cada emissor americano ganha o seu
arquivo.

### O eixo SEGMENT

```
Petrobras → CVM → SEGMENT = NO_BREAKDOWN_SOURCE
Intel     → SEC → SEGMENT = resolução estruturada, resíduo ZERO
```

Receita da Intel FY2023→FY2024: All other −1.784, Intel Foundry −1.367, CCG
**+1.032**, eliminação intersegmento +742, DCAI +182, NEX +68 — soma −1.127,
resíduo **0**.

**Os membros são declarados no TOML, não descobertos.** A fonte publica os
membros mas não a hierarquia entre eles. Lado a lado com as folhas, a DERA
publica um roll-up (`ClientComputingGroupDatacenterAndAIAndNetworkAndEdge` = CCG
+ DCAI + NEX) e um recorte dentro da Foundry. Somar tudo contaria três segmentos
duas vezes — e o total pareceria plausível.

**A eliminação intersegmento é um membro, não um resíduo.** Sem ela a soma dos
segmentos excederia a consolidada em 17,2 bi, e esse valor apareceria como "não
explicado" — o que seria falso: é uma eliminação publicada.

---

## Segunda jurisdição: SEC e us-gaap — Fase 5, M5.6

```bash
export PAT_SEC_USER_AGENT="Seu Nome (seu@email)"   # a SEC exige contato; 403 sem ele
pat company --cik 50863
pat decompose revenue_by_segment@v1 --cik 50863 \
    --from 2023-12-31 --to 2024-12-31 --as-of 2026-06-30
```

**Nenhum conceito, métrica ou decomposição mudou para o regime americano
existir.** `ebitda@v1` foi escrita pensando na CVM e roda sobre a Intel sem uma
linha de alteração — era o que `test_second_framework.py` prometia com um regime
fictício desde a Fase 2.

| | Intel FY2023 | Intel FY2024 |
|---|---|---|
| receita_liquida@v1 | 54.228 MM USD | 53.101 MM USD |
| ebit@v1 | 93 MM USD | −11.678 MM USD |
| d_and_a@v1 | 9.602 MM USD | 11.379 MM USD |
| **ebitda@v1** | **9.695 MM USD** | **−299 MM USD** |

No mesmo motor, na mesma execução: Petrobras FY2024 em 204.234 MM BRL, regime
`ifrs_cpc_br/BR`.

### A entidade é universal; o identificador é local

`gold_fact` guarda **só** `entity_id`, opaco. CNPJ, `cod_cvm`, CIK e ticker vivem
em `entity`, uma linha por regime. Uma jurisdição nova não pede coluna nova —
pede uma linha. `--cod-cvm` continua existindo por compatibilidade, mas para numa
porta única e vira `entity_id` ali.

Três ausências que antes se confundiam agora são distinguíveis: entidade
inexistente, entidade conhecida sem fatos, e linha não publicada. Cada uma pede
uma ação diferente.

### Por que `equivalence_basis` existe

A Intel **não usa** `Revenues`. **Não usa** `CostOfRevenue`. E **não publica**
`DepreciationDepletionAndAmortization` — reporta D&A em dois elementos separados.
Um sistema que casasse elemento por semelhança de nome concluiria que a Intel não
divulga receita, ou acharia algo parecido de outra grandeza e devolveria um
número errado com confiança.

Não há família us-gaap, de propósito: a CVM padroniza o plano de contas, o
us-gaap não padroniza a escolha de elemento. Cada emissor americano ganha o seu
arquivo.

### O eixo SEGMENT

```
Petrobras → CVM → SEGMENT = NO_BREAKDOWN_SOURCE
Intel     → SEC → SEGMENT = resolução estruturada, resíduo ZERO
```

Receita da Intel FY2023→FY2024: All other −1.784, Intel Foundry −1.367, CCG
**+1.032**, eliminação intersegmento +742, DCAI +182, NEX +68 — soma −1.127,
resíduo **0**.

**Os membros são declarados no TOML, não descobertos.** A fonte publica os
membros mas não a hierarquia entre eles. Lado a lado com as folhas, a DERA
publica um roll-up (`ClientComputingGroupDatacenterAndAIAndNetworkAndEdge` = CCG
+ DCAI + NEX) e um recorte dentro da Foundry. Somar tudo contaria três segmentos
duas vezes — e o total pareceria plausível.

**A eliminação intersegmento é um membro, não um resíduo.** Sem ela a soma dos
segmentos excederia a consolidada em 17,2 bi, e esse valor apareceria como "não
explicado" — o que seria falso: é uma eliminação publicada.

`pat company` reflete isso: a Intel lista `revenue_by_segment@v1` entre as
decomposições disponíveis, a Petrobras não.

---

## Company Workspace e auditoria (L4/L5) — Fase 5, M5.5

```bash
pat company --cod-cvm 9512                              # o que se sabe, e o que falta
pat run-program --program-file p.json --writer --audit  # + critic de modelo
```

### `READY` não é um adjetivo

É a conjunção de **seis requisitos conferíveis**, cada um com nome próprio e
remédio de uma linha:

1. tem fatos no gold;
2. pelo menos **dois períodos** — sem dois não há variação, e sem variação não há
   pergunta causal a fazer;
3. mapeamento próprio e **conferido** (cair na família default é razoável para
   explorar, não para declarar a empresa pronta);
4. os conceitos que as decomposições exigem estão ligados;
5. há documentos no corpus;
6. há unidades indexadas na versão corrente do índice.

O contrato recusa `READY` com pendência **e** `DRAFT` sem pendência declarada: se
nada falta o estado é READY, se algo falta tem que ter nome.

```
pat company --cod-cvm 9512  → READY   Petrobras: 4.428 fatos, 3 períodos,
                                      mapeamento conferido, 3 decomposições,
                                      28 documentos, 4.676 unidades
pat company --cod-cvm 4170  → DRAFT   Vale: [no_documents] nenhum documento
                                      qualitativo — saída: pat docs --sync
```

`missing_concepts` e `extraction_failures` são campos de primeira classe, não
rodapé: cobertura que só mostra o que existe mente sobre si mesma.

### O critic de modelo julga o que a máquina não consegue julgar

Entra **depois** do critic mecânico e só sobre o que sobrou. Recebe o conjunto
**finito** de evidências recuperadas — inclusive as que o escritor **não** citou,
marcadas como tal. É isso que torna `SELECTIVE_EVIDENCE` possível: a pergunta
"existe um trecho que contradiz a conclusão e ficou de fora?" só tem resposta
porque o conjunto é fechado e auditável.

Ele não pesquisa, não busca documento novo, não acessa o warehouse, não corrige e
não rechama o escritor — conferido por AST.

### O modelo não escolhe sozinho o que bloqueia

Um achado de critic de modelo é ele próprio um julgamento não verificado.
Deixá-lo declarar qualquer coisa como dura seria dar-lhe veto sobre um relatório
possivelmente correto. A severidade que ele pede é limitada por código
versionado:

| pode bloquear | só acompanha |
|---|---|
| `SELECTIVE_EVIDENCE`, `CAUSAL_OVERREACH` | `QUOTE_OUT_OF_CONTEXT`, `STALE_EVIDENCE`, `MISSING_DECOMPOSITION`, `UNQUANTIFIED_MAGNITUDE` |

Os dois primeiros tornam o relatório **enganoso** — um cita só o que confirma, o
outro afirma causa onde há menção. Em research, enganoso é pior que ausente. Os
demais o deixam incompleto, e bloquear seria tratar "faltou dizer" como "disse
errado".

---

## Grafo de afirmações e critic (L4) — Fase 5, M5.4

Nenhuma frase de um relatório existe sem procedência tipada. Cinco espécies:

| espécie | procedência exigida | quem cria |
|---|---|---|
| `FACT` | `result_id` → `fact_id` → byte no bronze | motor |
| `CALCULATION` | `result_id`, e o total que ele ajuda a explicar | motor |
| `QUOTE` | `unit_id` → documento → byte | corpus |
| `INFERENCE` | `supports` (≥1) + `strength` | modelo |
| `CONCLUSION` | `supports` (≥1) + `falsified_by` (≥1) | modelo |

```bash
pat run-program --program-file p.json            # determinístico
pat run-program --program-file p.json --writer   # + relatório e critic
```

As três primeiras são verificáveis mecanicamente; as duas últimas não — e é por
isso que são espécies distintas. A diferença entre "a margem foi 3,89%" e "a
margem caiu por alavancagem operacional" não pode depender de o leitor prestar
atenção: ela é estrutural.

`strength` é enum (`quantified`/`attributed`/`suggested`), nunca número. Um
escore 0–1 seria um número produzido por modelo — e o pior tipo, porque
pareceria medido.

`falsified_by` é obrigatório numa conclusão. Não é formalidade: uma conclusão que
não diz o que a derrubaria é opinião, e na prática esse campo é a lista do que
monitorar no próximo trimestre.

### O portão contra a lavagem de número do emissor

**`CALCULATION` não pode se apoiar em `QUOTE`** — conferido na *construção* do
grafo, antes do critic, antes do escritor, antes de qualquer prompt. Um relatório
que violasse isso não chega a existir. O critic confere de novo, porque um grafo
pode chegar montado por outro caminho.

O grafo também recusa ciclo e recusa leitura que não alcance nenhum nó ancorado —
uma torre de inferências sem nada embaixo é como um relatório fica eloquente e
vazio.

### O critic mecânico: metade do critic não é um modelo

Taxonomia fechada. **Duro** bloqueia (`QUOTE_NOT_VERBATIM`,
`DIGIT_OUTSIDE_QUOTE`, `UNKNOWN_TOKEN`, `EVIDENCE_AFTER_AS_OF`,
`UNSUPPORTED_CLAIM`, `CONCLUSION_WITHOUT_FALSIFIER`, `ISSUER_NUMBER_LAUNDERED`);
**leve** acompanha a resposta visível (`FIDELITY_UNDISCLOSED`, `ORPHAN_RESULT`).

A distinção responde "esta resposta pode sair?" — e a resposta certa para um
número que não resolve até o byte é não. Um relatório que carrega a ressalva é
mais útil que um limpo por reescrita.

**Não há loop `writer → critic → writer`**, pela mesma razão que não há
retentativa de planejador: "criticar até passar" é um amostrador que uma hora
aprova algo errado.

### A regra do dígito ganha exceção tipada

Algarismo é permitido **dentro de bloco de citação, e só lá** — é o que permite
ao relatório dizer que a companhia falou em "queda de 18% do Brent" sem que esse
número vire insumo de nada. Fora dali, dígito só por substituição de token.

O critic roda **antes** da substituição: é a única ordem em que dá para
distinguir "o modelo escreveu um número" de "o sistema substituiu um token".

---

## Programa de pesquisa (L3/L4) — Fase 5, M5.3

Um `ResearchPlan` responde "quanto foi". Um `ResearchProgram` responde **o que
precisa ser investigado** — e junta as duas metades do workspace numa execução
auditável:

```
ResearchProgram
├── compute          métricas e derivações       (ResearchPlan v1, INTACTO)
├── decompositions   de onde a variação veio     (M5.2)
└── evidence         o que a administração disse (M5.1)
```

```bash
pat program "Por que o resultado operacional da Petrobras caiu em 2024?" \
    --as-of 2025-06-30 --out p.json     # dois estágios, usa a API
pat run-program --program-file p.json   # executa, SEM modelo nenhum
```

Como sempre, são **duas invocações**: o programa sai em disco, um humano lê o
que o modelo escolheu investigar, e só então alguma coisa executa.

### Por que o planejador tem dois estágios

Não dá para saber que evidência pedir antes de saber o que os números fizeram.
"Por que a receita caiu?" — a pergunta a fazer ao corpus depende de a queda ter
sido de 3% ou de 30%, e de qual componente puxou. Um prompt único escolheria a
citação antes de conhecer o número, que é exatamente como se produz uma
narrativa convincente e errada.

```
estágio 1  planner.compute    pergunta + capability → plano + decomposições
           [executa DETERMINISTICAMENTE, sem persistir nada]
estágio 2  planner.evidence   pergunta + FORMA dos resultados → buscas
```

Dois estágios não é retentativa: são dois papéis, com prompts e entradas
diferentes, cada um com sua própria `PlanProvenance` e sua própria linha em
`llm_call`. Colapsá-las esconderia qual estágio produziu o quê.

### O estágio 2 vê forma, nunca valor

| campo | exemplo |
|---|---|
| `direction` | `down` |
| `magnitude` | `large` — faixas fixas, em código versionado |
| `top_contributors` | `("operating_expenses_net", "revenue_net", "cogs")` |
| `residual_is_material` | a decomposição não fechou |
| `hits` | quantos trechos, jamais quais |

Saber que a despesa operacional puxou mais que a receita é o que permite buscar
o que a administração disse sobre despesa — e isso não exige saber quanto.

`ResultShape` não tem campo capaz de carregar um `Decimal`. Um teste confere por
AST; outro monta o **prompt real** com a decomposição real da Petrobras e
verifica que nenhum dos algarismos aparece nos bytes enviados. A garantia é a
forma do tipo, como em `MetricStep` e `ConversationContext`.

Os degraus de magnitude são código, não prompt: um modelo não decide o que é
"grande", ele recebe a classificação já feita.

### A ordem importa

Decomposição roda **antes** da busca, porque é ela que diz o que vale procurar.
Buscar primeiro seria escolher a citação e depois procurar o número que a
sustenta.

Falha parcial não aborta: um pedido que falha vira recusa nomeada e o programa
continua. Saber que a decomposição fechou mas o corpus não tem nada sobre o
assunto é um resultado útil.

---

## Decomposição (L3) — Fase 5, M5.2

`delta_pct` diz *quanto* mudou. A decomposição diz *de onde* a mudança veio:

```
delta_total = contribuição_A + contribuição_B + … + residual
```

É o primeiro passo de toda resposta causal, e ele é **quantitativo**. Um sistema
que pula direto para "o release diz que foi o Brent" está fazendo jornalismo, não
research — a citação explica a decomposição, nunca a substitui.

```bash
pat decompositions          # as identidades declaradas, e seus termos
pat decompose ebit_by_line@v1 --cod-cvm 9512 \
    --from 2023-12-31 --to 2024-12-31 --as-of 2025-06-30
```

Petrobras, FY2023 → FY2024 consolidado — EBIT cai R$ 52,1 bi:

| | de | para | efeito | parte |
|---|---|---|---|---|
| Despesas operacionais líquidas | 80.591 | 109.261 | −28.670 | 55,0% |
| Receita líquida | 511.994 | 490.829 | −21.165 | 40,6% |
| Custo dos bens e serviços | 242.061 | 244.367 | −2.306 | 4,4% |
| **Residual (não explicado)** | | | **0** | 0% |

(R$ milhões, fidelidade `exact`.)

### O residual é o ponto do desenho

`contribuições + residual == target_delta` é **exata**, em `Decimal`, conferida
na construção do contrato. Não existe forma de montar um `DecompositionResult`
cujas partes não somem o todo. A tolerância decide se a decomposição sai marcada
como **fechada** — nunca se ela pode existir.

O residual é impresso **sempre**, inclusive quando é zero: um relatório que só o
mostra quando incomoda ensina o leitor a não procurar por ele. E ele nunca é
rateado entre os membros, estimado por regressão ou normalizado para somar 100% —
cada uma dessas faria a conta fechar sem que ela feche, com a aparência exata de
uma análise correta.

Na prática, o residual é o que transforma um binding errado — que sairia como um
número plausível — em algo que se vê na tela.

**Membro presente em um só período recusa**, nunca vira zero. Uma companhia que
deixou de reportar um componente não contribuiu com "menos o valor inteiro do ano
passado"; ela mudou de apresentação, e as duas coisas se pareceriam idênticas no
gráfico.

### O eixo SEGMENT está bloqueado, e isso é o comportamento certo

`BreakdownAxis.SEGMENT` existe no vocabulário e recusa com
`NO_BREAKDOWN_SOURCE`. A DFP da CVM não publica segmento: o ZIP tem as
demonstrações padronizadas e nada mais. Segmento (IFRS 8) vive nas notas
explicativas, em PDF — e reconstruir qual número pertence a qual segmento a
partir de texto achatado é dedução por formato, o mesmo erro de casar conta por
rótulo.

Autorizar isso reverteria o invariante da M5.1 de que **texto de documento não
tem caminho até o gold**. A decisão registrada em `docs/phase5_proposal.md` é não
reverter: número de documento continua sendo citação, nunca insumo. O eixo é
retomado na M5.6, com SEC/XBRL, onde a dimensão de segmento é elemento de
taxonomia com valor tipado.

A recusa distingue "o sistema não tem por onde ler" de "a empresa não reporta" —
são ações diferentes para quem pesquisa, e colapsá-las numa lista vazia seria a
ausência que se lê como evidência de ausência.

---

## Corpus qualitativo (L1.5) — Fase 5, M5.1

O lado textual, construído com a mesma disciplina do lado numérico. Nenhum
modelo participa deste caminho — a recuperação inteira é determinística, e isso
é o critério, não um detalhe.

```
L1.5 CORPUS   SourceDocument → DocumentUnit → índice léxico → QuoteClaim
              published_at ≤ as_of, sempre. Texto verbatim, sempre.
```

A simetria com a Fase 3 é exata:

| numérico | textual |
|---|---|
| `Fact` | `DocumentUnit` |
| `MetricResult` | `EvidenceResult` |
| `MetricUnavailable` | `EvidenceUnavailable` |
| `NumericClaim` | `QuoteClaim` |
| `pat provenance <fact_id>` | `pat provenance-unit <unit_id>` |

```bash
pat fetch cvm.ipe --year 2024                    # catálogo de entregas ao regulador
pat docs --cod-cvm 9512 --sync --year 2024       # busca, extrai e indexa os documentos
pat docs --cod-cvm 9512                          # o acervo, e o que faltou dele
pat docs --cod-cvm 9512 --failures               # o que NÃO foi extraído, e por quê

pat evidence --cod-cvm 9512 --query "queda brent receita" --as-of 2024-12-31
pat provenance-unit <unit_id>                    # da citação até o byte
```

O catálogo IPE e o documento são **datasets separados** de propósito:
`cvm.ipe` é o índice anual de tudo que foi entregue ao regulador, e `cvm.ipe_doc`
busca os bytes de um documento. O provider não descobre a URL de um documento —
ela vem do catálogo, cuja leitura é da camada silver. Um provider que abrisse o
ZIP para achar o link estaria interpretando, que é exatamente o que L0 não faz.

### Os dois eixos de tempo valem para texto

`published_at` é o `knowledge_date` do documento; `reference_date` é o que o
emissor declarou como referência — e **não** é período coberto, porque o mesmo
campo do protocolo diz "2024-09-30" tanto para um relatório do 3T24 quanto para
uma ata de assembleia marcada naquele dia. Derivar período disso seria inferência
por formato, o mesmo erro de casar conta por rótulo.

Toda data carrega `DateBasis` — de onde ela veio. Uma data adivinhada que se
apresenta como lida é o análogo textual do número aproximado que se apresenta
como exato, e a base viaja até a citação.

Consulta ao corpus sem `as_of` não existe: `EvidenceQuery.as_of` não aceita
`None`, e o corte `published_at <= as_of` está no SQL, não numa checagem
posterior. As duas consultas abaixo devolvem conjuntos diferentes porque há
documento publicado entre elas:

```bash
pat evidence --cod-cvm 9512 --query "producao pre-sal recorde" --as-of 2024-06-30
pat evidence --cod-cvm 9512 --query "producao pre-sal recorde" --as-of 2024-12-31
```

A primeira vê 15 documentos, a segunda 28 — e o trecho que encabeça a resposta
muda. É o critério de conclusão da M5.1, e o análogo textual exato do critério
da Fase 1.

### Número de emissor é citação, nunca insumo

A regra nova da Fase 5, e a que mais importa. Um release diz "receita de
R$ 511,9 bilhões". Esse algarismo **pode** sair num relatório — dentro de uma
citação verbatim, atribuído a quem o publicou, resolvendo até o byte. O que ele
**não pode** é alimentar uma conta: não entra no gold, não vira insumo de
derivação, e não é comparado com métrica sem que os dois lados estejam
rotulados.

Como sempre neste projeto, isso é forma de tipo e não instrução de prompt:
`QuoteClaim` não tem `value`, não tem `unit`, não tem `currency` e não tem
`Decimal`. Não há por onde o número ser lido como quantidade. Um teste por AST
confere que nenhum campo desses apareça, e `test_layering_corpus.py` confere que
nenhum módulo da camada de texto tem caminho até `store/gold`.

### Extração: versionada, e que falha com nome

`extraction_version` embute a versão efetiva do `pypdf` — não a versão mínima do
`pyproject.toml`, que é outra coisa — e entra no `unit_id`. Reprocessar com
extrator novo cria unidades **ao lado** das antigas. É a regra de
`extractor_version` da Fase 1 aplicada a texto, e é o que faz uma citação de seis
meses atrás continuar resolvendo depois de um upgrade de biblioteca.

O texto de uma unidade é uma **fatia literal** do texto da página:
`page_text[char_start:char_end]`, sem reescrita, sem colapsar espaço, sem juntar
linha. Normalizar deixaria a citação mais bonita e menos verbatim, e a
conferência `reextrair → fatiar → comparar` deixaria de ser byte a byte. É essa
conferência que `pat provenance-unit` roda.

Falha de extração é registro de primeira classe, com motivo nomeado
(`no_text_layer`, `encrypted`, `malformed`, `unsupported_media_type`…), porque
documento sem unidade e sem falha é indistinguível de documento que nunca foi
buscado — e a diferença muda o que um analista conclui. **Não há fallback para
OCR**, e não deve passar a haver: uma citação vinda de reconhecimento óptico é um
texto que ninguém escreveu, e entraria no corpus indistinguível de uma real.

### O índice é derivado, e o escore não é grandeza financeira

Índice invertido explícito com BM25 em `Decimal`, e não a extensão FTS do
DuckDB — que baixaria binário da internet na primeira execução e traria a versão
que estivesse na máquina, deixando o ranking dependente disso. `index_version`
viaja em todo `EvidenceResult`. Apagar a tabela de tokens e reconstruir a partir
das unidades não perde nada; as unidades é que são a fonte, e o bronze é a fonte
delas.

`relevance` ordena texto: não tem unidade, não tem moeda, nunca entra num cálculo
e nunca aparece na prosa. Existe para tornar o ranking auditável — "por que este
trecho voltou em primeiro" tem que ter resposta.

### Testes

```bash
uv run pytest              # suite padrão (sem rede, sem LLM) — 961 testes
uv run pytest tests/research  # só a camada de pesquisa — 487
uv run pytest tests/corpus    # só a camada de corpus — 58
uv run pytest -m network   # contra CVM e SEC reais — 24 testes
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
│   ├── research.py    ResearchPlan, PlanStep, ComputationResult, Claim, manifesto
│   │                  ← nenhum passo de plano aceita Decimal: número literal é
│   │                    inexprimível na gramática, não apenas proibido
│   ├── corpus.py      SourceDocument, DocumentUnit, QuoteClaim, EvidenceQuery
│   │                  ← QuoteClaim não aceita Decimal, value, unit nem currency:
│   │                    número de emissor é citação, jamais insumo de cálculo
│   ├── decomposition.py  Contribution, DecompositionResult — as partes somam o
│   │                  todo por construção; o residual nunca é opcional
│   ├── program.py     ResearchProgram, ResultShape — a forma que o estágio 2 vê
│   ├── claims.py      ClaimNode, ClaimGraph — CALCULATION não se apoia em QUOTE
│   └── workspace.py   CompanyWorkspace — READY é conjunção, não adjetivo
├── sources/         L0 — busca bytes com procedência, nunca interpreta
│   ├── base.py        SourceProvider, PublicSourceProvider, LicensedSourceProvider
│   ├── registry.py    resolve dataset_id → provider
│   └── public/cvm.py  DFP, ITR, cadastro, IPE (catálogo e documento)
├── parse/           L1 — bytes → linhas tipadas
│   ├── cvm_dfp.py     abre o ZIP da DFP, tipa cada CSV, registra o que descartou
│   └── cvm_ipe.py     catálogo de entregas; Categoria → DocumentKind por tabela
├── store/           L1 — bronze imutável + catálogo + silver + gold
│   ├── bronze.py      content-addressed, atômico, somente-leitura, verificável
│   ├── catalog.py     runs, documentos, retrievals, detecção de mudança
│   ├── silver.py      persiste AccountLine; idempotente por silver_id
│   ├── gold.py        escala, tipo de período, validação → Fact. Append-only.
│   ├── research.py    persiste o manifesto de pesquisa. Quem calcula não grava.
│   ├── corpus.py      documentos, unidades e falhas de extração. Append-only.
│   └── db.py          conexão e esquema
├── semantics/       L2 — conceitos universais, mapeamentos por regime, métricas
│   ├── concepts.py    catálogo universal; não menciona plano de contas
│   ├── decompositions.py  identidades contábeis declaradas, universais e versionadas
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
│   ├── decompose.py   variação de um total → contribuições + residual explícito
│   ├── program.py     executa o programa: cálculo + decomposição + evidência
│   ├── program_planner.py  os dois estágios; o 2º vê forma, nunca valor
│   ├── shape.py       resultado → forma (direção, faixa, ordem) — sem Decimal
│   ├── claims.py      resultado → nós ancorados; o modelo nunca os cria
│   ├── program_writer.py  grafo → leituras e conclusões; nunca vê valor
│   ├── critic.py      critic mecânico, taxonomia fechada, sem loop
│   ├── model_critic.py  julga o que a máquina não julga; severidade limitada
│   ├── workspace.py   DRAFT/READY por seis requisitos conferíveis
│   ├── capability.py  o que o sistema sabe fazer; jamais um valor financeiro
│   ├── validate.py    validação pura: sem banco, sem relógio, sem rede
│   ├── resolve.py     o que só o warehouse sabe responder
│   ├── derive.py      as 7 derivações fechadas e suas condições de recusa
│   ├── execute.py     único módulo que segura um Engine; só aceita plano certificado
│   ├── render.py      único lugar que formata número para exibição
│   ├── answer.py      montagem de claims, substituição de token, regra do dígito
│   └── manifest.py    o que foi executado, com que versões e sob que hashes
├── corpus/          L1.5 — documento qualitativo → unidade citável → evidência
│   ├── extract.py     único lugar que conhece pypdf; determinístico e versionado
│   ├── index.py       tokenizador e BM25 em Decimal; índice derivado e descartável
│   ├── retrieve.py    única porta de leitura de evidência; as_of obrigatório
│   ├── identity.py    unit_id e query_id endereçados por conteúdo
│   └── __init__.py    catálogo → bronze → unidades → índice, tudo idempotente
├── canonical.py     serialização canônica do sistema inteiro; uma implementação
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
| **5** | Company Research Workspace: corpus qualitativo, decomposição, programa de pesquisa, grafo de claims, critic, workspace, segunda jurisdição | Petrobras vai de `DRAFT` a `READY`; `ebitda@v1` computa sobre a Intel via SEC sem que nenhuma métrica mude; receita da Intel decompõe por segmento com resíduo zero | ✅ |
| **6** | B3 (preços, proventos), BCB, IBGE | | |
| **7** | SEC EDGAR (EUA) — reusa `contracts`, novo provider | | |

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

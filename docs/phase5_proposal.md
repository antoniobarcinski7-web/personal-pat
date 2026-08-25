# Fase 5 — Company Research Workspace

Documento de referência da fase. Registra as decisões arquiteturais aprovadas e
o estado de cada milestone. Nada aqui descreve comportamento que não esteja
implementado — o que é plano está marcado como plano.

---

## Objetivo

Transformar o PAT de um sistema que responde perguntas quantitativas isoladas
num **Company Research Workspace**: uma empresa por vez, combinando evidência
quantitativa e qualitativa para responder não só "quanto foi" mas "por quê".

```
Company → Workspace → Facts + Documents → Research Program
        → Deterministic Research → Claims / Evidence
        → Writer → Mechanical Critic → Model Critic → Auditable Conclusion
```

O princípio central não muda: **o LLM não é fonte da verdade numérica.** Ele
pode planejar, sintetizar e criticar. Não pode criar, alterar ou servir de fonte
primária de número financeiro usado em cálculo.

---

## Diagnóstico que motivou a fase

O vocabulário do sistema até a Fase 4 era uma tripla — `(entity_id,
metric@version, period_end)` — mais sete derivações fechadas. Isso é uma
linguagem de **medição**, não de **explicação**. Três lacunas mecânicas:

1. **Nenhum eixo de decomposição.** `Fact` não tinha onde uma parte morar; o
   sistema não conseguia *representar* "receita do E&P caiu 12%". → M5.2.
2. **Nenhum corpus textual.** `parse/` só tinha `cvm_dfp.py`; não havia contrato
   para documento, trecho ou citação. → M5.1.
3. **Gramática fechada em cálculo.** Um planner não conseguia *pedir* evidência
   porque não existia passo que a pedisse. → M5.3.

O problema era de gramática e de eixos, não de prompt.

---

## Decisão de dependência (registrada)

`pypdf` foi adicionada como dependência explícita em M5.1. Justificativa:

- Todos os 14 documentos qualitativos amostrados da Petrobras no IPE/RAD voltam
  como PDF (`%PDF-1.6`). Não existe caminho HTML.
- Extrair texto de PDF não é atendível pela stdlib.
- Entre as opções, `pypdf` é a mínima: Python puro, BSD-3, zero dependências
  transitivas. `pdfminer.six` puxaria binário nativo; `pymupdf` é AGPL.

Condições que acompanham a decisão, e que estão implementadas:

1. A versão **efetiva** do `pypdf` entra em `extraction_version` — não a versão
   mínima do `pyproject.toml`, que é outra coisa.
2. `extraction_version` entra no `unit_id`, então atualizar o extrator cria
   unidades ao lado das antigas em vez de redefinir o que uma citação queria
   dizer.
3. `pypdf` fica confinado a `corpus/extract.py`, e há teste de layering que
   falha se aparecer em outro arquivo. É o que torna a decisão reversível.
4. Não há fallback para OCR, para segunda biblioteca ou para heurística.
   Falha de extração é registro nomeado.

---

## M5.1 — Corpus point-in-time + evidência citável ✅

Espelha deliberadamente o que funcionou na Fase 3: núcleo determinístico
primeiro, modelo depois. Se a recuperação não presta sem modelo, ela não vai
prestar com um — vai só ficar difícil de perceber.

### Entregue

| | |
|---|---|
| Contratos | `contracts/corpus.py` — `SourceDocument`, `DocumentUnit`, `UnitLocator`, `SpeakerRef`, `DocumentKind`, `DateBasis`, `ExtractionOutcome`/`ExtractionFailure`, `QuoteClaim`, `EvidenceQuery`/`EvidenceHit`/`EvidenceResult`/`EvidenceUnavailable` |
| Provider | `cvm.ipe` (catálogo anual) e `cvm.ipe_doc` (um documento) em `sources/public/cvm.py` |
| Parser | `parse/cvm_ipe.py` — catálogo tipado, `Categoria → DocumentKind` por tabela declarada |
| Extração | `corpus/extract.py` — PDF via pypdf, blocos por linha em branco, fatia literal |
| Índice | `corpus/index.py` — tokenizador versionado + BM25 em `Decimal` |
| Recuperação | `corpus/retrieve.py` — única porta de leitura de evidência |
| Persistência | `store/corpus.py` + tabelas `source_document`, `document_unit`, `extraction_failure`, `document_unit_token` |
| CLI | `pat docs`, `pat docs --sync`, `pat docs --failures`, `pat evidence`, `pat provenance-unit` |
| Refactor | serializador canônico movido para `pat/canonical.py`, sem mudança de comportamento |

### Invariantes, e onde cada um é garantido

| # | Invariante | Onde |
|---|---|---|
| 1 | `published_at <= as_of` | `EvidenceQuery` (contrato), SQL de `_scope`, e revalidação em `EvidenceResult` |
| 2 | Evidência posterior ao `as_of` nunca aparece | três barreiras independentes, acima |
| 3 | Texto é verbatim | `DocumentUnit.text` é `page_text[a:b]`; `QuoteClaim.text` é idêntico |
| 4 | Localização verificável | `UnitLocator` valida cobertura contra `char_count` |
| 5 | `QuoteClaim` → `DocumentUnit` | `unit_id` obrigatório |
| 6 | `DocumentUnit` → `SourceDocument` | `document_id` obrigatório, validado em `ExtractionOutcome` |
| 7 | `SourceDocument` → blob | `document_id` **é** o sha256 do conteúdo |
| 8 | Recuperação sem LLM | teste de layering proíbe import; teste funcional roda sem credencial |
| 9 | Contratos não importam implementação | teste de layering: só `contracts.common` |
| 10 | `research` não vaza para `contracts` | idem |
| 11 | Índice é derivado | tabela reconstruível; `_quote` monta da unidade, nunca do índice |
| 12 | `extraction_version` explícita | embutida no `unit_id`, gravada na unidade e na falha |
| 13 | `index_version` explícita | viaja em todo `EvidenceResult` |
| 14 | Append-only | teste por AST sobre o SQL de `store/corpus.py` |
| 15 | Falha nunca silenciada | `ExtractionOutcome` exige unidades **ou** falha nomeada |

### Gate de aceitação — PASS

Verificado contra a Petrobras real (`cod_cvm` 9512, 28 documentos do IPE 2024,
4.728 unidades, zero falhas de extração):

1. **Evidência real, com procedência completa.**
   `pat evidence --query "queda brent receita" --as-of 2024-12-31` devolve, entre
   outros: *"em 2023 a receita líquida foi menor em comparação com 2022,
   principalmente devido à queda de 18% do preço do Brent e dos crack spreads de
   derivados"* — com `document_id`, `unit_id`, `published_at`, `document_kind`,
   `locator` e `source_tier`.

2. **Prova point-in-time.** A mesma consulta com dois `as_of`:

   | `as_of` | documentos no escopo | primeiro trecho |
   |---|---|---|
   | 2024-06-30 | 15 | release do 4T23 (mar/24) |
   | 2024-12-31 | 28 | relatório de produção do 2T24 (jul/24) |

   O documento de julho só se torna elegível no `as_of` posterior. É o análogo
   textual exato do critério da Fase 1.

3. **Cadeia até os bytes.** `pat provenance-unit` reextrai do blob e compara:
   `hash confere: sim` / `texto reextraído: IDENTICO`.

4. **Zero LLM.** Nenhum import de modelo em `pat/corpus/`; teste roda com
   `ANTHROPIC_API_KEY` removida.

### Testes

- `tests/corpus/` — 58 testes offline (contratos por AST, extração, recuperação,
  layering, CLI ponta a ponta com PDF montado à mão).
- `tests/network/test_corpus_live.py` — 7 testes contra a CVM real, marcados
  `network`: colunas do catálogo, tradução de categoria ainda casando, documento
  real baixando como PDF e extraindo verbatim.
- Suíte offline total: 827 passando (era 769).

### Limitações conhecidas

- Só PDF. HTML e transcrição têm contrato (`LocatorScheme.HTML_NODE`,
  `TRANSCRIPT_TURN`, `SpeakerRef`) mas não têm extrator.
- `section_path` e `speaker` ficam vazios: o IPE não publica transcrição, e a
  estrutura de heading do PDF não é confiável o suficiente para afirmar seção.
  Vazio é a resposta honesta — não há inferência por formato.
- `period_covered` não existe. `reference_date` é dado bruto do protocolo e está
  marcado como não sendo período.
- Recuperação é léxica. Embeddings, quando entrarem, serão expansão de
  candidatos — nunca justificativa de citação.

---

## M5.2 — Decomposição quantitativa ✅ (eixo COMPONENT)

### O que a decomposição é

`delta_pct` responde *quanto* uma grandeza mudou. Uma decomposição responde *de
onde* a mudança veio:

```
delta_total = contribuição_A + contribuição_B + … + residual
```

É o primeiro passo de toda resposta causal, e ele é **quantitativo**. Um sistema
que pula direto para "o release diz que foi o Brent" está fazendo jornalismo, não
research — a citação explica a decomposição, nunca a substitui.

### Entregue

| | |
|---|---|
| Contratos | `contracts/decomposition.py` — `BreakdownAxis`, `Contribution`, `DecompositionResult`, `DecompositionUnavailable`, `DecompositionFailureReason` |
| Catálogo | `semantics/decompositions.py` — identidades declaradas, universal como `concepts.py` |
| Executor | `research/decompose.py` — determinístico, sem modelo |
| CLI | `pat decompositions`, `pat decompose` |
| Aditivo | `ConceptUnavailable` (contrato), `Engine.resolve_concept`, `Engine.mapping_chain_for` |

Três identidades registradas: `gross_profit_by_line@v1`, `ebit_by_line@v1` e
`ebit_by_stage@v1`. As duas do EBIT coexistem de propósito — uma diz se o
problema foi na operação ou na estrutura, a outra abre a operação em receita e
custo. Registrar as duas é mais honesto que escolher uma e chamá-la de *a*
decomposição do EBIT.

### O residual é o ponto do desenho

A igualdade `contribuições + residual == target_delta` é **exata**, em `Decimal`,
conferida na construção do contrato. Não existe forma de montar um
`DecompositionResult` cujas partes não somem o todo: o residual é calculado por
diferença e absorve sozinho tudo que a identidade não explica.

A tolerância decide se o resultado sai marcado como **fechado**, nunca se ele
pode existir. Uma decomposição que não fecha continua publicável e diz, no
próprio corpo, quanto sobrou — e o CLI imprime o residual **sempre**, inclusive
quando é zero, porque um relatório que só o mostra quando incomoda ensina o
leitor a não procurar por ele.

O que não existe, e não deve passar a existir: rateio do residual entre membros,
estimativa de contribuição por regressão, normalização para somar 100%, e membro
pequeno empurrado para "outros". Cada uma faria a conta fechar sem que ela feche,
e o resultado teria a aparência exata de uma análise correta.

**Membro presente em um só período recusa**, nunca vira zero. Uma companhia que
deixou de reportar um componente não contribuiu com "menos o valor inteiro do ano
passado" — ela mudou de apresentação, e as duas coisas se pareceriam idênticas
no gráfico.

### Verificado contra dados reais

Petrobras, FY2023 → FY2024, consolidado, `AS OF 2025-06-30`:

| | de | para | efeito | parte |
|---|---|---|---|---|
| Despesas operacionais líquidas | 80.591 | 109.261 | −28.670 | 55,0% |
| Receita líquida | 511.994 | 490.829 | −21.165 | 40,6% |
| Custo dos bens e serviços | 242.061 | 244.367 | −2.306 | 4,4% |
| **Residual** | | | **0** | 0% |

(R$ milhões.) EBIT cai R$ 52,1 bi, residual **exatamente zero**, fidelidade
`exact`. 24 testes cobrem a matriz: membro ausente, membro novo, membro
encerrado, escopo incompatível, período incompatível, moeda mista, residual
não-zero e mapeamento que pega a linha errada.

---

## Decisão arquitetural — por que o eixo SEGMENT permanece bloqueado

Decisão tomada em M5.2 e **aprovada**. Registrada aqui porque o comportamento
que ela produz (`NO_BREAKDOWN_SOURCE`) é fácil de confundir com uma lacuna a ser
contornada, e não é: é o comportamento correto.

### 1. A DFP da CVM não publica o eixo de segmento

Verificado sobre o ZIP real: `dfp_cia_aberta_2023.zip` contém 19 membros — as
demonstrações padronizadas (BPA, BPP, DFC_MD, DFC_MI, DMPL, DRA, DRE, DVA, nas
versões individual e consolidada), mais composição de capital e parecer. Nenhum
deles tem dimensão de segmento operacional.

A informação por segmento (IFRS 8) existe apenas nas **notas explicativas**, que
a CVM publica dentro do documento de demonstrações financeiras completas — em
PDF. Não há dataset estruturado equivalente.

### 2. PDF textual não é fonte quantitativa estruturada

Amostrado contra o corpus real da Petrobras já ingerido em M5.1. A extração de
tabelas é **irregular**, e a irregularidade é o problema:

- Algumas tabelas saem linearizadas e legíveis:
  `Exploração e Produção  2.472 2.040 21,2`
- Outras saem com o cabeçalho desmontado em fragmentos, em blocos separados dos
  valores: `Exploração e / Produção / (E&P) / Refino, / Transporte e / …`, com os
  números em outro bloco.

Reconstruir qual número pertence a qual segmento a partir desse texto achatado é
**dedução por formato** — exatamente o mesmo erro que casar conta por rótulo, que
a Fase 2 existe para impedir. Um parser que acertasse na primeira tabela e
associasse errado na segunda produziria fatos financeiros incorretos com
aparência de corretos: erro intermitente, a pior classe.

### 3. Isso preserva o invariante da M5.1

M5.1 estabeleceu e testa que **texto de documento não tem caminho até o gold**
(`test_o_texto_de_documento_nao_tem_caminho_ate_o_gold`). Autorizar um parser de
tabela de PDF para alimentar fatos de segmento reverteria esse invariante um
milestone depois de o commitar. A decisão aprovada é **não reverter**.

Número lido de documento continua sendo citação (`QuoteClaim`) — pode aparecer
num relatório, atribuído a quem publicou, resolvendo até o byte — e nunca insumo
de cálculo.

### 4. O que acontece hoje, e quando destrava

`BreakdownAxis.SEGMENT` existe no vocabulário e **recusa** com
`NO_BREAKDOWN_SOURCE`, cuja mensagem diz que o plano padronizado não publica a
dimensão e que a nota que a contém é PDF. A recusa distingue deliberadamente:

- **"o sistema não tem por onde ler"** (o que é o caso), de
- **"a empresa não reporta"** (o que seria falso — ela reporta, em PDF).

São ações diferentes para quem pesquisa, e colapsá-las numa lista vazia seria a
ausência que se lê como evidência de ausência.

**SEGMENT é retomado em M5.6**, com fonte estruturada apropriada: SEC/XBRL,
onde as dimensões de segmento são elementos de taxonomia com valor tipado — e a
Petrobras arquiva 20-F. Aí o eixo ganha membros mapeados por declaração, na mesma
disciplina de `equivalence_basis`, e o executor de decomposição já construído
funciona sem alteração: ele é agnóstico de eixo por construção.

## M5.3 — `ResearchProgram` e planner em dois estágios ✅

### O que um programa é

Um `ResearchPlan` responde "quanto foi". Um `ResearchProgram` responde "o que
precisa ser investigado":

```
ResearchProgram
├── compute          métricas e derivações   (ResearchPlan v1, INTACTO)
├── decompositions   de onde a variação veio (M5.2)
└── evidence         o que a administração disse (M5.1)
```

`compute` é um `ResearchPlan v1` sem uma vírgula de alteração — um plano salvo
em disco na Fase 3 continua executando igual. O programa **envolve** o plano,
nunca o reescreve.

### Por que dois estágios

Não dá para saber que evidência pedir antes de saber o que os números fizeram.
"Por que a receita caiu?" — a pergunta a fazer ao corpus depende de a queda ter
sido de 3% ou de 30%, e de qual componente puxou. Um prompt único escolheria a
citação antes de conhecer o número, que é como se produz uma narrativa
convincente e errada.

```
estágio 1  planner.compute    pergunta + capability
                              → plano de cálculo + decomposições
           [executa DETERMINISTICAMENTE, sem persistir]
estágio 2  planner.evidence   pergunta + capability + FORMA dos resultados
                              → plano de evidência
```

Dois estágios **não** é retentativa: são dois papéis, com prompts e entradas
diferentes, cada um com sua própria `PlanProvenance` e sua própria linha em
`llm_call`. `ProgramEnvelope` guarda as duas separadas — colapsá-las esconderia
qual estágio produziu o quê.

### `ResultShape` — a fronteira

O estágio 2 vê a **forma** dos resultados, nunca os valores:

| campo | exemplo |
|---|---|
| `direction` | `down` |
| `magnitude` | `large` (faixas fixas em `shape.py`, versionadas) |
| `top_contributors` | `("operating_expenses_net", "revenue_net", "cogs")` |
| `residual_is_material` | `false` |
| `hits` | quantos trechos, jamais quais |

`ResultShape` não tem campo capaz de carregar um `Decimal`, e um teste por AST
confere. Outro teste monta o **prompt real** com a decomposição real da
Petrobras e verifica que nenhum dos algarismos (`189342`, `52141`, `0.5498`…)
aparece nos bytes enviados. É a mesma técnica de
`test_o_escritor_nao_le_valor_nenhum` da Fase 3: a garantia é a forma do tipo,
não uma instrução de prompt.

Os degraus de magnitude são **código versionado**, não prompt. Um modelo não
decide o que é "grande" — ele recebe a classificação já feita.

### Ordem: decomposição antes de evidência

O executor roda decomposição **antes** da busca, porque é ela que diz o que vale
procurar. Um sistema que buscasse primeiro estaria escolhendo a citação e depois
procurando o número que a sustenta.

Falha parcial não aborta: um pedido que falha vira recusa nomeada e o programa
continua. Saber que a decomposição fechou mas o corpus não tem nada sobre o
assunto é um resultado útil.

### Entregue

| | |
|---|---|
| Contratos | `contracts/program.py` — `ResearchProgram`, `DecompositionRequest`, `EvidenceRequest`, `ResultShape`, `ProgramResult`, `ProgramEnvelope` |
| Forma | `research/shape.py` — derivação determinística, faixas versionadas |
| Executor | `research/program.py` — sem modelo, motor injetado |
| Planejador | `research/program_planner.py` — dois estágios, duas procedências |
| Capability | `DecompositionCard` e `CorpusCard` no snapshot — o que existe, nunca o que vale |
| CLI | `pat program` (dois estágios, grava envelope), `pat run-program` (determinístico) |

`CorpusCard` é o análogo de `EntityCard` do lado qualitativo: quantos documentos
de cada tipo, em que janela, quantas unidades indexadas e **quantas falhas de
extração** — cobertura que esconde o que faltou mente sobre si mesma. Sem ele o
estágio 2 escreveria buscas no escuro, pedindo transcrição para uma empresa cujo
corpus só tem release.

### Verificado ponta a ponta

`pat run-program` sobre a Petrobras, sem nenhuma chamada de modelo: EBIT cai
R$ 52,1 bi com resíduo zero, despesa operacional identificada como maior
contribuidor (55%), e a busca por `despesas provisao impairment` devolve trecho
verbatim do release — *"crescimento das provisões em campos devolvidos em
2023"* — com `unit_id`, página e offset. As duas metades do workspace numa
execução auditável.

26 testes novos. Suíte offline: 878.

## M5.4 — Grafo de claims, writer e critic mecânico ✅

### As cinco espécies

| espécie | procedência exigida | quem cria |
|---|---|---|
| `FACT` | `result_id` → `fact_id` → byte no bronze | motor |
| `CALCULATION` | `result_id`, mais `supports` para o total que ajuda a explicar | motor |
| `QUOTE` | `unit_id` → documento → byte | corpus |
| `INFERENCE` | `supports` (≥1) + `strength` declarada | modelo |
| `CONCLUSION` | `supports` (≥1) + `falsified_by` (≥1) | modelo |

As três primeiras são **verificáveis mecanicamente**; as duas últimas não são —
e é exatamente por isso que são espécies distintas. A diferença entre "a margem
foi 3,89%" e "a margem caiu por alavancagem operacional" não pode depender de o
leitor prestar atenção: ela é estrutural, aparece no JSON, na tela e no
relatório.

`INFERENCE` e `CONCLUSION` não têm campo de valor nem de endereço. `strength` é
enum (`quantified` / `attributed` / `suggested`), nunca número — um escore 0–1
seria um número produzido por modelo, e o pior tipo, porque pareceria medido.

### As invariantes do grafo

Conferidas na construção, não avisadas:

1. Todo `supports` resolve para um nó que existe.
2. Não há ciclo.
3. Toda `INFERENCE`/`CONCLUSION` alcança, por algum caminho, um nó ancorado.
4. **`CALCULATION` não pode se apoiar em `QUOTE`.**

A quarta é o portão contra a **lavagem de número do emissor**, e ela vive na
construção do grafo — antes do critic, antes do escritor, antes de qualquer
prompt. Um relatório que a violasse não chega a existir. O critic confere de
novo, porque um grafo pode chegar montado por outro caminho (desserializado de
um arquivo), e a barreira mais importante do sistema merece ser conferida onde a
resposta sai.

A terceira recusa uma torre de leituras sem nada embaixo — que é como um
relatório fica eloquente e vazio.

### O writer sobre o grafo

Recebe nós já ancorados, com **token e rótulo, nunca valor**. Só pode
acrescentar `INFERENCE` e `CONCLUSION`, e só pode citar apontando o `claim_id` de
um nó `QUOTE` que a execução produziu — o texto do bloco vem da **unidade**, não
do modelo, o que faz a citação ser verbatim por construção e não por conferência.

Ele *vê* o texto das citações (precisa, para escrever sobre elas). O
guarda-corpo não é cegueira, é verificação.

### O critic mecânico — metade do critic não é um modelo

Taxonomia fechada. **Duro** é o que torna a resposta errada ou não-auditável;
**leve** é o que a torna incompleta mas ainda verdadeira.

| código | severidade |
|---|---|
| `QUOTE_NOT_VERBATIM` | hard |
| `DIGIT_OUTSIDE_QUOTE` | hard |
| `UNKNOWN_TOKEN` | hard |
| `EVIDENCE_AFTER_AS_OF` | hard |
| `UNSUPPORTED_CLAIM` | hard |
| `CONCLUSION_WITHOUT_FALSIFIER` | hard |
| `ISSUER_NUMBER_LAUNDERED` | hard |
| `FIDELITY_UNDISCLOSED` | soft |
| `ORPHAN_RESULT` | soft |

A distinção não é de gosto: ela responde "esta resposta pode sair?", e a resposta
certa para um número que não resolve até o byte é não. Achado leve **acompanha**
a resposta, visível — um relatório que carrega a ressalva é mais útil que um
relatório limpo por reescrita.

**Não há loop `writer → critic → writer`.** Pela mesma razão que não há
retentativa de planejador: "criticar até passar" é um amostrador que uma hora
aprova algo errado-mas-aprovado. O critic aponta; ele não corrige, não reescreve
e não rechama o escritor.

### A regra do dígito, com exceção tipada

Dígito é permitido **dentro de bloco de citação, e só lá**. Fora dele, um
algarismo só chega por substituição de token. O critic roda **antes** da
substituição — é a única ordem em que dá para distinguir "o modelo escreveu um
número" de "o sistema substituiu um token".

### Entregue

| | |
|---|---|
| Contratos | `contracts/claims.py` — `ClaimNode`, `ClaimGraph`, `EvidenceStrength`, `CriticFinding`, `CriticReport` |
| Ancoragem | `research/claims.py` — `ProgramResult` → nós FACT/CALCULATION/QUOTE |
| Writer | `research/program_writer.py` — grafo → leituras, conclusões e blocos |
| Critic | `research/critic.py` — determinístico, taxonomia fechada |
| CLI | `pat run-program --writer` |

O residual material vira **nó**, não rodapé: se ficasse só na nota, uma conclusão
poderia se apoiar nas contribuições e ignorar o não explicado sem que nada no
grafo registrasse a omissão.

26 testes novos. Suíte offline: 904.

## M5.5 — Critic de modelo e Company Workspace ✅

### O critic de modelo entra depois, e só sobre o que sobrou

A divisão entre os dois critics é o ponto do desenho. Citação que não bate com
os bytes, dígito fora de citação e evidência posterior ao `as_of` são conferíveis
por código — um modelo no meio disso só pioraria a auditoria. O que resta —
*"esta conclusão vai além do que a evidência sustenta?"* — não tem como ser
conferido mecanicamente.

Ele recebe o **conjunto finito** de evidências que a execução recuperou,
**inclusive as que o escritor não citou**, cada uma marcada com
`citado_no_relatorio`. É isso que torna `SELECTIVE_EVIDENCE` possível: a pergunta
"existe um trecho que contradiz a conclusão e ficou de fora?" só tem resposta
porque o conjunto é fechado, registrado e auditável.

Ele **não** pesquisa, não busca documento novo, não acessa o warehouse, não
corrige o escritor e não o rechama. Um teste por AST confere que ele não importa
`pat.query`, `pat.store`, `pat.corpus.retrieve`, `duckdb` nem `httpx`.

### Por que o modelo não escolhe sozinho o que bloqueia

Um achado de critic de modelo é, ele próprio, um julgamento não verificado.
Deixá-lo declarar qualquer achado como duro seria dar-lhe veto sobre um relatório
possivelmente correto. A severidade que ele pede é **limitada por
`_MAX_SEVERITY`**, que é código versionado:

| código | teto |
|---|---|
| `SELECTIVE_EVIDENCE` | hard |
| `CAUSAL_OVERREACH` | hard |
| `QUOTE_OUT_OF_CONTEXT` | soft |
| `STALE_EVIDENCE` | soft |
| `MISSING_DECOMPOSITION` | soft |
| `UNQUANTIFIED_MAGNITUDE` | soft |

Os dois que podem bloquear são os que tornam o relatório **enganoso** — um cita
só o que confirma, o outro afirma causa onde há menção. Em research, enganoso é
pior que ausente. Os demais deixam o relatório incompleto, e bloquear seria
tratar "faltou dizer" como "disse errado".

`ModelFinding` recusa na construção uma severidade acima do teto. A escolha de
quais códigos podem bloquear é uma decisão de arquitetura, revisável num diff —
não uma linha de prompt.

`evidence_considered` viaja no relatório porque "não achei evidência seletiva"
sobre dois trechos é uma afirmação bem mais fraca do que sobre vinte.

### O Company Workspace

`READY` não é um adjetivo: é a **conjunção de seis requisitos conferíveis**, cada
um com nome próprio e remédio de uma linha.

1. A empresa tem fatos no gold.
2. Pelo menos **dois períodos** — sem dois não há variação, e sem variação não há
   pergunta causal a fazer, que é o que a fase existe para responder.
3. Mapeamento próprio e **conferido**. Cair na família default é razoável para
   explorar; declarar a empresa pronta sem saber que caiu nela, não.
4. Os conceitos que as decomposições registradas exigem estão ligados.
5. Há documentos no corpus.
6. Há unidades indexadas na versão corrente do índice.

Nenhum é opinião e nenhum tem tolerância — um requisito com "mais ou menos" seria
a porta por onde um workspace incompleto se declara pronto. O contrato recusa
`READY` com pendência **e** `DRAFT` sem pendência declarada: se nada falta o
estado é READY, se algo falta tem que ter nome.

`missing_concepts` e `extraction_failures` são campos de primeira classe, não
notas de rodapé. Cobertura que só mostra o que existe mente sobre si mesma — e a
mentira aparece como ausência de evidência, que é o que um analista leria como
evidência de ausência.

`workspace_sha256` cobre cadeia de mapeamento, conjunto de documentos, versão do
índice e versões de métrica; `built_at` fica de fora, pela mesma razão do
`capability_sha256`. É o que distingue "a resposta mudou" de "os dados mudaram".

### Verificado

```
pat company --cod-cvm 9512   → READY   (Petrobras: 4.428 fatos, 3 períodos,
                                        mapeamento conferido, 3 decomposições,
                                        28 documentos, 4.676 unidades)
pat company --cod-cvm 4170   → DRAFT   (Vale: [no_documents] nenhum documento
                                        qualitativo; saída: pat docs --sync)
```

```bash
pat run-program --program-file p.json --writer --audit
```

10 testes novos. Suíte offline: 914.

## M5.6 — SEC / US GAAP (planejado)

Só depois que a Petrobras funcionar de ponta a ponta. Absorve também o
desbloqueio do eixo `SEGMENT`, pela decisão registrada acima: XBRL traz as
dimensões de segmento como elementos de taxonomia com valor tipado, que é a
fonte estruturada que a CVM não oferece.

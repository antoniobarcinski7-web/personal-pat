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

## M5.3 — `ResearchProgram` e planner em dois estágios (planejado)

Envelope sobre `ResearchPlan v1`, que fica bit a bit intacto. Estágio 1 planeja
cálculo; estágio 2 planeja evidência, vendo a **forma** dos resultados
(direção, magnitude em faixas fixas) e nunca os valores.

## M5.4 — Grafo de claims e critic mecânico (planejado)

Cinco tipos: `FACT`, `CALCULATION`, `QUOTE`, `INFERENCE`, `CONCLUSION`. Toda
conclusão precisa de caminho de suporte e de falsificador. Critic mecânico com
taxonomia fechada, incluindo `ISSUER_NUMBER_LAUNDERED`.

## M5.5 — Critic de modelo e workspace (planejado)

Critic sobre o conjunto **finito** de evidências recuperadas. Não pesquisa, não
corrige, não reescreve. Sem loop writer↔critic. Workspace com estados
`DRAFT`/`READY`.

## M5.6 — SEC / US GAAP (planejado)

Só depois que a Petrobras funcionar de ponta a ponta. Absorve também o
desbloqueio do eixo `SEGMENT`, pela decisão registrada acima: XBRL traz as
dimensões de segmento como elementos de taxonomia com valor tipado, que é a
fonte estruturada que a CVM não oferece.

# A camada Opportunity (L5)

    PAT          motor de pesquisa determinístico. Produz número.
    Opportunity  parceiro de pesquisa. Não produz número nenhum.

A divisão é literal e vale para o código: nada em `src/pat/opportunity/`
calcula, agrega ou converte, com uma exceção declarada (`valuation.py`, que
existe justamente para calcular e é testada contra números conferidos à mão).
Quando um número aparece numa tese escrita aqui, ele veio de um `MetricResult`
do motor ou de um `QuoteClaim` verbatim de documento — e os dois carregam para
dentro do Opportunity toda a procedência que tinham.

O que esta camada acrescenta é o que o motor deliberadamente não tem: **memória
de uma investigação**. O que foi perguntado, o que ficou aberto, o que foi
suposto, o que foi refutado, e por que alguém mudou de ideia.

O PAT continua não sendo um chatbot. O Opportunity é uma camada *acima* dele,
e a conversa é a porta dessa camada — não o lugar onde as coisas acontecem.

---

## As sete palavras que o sistema não confunde

Quase todo erro caro nesse tipo de sistema é uma dessas sete coisas se
passando por outra. Elas são tipos diferentes, e não campos de um mesmo
registro com um rótulo:

| | é | não é | mora em |
|---|---|---|---|
| **Fato** | número do motor, com linhagem até o byte | nada que o Opportunity escreva | `MetricResult` (PAT) |
| **Evidência** | endereço do que sustenta algo | o texto ou o valor em si | `EvidenceLink` |
| **Hipótese** | afirmação com falsificador declarado | conclusão | `Hypothesis` |
| **Premissa** | escolha assinada, com base e justificativa | dado | `Assumption` |
| **Claim** | afirmação com evidência obrigatória | opinião | `Claim` |
| **Conclusão** | veredito sobre uma hipótese testada | resumo | `Conclusion` |
| **Tese** | posição, com contra-tese e o que a derruba | recomendação | `InvestmentThesis` |

Três garantias são **de tipo**, e não de convenção:

- `EvidenceLink` não tem `text` nem `value` — carrega endereço. Não dá para
  colar um número de release dentro de uma evidência.
- `Finding` não tem `value`, `unit` nem `currency`. Um achado aponta para
  `result_ids`; ele não guarda o número.
- `Assumption` e `DataPoint` são tipos distintos. Um valor escolhido não entra
  na conta pela porta dos dados, e `Assumption` rejeita `Actor.ENGINE`: o motor
  produz número, nunca escolha.

---

## O diário é a única fonte de verdade

Um workspace é um diretório com **um arquivo**:

    <PAT_HOME>/opportunity/<workspace_id>/journal.jsonl

Append-only. O estado corrente é sempre `fold(workspace_id, eventos)` — uma
função pura, sem I/O. Não existe arquivo de "estado corrente" ao lado: ele
seria uma segunda fonte de verdade, e no dia em que divergisse do diário
ninguém saberia qual das duas está certa.

Consequências práticas:

- **Reabrir é grátis e é exato.** `open_workspace()` redobra do disco, e o
  estado é igual campo a campo ao que estava em memória. É o que
  `tests/opportunity/test_end_to_end.py::test_a_investigacao_sobrevive_ao_processo`
  verifica com `model_dump()`.
- **Não existe sessão de conversa.** O índice do turno vem da dobra. "Continuar
  a conversa de ontem" é a mesma operação que "abrir o workspace".
- **Nada é editado.** Mudar de ideia sobre uma hipótese grava um evento novo; o
  anterior continua lá. Uma tese que muda de LONG para NO_POSITION é a
  informação mais valiosa que o diário guarda.
- **Um evento que a dobra recusa nunca chega ao disco.** `Workspace.apply()`
  dobra *antes* de escrever — senão um `as_of` para trás deixaria no diário um
  evento que impede o próprio workspace de abrir.

A dobra recusa o que é **impossível** (evento antes da criação, `as_of` para
trás, tarefa que não existe, slug repetido). Ela **não** julga a qualidade da
pesquisa — isso é trabalho do crítico. A distinção importa: uma investigação
passa por estados ruins no caminho, e a dobra tem que aceitá-los.

Apagar `data/opportunity/` perde a investigação inteira. Ao contrário de
`data/chat/`, isto não é conveniência de UI.

---

## Rodando o `pat`

Neste worktree o console script instalado pelo `uv` não funciona: o Python 3.14
ignora arquivos `.pth` marcados com a flag `UF_HIDDEN` do macOS, e o pacote
nunca entra no `sys.path`. Use o wrapper na raiz, que ancora no diretório certo
e funciona de qualquer lugar:

```bash
./pat opportunity list
/caminho/para/phase5/pat opportunity status --workspace ID
```

Um alias deixa o resto deste guia literal:

```bash
alias pat='/Users/você/.../worktrees/phase5/pat'
```

---

## Quem raciocina

Duas escolhas, e a diferença aparece na resposta:

```bash
pat opportunity chat "..."                  # shape: tabela, sem rede, sem custo
pat opportunity chat "..." --reasoner llm   # modelo: prosa que argumenta
```

`shape` é o piso e o default: planeja por tabela declarada, descreve a **forma**
de uma série — direção, magnitude, reversões — e nunca diz por quê. Roda sem
chave e sem custo.

`llm` é o andar de cima. Ele lê os resultados, liga séries que se movem juntas,
nota o que a administração afirma, e propõe **hipótese com falsificador** — que
uma regra não propõe, porque proporia a mesma para toda empresa. Exige
`ANTHROPIC_API_KEY` no ambiente ou no Keychain (ver README), e uma agenda
inteira leva vários minutos, porque é uma chamada de modelo por passo.

Não há queda silenciosa de um para o outro. Se o modelo falhar, você vê a recusa
nomeada — nunca metade de uma investigação apresentada como inteira. O
`reasoner_id` no relatório diz quem concluiu o quê, porque uma conclusão de
regra e uma conclusão de modelo têm forças diferentes.

---

## Começando uma investigação

```bash
pat opportunity init --cod-cvm 14826 --as-of 2026-06-30 \
    --mandate "o resultado operacional se sustenta sem equivalência patrimonial?"
```

`--cik` para uma companhia americana. O identificador para **aqui**: a CLI é a
raiz de composição, e nenhuma camada abaixo volta a ver identificador de
regime. Dentro do Opportunity a empresa é um `CompanyProfile` com
`jurisdiction` e `identifiers` opacos —
`tests/opportunity/test_layering_opportunity.py` falha se alguém escrever
`cvm`, `sec`, `cik` ou `cd_conta` na camada, comentário incluído.

O `as_of` do workspace é o corte point-in-time de tudo que a investigação vê.
Ele só anda para frente: recuar invalidaria evidência já admitida — um trecho
citado sob o `as_of` antigo passaria a ser posterior ao novo, e a conclusão que
ele sustenta viraria vazamento retroativo sem ninguém ter mexido nela.

O `--mandate` não é enfeite: é a pergunta que a investigação responde, e o
crítico e a tese voltam a ela.

---

## Conversando com o agente

```bash
pat opportunity chat "Por que a receita caiu?"
pat opportunity chat                      # laço de leitura, Ctrl-D para sair
```

A intenção é classificada por **tabela declarada** — sem modelo, sem distância
de string. O conjunto é fechado:

| Intenção | Exemplo | O que o agente faz |
|---|---|---|
| `ASK` | "Por que a receita caiu?" | consulta o motor; se não há métrica, busca no corpus; se não há nada, **recusa com motivo** |
| `INVESTIGATE` | "Investiga isso." | decompõe em agenda e executa, sem confirmação |
| `ASSERT` | "Acho que o moat é escala." | abre **hipótese**, atribuída a você, e cobra o falsificador |
| `CHALLENGE` | "Mas e a concorrência?" | roda o crítico **e** põe as tarefas de falsificação na agenda |
| `CRITIQUE` | "Roda o advogado do diabo" | roda o crítico, sem mexer na agenda |
| `RESUME` | "Volta naquela hipótese de margem." | retoma o fio; se mais de uma casa, **pergunta qual** |
| `STATUS` | "Onde estamos?" | lê o estado |
| `UNCLEAR` | qualquer outra coisa | **pergunta de volta** |

`UNCLEAR` pergunta em vez de adivinhar porque adivinhar errado gasta uma
corrida de pesquisa e enche a agenda de tarefa que ninguém pediu — num diário
append-only isso é estado que não dá para dobrar depois.

Cada turno grava **o que foi dito e o que foi feito**, separados
(`ChatTurnRecorded.actions`). Uma resposta bem escrita é indistinguível, na
leitura, de uma resposta bem escrita que não fez nada; a lista de ações é o que
responde "o que mudou no workspace?".

Na saída, `stdout` é a fala e `stderr` traz a linha de auditoria:

    [turno 3 | investigate | planned_agenda, ran_tasks]

### Três coisas que o turno nunca faz

1. **Não inventa número.** Quando a resposta tem número, ele veio do motor e
   `grounded_in` traz o endereço. Uma pergunta sobre grandeza que o motor não
   responde vira recusa nomeada, nunca prosa que preenche a lacuna.
2. **Não transforma sua afirmação em claim.** Claim exige evidência. Uma
   afirmação sua é o começo de uma investigação, não o fim — vira hipótese
   aberta, atribuída a `Actor.USER`.
3. **Não mistura citação com cálculo.** Um trecho de release e um número do
   motor nunca aparecem na mesma frase sem dizer qual é qual. É por aí que um
   número de emissor entra numa resposta parecendo calculado.

---

## Pesquisa autônoma

```bash
pat opportunity research --objective "o que você acha da companhia?"
```

Uma pergunta de investimento **não** se decompõe em métricas — ela se decompõe
em **temas**, e cada tema pergunta algo que um analista reconheceria:

| tema | pergunta |
|---|---|
| `crescimento` | a receita cresce, e a que ritmo? |
| `rentabilidade` | o crescimento chega ao resultado operacional? |
| `geracao-de-caixa` | o resultado vira caixa? |
| `solvencia` | a estrutura de capital aguenta? |
| `retorno-sobre-capital` | o capital empregado rende mais do que custa? |
| `alocacao-de-capital` | para onde vai o caixa gerado? |
| `narrativa` | o que a companhia diz sobre o próprio negócio e os riscos? |

A tabela é declarada e versionada com o código (`opportunity/themes.py`). O que
ela decide é apenas **quais** temas se aplicam: por relevância declarada e por
disponibilidade real de métrica. Um planejador livre produziria decomposição
diferente a cada execução, e duas investigações da mesma empresa não seriam
comparáveis.

Três consequências que valem registrar:

- **Tema sem insumo não entra.** Viraria tarefa que só pode terminar em
  bloqueio, e bloqueio previsível treina o leitor a ignorar bloqueio.
- **Nenhum tema propõe hipótese.** Nenhuma regra sabe qual afirmação vale a
  pena testar *nesta* empresa; propor uma por template daria a mesma para
  todas. O que o tema abre é **pergunta**, honesta sobre o que o motor não
  responde.
- **A tabela é genérica.** Sem "assinantes", sem "churn": vocabulário de uma
  indústria faria "investiga o churn" ser respondido com a série de receita —
  substituição silenciosa do que foi perguntado. Pergunta que nenhum tema
  responde não vira agenda inventada.

Pergunta estreita continua pedindo um tema só: `--objective "investiga a
dívida"` abre `solvencia`, e não os seis.

O ciclo é explícito, e cada fase é uma função com nome:

    UNDERSTAND -> PLAN -> RESEARCH -> ANALYZE -> TEST -> CRITICIZE -> UPDATE -> NEXT

Sem framework de agente. O laço é um `for` sobre tarefas prontas. O valor deste
desenho está em poder abrir o relatório e ver exatamente o que foi perguntado,
o que voltou, o que foi interpretado e o que foi barrado — um framework
esconderia justamente isso.

Duas fronteiras que o laço mantém:

- **RESEARCH é determinístico.** Só fala com `PatTools`. Nenhum modelo
  participa e nenhum número é produzido: os números vêm do motor, com
  procedência, e o que o laço faz é colecionar os endereços deles.
- **TEST é mecânico.** Toda citação de uma interpretação tem que estar no que
  os passos daquela tarefa de fato produziram. Uma frase que cita um ID
  inexistente é barrada **e registrada** (`RejectedFinding`), nunca descartada
  em silêncio — descartar caladamente esconderia que o interpretador cita o que
  não existe, e isso é informação sobre a qualidade do raciocínio.

O raciocinador default é o `ShapeReasoner`: planeja por tabela declarada,
interpreta por *forma* de série (direção e magnitude, nunca causa) e julga por
critério. Ele não chama modelo nenhum. Isso não é uma versão reduzida do
sistema — é o piso: a suíte inteira testa o laço de verdade, e a fronteira
entre trabalho-de-regra e trabalho-de-modelo fica visível em vez de dissolvida
numa frase.

O laço grava a cada tarefa, e não no fim. Uma volta que morra na terceira
tarefa deixa as duas primeiras gravadas, e o operador reabre e continua.

`MAX_TASKS = 12` por corrida. Não é economia: é o que impede uma agenda que se
realimenta de rodar para sempre sem ninguém olhar.

### Insumo ausente

Nunca zero, nunca parcial, nunca `None` que se lê como zero. Uma tarefa que não
tem como ser feita vira `BLOCKED` (o agente pode destravar) ou `NEEDS_HUMAN`
(depende de uma decisão sua), com motivo e remédio nomeados. `pat opportunity
research` **sai com 0** nesse caso: travado é o estado correto de quem não tem
o dado, e sair com 1 faria um script tratar "faltou cobertura" como "o comando
quebrou".

---

## Lendo o status

```bash
pat opportunity status
```

    CIA BRASILEIRA DE DISTRIBUICAO - as_of 2026-06-30 - open
    objetivo: a margem e a receita
    tarefas: complete=2, needs_human=1
    hipoteses: 2 (1 aberta)
    claims: 1; conclusoes: 1; valuations: 1; teses: 1
      h-moat-escala: weakened - 1 falsificador(es) por testar
      h-margem-baixa: open
    travado, esperando:
      churn: nenhuma metrica registrada casa com o objetivo -> escolha a metrica

O que ler primeiro: **o que está travado e o que falta testar**. Uma hipótese
`SUPPORTED` com falsificador por testar é uma hipótese que ninguém tentou
derrubar, e o crítico vai dizer isso.

Estados de tarefa: `PENDING` → `RUNNING` → `COMPLETE`, ou `BLOCKED` /
`NEEDS_HUMAN`. Marcar `RUNNING` no diário é o que faz um processo morto no meio
deixar rastro: ao reabrir, uma tarefa `RUNNING` que ninguém está rodando é
visivelmente uma corrida interrompida, e não uma tarefa que nunca começou.

Estados de hipótese: `OPEN` → `SUPPORTED` / `WEAKENED` / `REJECTED` /
`INCONCLUSIVE`. `SUPPORTED` exige evidência a favor; `REJECTED` exige
contra-evidência (rejeitar por mudança de opinião apagaria a razão, que é o que
se lê depois); voltar para `OPEN` limpa a força declarada.

Concluir exige uma hipótese **testada** — só `OPEN` não conclui. `WEAKENED` e
`INCONCLUSIVE` são resultados de teste, e "há indício, insuficiente" e "os
dados não resolvem isso" são conclusões, das mais úteis. Exigir `SUPPORTED` ou
`REJECTED` empurraria as dúvidas para um dos extremos.

---

## O advogado do diabo

```bash
pat opportunity critic     # sai com 1 se houver achado DURO
```

O código de saída é a parte útil num script: "esta tese pode ser apresentada?"
tem que ser respondível sem alguém ler a prosa.

Achados **duros**:

- `DECISIVE_COUNTER_IGNORED` — a hipótese continua `SUPPORTED` com
  contra-evidência marcada como decisiva: o falsificador ocorreu e não foi
  honrado.
- `SUPPORTED_WITHOUT_FALSIFICATION` — sustentada sem nenhum registro de busca
  por contra-evidência. Zero contra-evidências é ambíguo entre "procuramos e
  não achamos" e "não procuramos".
- `SUPPORTED_WITH_BLOCKED_TASK` — sustentada enquanto a tarefa que a testaria
  continua travada.
- `USER_ASSERTION_CONTRADICTED` — **o agente discorda de você.**
- `ASSUMPTION_AS_OBSERVATION` — um claim afirma grandeza e se ancora só em
  premissa. Premissa sustenta projeção, nunca histórico.

Achados leves: `NO_ALTERNATIVE_CONSIDERED`, `SINGLE_PERIOD_SUPPORT`,
`ISSUER_ONLY_SUPPORT`, `FALSIFIER_UNTESTED`, `ORPHAN_HYPOTHESIS`.

O crítico **não muda estado** e não reescreve hipótese. Corrigir a partir de
uma crítica seria o laço `crítico → autor → crítico`, que é um amostrador que
uma hora aprova algo errado.

O que ele faz, além de apontar, é devolver **tarefas**
(`falsification_agenda`), em prioridade alta. Testar o que derrubaria a tese é
mais urgente que acrescentar mais uma evidência a favor: a segunda só muda a
confiança, a primeira muda a conclusão. Um crítico que só aponta produz um
relatório que alguém lê, concorda e arquiva.

### O agente pode discordar de você

`USER_ASSERTION_CONTRADICTED` só aparece quando há contra-evidência registrada
apontando para o contrário do que você afirmou, e o achado carrega os endereços
dela. Discordar sem evidência não é ceticismo — é outra opinião, e o sistema
não tem por que ter opinião.

Na conversa isso vem em `TurnResponse.disagreement`, e a decisão volta para
você: manter a hipótese mesmo assim é legítimo, e fica no diário como **sua**.

---

## Valuation

```bash
pat opportunity valuation --declare modelo.toml
```

TOML, e não vinte flags: uma premissa exige valor, unidade, base e
justificativa, e espremer isso numa linha de comando produziria a justificativa
de uma palavra que o campo existe para impedir.

```toml
slug = "base"
currency = "BRL"
horizon_years = 5

[data.revenue-base]                 # DADO: veio do motor, com endereço
label = "receita FY2023"
value = "100000000"
unit = "BRL"
result_id = "receita_liquida@v1|br:cnpj:...|2023-12-31|consolidated|2026-06-30"

[assumptions.wacc]                  # PREMISSA: escolha, assinada
label = "custo de capital"
value = "0.14"
basis = "market"
rationale = "custo de capital de varejo alavancado no país"
```

Quatro coisas separadas, e nunca colapsadas:

- **DADO** (`DataPoint`) — número do motor, com `result_id`.
- **PREMISSA** (`Assumption`) — escolha, com `basis` e `rationale`
  obrigatórios. `HISTORICAL` e `ISSUER_GUIDANCE` exigem `derived_from`: uma
  premissa que se diz derivada precisa dizer de quê, senão é `JUDGMENT` com
  nome melhor.
- **CÁLCULO** — determinístico, `Decimal`, sem arredondamento intermediário.
- **INTERPRETAÇÃO** — prosa, gravada separada
  (`ValuationInterpreted`).

**Não existe premissa default.** Falta uma? `ValuationUnavailable` dizendo
qual. Um default seria uma escolha de investimento embutida na biblioteca.

O modelo vai para o diário; o **resultado não**. Ele é recomputável a partir do
modelo, e guardar os dois criaria uma segunda verdade que poderia divergir.

`terminal_share` sai junto do valor. Acima de ~0,75 o resultado avisa: o modelo
está dizendo mais sobre a premissa terminal do que sobre a companhia.

A `sensitivity()` reexecuta o modelo inteiro por célula em vez de interpolar —
as bordas são justamente onde a tese quebra, e é onde a aproximação erra mais.
Célula que não converge **some** da grade; um zero num canto se leria como
"vale zero neste cenário", quando o certo é "a fórmula não tem solução aqui".

`implied_growth()` responde a pergunta que uma tese honesta faz antes das
outras — "o que o preço de hoje já está assumindo?".

Trocar uma premissa é `AssumptionChanged`, e o valor antigo continua no diário.
Trocar premissa é onde uma tese se auto-ajusta até dar o número que o autor
queria; o histórico não proíbe isso — torna visível.

---

## A tese

```bash
pat opportunity thesis --draft tese.toml    # sai com 1 se a auditoria achar algo DURO
```

Todo campo obrigatório existe porque a ausência dele é uma forma conhecida de a
tese parecer mais forte do que é:

- `key_assumptions` — vazio esconderia de que a tese depende.
- `risks` — vazio afirmaria que não há nenhum.
- `falsifiers` — sem isso a tese não está sendo afirmada, está sendo torcida.
- `counter_thesis` — o caso do outro lado, **na versão forte**. A fraca dá só a
  impressão de ter considerado.

Um risco `THESIS_BREAKING` que não aparece entre os falsificadores é recusado
pelo contrato: seria uma tese que lista o que a mata na seção de riscos e jura,
na seção de falsificadores, que nada a mata. `WATCH` sem catalisador também —
`WATCH` quer dizer "sem posição hoje, **com** gatilho"; sem ele, é
`NO_POSITION`.

`audit_thesis()` percorre a cadeia até a evidência e diz onde ela se rompe:
claim citado que não existe, claim sem evidência, hipótese de apoio rejeitada,
valuation citada que não foi declarada. Integridade **referencial** — conferir
que um `result_id` resolve até os bytes é outro trabalho, e ele já existe
(`pat provenance`, `pat provenance-unit`).

Reescrever a tese substitui a versão corrente no estado; o diário mantém todas.

---

## Brasil e Estados Unidos

A camada não tem jurisdição. A mesma investigação roda para as duas, pelo mesmo
código, sem uma linha de exceção — `PatTools` nem sequer aceita `entity_id`
como parâmetro, o que torna resolução cross-jurisdiction **inexpressível** em
vez de proibida por convenção.

O que **não** é igual é a cobertura, e o sistema diz isso em vez de esconder:

| | Brasil | Estados Unidos |
|---|---|---|
| Fatos estruturados | sim | sim |
| Métricas semânticas | sim | sim |
| Descoberta de documentos | sim | **`NOT_IMPLEMENTED`** |
| Busca no corpus | sim | vazia, porque nada foi descoberto |

`USDocumentProvider.discover()` devolve `ProviderUnavailable` com remédio —
nunca `()`. Lista vazia seria uma afirmação sobre o **mundo** ("a companhia não
comenta as próprias margens"); o fato é sobre o **sistema**. As duas chegam ao
analista como a mesma ausência e pedem ações opostas: uma entra na tese como
achado, a outra é uma lacuna do PAT.

**Consequência prática:** uma investigação sobre uma companhia americana hoje
sustenta hipóteses em métrica do motor, não em evidência textual. Isso não é um
detalhe — é o que separa "a margem caiu 300bps" de "a administração atribuiu a
queda a mix".

---

## Os limites do agente

Ditos em voz alta, porque um sistema que não os diz os esconde:

1. **Não produz número.** Nem interpolando, nem estimando, nem "aproximadamente".
2. **Não converte moeda.** Insumos em moedas diferentes param o cálculo.
3. **Não infere período a partir de `reference_date`.** É inferência por
   formato, o mesmo erro de casar conta por rótulo.
4. **Não casa métrica por semelhança de string.** A tabela é declarada; termo
   que não está nela não casa, e a tarefa vira `NEEDS_HUMAN` dizendo isso.
5. **Não explica causa.** O `ShapeReasoner` diz a *forma* da série. "A receita
   caiu 12%" é observação; "porque a concorrência apertou" é hipótese, e tem
   que ser aberta como uma.
6. **Não usa OCR.** Citação vinda de reconhecimento óptico é texto que ninguém
   escreveu.
7. **Não decide investir.** A tese carrega direção, contra-tese e o que a
   derruba; a posição é sua.
8. **Não adivinha o que você quis dizer.** Pergunta.

---

## Referência de comandos

```
pat opportunity init       --cod-cvm N | --cik N --as-of DATA [--mandate ...] [--title ...]
pat opportunity list
pat opportunity status     [--workspace ID]
pat opportunity chat       [texto...] [--workspace ID]
pat opportunity research   --objective "..." [--workspace ID] [--max-tasks N]
pat opportunity critic     [--workspace ID]              # sai 1 se houver achado DURO
pat opportunity valuation  [--declare TOML] [--model SLUG] [--workspace ID]
pat opportunity thesis     [--draft TOML] [--slug SLUG] [--workspace ID]
```

Sem `--workspace`, os comandos usam o único que existe. Com mais de um, a CLI
lista e pede que você escolha — pegar "o mais recente" seria conveniência que
erra em silêncio: você digitaria um turno na investigação errada e só
perceberia páginas depois.

---

## Onde as coisas moram

```
src/pat/contracts/opportunity/   contratos (não dependem de mais nada do projeto)
  base.py        workspace, perfil da empresa, ator
  agenda.py      tarefas, achados, bloqueios, perguntas
  hypothesis.py  hipótese, evidência, contra-evidência, claim, conclusão
  documents.py   capacidades e indisponibilidade de provider
  loop.py        passos, resultados, propostas, veredito
  critic.py      achados de workspace, tentativa de falsificação
  valuation.py   premissa, dado, modelo, resultado, grade
  thesis.py      tese, risco, catalisador, auditoria
  chat.py        intenção, ação, turno
  events.py      a união dos corpos de evento (folha; ninguém importa de volta)

src/pat/opportunity/
  journal.py     append-only, com detecção de buraco de sequência
  state.py       a dobra pura: eventos -> estado
  store.py       onde os workspaces moram, e como se abre um
  company.py     catálogo -> CompanyProfile           (exceção de camada)
  tools.py       a mesa: a única porta para o motor    (exceção de camada)
  documents.py   inventário de documentos armazenados
  providers.py   adapters por jurisdição               (exceção de camada)
  reason.py      Reasoner (Protocol), ShapeReasoner, ScriptedReasoner
  research.py    o ciclo de oito fases
  critic.py      o advogado do diabo, mecânico
  valuation.py   DCF, sensibilidade, valuation reversa (exceção: calcula)
  thesis.py      auditoria da cadeia da tese
  chat.py        a porta: turno -> ação

src/pat/cli_opportunity.py       a CLI (fora da camada, de propósito)
tests/opportunity/               inclusive test_end_to_end.py e o teste de camada
```

`tests/opportunity/test_layering_opportunity.py` verifica por AST e por texto
que a camada não cita regime e não importa ingestão, parsing ou taxonomia
concreta. As exceções são três arquivos, cada uma com a razão escrita na lista
— e cada uma tem um teste que confere que ela continua **estreita**.

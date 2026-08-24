# Convenções do projeto

Personal Investment Research Agent. Leia o `README.md` para a arquitetura.
Este arquivo cobre o que não é óbvio a partir do código.

## Regras que não se negociam

1. **O LLM nunca produz números.** Produz código que produz números. Aritmética,
   agregação e séries temporais são determinísticas. Se você se pegar pedindo a um
   modelo para calcular ou estimar um valor, o desenho está errado.

2. **O bronze é imutável.** Nada neste repositório edita, sobrescreve ou remove um
   blob. Reprocessamento gera dados novos ao lado dos antigos. Blobs são gravados
   somente-leitura no sistema de arquivos — se um código precisar de `chmod` para
   escrever, ele está violando a arquitetura.

3. **Providers não interpretam.** `sources/` baixa bytes e registra procedência.
   Todo parsing pertence a silver (Fase 1). Um provider que abre um ZIP está no lugar
   errado.

4. **Todo fato é bitemporal.** `period_end` e `knowledge_date` são obrigatórios e
   distintos. Nenhuma consulta analítica roda sem uma data `AS OF`.

5. **Datetime sempre timezone-aware.** Os contratos rejeitam datetime ingênuo. Isso
   é o que mantém comparações point-in-time com o mesmo significado em qualquer
   máquina.

6. **`Decimal` para dinheiro, nunca `float`.**

## Contratos primeiro

`src/pat/contracts/` não depende de nenhum outro módulo do projeto; todo o resto
depende dele. Mudança de contrato é mudança de arquitetura — pense antes.

Todos os modelos herdam de `Frozen`: imutáveis e `extra="forbid"`. Campo inesperado
falha na fronteira de entrada, não três camadas adiante, já como número errado no
relatório.

## Versionamento é como o passado continua reproduzível

Três `*_version` existem no sistema e nenhum é decorativo:

- `SourceProvider.version` — muda quando a lógica de resolução de URL muda.
- `Lineage.extractor_version` — reprocessar com extrator novo cria fatos novos; não
  sobrescreve os antigos.
- `Fact.metric_version` — mudou a definição de EBITDA? Vira `v2`. Análises antigas
  continuam reproduzindo com `v1`.

Se você mudar comportamento sem incrementar a versão correspondente, quebrou I3.

## Reapresentações da CVM

A CVM reescreve o ZIP anual quando há reapresentação: URL estável, conteúdo variável.
Por isso `resource_key` é o ano (recurso lógico) e a identidade é o SHA-256 (recurso
físico). Anos já ingeridos **não** são imutáveis — precisam ser re-buscados
periodicamente. `pat changed` lista o que mudou.

Este comportamento tem teste dedicado:
`tests/unit/test_ingest.py::test_conteudo_alterado_na_origem_e_detectavel`. Se ele
quebrar, o tratamento de reapresentação quebrou.

A ponta a ponta, contra números públicos reais, o caso vive em
`tests/network/test_dfp_pipeline_live.py::test_reapresentacao_real_do_gpa_ponta_a_ponta`
— a receita FY2023 do GPA, divulgada em fev/2024 e reapresentada em fev/2025. É o
teste que prova o critério da Fase 1, e ele só roda com `-m network`.

Cuidado com a chave lógica ao consultar reapresentações: uma companhia costuma
reapresentar individual **e** consolidado no mesmo documento. `restatements()` não
filtra escopo por default — de propósito, porque esconder uma das metades daria uma
contagem que parece certa e está errada.

## A camada semântica não é brasileira

`3.01` não é receita líquida — é *onde* a receita líquida aparece no plano da
CVM. Manter as duas coisas separadas é o que impede o sistema de virar
permanentemente brasileiro. Três eixos, nunca colapsados:

- **Conceito** (`revenue_net`) — ideia econômica, sem jurisdição. Vive em
  `semantics/concepts.py`. `concept_id` é imutável para sempre: significado
  refinado vira conceito novo, não `revenue_net@v2`.
- **Endereço** (`LineAddress`) — onde a ideia aparece numa taxonomia. Opaco
  fora do adapter daquela taxonomia.
- **Regime** — framework, jurisdição e fonte são metadados do *mapeamento*.
  Nunca da métrica.

Consequências que não se negociam:

1. **Nenhum módulo em `definitions/` ou `concepts.py` pode citar `cd_conta`,
   `cod_cvm` ou elemento XBRL** — nem em comentário. `tests/semantics/
   test_layering.py` faz a checagem por AST e falha se alguém escorregar.
2. **Só `frameworks/cvm_dfp/resolver.py` importa `pat.query`.** O motor fala
   com o mundo pelo Protocol `FactResolver`. Plugar a SEC é escrever um adapter
   e um resolver — `tests/semantics/test_second_framework.py` roda as métricas
   reais contra um regime fictício em dólar justamente para provar isso.
3. **Equivalência semântica é afirmada, nunca deduzida.** Não existe casamento
   por rótulo em lugar nenhum. Todo `[[binding]]` exige `equivalence_basis`
   dizendo por que aquela linha é aquele conceito. `label_as_reported` é
   conferido por `pat mapping-check` e jamais usado para busca — conta
   renomeada tem que quebrar teste, não resolver calada para outra coisa.

## Fidelidade: aproximar pode, esconder não

`fidelity` no binding é `exact`, `approximate` ou `partial`, e qualquer coisa
diferente de `exact` **exige** `divergence_note`. A fidelidade sobe pelo grafo:
o `MetricResult` carrega a mais fraca de toda a cadeia.

O caso que motiva isso é `d_and_a_pnl`. A D&A que passou pelo resultado tem
código estável no DFC de *cada empresa*, mas não no plano padronizado; a família
default aproxima pela DVA (7.04.01), que é `d_and_a_retained` — grandeza
economicamente diferente. No GPA a diferença é 0,3% em FY2023 e 46% em FY2022.
Um sistema que tratasse as duas como sinônimas erraria por quase R$ 900 milhões
num ano e acertaria no outro: erro intermitente, o pior tipo de achar.

Por isso são conceitos separados, e por isso `ebitda@v1` conceitualmente usa
`d_and_a_pnl` mesmo quando o único binding disponível é aproximado. O número
sai, marcado.

## Versões de métrica

`ebitda@v1` depende de `ebit@v1`, pinado. Publicar `ebit@v2` deixa `ebitda@v1`
bit a bit idêntica — é assim que análise antiga continua reproduzindo. Mudou a
aritmética ou o conjunto de conceitos? Módulo novo, versão nova, os dois
registrados ao mesmo tempo. O antigo nunca é editado.

Três coisas entram em todo `MetricResult` porque as três podem mudar um número:
versão da métrica, `as_of`, e o sha256 da **cadeia** de mapeamento (não só do
arquivo da empresa — editar a família muda o resultado sem tocar no arquivo
dela).

## Decisões contábeis já tomadas

Não reabrir sem versão nova:

- **A1 — `ebit@v1` inclui equivalência patrimonial.** É o resultado operacional
  como reportado. No GPA FY2023: EBIT de R$ 677 MM contém R$ 768 MM de
  equivalência; sem ela seria −R$ 91 MM. EBIT ex-equivalência é métrica nova.
- **A3 — `lucro_liquido@v1` será o atribuível à controladora.** Resultado total
  (incluindo não controladores) é métrica separada, sem fallback automático.
- **A4 — dívida bruta trata arrendamento explicitamente**, com o tratamento
  visível no resultado. Sem normalização silenciosa.
- **EBITDA ajustado divulgado** é conceito e fonte (`issuer`) separados. Nunca
  fallback de `ebitda@v1`.

## Como adicionar uma empresa

Um TOML em `semantics/mappings/`, herdando da família e sobrescrevendo só o que
diverge — o mapeamento do GPA tem *um* binding. O caminho:

```
pat concepts                       que conceitos existem
pat accounts --cod-cvm N --statement DFC_MI --period-end ... --as-of ...
pat mapping-check --cod-cvm N --period-end ... --as-of ...
```

`pat accounts` existe para um humano escolher a linha. Não é inferência, e não
deve virar uma.

## Insumo ausente

Nunca zero, nunca parcial, nunca `None` que se lê como zero. `MetricUnavailable`
com motivo nomeado, conceito que faltou, endereços tentados e o que fazer. Não
existe `allow_missing=True`, e não deve passar a existir.

Moeda nunca é convertida implicitamente. Insumos em moedas diferentes param o
cálculo.

## A camada de corpus (Fase 5)

Evidência textual é uma camada **paralela** aos fatos, nunca uma fonte deles.
As regras que não se negociam:

1. **Número de emissor é citação, nunca insumo.** Um release diz "receita de
   R$ 511,9 bilhões". O algarismo pode sair no relatório dentro de um
   `QuoteClaim`, atribuído a quem publicou. Não pode entrar no gold, virar
   insumo de derivação, ser reescalado ou convertido. Como sempre, a garantia é
   de tipo: `QuoteClaim` não tem `value`, `unit`, `currency` nem `Decimal`.
   Chamo o modo de falha de *lavagem de número do emissor*; é por onde todo
   sistema desse tipo acaba publicando um número que ninguém calculou.

2. **`published_at` é o `knowledge_date` do texto.** Consulta ao corpus sem
   `as_of` não existe, e o corte está no SQL. Citar documento posterior ao
   `as_of` é o vazamento mais fácil de cometer — porque deixa a resposta
   *melhor*.

3. **Toda data carrega `DateBasis`.** Data adivinhada que se apresenta como
   lida é o análogo textual do número aproximado que se apresenta como exato.
   `RETRIEVED_AT_FALLBACK` erra para o lado seguro (limite superior) e diz que
   errou.

4. **`reference_date` não é período coberto.** O campo do protocolo diz
   "2024-09-30" tanto para um relatório do 3T24 quanto para uma ata marcada
   naquele dia. Derivar período disso é inferência por formato — o mesmo erro
   de casar conta por rótulo.

5. **Verbatim é byte a byte.** `DocumentUnit.text` é fatia literal do texto da
   página; `QuoteClaim.text` é idêntico a ela. Sem normalizar, sem `strip`, sem
   reticência. A conferência é `reextrair → fatiar → comparar`, e é o que
   `pat provenance-unit` roda.

6. **Falha de extração é registro, nunca ausência.** Documento sem unidade e
   sem falha se lê como "não há nada a dizer". Não existe fallback para OCR nem
   para uma segunda biblioteca: citação vinda de reconhecimento óptico é texto
   que ninguém escreveu.

7. **`extraction_version` entra no `unit_id`**, e inclui a versão efetiva do
   `pypdf`. Reprocessar cria unidades ao lado das antigas. É
   `extractor_version` da Fase 1, aplicado a texto.

`Categoria` da CVM → `DocumentKind` é uma **tabela declarada** em
`parse/cvm_ipe.py`, não uma heurística. Categoria desconhecida vira `OTHER`
explicitamente; o documento continua armazenado e citável, marcado como não
classificado. É a mesma disciplina de `equivalence_basis`.

## Testes

- `pytest` — suite padrão, sem rede.
- `pytest -m network` — contra fontes públicas reais. Detecta mudança de layout de
  URL na origem; não roda em CI por depender de terceiro.

Os golden tests da Fase 2 (`tests/semantics/golden_gpa.py`) transcrevem linhas
reais da DFP do GPA à mão, e passam pelo parser e pelo builder de verdade — não
inserem fatos direto no gold. O par offline/network é deliberado: o offline prova
que o cálculo está certo, o de rede prova que o *mapeamento* ainda aponta para as
linhas certas no que a CVM publica hoje. Conta renumerada na origem só aparece
no segundo.

Helpers de teste são importados como `tests.conftest`, com nome qualificado.
Importar `conftest` solto colide assim que existe mais de um `conftest.py` na
árvore.

Ao adicionar um provider, escreva os testes de `resolve()` sem rede (construção de
URL, validação de parâmetro) e deixe só a verificação de layout em `-m network`.

## Estilo

- Identificadores em inglês, docstrings e comentários em português.
- Comentário explica *por que*, não *o quê*. A maior parte do valor dos comentários
  aqui está em registrar a razão de uma restrição, para que ninguém a remova depois
  por parecer excesso de zelo.
- Dependências mínimas e travadas em `uv.lock`. Ao mexer em `pyproject.toml`, rode
  `uv lock` e commite o lockfile junto — `uv lock --check` falha se eles divergirem.
  Instale sempre com `--locked`. Evite frameworks de agente: o valor do desenho está
  no pipeline explícito e inspecionável, e frameworks escondem exatamente o que
  precisa ser auditado.

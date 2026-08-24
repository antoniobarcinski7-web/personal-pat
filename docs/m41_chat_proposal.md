# M4.1 — Interface conversacional local

**Proposta arquitetural. Nenhuma linha de código foi escrita.**

- **Baseline:** `f8f5e8a` (`feat(research): persist LLM call provenance`) + árvore de trabalho suja com `writer.py`, os três mapeamentos BR e os testes de M3/M4.
- **Escopo:** uma camada de interação sobre a Fase 3. Nada abaixo dela muda de comportamento.
- **Dependências novas propostas:** **zero.**
- **Superfície nova estimada:** 7 arquivos novos de código (~700 linhas), 1 arquivo de contrato novo, 3 arquivos existentes modificados (~60 linhas), ~600 linhas de teste.

---

## Sumário

1. [Executive Summary](#1-executive-summary)
2. [Estado atual da arquitetura](#2-estado-atual-da-arquitetura)
3. [Como o chat se conecta à Fase 3](#3-como-o-chat-se-conecta-à-fase-3)
4. [Fluxo completo de uma mensagem](#4-fluxo-completo-de-uma-mensagem)
5. [Arquitetura de conversation state](#5-arquitetura-de-conversation-state)
6. [Como preservar "LLM interpreta, código calcula"](#6-como-preservar-llm-interpreta-código-calcula)
7. [Proposta de API/backend](#7-proposta-de-apibackend)
8. [Proposta de frontend](#8-proposta-de-frontend)
9. [Proposta de stack](#9-proposta-de-stack)
10. [Estrutura de arquivos](#10-estrutura-de-arquivos)
11. [Reutilização dos componentes existentes](#11-reutilização-dos-componentes-existentes)
12. [Novos componentes necessários](#12-novos-componentes-necessários)
13. [Contratos necessários](#13-contratos-necessários)
14. [Erros e recusas](#14-erros-e-recusas)
15. [Provenance e citations](#15-provenance-e-citations)
16. ["Ver plano"](#16-ver-plano)
17. [Perguntas de follow-up](#17-perguntas-de-follow-up)
18. [Testes necessários](#18-testes-necessários)
19. [Divisão em sub-agents](#19-divisão-em-sub-agents)
20. [Ordem de implementação](#20-ordem-de-implementação)
21. [Critérios objetivos de aceitação](#21-critérios-objetivos-de-aceitação)
22. [Riscos arquiteturais](#22-riscos-arquiteturais)
23. [O que explicitamente NÃO deve ser implementado agora](#23-o-que-explicitamente-não-deve-ser-implementado-agora)
24. [Plano de rollout incremental](#24-plano-de-rollout-incremental)
25. [Prompt exato para cada sub-agent](#25-prompt-exato-para-cada-sub-agent)

---

# 1. Executive Summary

A M4.1 é **pequena**, e essa é a descoberta principal desta auditoria. O pipeline conversacional já existe inteiro: `plan_for_question()` e `run_plan()` em `src/pat/research/__init__.py` compõem, em processo, exatamente o que `pat plan` + `pat ask --writer` fazem em duas invocações. O que falta é (a) um lugar para guardar o estado de uma conversa, (b) uma casca HTTP, (c) uma página.

**A proposta em uma frase:** um pacote novo `src/pat/chat/` que orquestra `plan_for_question → review_plan → run_plan` por mensagem, um servidor `http.server` da biblioteca padrão em um arquivo, e um `index.html` sem build.

Três achados da auditoria mudam o desenho e valem ser ditos antes de qualquer coisa:

**Achado 1 — o escritor hoje não consegue distinguir empresas.** `render.describe()` (`src/pat/research/render.py:77`) devolve `"ebitda@v1, consolidado, exercicio findo em 2024-12-31, conhecido em ..., AS OF ..."`. Não há `entity_id` nem `display_name`. Numa comparação de três empresas no mesmo período, o escritor recebe **três `means` idênticos** e só consegue diferenciá-los pelo `step_id` que o planejador escolheu. O caso de uso central da M4.1 — "Compare o EBITDA de Petrobras, Vale e WEG" — é precisamente o caso em que isso quebra. `MetricResult` já carrega `entity_id` e `display_name` (`contracts/semantics.py:447,453`); `describe()` simplesmente não os lê. **Correção obrigatória, uma linha, em `render.py`.**

**Achado 2 — "qual teve a maior queda?" não é respondível com atribuição.** `DerivationOp.MIN/MAX` (`research/derive.py:184-187`) devolvem o *valor* extremo, nunca *qual insumo* o produziu. `DerivedValue` não tem campo para isso e o escritor não vê valores, então não pode dizer qual empresa caiu mais. Isso não é bug: é o limite da gramática fechada. A resposta certa para a M4.1 é **recusar com nome** (`unsupported_question`) e mostrar as três variações lado a lado, deixando a comparação com o leitor. Um `argmin`/`argmax` seria `DerivationOp` novo + campo novo em `DerivedValue` — mudança de contrato, fora de escopo, registrada em §23 como candidato a M4.2.

**Achado 3 — `pinned_periods` é whitelist, não piso.** `validate.py:245` rejeita qualquer passo cujo `period_end` não esteja em `pinned_periods`. Carregar pinos de período adiante entre turnos — a solução ingênua para follow-up — faria "E como isso mudou desde 2023?" ser recusado com `PIN_CONTRADICTED_PERIOD`. **Pinos não são o mecanismo de contexto conversacional.** O mecanismo é um bloco de histórico *estrutural* no prompt do planejador, e os pinos continuam significando o que sempre significaram: o que o **usuário** fixou.

O invariante da M4.1, e o que a arquitetura precisa garantir estruturalmente:

> Todo número exibido em qualquer turno de qualquer conversa foi produzido pelo Metric Engine **naquele turno**. Nenhum número atravessa turnos. O histórico da conversa entra no prompt do planejador e em lugar nenhum mais.

Isso é verificável, não prometido: o único caminho de um `Decimal` até a tela continua sendo `execute_plan → render.render_results → answer.substitute`, e o histórico que a camada de chat monta é um objeto Pydantic sem nenhum campo capaz de carregar um valor — a mesma técnica que faz `ResearchPlan` não ter onde escrever um número.

---

# 2. Estado atual da arquitetura

## 2.1 O que foi lido

Auditoria completa de `src/pat/` (48 módulos), `tests/` (39 arquivos), `pyproject.toml`, `CLAUDE.md`, `README.md` e `docs/phase3_proposal.md`. O warehouse local (`data/warehouse.duckdb`, 19 MB) foi consultado com `pat capability`.

## 2.2 Dependências

```toml
dependencies = ["pydantic>=2.7", "httpx>=0.27", "duckdb>=1.0", "pytz>=2024.1", "anthropic>=0.122.0"]
[project.optional-dependencies]
dev = ["pytest>=8.0"]
```

**Não há FastAPI, Starlette, uvicorn, Flask, Jinja ou qualquer servidor HTTP.** `httpx` é cliente, usado por `sources/public/cvm.py` para baixar da CVM. Não há nada de frontend, nenhum `package.json`, nenhum passo de build.

## 2.3 Como o PAT é executado hoje

Entrypoint único: `pat = "pat.cli:main"` — `argparse` com 22 subcomandos (`src/pat/cli.py:1169`). Cada comando abre a conexão DuckDB, faz o trabalho, fecha. Não há processo de vida longa em nenhum lugar do sistema.

O caminho conversacional atual são duas invocações:

```bash
pat plan "Qual a margem EBITDA do GPA em 2024?" --out p.json   # 1 chamada de modelo
pat ask --plan-file p.json --writer                             # 1 chamada de modelo
```

## 2.4 O que já existe da Fase 3

| Componente | Arquivo | Status |
|---|---|---|
| Contratos | `contracts/research.py` (638 ln) | fechado |
| Canonicalização/hash | `research/canonical.py` | fechado |
| Capability snapshot | `research/capability.py` | fechado |
| Planejador (LLM) | `research/planner.py` (400 ln) | fechado |
| Validador | `research/validate.py` (376 ln) | fechado |
| Resolver | `research/resolve.py` | fechado |
| Derivação | `research/derive.py` | fechado |
| Executor | `research/execute.py` | fechado |
| Renderer / claims | `research/render.py` | fechado |
| Answer / regra do dígito | `research/answer.py` | fechado |
| Escritor (LLM) | `research/writer.py` (532 ln) | novo, não commitado |
| Manifesto | `research/manifest.py` | fechado |
| Porta de LLM | `research/llm/__init__.py` | fechado |
| Adapter Anthropic | `research/llm/anthropic.py` | fechado |
| Cache de LLM | `research/llm/cache.py` + `llm/store.py` | fechado |
| Persistência | `store/research.py`, `store/llm_calls.py` | fechado |
| Raiz de composição | `research/__init__.py` (306 ln) | fechado |

## 2.5 Capacidades reais no warehouse

```
capability_sha256  347acc851cd116f4f7c6f05981119382ead084a7db009bd88f66f157322993db

métricas (5)     d_and_a@v1 · ebit@v1 · ebitda@v1 · margem_ebitda@v1 · receita_liquida@v1
derivações (7)   cagr · delta · delta_pct · max · mean · min · ratio
entidades (4)    br:cnpj:33000167000101  PETROLEO BRASILEIRO S.A. PETROBRAS  (2022, 2023, 2024)
                 br:cnpj:33592510000154  VALE S.A.                           (2022, 2023, 2024)
                 br:cnpj:47508411000156  CIA BRASILEIRA DE DISTRIBUICAO      (2022, 2023, 2024)
                 br:cnpj:84429695000111  WEG S.A.                            (2022, 2023, 2024)
mapeamentos      4 próprios (fidelidade exact) + família default (approximate)
```

`SnapshotLimits`: `max_steps=32`, `max_entities=4`, `max_periods_per_entity=12`, `max_serialized_bytes=65_536`. O exemplo do usuário (3 empresas × 1 métrica) cabe folgado. `max_entities=4` **não sobe** — o warehouse tem exatamente 4 empresas.

## 2.6 As regras de camada que restringem o desenho

`tests/research/test_layering_research.py` verifica por AST, sobre `src/pat/research/`:

| Guard | Regra | Consequência para a M4.1 |
|---|---|---|
| `test_a_rede_entra_por_um_arquivo_so` | conjunto **exato** de arquivos com `httpx\|urllib\|socket\|requests\|anthropic` = `{llm/anthropic.py}` | **o servidor HTTP não pode morar em `research/`** |
| `test_so_o_escritor_e_o_planejador_falam_com_o_modelo` | conjunto **exato** de arquivos com `llm.complete(` = `{planner.py, writer.py}` | **uma terceira chamada de modelo dentro de `research/` quebraria o guard** |
| `test_existe_exatamente_um_adapter_concreto` | `llm/*.py` = `{__init__, anthropic, cache, store}` | nada novo em `llm/` |
| `test_so_o_adapter_le_o_ambiente` | `os.environ`/`getenv` em `research/` só em `llm/anthropic.py` | config do servidor lê ambiente em `chat/`, não em `research/` |
| `test_o_planejador_nao_alcanca_dado...` | `planner.py` não importa `os`, `pathlib`, `duckdb`, `pat.store`, `pat.query`, `pat.semantics` | o contexto conversacional chega ao planejador **por parâmetro**, montado fora |
| `test_o_escritor_nao_le_valor_nenhum` | `writer.py` não acessa `.value` nem `.rendered_value` | o escritor continua cego a números no chat |
| `test_so_o_renderer_formata_numero...` | `quantize(` só em `render.py` | o backend HTTP **não formata número**; serializa `rendered_value` |
| `test_nao_existe_execucao_de_codigo_arbitrario` | sem `subprocess`/`eval(`/`exec(` em `research/` | manter em `chat/` por disciplina |
| `test_so_a_raiz_de_composicao_constroi_o_motor` | `build_engine` só em `research/__init__.py` | `chat/` chama `run_plan`, nunca `build_engine` |

**Conclusão operacional:** `src/pat/chat/` é um pacote **fora** de `src/pat/research/`, e é por isso que ele pode abrir um socket. Nenhum guard existente precisa ser afrouxado. Guards **novos** serão adicionados para a fronteira nova (§18).

---

# 3. Como o chat se conecta à Fase 3

## 3.1 O ponto de conexão já existe

`src/pat/research/__init__.py` expõe duas funções que compõem em processo:

```python
plan_for_question(conn, *, question, llm, model, source, max_tokens, temperature, timeout_s) -> PlannerOutcome
review_plan(conn, *, plan, question, constraints, source) -> ResearchOutcome
run_plan(conn, *, plan, question, ..., llm, model, ...) -> ResearchOutcome
```

`run_plan` já chama `review_plan` internamente (linha 155) e já invoca o escritor quando `llm is not None and outcome.outputs_available`. Um turno de chat é literalmente:

```
planned = plan_for_question(conn, question=q, llm=cli, model=M)
outcome = run_plan(conn, plan=planned.plan, question=q, llm=cli, model=M, git_sha=...)
```

Duas chamadas de modelo, exatamente como hoje. **Nenhuma terceira.**

## 3.2 O que a camada de chat NÃO faz

- Não constrói `Engine` (`build_engine` continua exclusivo de `research/__init__.py`).
- Não chama `execute_plan`, `derive`, `validate_plan`, `resolve_plan` — só `run_plan`.
- Não formata número. Consome `NumericClaim.rendered_value`, já formatado por `render.py`.
- Não constrói `AnthropicClient` diretamente sem passar pelo mesmo ponto de montagem que o CLI usa (`_llm_client`, `cli.py:939`) — ver §12.3.
- Não escreve no warehouse fora de `store/research.py` e `store/llm_calls.py`.

## 3.3 As três mudanças em código existente

Todas pequenas, todas justificadas, nenhuma em `contracts/` existente nem no Metric Engine.

### M1 — `render.describe()` passa a nomear a empresa (**obrigatória**)

```python
# src/pat/research/render.py:77
def describe(result: ComputationResult) -> str:
    if result.kind is ResultKind.METRIC:
        metric = result.metric_result
        escopo = "consolidado" if metric.scope == "consolidated" else "individual"
        quem = metric.display_name or metric.entity_id          # <- NOVO
        return (
            f"{metric.metric}@{metric.metric_version}, {quem}, {escopo}, exercicio "   # <- NOVO
            f"findo em {metric.period_end}, conhecido em {metric.knowledge_date}, "
            f"AS OF {metric.as_of}"
        )
```

**Por quê:** sem isso, uma comparação de N empresas dá ao escritor N descrições idênticas, e a atribuição no texto passa a depender de o planejador ter escolhido `step_id` legíveis. "O sistema funciona porque o modelo escolheu bons nomes" não é uma propriedade — é sorte. `display_name` e `entity_id` já estão em `MetricResult`; é leitura, não campo novo.

**Efeitos colaterais conhecidos:** (a) muda `prompt_sha256` do escritor → cache de escritor invalidado uma vez; (b) muda `NumericClaim.means`, que aparece em `pat ask` e nas citações da UI — melhoria; (c) `describe()` continua sem valor nenhum, então `test_o_escritor_nao_le_valor_nenhum` e o teste de "nenhum valor renderizado no prompt" continuam valendo. **Adicionar teste:** razão social com dígito (não há hoje entre as quatro) só entra em `means`, nunca na prosa — a regra do dígito continua intacta porque `means` não é prosa.

### M2 — `planner` aceita contexto conversacional opcional

```python
# src/pat/research/planner.py
def build_user_prompt(
    question: ResearchQuestion,
    snapshot: CapabilitySnapshot,
    context: ConversationContext | None = None,      # <- NOVO
) -> str:
    ...
    if context is None:
        return bloco_pergunta_e_capacidades          # BYTE-IDÊNTICO ao de hoje
    return bloco_pergunta_e_capacidades + "\n\nCONVERSA ATE AQUI\n" + canonical_bytes(context)...
```

`build_request()` e `plan_question()` ganham o mesmo parâmetro opcional; `plan_for_question()` em `research/__init__.py` repassa.

**Requisito duro:** com `context=None`, o prompt tem que sair **byte a byte igual** ao atual. Isso preserva todo o cache de planejador existente e é testável em uma linha (`assert build_user_prompt(q, s) == build_user_prompt(q, s, None)` e um teste de hash congelado).

`SYSTEM_PROMPT` ganha uma seção nova (§17.3). Isso muda `system_prompt_sha256` para **todas** as chamadas, com ou sem contexto — invalida o cache de planejador uma vez, o que é o comportamento correto e já documentado em `LLMRequest.prompt_sha256`: "prompt novo é pergunta nova".

`ConversationContext` mora em `contracts/chat.py`. `planner.py` importa `pat.contracts.chat` — permitido: os proibidos são `pat.store`, `pat.query`, `pat.semantics`, `os`, `pathlib`, `duckdb`, rede. Contrato é contrato.

### M3 — `pat serve` em `cli.py`

Um subcomando novo (~25 linhas) que monta paths, cliente de LLM e sobe o servidor. Nenhum comando existente muda.

---

# 4. Fluxo completo de uma mensagem

```
                         ┌──────────────── browser ────────────────┐
 (1) usuário digita  ───▶ │ POST /api/chat {session_id, text, pins} │
                         └────────────────┬────────────────────────┘
                                          ▼
 (2) chat/http.py           decodifica JSON, valida contra ChatRequest (Pydantic)
                                          ▼
 (3) chat/session.py        carrega ConversationState; monta ConversationContext
                            a partir dos N turnos anteriores — SÓ ESTRUTURA
                                          ▼
 (4) chat/turn.py           monta ResearchQuestion (text, as_of, asked_at, pins,
                            requested_output). as_of = o da sessão, fixo.
                                          ▼
 (5) research/__init__      plan_for_question(conn, question, llm, model, context)
       └▶ planner.py            1 chamada de modelo → ResearchPlan + PlanProvenance
                                          ▼
 (6) chat/turn.py           persiste a chamada em llm_call (kind="planner")
                                          ▼
 (7) research/__init__      run_plan(conn, plan, question, llm, model)
       ├▶ validate.py           violações estruturais            ─┐
       ├▶ resolve.py            pendências do warehouse           ├▶ recusa → (11)
       ├▶ certify()             plano certificado                ─┘
       ├▶ semantics/engine      MetricResult (OS NÚMEROS)
       ├▶ execute.py            ComputationResult[]
       ├▶ derive.py             DerivedValue[]
       ├▶ writer.py             1 chamada de modelo → prosa COM TOKENS
       ├▶ manifest.py           ResearchRunManifest
       └▶ answer.py             check_prose + substitute → ResearchAnswer
                                          ▼
 (8) chat/turn.py           persiste manifesto (research_run) + chamada do
                            escritor (llm_call, kind="writer", manifest_id=...)
                                          ▼
 (9) chat/session.py        grava o ChatTurn no log da sessão (append-only)
                                          ▼
(10) chat/view.py           ChatTurn → dict JSON (prosa, claims, warnings,
                            plano, manifesto, procedências, refusal)
                                          ▼
(11) http.py                200 com o turno — inclusive quando é recusa
                                          ▼
     browser                renderiza mensagem, chips [Ver plano] [Ver fontes]
```

## 4.1 Onde o dinheiro é gasto

Exatamente **duas chamadas de modelo por mensagem bem-sucedida**, as mesmas de `pat plan` + `pat ask --writer`. Uma mensagem recusada na validação gasta **uma** (a do planejador) — e ela é gravada em `llm_call` com `manifest_id` nulo, exatamente o caso que `store/llm_calls.py` documenta.

## 4.2 Onde o DuckDB é aberto

Três vezes por turno, sequencialmente, nunca simultâneas:

1. leitura (`connect(warehouse, read_only=True)`) para planejar + executar → fecha
2. escrita para `write_call(planner)` → fecha
3. escrita para `write_manifest` + `write_call(writer)` → fecha

É o mesmo padrão de `cmd_ask` (`cli.py:841`, com o comentário explicando que o DuckDB não aceita escrita com leitura aberta no mesmo arquivo). No servidor isso vira **um lock global de processo** (§7.5).

---

# 5. Arquitetura de conversation state

## 5.1 A decisão central

> Contexto conversacional serve **exclusivamente** para o planejador interpretar referências. Ele entra no prompt do planejador e não toca em mais nada.

Três coisas que o contexto **não** é:

- **Não é pino.** `pinned_periods` é whitelist (`validate.py:245`): carregá-lo adiante recusaria "E como isso mudou desde 2023?" com `PIN_CONTRADICTED_PERIOD`. Pinos continuam significando "o que o usuário fixou", e a UI os expõe como chips que o usuário liga e desliga.
- **Não é cache de resultado.** Nenhum `ComputationResult`, `NumericClaim`, `MetricResult` ou `Decimal` entra em `ConversationContext`. Isso é estrutural: o tipo não tem campo para isso.
- **Não é memória do escritor.** O escritor recebe só os resultados do turno atual. A prosa do turno 3 não sabe o que a prosa do turno 2 disse. É uma limitação real (§22.4) e é a escolha certa para V1: um escritor com memória escreveria "caiu em relação ao que vimos antes" sobre números que ele nunca viu.

## 5.2 O que entra no contexto

Por turno anterior, na ordem, no máximo os **4 últimos**:

| Campo | Origem | Por que é seguro |
|---|---|---|
| `question_text` | `ResearchQuestion.text` | o que o usuário digitou |
| `objective` | `ResearchPlan.objective` | frase do planejador, sem número (validada) |
| `entity_ids` | `MetricStep.entity_id` distintos | identificadores, não valores |
| `metric_refs` | `str(MetricStep.metric)` | `"ebitda@v1"` |
| `period_ends` | `MetricStep.period_end` distintos | datas, não valores |
| `scope` | `ResearchPlan.scope` | enum |
| `derivation_ops` | `DerivationStep.op` distintos | enum |
| `outcome` | `answered` \| `refused` | estado, não conteúdo |
| `refusal_summary` | códigos de violação/pendência | `"unknown_entity"`, não texto livre |

O que **não** entra: prosa da resposta, claims, warnings com mensagem (as de `CHECK_FAILED` carregam `observed`/`expected`, que são números do motor — mesmo recorte que `writer.evidence_payload` já faz em `research/writer.py:305`), `result_id`, `manifest_id`, `fact_id`.

`ConversationContext` é `Frozen` (imutável, `extra="forbid"`) e nenhum de seus campos aceita `Decimal`. Testável por AST, do mesmo jeito que `test_a_gramatica_de_plano_nao_tem_onde_por_um_numero` faz com `MetricStep`.

## 5.3 Onde o estado mora

**Em memória, com log append-only em disco.**

```
data/chat/<session_id>.jsonl     um ChatTurn serializado por linha
```

`Paths` (`src/pat/config.py`) ganha `chat` — irmão de `llm`, com a mesma justificativa: procedências paralelas que não se misturam.

Por que JSONL e não tabela DuckDB:

- O warehouse é para **fatos e auditoria**, e a auditoria de um turno **já está** em `research_run` + `llm_call`, ligados por `manifest_id`. O log de sessão é conveniência de UI: qual turno veio antes de qual.
- Uma tabela nova em `store/db.py` seria migração de esquema para dado que não é fato e não tem invariante bitemporal.
- Reabrir a conexão de escrita mais uma vez por turno, só para o log, é custo puro.
- Apagar `data/chat/` não perde nada auditável. Apagar `research_run` sim. A separação torna isso óbvio.

`session_id`: `sha256` dos bytes de `(criado_em, nonce)` truncado em 16 hex. Não vem do cliente — o cliente pede `POST /api/session` e recebe um. Isso impede path traversal via `session_id` e é uma linha de validação a menos.

## 5.4 `as_of` é da sessão, não da mensagem

`ResearchQuestion.as_of` é obrigatório e sem default (`contracts/research.py:194`). Numa conversa ele tem que ser **fixo para a sessão inteira** — senão o turno 1 responde `AS OF 2026-08-20`, o turno 5 responde `AS OF 2026-08-21` depois da meia-noite, e "isso mudou?" compara duas visões do mundo diferentes sem avisar ninguém.

`ConversationState.as_of` é escolhido em `POST /api/session` (default `date.today()`) e a UI o mostra no cabeçalho. Trocar `as_of` **abre sessão nova**. Isso é o análogo conversacional do `AS OF` obrigatório do I1.

## 5.5 O problema do `question_id` — decisão explícita

`question_id = sha256(pergunta canônica)` (`research/canonical.py`). A pergunta de follow-up "E a margem EBITDA?" tem o mesmo texto e os mesmos pinos em duas conversas diferentes → **mesmo `question_id`**, planos diferentes.

Isso já era verdade antes do chat (a mesma pergunta feita duas vezes sempre teve o mesmo `question_id`) e nada fica falso: o manifesto liga `question_id → plan_id → result_ids`, e os dois planos são duas linhas distintas em `research_run`. O rastro está intacto; `question_id` apenas não é único por plano — e nunca foi.

**O que se ganha de graça:** `PlanProvenance.prompt_sha256` **difere** entre os dois casos, porque o contexto está no prompt. Então "que chamada produziu este plano" continua respondível com precisão.

**Decisão:** aceitar e documentar. `ChatTurn` guarda `context_sha256` (sha256 da forma canônica do `ConversationContext` daquele turno) no log de sessão, para que "sob que contexto esta pergunta foi interpretada" seja respondível sem depender de recomputar o prompt. Nenhum contrato existente muda.

**Alternativa rejeitada:** materializar a pergunta reescrita ("Qual a margem EBITDA de Petrobras, Vale e WEG em FY2024?") como `text`. Exigiria uma **terceira chamada de LLM** para reescrever, quebraria `test_so_o_escritor_e_o_planejador_falam_com_o_modelo`, e poria o modelo a reescrever a pergunta do usuário — o que significa que o texto auditado deixa de ser o que a pessoa digitou. Custo alto, ganho cosmético.

---

# 6. Como preservar "LLM interpreta, código calcula"

O princípio já é estrutural na Fase 3. A M4.1 tem que **não abrir buraco novo**. Os cinco pontos onde um buraco poderia aparecer, e o que fecha cada um:

| # | Buraco possível | O que o fecha |
|---|---|---|
| 1 | Número do turno anterior entra no contexto e o planejador o repete | `ConversationContext` não tem campo que aceite `Decimal`, e nenhum campo dele deriva de `ComputationResult`. Guard por AST. |
| 2 | Backend HTTP formata/arredonda um número para a UI | O backend serializa `NumericClaim.rendered_value` (string pronta). Guard: `quantize(` não aparece em `chat/`. |
| 3 | Frontend faz aritmética em JS (ordena, soma, calcula variação) | O JS não faz conta com valores. Guard textual em `index.html`: sem `parseFloat`, `Number(`, `toFixed`, `Math.`. Números são strings opacas. |
| 4 | Prosa do turno anterior é reaproveitada quando o turno atual falha | Recusa é recusa: turno recusado não tem prosa. `ChatTurn.answer is None` e a UI mostra o bloco de recusa. |
| 5 | Uma terceira chamada de modelo aparece "só para desambiguar" | Guard de conjunto exato em `chat/` **e** o guard existente em `research/`. `llm.complete(` continua em dois arquivos. |

E o portão que já existe e continua sendo o último: `answer.check_prose()` (`research/answer.py:61`) rejeita qualquer dígito fora de token. Toda prosa do chat passa por ele — não porque a camada de chat o chame, mas porque `build_answer` o chama e é o único caminho até `ResearchAnswer.prose`.

**O teste que amarra tudo, e que vale mais que os outros juntos** (§18, T-E2E-2):

> Rodar a conversa de 4 turnos do enunciado com dois `FakeLLMClient`, capturar **todos** os `LLMRequest` recebidos, e afirmar que nenhum `rendered_value` de nenhum turno anterior aparece em nenhum prompt de nenhum turno posterior.

É o análogo conversacional de `test_o_escritor_nao_le_valor_nenhum`, e é a única afirmação que não dá para fazer olhando um turno isolado.

---

# 7. Proposta de API/backend

## 7.1 Endpoints

| Método | Rota | Corpo | Resposta |
|---|---|---|---|
| `GET` | `/` | — | `index.html` |
| `GET` | `/api/capability` | — | entidades, métricas, derivações, limites, `capability_sha256` |
| `POST` | `/api/session` | `{as_of?: "YYYY-MM-DD"}` | `{session_id, as_of, created_at}` |
| `GET` | `/api/session/{id}` | — | `{session_id, as_of, turns: [TurnView...]}` |
| `POST` | `/api/chat` | `ChatRequest` | `TurnView` |
| `GET` | `/api/turn/{session_id}/{turn_index}/plan` | — | `PlanEnvelope` completo (JSON) |
| `GET` | `/health` | — | `{status, pat_version, warehouse, model}` |

Sem streaming, sem WebSocket, sem SSE. Uma mensagem é uma requisição que demora ~10-40 s e devolve tudo de uma vez. Streaming exigiria fatiar o pipeline — e o pipeline é atômico de propósito: não existe "resposta parcial" antes de `check_prose` passar.

## 7.2 `ChatRequest`

```python
class ChatRequest(Frozen):
    session_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    text: str = Field(min_length=1, max_length=2000)
    pinned_entities: tuple[str, ...] = ()      # entity_id, do chip da UI
    pinned_periods: tuple[date, ...] = ()
    pinned_scope: ReportingScope | None = None
    requested_output: OutputKind = OutputKind.NARRATIVE
```

`extra="forbid"` via `Frozen`: campo desconhecido no corpo falha na fronteira, que é o padrão do projeto. Nota: `ResearchQuestion` exige `pinned_*` **ordenados e sem repetição** (`contracts/research.py:210-215`); `chat/turn.py` normaliza com `tuple(sorted(set(...)))`, exatamente como `_question_from_args` faz em `cli.py:969`.

## 7.3 `TurnView` (o JSON que o frontend consome)

Contrato entre Agent A e Agent B. Congelado nesta proposta para que os dois trabalhem em paralelo.

```jsonc
{
  "turn_index": 2,
  "session_id": "a3f9...",
  "as_of": "2026-08-20",
  "asked_at": "2026-08-20T14:03:11.442Z",
  "question": "E a margem EBITDA?",

  "status": "answered",              // answered | refused | failed

  "answer": {                        // null quando status != answered
    "prose": "A margem EBITDA consolidada da Petrobras foi de 38,12% ...",
    "numeric_claims": [
      { "token": "{{s:margem_petrobras_fy2024}}",
        "rendered_value": "38.12%",
        "unit": null,
        "means": "margem_ebitda@v1, PETROLEO BRASILEIRO S.A. PETROBRAS, consolidado, exercicio findo em 2024-12-31, conhecido em 2025-02-26, AS OF 2026-08-20",
        "result_id": "9f2c1e...", "step_id": "margem_petrobras_fy2024" }
    ],
    "interpretive_claims": [
      { "text": "A rentabilidade operacional manteve-se estável.",
        "supports": ["9f2c1e...", "77ab03..."] }
    ],
    "warnings": [
      { "kind": "approximate_fidelity", "message": "...", "result_id": "9f2c1e..." }
    ]
  },

  "refusal": null,                   // ver §14

  "plan": {                          // sempre presente quando houve plano
    "plan_id": "4b71...", "question_id": "c0de...",
    "objective": "Margem EBITDA consolidada de Petrobras, Vale e WEG em FY2024",
    "as_of": "2026-08-20", "scope": "consolidated",
    "steps": [
      { "step_id": "margem_petrobras_fy2024", "step_kind": "metric",
        "metric": "margem_ebitda@v1",
        "entity_id": "br:cnpj:33000167000101",
        "entity_name": "PETROLEO BRASILEIRO S.A. PETROBRAS",
        "period_end": "2024-12-31" }
    ],
    "outputs": ["margem_petrobras_fy2024"],
    "assumptions": [], "unresolved": []
  },

  "manifest": {                      // null quando não executou
    "manifest_id": "8e1a...", "capability_sha256": "347acc...",
    "executed_at": "2026-08-20T14:03:29Z",
    "metric_versions": ["margem_ebitda@v1"],
    "mapping_sha256s": ["1f0b..."],
    "fact_ids": ["...", "..."],
    "result_ids": ["9f2c1e..."],
    "pat_version": "0.1.0", "git_sha": "f8f5e8a", "python_version": "3.12.4"
  },

  "provenance": {
    "planner": { "model_id": "claude-opus-5", "client_fingerprint": "anthropic/v1/9a3f21bc",
                 "prompt_sha256": "...", "response_sha256": "...",
                 "called_at": "...", "cached": false, "max_tokens": 16384 },
    "writer":  { "...": "idem, ou null" }
  },

  "context_sha256": "b71e...",
  "elapsed_ms": 18422
}
```

`entity_name` em cada `MetricStep` é enriquecimento do backend a partir do `EntityCard.denom_cia` do snapshot — o plano cru não o tem, e a UI precisa dele. Não é interpretação: é um join por `entity_id`.

## 7.4 Códigos HTTP

| Situação | Código | Motivo |
|---|---|---|
| Turno respondido | 200 | — |
| Turno recusado (plano com `unresolved`, violação, pendência, falha de métrica) | **200** | A recusa **é** a resposta correta do sistema. `refusal` preenchido. Um 4xx faria a UI tratar como erro de cliente o que é o comportamento projetado. |
| `PlannerError` / `WriterError` | **200** com `status:"failed"` | O modelo não produziu saída válida. É informação de produto, não erro de transporte. |
| Corpo inválido / `session_id` desconhecido | 400 / 404 | erro real de cliente |
| `LLMError` (transporte, timeout, credencial) | 502 | falha de infra, com `detail` |
| Warehouse ausente ou ilegível | 503 | com a mesma mensagem de `_open_readonly` (`cli.py:84`) |

## 7.5 Concorrência

DuckDB: um processo escritor **ou** N leitores. Dentro do processo, escrita e leitura do mesmo arquivo não coexistem — daí os `conn.close()` antes de gravar em `cmd_ask`.

**Decisão: um `threading.Lock` global; um turno por vez.** É um chat pessoal local. Duas abas do mesmo browser não devem poder disparar duas execuções simultâneas contra o mesmo arquivo. Requisições que chegam durante um turno esperam no lock.

`ThreadingHTTPServer` continua sendo útil: `GET /` e `GET /api/capability` não pegam o lock, então a UI carrega enquanto um turno roda.

**Documentar em `/health` e no README:** se o usuário estiver com `pat build` rodando em outro terminal (conexão de escrita aberta), `pat serve` falha ao abrir leitura. Mensagem nomeada, não stack trace.

## 7.6 Segurança do bind

`127.0.0.1` **fixo**, sem opção de `--host`. Sem auth (§23). Um `--host 0.0.0.0` exposto sem autenticação num serviço que gasta crédito de API é exatamente o tipo de default que só se descobre errado depois. Quem quiser expor põe um túnel na frente e assume a decisão.

---

# 8. Proposta de frontend

## 8.1 Um arquivo

`src/pat/chat/static/index.html` — HTML + CSS + JS vanilla, sem build, sem CDN, sem dependência externa. Servido pelo próprio `http.py` do diretório do pacote.

Por que dentro do pacote e não em `web/` na raiz: `pat serve` tem que funcionar de qualquer diretório, do mesmo jeito que `semantics/mappings/*.toml` são carregados de dentro do pacote por `loader.load_dir()`.

## 8.2 Layout

```
┌────────────────────────────────────────────────────────────────┐
│ PAT Research          AS OF 2026-08-20    capability 347acc85…│
├────────────────────────────────────────────────────────────────┤
│ pinos: [Petrobras ×] [Vale ×] [WEG ×]  [+ empresa]  escopo: ▾  │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  você  Compare o EBITDA de Petrobras, Vale e WEG em FY2024.    │
│                                                                │
│  PAT   No exercício findo em 31/12/2024, a Petrobras registrou │
│        EBITDA consolidado de R$ 204.19 bn, ante R$ 121.03 bn   │
│        da Vale e R$ 8.13 bn da WEG.                            │
│                                                                │
│        ⚠ mapeamento não conferido — WEG usou a família default │
│                                                                │
│        [Ver plano] [Ver fontes] [Ver leituras (1)]             │
│                                                                │
│  você  E a margem EBITDA?                                      │
│                                                                │
│  PAT   ⏳ planejando…                                           │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│ Pergunte ao PAT…                                        [Send] │
└────────────────────────────────────────────────────────────────┘
```

Painel lateral (drawer) abre com `[Ver plano]` / `[Ver fontes]`.

## 8.3 Regras de renderização

1. **Prosa é texto.** `textContent`, nunca `innerHTML`. Sem markdown, sem HTML. A prosa vem de um modelo; renderizá-la como markup é injeção com passos extras.
2. **Números são strings opacas.** `rendered_value` vai para o DOM como veio. **Zero aritmética em JS.** Sem `parseFloat`, `Number(`, `toFixed`, `Math.`, `sort()` por valor. Guard textual no teste (§18).
3. **Claims interpretativos ficam em bloco separado**, com rótulo "leitura do modelo, não medição" — a mesma separação que `cmd_ask` faz no terminal (`cli.py:907`, com o comentário explicando por quê). Nunca no mesmo parágrafo da prosa.
4. **Warnings sempre visíveis**, nunca atrás de um clique. `WarningKind` nunca suprime número e não pode ser suprimido na tela.
5. **Recusa tem estilo próprio**, distinto de resposta e distinto de erro (§14).
6. **Estados de loading nomeados**: "planejando…" → "calculando…" → "redigindo…". Como não há streaming, são temporizados por heurística de UI (progressão fixa), e a barra é indeterminada — **não** inventar percentuais.
7. **Tema escuro/claro** via `prefers-color-scheme`. Sem toggle, sem estado.

## 8.4 Painel "Ver fontes"

```
FONTES · turno 2

  {{s:margem_petrobras_fy2024}}  →  38.12%
    margem_ebitda@v1, PETROLEO BRASILEIRO S.A. PETROBRAS, consolidado,
    exercício findo em 2024-12-31, conhecido em 2025-02-26, AS OF 2026-08-20
    result_id  9f2c1e34a7b0…

  MANIFESTO
    manifest_id   8e1a…       capability   347acc85…
    métricas      margem_ebitda@v1
    mapeamentos   1f0b…
    fatos folha   6
    pat 0.1.0 · git f8f5e8a · python 3.12.4

  PROCEDÊNCIA
    planejador  claude-opus-5 · anthropic/v1/9a3f21bc · cache: não
                prompt a71c…  resposta 4d92…
    escritor    claude-opus-5 · anthropic/v1/9a3f21bc · cache: não
                prompt 3e08…  resposta bb17…

  RESSALVAS
    ⚠ unconfirmed_mapping · margem_weg_fy2024
```

Mesma informação de `_print_provenance` e do bloco MANIFESTO de `cmd_ask` (`cli.py:907-921`). Não é dado novo; é o mesmo dado com outra saída.

---

# 9. Proposta de stack

## 9.1 Recomendação

| Camada | Escolha | Dependências novas |
|---|---|---|
| Servidor HTTP | `http.server.ThreadingHTTPServer` (stdlib) | **0** |
| Roteamento | `re.match` sobre `self.path`, ~8 rotas | **0** |
| Validação de entrada | Pydantic (já é dependência) | **0** |
| Serialização | `model_dump_json` / `json.dumps` | **0** |
| Frontend | 1 arquivo HTML, JS vanilla, `fetch()` | **0** |
| Build | nenhum | **0** |

**Total: zero dependências novas, `uv.lock` intocado.**

## 9.2 Por que não FastAPI

FastAPI é a escolha default e seria a resposta certa em quase qualquer outro repositório. Aqui não, por quatro razões concretas:

1. **Custo real.** `fastapi` + `uvicorn` traz `starlette`, `anyio`, `sniffio`, `h11`, `click`, `httptools`, `uvloop`, `python-multipart`, `typing-extensions` — 8-10 pacotes transitivos num projeto cujo `CLAUDE.md` diz "dependências mínimas e travadas em `uv.lock`" e cujo total hoje é 5.
2. **Não usaríamos o que ele traz.** O valor do FastAPI está em async I/O, injeção de dependência, OpenAPI e validação de rota. Aqui: o pipeline é síncrono e bloqueante (DuckDB é síncrono, o SDK da Anthropic é chamado em modo síncrono em `llm/anthropic.py`), há **um** lock global, são 8 rotas, e não há consumidor de OpenAPI. Async sobre um lock global é cerimônia.
3. **Superfície de auditoria.** O projeto inteiro é auditável por leitura. `http.server` numa classe de ~200 linhas é lida em dez minutos; a pilha ASGI não.
4. **É reversível.** Trocar `http.server` por FastAPI depois é reescrever um arquivo — `chat/http.py` — sem tocar em `turn.py`, `session.py` ou `view.py`, que é onde mora a lógica. O contrário (nascer com FastAPI e tentar tirar) não é reversível.

**Gatilho documentado para migrar:** streaming de resposta ao vivo, mais de um usuário simultâneo, ou upload de arquivo. Nenhum dos três está na M4.1.

## 9.3 Limitações de `http.server` que serão aceitas explicitamente

- Sem HTTP/2, sem keep-alive agressivo, sem compressão. Irrelevante em localhost.
- `ThreadingHTTPServer` cria uma thread por conexão. Com um usuário e um lock, é ordem de unidades.
- Tratamento de erro é manual: **todo handler dentro de `try/except Exception`** devolvendo 500 com JSON, senão a exceção sai como HTML e mata a thread.
- Sem reload automático. `pat serve` reinicia à mão durante desenvolvimento.

---

# 10. Estrutura de arquivos

```
src/pat/
  contracts/
    chat.py                    ★ NOVO   ConversationContext, ChatRequest, ChatTurn, TurnRefusal
  chat/                        ★ NOVO   pacote inteiro
    __init__.py                         ChatService — a raiz de composição do chat
    session.py                          ConversationState, SessionStore, build_context()
    turn.py                             run_turn(): mensagem → ChatTurn
    record.py                           persistência (manifesto + llm_call), com lock
    view.py                             ChatTurn → dict do TurnView (§7.3)
    http.py                             ThreadingHTTPServer + rotas + estático
    static/
      index.html                        frontend inteiro
  research/
    planner.py                 ✎ MOD    parâmetro `context` opcional + seção no SYSTEM_PROMPT
    render.py                  ✎ MOD    describe() nomeia a empresa
    __init__.py                ✎ MOD    plan_for_question repassa `context`
  config.py                    ✎ MOD    Paths.chat
  cli.py                       ✎ MOD    subcomando `pat serve`

tests/
  chat/                        ★ NOVO
    conftest.py                         fixtures: warehouse, cliente falso, sessão
    test_contracts_chat.py              o contexto não tem onde pôr um número
    test_session.py                     estado, contexto, janela, persistência
    test_turn.py                        um turno: sucesso, recusa, falha do modelo
    test_view.py                        serialização; nenhum Decimal cru no JSON
    test_http.py                        rotas, códigos, corpo inválido, lock
    test_layering_chat.py               os guards novos da fronteira (§18.4)
    test_conversation_e2e.py            os 4 turnos do enunciado, ponta a ponta
  research/
    test_planner.py            ✎ MOD    contexto=None é byte-idêntico; contexto entra
    test_render_and_answer.py  ✎ MOD    describe() nomeia a empresa
    test_layering_research.py  ✎ MOD    guards estendidos a src/pat/chat/

docs/
  m41_chat_proposal.md         ★ este arquivo
```

**Contagem:** 8 arquivos novos de código/contrato, 8 novos de teste, 6 modificados. `pyproject.toml` e `uv.lock` **não são tocados**.

---

# 11. Reutilização dos componentes existentes

Reutilizado **sem modificação** (o grosso do sistema):

| Componente | Onde | Como o chat usa |
|---|---|---|
| `run_plan` | `research/__init__.py:132` | o turno inteiro depois do plano |
| `plan_for_question` | `research/__init__.py:269` | pergunta → plano |
| `review_plan` | `research/__init__.py:104` | chamado por dentro de `run_plan` |
| `build_snapshot` | `research/capability.py:84` | `/api/capability` e nomes de entidade |
| `ResearchQuestion` | `contracts/research.py:183` | montado por turno |
| `ResearchAnswer` / `NumericClaim` / `InterpretiveClaim` | `contracts/research.py` | direto no `TurnView` |
| `ResearchRunManifest` | `contracts/research.py:611` | direto no `TurnView` |
| `PlanEnvelope` | `contracts/research.py:383` | `GET .../plan` |
| `write_manifest` | `store/research.py:75` | idempotente, `ON CONFLICT DO NOTHING` |
| `write_call` | `store/llm_calls.py` | planner + writer |
| `call_sha256_of` | `research/llm/cache.py` | chave da chamada |
| `AnthropicClient` + `CachedLLMClient` + `FileLLMCache` | `research/llm/` | montados na borda, como no CLI |
| `connect` / `migrate` | `store/db.py:263` | leitura e escrita |
| `resolve_paths` | `config.py:51` | `PAT_HOME` |
| `current_git_sha` | `audit/run.py` | manifesto |
| `FakeLLMClient` | `research/llm/__init__.py` | **todos** os testes de chat |

**Lógica do CLI que será portada, não importada:** `cli.py:_record_manifest`, `cli.py:_record_call` e `cli.py:_llm_client` recebem `args` do argparse. `chat/record.py` reimplementa as duas primeiras recebendo `Paths` (não `args`), ~30 linhas. **Duplicação deliberada na V1**, para que a M4.1 não precise mexer em `cmd_ask`/`cmd_plan` — os comandos que os testes de M3/M4 exercitam. Refatorar `cli.py` para delegar a `chat/record.py` é item de M4.2, com a suíte verde como rede.

---

# 12. Novos componentes necessários

## 12.1 `chat/session.py`

```python
@dataclass
class ConversationState:
    session_id: str
    as_of: date
    created_at: datetime
    turns: list[ChatTurn]

class SessionStore:
    def __init__(self, root: Path) -> None: ...
    def create(self, *, as_of: date) -> ConversationState: ...
    def get(self, session_id: str) -> ConversationState | None: ...
    def append(self, state: ConversationState, turn: ChatTurn) -> None: ...  # memória + JSONL

CONTEXT_WINDOW = 4

def build_context(state: ConversationState, *, window: int = CONTEXT_WINDOW) -> ConversationContext | None:
    """Turnos anteriores → contexto estrutural. `None` quando não há turno anterior.

    `None` e não contexto vazio: contexto vazio mudaria o prompt do primeiro
    turno de uma conversa em relação ao de `pat plan`, e as duas coisas são a
    mesma pergunta. Uma sessão nova tem que produzir o prompt de hoje, byte a
    byte, ou perde-se a única evidência de que a M4.1 não mexeu no planejador.
    """
```

A janela de 4 tem justificativa própria: `max_entities=4` limita o plano, o snapshot já ocupa a maior parte do prompt (`max_serialized_bytes=65_536`), e contexto longo é onde um modelo começa a arrastar coisas de turnos antigos. Turnos recusados **entram** no contexto — "isso foi recusado por X" é exatamente o que evita o modelo replanejar a mesma coisa.

## 12.2 `chat/turn.py`

```python
def run_turn(
    request: ChatRequest,
    state: ConversationState,
    *,
    paths: Paths,
    llm: LLMClient,
    model: str,
    source: str = DEFAULT_SOURCE,
) -> ChatTurn:
```

Ordem fixa: monta `ResearchQuestion` → `build_context` → `plan_for_question` → grava chamada do planejador → `run_plan` → grava manifesto + chamada do escritor → devolve `ChatTurn`. Traduz `PlannerError`, `WriterError`, `LLMError` e as violações/pendências em `TurnRefusal` (§14). **Não levanta por dado** — a única exceção que escapa é `LLMError`, que o handler HTTP mapeia para 502.

## 12.3 `chat/__init__.py` — `ChatService`

A raiz de composição do chat, análoga a `research/__init__.py`. Guarda `Paths`, `SessionStore`, o `LLMClient` já montado, o modelo e o lock. `http.py` só fala com ela.

**O cliente de LLM é injetado, nunca construído aqui** — mesma regra de `research/__init__.py`: se `chat/__init__.py` importasse o adapter, o pacote ganharia saída de rede no import. Quem monta é `cli.py:cmd_serve`, reutilizando `_llm_client`.

## 12.4 `chat/record.py`

`record_planner_call(paths, provenance, *, fingerprint)` e `record_turn(paths, manifest, writer_provenance, *, fingerprint)`. Abrem conexão de escrita, `migrate()`, gravam, fecham. Serializadas pelo mesmo lock do `ChatService`.

## 12.5 `chat/view.py`

`ChatTurn → dict` do §7.3. É onde entra o enriquecimento de `entity_name` a partir do snapshot. **Não formata número** (guard). Todo `Decimal` que porventura chegue aqui é erro de programação, não caso a tratar — `rendered_value` já é string.

## 12.6 `chat/http.py`

`ThreadingHTTPServer` + `BaseHTTPRequestHandler`. Tabela de rotas `(método, regex) → handler`. Todo handler em `try/except Exception` → 500 JSON. Estático servido de `Path(__file__).parent / "static"`, **lista branca de nomes** (não `os.path.join` com entrada do usuário).

## 12.7 `pat serve`

```bash
pat serve [--port 8765] [--model claude-opus-5] [--as-of YYYY-MM-DD] [--no-cache] [--open]
```

Recusa de partida se o warehouse não existir (mensagem de `_open_readonly`) ou se `ANTHROPIC_API_KEY` faltar — `AnthropicClient()` já levanta `LLMError` nomeado. Falhar na partida, não no primeiro turno.

---

# 13. Contratos necessários

Arquivo novo `src/pat/contracts/chat.py`. **Nenhum contrato existente muda.**

Justificativa para ser contrato e não dataclass em `chat/`: `ConversationContext` **atravessa a fronteira do modelo** — vai para dentro do prompt do planejador via `canonical_bytes`. Todo objeto nessa posição no projeto é `Frozen` + Pydantic, pelas razões escritas em `research/llm/__init__.py` (canonicalização conhece `BaseModel`; `extra="forbid"` falha na fronteira). E `planner.py` não pode importar de `pat.chat` sem que a Fase 3 passe a depender da camada de apresentação.

```python
class TurnPlanSummary(Frozen):
    """O que UM turno anterior faz saber ao planejador.

    Nenhum campo aqui aceita Decimal, e nenhum deriva de ComputationResult.
    Isso é a propriedade estrutural desta camada, e é conferida por AST em
    tests/chat/test_contracts_chat.py — a mesma técnica que
    test_a_gramatica_de_plano_nao_tem_onde_por_um_numero usa em MetricStep.
    """
    question_text: str = Field(min_length=1)
    objective: str | None = None
    entity_ids: tuple[str, ...] = ()
    metric_refs: tuple[str, ...] = ()
    period_ends: tuple[date, ...] = ()
    scope: ReportingScope | None = None
    derivation_ops: tuple[DerivationOp, ...] = ()
    outcome: Literal["answered", "refused", "failed"]
    refusal_codes: tuple[str, ...] = ()


class ConversationContext(Frozen):
    context_version: Literal["v1"] = "v1"
    as_of: date
    turns: tuple[TurnPlanSummary, ...] = Field(max_length=8)


class ChatRequest(Frozen):     # §7.2
    ...


class RefusalKind(StrEnum):
    PLANNER_UNRESOLVED = "planner_unresolved"       # o modelo devolveu a dúvida
    PLAN_INVALID       = "plan_invalid"             # PlanViolation
    PLAN_UNRESOLVABLE  = "plan_unresolvable"        # ResolutionIssue
    METRIC_UNAVAILABLE = "metric_unavailable"       # ComputationFailure
    PLANNER_FAILED     = "planner_failed"           # PlannerError
    WRITER_FAILED      = "writer_failed"            # WriterError


class TurnRefusal(Frozen):
    kind: RefusalKind
    summary: str = Field(min_length=1)
    detail: str | None = None
    codes: tuple[str, ...] = ()
    remedies: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()                # de UnresolvedItem.candidates


class ChatTurn(Frozen):
    turn_index: int = Field(ge=0)
    session_id: str
    asked_at: AwareDatetime
    question: ResearchQuestion
    context_sha256: Sha256 | None = None
    plan: ResearchPlan | None = None
    plan_id: Sha256 | None = None
    answer: ResearchAnswer | None = None
    manifest: ResearchRunManifest | None = None
    planner_provenance: PlanProvenance | None = None
    writer_provenance: PlanProvenance | None = None
    refusal: TurnRefusal | None = None
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> "ChatTurn":
        if (self.answer is None) == (self.refusal is None):
            raise ValueError("um turno é exatamente uma resposta ou exatamente uma recusa")
        return self
```

O validador final é o que impede o estado que estraga a UI: turno com resposta **e** recusa, ou com nenhuma das duas.

---

# 14. Erros e recusas

## 14.1 Seis categorias, seis apresentações

| # | Categoria | Origem | HTTP | UI |
|---|---|---|---|---|
| 1 | **Dúvida do planejador** — `plan.unresolved` não vazio → `PLAN_NOT_EXECUTABLE` (`validate.py:160`) | modelo | 200 | "Preciso que você desambigue", com `candidates` como botões que viram pinos |
| 2 | **Plano inválido** — `PlanViolation[]` | validador | 200 | "O plano não passou na validação", lista `code` + `remedy` |
| 3 | **Warehouse não serve** — `ResolutionIssue[]` (`unknown_entity`, `period_not_covered`, `no_mapping`, `unconfirmed_mapping`) | resolver | 200 | "Não tenho esse dado", com o `remedy` (ex.: `pat fetch`) |
| 4 | **Métrica indisponível** — `ComputationFailure` com `MetricUnavailable` | executor | 200 | "Não consegui calcular", motivo + conceito faltante + endereços tentados |
| 5 | **Modelo falhou** — `PlannerError` / `WriterError` | modelo | 200 `failed` | "O modelo não produziu uma saída válida", `failure` + `response_sha256[:12]` |
| 6 | **Infra** — `LLMError`, warehouse ausente, exceção não prevista | fora | 502/503/500 | banner vermelho, distinto de tudo acima |

## 14.2 A regra que amarra

**Nunca existe resposta parcial.** Se qualquer saída do plano não foi calculada, `outcome.outputs_available` é `False`, `run_plan` não chama o escritor, `answer` é `None` — e o turno é recusa. É o `MetricUnavailable` da Fase 2 chegando à tela sem reescrita, e é o comportamento de `cmd_ask` (`cli.py:877`).

**Nunca há fallback silencioso.** `WriterError` **não** cai para `deterministic_prose`. O comentário em `cli.py:828` explica: apresentar um texto que ninguém pediu como se fosse o pedido é pior que falhar. A UI oferece um botão **"Tentar de novo"** que dispara um turno novo (nova mensagem, nova provenance, novo `llm_call`) — nunca uma retentativa interna, porque retentativa interna é um segundo caminho de influência que a procedência não registraria.

## 14.3 Recusa entra no contexto

Um turno recusado vira `TurnPlanSummary(outcome="refused", refusal_codes=(...))`. O planejador do turno seguinte vê que a tentativa anterior foi recusada e por qual código. É o mecanismo mais barato de correção de curso, e não custa chamada nenhuma.

---

# 15. Provenance e citations

Nada de novo é produzido. É o mesmo dado de `pat ask`, com outra saída.

| O que | Origem | Onde aparece |
|---|---|---|
| Valor + token + significado | `NumericClaim` (`render.render_results`) | painel Fontes, uma linha por número |
| `result_id` | `ComputationResult.result_id` | painel Fontes, monospace |
| Leituras do modelo | `InterpretiveClaim` | **bloco separado**, rotulado "leitura, não medição" |
| Ressalvas | `ResearchWarning` (`answer.collect_warnings`) | inline na mensagem, sempre visível |
| Métricas/mapeamentos/fatos | `ResearchRunManifest` | painel Fontes |
| `capability_sha256` | manifesto | cabeçalho |
| Modelo, fingerprint, cache, hashes | `PlanProvenance` × 2 | painel Fontes |
| `manifest_id` | manifesto | painel Fontes + `pat runs --research <id>` |

**A ponte com o CLI é o critério de aceitação mais forte da auditoria:** todo `manifest_id` mostrado na UI tem que ser consultável por `pat runs --research <manifest_id>`. Isso prova que a conversa não é um universo paralelo — ela grava nas mesmas tabelas que tudo mais.

**Nunca colapsar as duas classes de afirmação.** `NumericClaim` e `InterpretiveClaim` existem separados porque um tem campo de valor e o outro estruturalmente não tem. Se a UI os pusesse no mesmo bloco visual, a distinção morreria na última perna — que é onde ela importa.

---

# 16. "Ver plano"

`GET /api/turn/{session_id}/{turn_index}/plan` devolve o `PlanEnvelope` completo (`{question, plan}`) via `model_dump_json`.

**A propriedade que isso dá, e que é o argumento inteiro para o endpoint existir:** o JSON baixado é exatamente o formato que `pat ask --plan-file` aceita. `cmd_plan` (`cli.py:1098`) grava com `model_dump_json(indent=2)` e `cmd_ask` lê com `load_envelope`. Então:

```bash
curl localhost:8765/api/turn/a3f9.../2/plan > p.json
pat ask --plan-file p.json          # sem --writer: caminho determinístico puro
```

reproduz os mesmos números **sem nenhuma chamada de modelo**. A UI mostra isso como um bloco copiável com o comando pronto. É a demonstração mais direta de que o chat não é fonte de verdade — é uma casca sobre um pipeline que roda sem ele.

O painel mostra: objetivo, `as_of`, escopo, tabela de passos (`step_id`, tipo, métrica, empresa com nome, período), saídas, premissas, pendências, `plan_id`, `question_id`, `capability_sha256`. Mesmos campos de `_print_research` (`cli.py:711`).

---

# 17. Perguntas de follow-up

## 17.1 Os quatro exemplos, resolvidos

| Turno | Mensagem | Como o contexto resolve | Plano resultante |
|---|---|---|---|
| 1 | "Compare Petrobras, Vale e WEG em EBITDA em 2024." | — | 3 × `MetricStep(ebitda@v1, 2024-12-31)` |
| 2 | "E a margem EBITDA?" | `entity_ids` e `period_ends` do turno 1 | 3 × `MetricStep(margem_ebitda@v1, 2024-12-31)` |
| 3 | "E como isso mudou desde 2023?" | `metric_refs=("margem_ebitda@v1",)`, `entity_ids`, `period_ends=(2024,)` | 6 × `MetricStep` + 3 × `DerivationStep(delta_pct)` = 9 passos (≤ 32 ✓) |
| 4 | "Qual teve a maior queda?" | contexto do turno 3 | **`unresolved: unsupported_question`** — ver 17.2 |

## 17.2 O turno 4 e o Achado 2

`DerivationOp.MIN` devolve o valor mínimo, não qual insumo o produziu (`derive.py:184`). `DerivedValue` não tem campo de atribuição e `describe()` para derivada diz só `"min sobre 2023-12-31 -> 2024-12-31"` — sem empresa. E o escritor não vê valores, então não pode dizer qual empresa caiu mais nem olhando.

Três saídas possíveis, e a escolhida:

- ❌ **Deixar o escritor inferir.** Impossível por construção, e se fosse possível seria o modelo produzindo o achado — exatamente o que a arquitetura proíbe.
- ❌ **`argmin`/`argmax` novos.** `DerivationOp` novo + campo de atribuição em `DerivedValue` + regra de renderização. Mudança de contrato, teste de camada, versão de derivação. **Fora de escopo** — candidato a M4.2.
- ✅ **Recusar com nome, e mostrar o que dá.** O plano do turno 4 sai com `unresolved: [{kind: "unsupported_question", detail: "as derivações disponíveis produzem o valor extremo, nunca qual empresa o produziu", candidates: [...]}]`. A UI mostra a recusa **e** oferece "ver as três variações lado a lado" — que é o turno 3, já calculado, com os três `delta_pct` visíveis. O leitor compara.

Isso exige uma linha no `SYSTEM_PROMPT` do planejador dizendo que `min`/`max` não atribuem. Sem ela, o modelo produzirá um `min` de três `delta_pct` e o sistema devolverá um número correto para uma pergunta que ninguém fez — o pior tipo de acerto.

## 17.3 A seção nova do `SYSTEM_PROMPT` do planejador

```
CONVERSA
Você pode receber um bloco "CONVERSA ATE AQUI" com os turnos anteriores desta
conversa. Ele existe para UMA coisa: resolver referências da pergunta atual
("isso", "as três empresas", "e a margem?", "desde 2023").

O bloco contém apenas ESTRUTURA — que empresas, que métricas, que períodos e
que escopo foram planejados antes, e se o turno foi respondido ou recusado.
Ele NÃO contém valores, e isso é deliberado: os números de todo turno são
recalculados do zero pelo motor. Não afirme, não repita e não pressuponha
nenhum valor de turno anterior.

Se um turno anterior foi recusado, o código da recusa está ali. Não replaneje
a mesma coisa que já foi recusada pelo mesmo motivo.

A pergunta atual continua sendo a pergunta. O contexto ajuda a entendê-la;
não a substitui, não a amplia e não a reinterpreta.

ATRIBUIÇÃO
As derivações "min" e "max" produzem o VALOR extremo, nunca QUAL insumo o
produziu. Uma pergunta do tipo "qual empresa teve a maior queda" não é
respondível por elas: use "unresolved" com kind "unsupported_question" e
planeje, em vez disso, a variação de cada empresa separadamente, se couber.
```

## 17.4 Pinos na UI

Os chips de pino são **do usuário**, não inferidos. Ao clicar em "Petrobras" nos candidatos de uma recusa por ambiguidade, o chip é criado e permanece nas mensagens seguintes até ser removido. Isso dá controle explícito e usa `pinned_*` para o que eles são: soberania do usuário sobre o planejador (`validate.py:166-253`).

A UI **avisa** quando um pino de período está ativo e o texto sugere outro período ("você fixou 2024; a pergunta menciona 2023 — remova o pino ou reformule"), porque essa é a combinação que produz `PIN_CONTRADICTED_PERIOD` e é confusa quando aparece como recusa crua.

---

# 18. Testes necessários

Todos offline, com `FakeLLMClient`. **Nenhum teste novo sob `-m llm`** exceto um smoke opcional.

## 18.1 Contratos — `tests/chat/test_contracts_chat.py`

| Id | Afirmação |
|---|---|
| C-1 | `ConversationContext`/`TurnPlanSummary` não têm campo `Decimal` — **por AST**, como `test_a_gramatica_de_plano_nao_tem_onde_por_um_numero` |
| C-2 | `contracts/chat.py` não importa `pat.store`, `pat.query`, `pat.semantics`, `pat.research.*` (exceto contratos), `httpx`, `duckdb` |
| C-3 | `ChatTurn` com `answer` **e** `refusal` levanta; com nenhum dos dois, levanta |
| C-4 | `ChatRequest` rejeita campo extra (`extra="forbid"`) |
| C-5 | `ConversationContext` é serializável por `canonical_bytes` e o hash é estável |

## 18.2 Sessão — `tests/chat/test_session.py`

| Id | Afirmação |
|---|---|
| S-1 | Sessão nova → `build_context()` devolve `None` (não contexto vazio) |
| S-2 | Contexto tem no máximo `window` turnos, os mais recentes, em ordem |
| S-3 | Turno recusado entra no contexto com `outcome="refused"` e códigos |
| S-4 | Nenhum `rendered_value`/`result_id`/`manifest_id` aparece na forma canônica do contexto — **varredura textual sobre `canonical_bytes(context)`** |
| S-5 | JSONL: gravar e reler N turnos preserva ordem e conteúdo |
| S-6 | `session_id` fora de `^[0-9a-f]{16}$` é rejeitado antes de virar caminho |
| S-7 | `as_of` da sessão é imutável; segundo turno com `as_of` diferente é impossível pela API |

## 18.3 Turno — `tests/chat/test_turn.py`

| Id | Afirmação |
|---|---|
| T-1 | Turno feliz: 2 chamadas de modelo, `answer` preenchido, `refusal` nulo, manifesto gravado |
| T-2 | Plano com `unresolved` → 1 chamada (planejador), `refusal.kind=PLANNER_UNRESOLVED`, `candidates` preservados, **manifesto não gravado** |
| T-3 | `ResolutionIssue` → `PLAN_UNRESOLVABLE` com `remedy` |
| T-4 | `PlannerError` → `PLANNER_FAILED` com `response_sha256`, e a chamada é gravada em `llm_call` com `manifest_id` nulo |
| T-5 | `WriterError` → `WRITER_FAILED`, **sem fallback para `deterministic_prose`** |
| T-6 | Falha de métrica → `METRIC_UNAVAILABLE`, `answer is None`, escritor **não** chamado |
| T-7 | Turno feliz grava exatamente 2 linhas em `llm_call` (`kind` `planner` e `writer`), a do escritor com `manifest_id` |
| T-8 | `pinned_*` do request chegam ordenados e sem repetição na `ResearchQuestion` |

## 18.4 Camadas — `tests/chat/test_layering_chat.py` (**os guards que importam**)

| Id | Afirmação |
|---|---|
| L-1 | Conjunto **exato** de arquivos em `chat/` com `llm.complete(` = **`set()`** — o chat não fala com o modelo, `research/` fala |
| L-2 | `chat/` não contém `quantize(` — não formata número |
| L-3 | `chat/` não importa `pat.semantics.engine`, não contém `build_engine`, `execute_plan`, `derive(`, `validate_plan`, `resolve_plan` |
| L-4 | `chat/` não contém `subprocess`, `eval(`, `exec(` |
| L-5 | Só `chat/http.py` importa `http.server`/`socketserver` (conjunto exato) |
| L-6 | Só `chat/record.py` e `chat/session.py` escrevem em disco (conjunto exato de arquivos com `open(`/`write_text`/`write_call`/`write_manifest`) |
| L-7 | `research/` continua **não** importando `pat.chat` (a Fase 3 não sabe que existe chat) |
| L-8 | **Estendido em `tests/research/test_layering_research.py`:** a saída de rede do projeto inteiro fora de `sources/` cabe em `{research/llm/anthropic.py, chat/http.py}` — conjunto exato |
| L-9 | `index.html` não contém `parseFloat`, `Number(`, `toFixed`, `Math.`, `eval(`, `innerHTML` — **o frontend não calcula e não injeta** |
| L-10 | `pyproject.toml` `[project].dependencies` inalterado em relação ao baseline |

## 18.5 View e HTTP

| Id | Afirmação |
|---|---|
| V-1 | `TurnView` de turno feliz bate campo a campo com o §7.3 |
| V-2 | Nenhum `Decimal` cru sobrevive à serialização (`json.dumps` do dict não levanta) |
| V-3 | `entity_name` é preenchido a partir do snapshot para todo `MetricStep` |
| V-4 | Claims interpretativos saem em chave separada dos numéricos |
| H-1 | `POST /api/session` → 200 com `session_id` válido |
| H-2 | `POST /api/chat` com sessão desconhecida → 404 |
| H-3 | `POST /api/chat` com campo extra → 400 |
| H-4 | Turno recusado → **200** com `refusal` (não 4xx) |
| H-5 | `LLMError` → 502 |
| H-6 | `GET /` serve `index.html`; `GET /../../etc/passwd` → 404 |
| H-7 | `GET .../plan` devolve JSON que `load_envelope()` aceita — **round-trip real** |
| H-8 | Exceção não prevista no handler → 500 JSON, servidor continua de pé |

## 18.6 Ponta a ponta — `tests/chat/test_conversation_e2e.py`

| Id | Afirmação |
|---|---|
| **E2E-1** | Os 4 turnos do enunciado com `FakeLLMClient`: 1 e 2 e 3 respondem, 4 recusa com `unsupported_question` |
| **E2E-2** | **O teste central.** Captura todo `LLMRequest` de todos os turnos e afirma que nenhum `rendered_value` de nenhum turno anterior aparece em nenhum prompt posterior |
| E2E-3 | O turno 2 é replanejado do zero: os `result_id` do turno 2 são disjuntos dos do turno 1 |
| E2E-4 | Os `manifest_id` de todos os turnos respondidos existem em `research_run` e são legíveis por `read_manifest` |
| E2E-5 | O `PlanEnvelope` do turno 2, gravado em arquivo, roda em `pat ask --plan-file` **sem `--writer`** e produz os mesmos `rendered_value` |
| E2E-6 | Prosa de todo turno passa por `check_prose` — nenhum dígito fora de token |

## 18.7 Regressão em testes existentes

| Id | Afirmação |
|---|---|
| R-1 | `build_user_prompt(q, snap)` == `build_user_prompt(q, snap, None)`, **byte a byte** |
| R-2 | Hash congelado: `prompt_sha256` de uma pergunta fixa com `context=None` bate com um valor literal no teste |
| R-3 | `describe()` de um `MetricResult` contém `display_name` e continua sem valor |
| R-4 | Toda a suíte existente verde (`pytest`), com `test_render_and_answer.py` e `test_planner.py` atualizados |

---

# 19. Divisão em sub-agents

## 19.1 O que é realmente paralelizável

O acoplamento está nos **tipos**, não na lógica. Uma vez que `contracts/chat.py` e o `TurnView` do §7.3 existam, quatro frentes não se tocam. Antes disso, nada é paralelizável — dois agentes inventariam dois `ChatTurn` diferentes.

**Daí a estrutura: uma onda sequencial curta, depois três em paralelo, depois integração.**

## 19.2 Matriz de propriedade de arquivos

| Arquivo | Onda | Dono | Outros |
|---|---|---|---|
| `src/pat/contracts/chat.py` | 1 | **A** | read-only |
| `tests/chat/test_contracts_chat.py` | 1 | **A** | read-only |
| `docs/m41_chat_proposal.md` | — | ninguém | read-only (a especificação) |
| `src/pat/chat/turn.py` | 2 | **A** | read-only |
| `src/pat/chat/record.py` | 2 | **A** | read-only |
| `src/pat/chat/__init__.py` | 2 | **A** | read-only |
| `src/pat/chat/view.py` | 2 | **A** | read-only |
| `src/pat/research/planner.py` | 2 | **A** | read-only |
| `src/pat/research/render.py` | 2 | **A** | read-only |
| `src/pat/research/__init__.py` | 2 | **A** | read-only |
| `tests/chat/test_turn.py`, `test_view.py` | 2 | **A** | read-only |
| `tests/research/test_planner.py`, `test_render_and_answer.py` | 2 | **A** | read-only |
| `src/pat/chat/session.py` | 2 | **C** | read-only |
| `src/pat/config.py` | 2 | **C** | read-only |
| `tests/chat/test_session.py` | 2 | **C** | read-only |
| `src/pat/chat/http.py` | 2 | **B** | read-only |
| `src/pat/chat/static/index.html` | 2 | **B** | read-only |
| `tests/chat/test_http.py` | 2 | **B** | read-only |
| `tests/chat/test_layering_chat.py` | 2 | **D** | read-only |
| `tests/chat/conftest.py` | 2 | **D** | **A/B/C leem e usam** |
| `tests/research/test_layering_research.py` | 2 | **D** | read-only |
| `tests/chat/test_conversation_e2e.py` | 3 | **D** | read-only |
| `src/pat/cli.py` | 3 | **A** | read-only |
| `README.md` | 3 | **A** | read-only |
| `pyproject.toml` / `uv.lock` | — | **ninguém** | **congelados** |

**Nenhum arquivo tem dois donos.** É a condição para rodar em paralelo sem merge manual.

## 19.3 Read-only absoluto para todos os agentes

```
src/pat/contracts/{common,semantics,facts,documents,entities,lineage,silver}.py
src/pat/contracts/research.py
src/pat/semantics/**            (todo o Metric Engine)
src/pat/query/**  src/pat/store/**  src/pat/sources/**  src/pat/parse/**
src/pat/build.py  src/pat/ingest.py  src/pat/audit/**
src/pat/research/{validate,resolve,derive,execute,answer,manifest,capability,canonical,writer}.py
src/pat/research/llm/**
data/**                          (o warehouse não é tocado)
pyproject.toml  uv.lock  CLAUDE.md
```

## 19.4 Os quatro agentes

**Agent A — Contratos, pipeline e integração.** Onda 1 e 2 e 3. É o caminho crítico e o único que toca `research/`. Precisa entender a Fase 3 a fundo.

**Agent B — HTTP e frontend.** Onda 2. Trabalha contra o `TurnView` congelado (§7.3) e um fixture de `TurnView` que D produz. **Não depende do código de A** — depende do formato.

**Agent C — Estado de conversa.** Onda 2. `session.py` + `Paths.chat`. Depende só de `contracts/chat.py`.

**Agent D — Testes, guards, auditoria.** Onda 2 (conftest + guards de camada + fixtures de `TurnView`) e onda 3 (e2e + varredura final). Os guards de camada são asserções de AST sobre caminhos, escrevíveis antes do código existir.

## 19.5 Como integrar

1. Cada agente em **branch própria a partir do commit de fim da onda 1**.
2. Merge na ordem **C → A → B → D** (menos dependências primeiro). Sem sobreposição de arquivos, os merges são triviais por construção.
3. Integração feita pelo **Agent A**, que é quem escreve `cli.py:cmd_serve` e é o único a ver as quatro peças juntas.
4. `pytest` verde depois de cada merge, não só no fim.
5. Passo final do Agent D: suíte completa + os critérios de aceitação do §21, incluindo a **verificação manual pelo browser** (§21.9), que nenhum teste automatizado cobre.

---

# 20. Ordem de implementação

| # | Onda | Agente | Entrega | Portão |
|---|---|---|---|---|
| 0 | — | — | esta proposta revisada e aprovada | leitura humana |
| 1 | 1 | **A** | `contracts/chat.py` + C-1..C-5 | `pytest` verde; nenhum arquivo fora de `contracts/chat.py` e `tests/chat/` tocado |
| 2 | 2 | **A** | M1 (`describe`) + M2 (`planner` context) + R-1..R-4 | prompt byte-idêntico com `context=None`; suíte existente verde |
| 3 | 2 | **A** | `chat/turn.py`, `record.py`, `__init__.py`, `view.py` + T-1..T-8, V-1..V-4 | um turno roda com `FakeLLMClient`, manifesto em `research_run` |
| 4 | 2 | **C** | `Paths.chat` + `chat/session.py` + S-1..S-7 | contexto não contém número (S-4) |
| 5 | 2 | **B** | `chat/http.py` + `static/index.html` + H-1..H-8 | rotas contra fixture; página carrega |
| 6 | 2 | **D** | `tests/chat/conftest.py` + guards L-1..L-10 | guards vermelhos onde devem ser (pré-código), verdes no fim |
| 7 | 3 | **A** | `pat serve` + README | `pat serve` sobe, browser conversa de verdade |
| 8 | 3 | **D** | E2E-1..E2E-6 | **E2E-2 é o portão da milestone** |
| 9 | 3 | **A+D** | §21 completo, uma sessão real com `ANTHROPIC_API_KEY` | aceitação |

Passos 3, 4, 5 e 6 rodam **em paralelo**. 1 e 2 são sequenciais e no caminho crítico.

---

# 21. Critérios objetivos de aceitação

Cada um é verificável por comando ou por asserção. Nada é "parece bom".

**21.1 — Zero dependências novas.** `git diff pyproject.toml uv.lock` vazio.

**21.2 — Suíte verde.** `pytest` (sem `-m network`, sem `-m llm`) passa inteiro, incluindo todos os guards de camada.

**21.3 — O invariante do número.** E2E-2 passa: nenhum `rendered_value` de turno anterior em nenhum prompt posterior.

**21.4 — Recalculo por turno.** E2E-3 passa: cada turno produz `result_id` novos; nenhum é reaproveitado.

**21.5 — Reprodutibilidade sem modelo.** Baixar o plano do turno N pela API, rodar `pat ask --plan-file p.json` (**sem `--writer`**), obter os mesmos `rendered_value`. Zero chamadas de modelo. (E2E-5, e à mão uma vez.)

**21.6 — Auditoria pelo CLI.** Todo `manifest_id` da UI responde em `pat runs --research <id>`. Toda chamada de modelo tem linha em `llm_call`, inclusive as de turno recusado (com `manifest_id` nulo).

**21.7 — Recusa é caminho, não erro.** Uma pergunta ambígua devolve 200 com `refusal`, a UI mostra bloco de recusa e candidatos, e nenhum número aparece.

**21.8 — Prompt do planejador estável.** R-1 e R-2: `context=None` produz o prompt de hoje, byte a byte.

**21.9 — Verificação manual (obrigatória).** Com `ANTHROPIC_API_KEY` e `pat serve`, no browser:
   1. "Compare o EBITDA de Petrobras, Vale e WEG em FY2024." → três valores, cada um **atribuído à empresa certa**
   2. "E a margem EBITDA?" → três margens, mesmas empresas, mesmo período, **sem repetir o texto anterior**
   3. "E como isso mudou desde 2023?" → seis métricas + três variações
   4. "Qual teve a maior queda?" → **recusa nomeada**, não um número
   5. `[Ver plano]` e `[Ver fontes]` abrem e batem com `pat runs --research`
   6. Desligar a rede no meio → banner 502, servidor de pé, próxima mensagem funciona quando a rede volta

**21.10 — Sem número inventado.** Toda prosa passou por `check_prose`; todo número da tela tem `result_id` no painel Fontes; nenhum número está em texto sem `NumericClaim` correspondente.

**21.11 — Bind local.** `pat serve` só escuta `127.0.0.1`; não há flag para mudar isso.

**21.12 — Frontend não calcula.** L-9 passa.

---

# 22. Riscos arquiteturais

**22.1 — Contexto vira caminho de vazamento de número. (alto impacto, baixa probabilidade)**
O modo de falha é insidioso: alguém adiciona "só o valor do turno anterior, para o planejador entender a escala". *Mitigação:* `ConversationContext` sem campo `Decimal`, guard C-1 por AST, S-4 por varredura textual, E2E-2 sobre a conversa inteira. Três camadas independentes.

**22.2 — `SYSTEM_PROMPT` do planejador cresce e degrada. (médio, média)**
A seção CONVERSA é ~15 linhas sobre um prompt já grande + snapshot de até 64 KB. *Mitigação:* janela de 4 turnos; contexto estrutural (não texto livre); `test_llm_smoke.py` estendido com um caso conversacional sob `-m llm`. *Aceito:* nenhum teste offline prova que o modelo *usa bem* o contexto — pela mesma razão documentada em `test_llm_smoke.py`, um teste assim ficaria vermelho no dia em que o modelo escolhesse outro caminho igualmente válido.

**22.3 — Servidor de vida longa vs. DuckDB single-writer. (médio, alta se ignorado)**
Um `pat build` em outro terminal derruba `pat serve`. *Mitigação:* lock global, conexões curtas, erro nomeado na partida e em cada turno, documentado no README.

**22.4 — Prosa sem continuidade entre turnos. (baixo, certa)**
O escritor não vê turnos anteriores, então cada resposta é autocontida e um pouco repetitiva. É **escolha**, não bug: um escritor com memória escreveria sobre números que não viu. *Mitigação:* nenhuma na V1. Se incomodar, o caminho é passar ao escritor as `means` (nunca valores) dos turnos anteriores — M4.2, com teste próprio.

**22.5 — `question_id` colidindo entre conversas. (baixo, certa)**
§5.5. Aceito, documentado, mitigado por `context_sha256` no `ChatTurn` e por `prompt_sha256` já distinto.

**22.6 — Duas invalidações de cache de LLM. (baixo, certa)**
M1 muda o prompt do escritor; M2 muda o system prompt do planejador. Todo cache existente vira MISS uma vez. É o comportamento correto ("prompt novo é pergunta nova"), custa alguns dólares uma vez. *Mitigação:* fazer as duas mudanças **no mesmo commit**, para invalidar uma vez só.

**22.7 — `http.server` escrito à mão vaza exceção e mata a thread. (baixo, média)**
*Mitigação:* `try/except Exception` obrigatório em todo handler, com teste (H-8).

**22.8 — A UI vira o produto e a arquitetura vira detalhe. (alto, média — o risco de longo prazo)**
Assim que existe uma caixa de texto, a pressão é por "só deixar o modelo responder direto quando não tem dado", "só cachear a resposta anterior", "só deixar ele estimar". Cada uma isolada parece razoável. *Mitigação:* os guards de camada, e o fato de que E2E-2 e L-1 quebram ruidosamente. O CLAUDE.md deve ganhar uma seção sobre a camada de chat quando ela existir.

**22.9 — Path traversal / XSS na UI. (médio, baixa)**
*Mitigação:* `session_id` gerado pelo servidor com regex duro; estático por lista branca; `textContent` em toda prosa; L-9 proíbe `innerHTML`.

**22.10 — Nenhuma auth com um botão que gasta dinheiro. (médio, baixa em localhost)**
*Mitigação:* bind fixo em `127.0.0.1`, sem flag. Aceito explicitamente em §23.

---

# 23. O que explicitamente NÃO deve ser implementado agora

Confirmando as exclusões do pedido e acrescentando as que a auditoria revelou:

**Excluído pelo pedido:** empresas novas · `max_entities` acima de 4 · métricas novas · alteração de contratos existentes · alteração do Metric Engine · alteração da arquitetura contábil · LangChain · CrewAI · RAG · vector database · multi-agent runtime · cloud deploy · autenticação · infra de produção.

**Excluído pela auditoria:**

| # | Não fazer | Por quê |
|---|---|---|
| 1 | `DerivationOp.ARGMIN`/`ARGMAX` | resolve o turno 4, mas é `DerivationOp` novo + campo novo em `DerivedValue` + regra de render. M4.2 com desenho próprio. |
| 2 | Terceira chamada de LLM (reescrita de pergunta, roteador de intenção, classificador) | quebra `test_so_o_escritor_e_o_planejador_falam_com_o_modelo` e cria um caminho de influência sem procedência própria |
| 3 | Streaming (SSE/WebSocket) | exigiria fatiar um pipeline atômico; não há resposta parcial antes de `check_prose` |
| 4 | Retentativa automática de planejador ou escritor | segundo caminho de influência; documentado em três lugares do código como coisa que não deve existir |
| 5 | Fallback para `deterministic_prose` quando o escritor falha | apresenta texto que ninguém pediu como se fosse o pedido (`cli.py:828`) |
| 6 | Cache de turno de conversa | dois turnos idênticos em contextos diferentes não são o mesmo turno; e cachear resposta é a porta para número velho na tela |
| 7 | Memória do escritor entre turnos | ver 22.4 |
| 8 | Persistir sessões no warehouse | §5.3 |
| 9 | Markdown/HTML na prosa | injeção com passos extras; a prosa vem de um modelo |
| 10 | Export para PDF/Excel, gráficos, sparklines | gráfico é formatação de número fora de `render.py`; precisa de desenho próprio |
| 11 | Editar/reenviar mensagem anterior | rasura o histórico auditável; "tentar de novo" é mensagem nova |
| 12 | Múltiplas conversas na UI (abas, lista, busca) | uma sessão por vez basta para provar a arquitetura |
| 13 | Refatorar `cmd_ask`/`cmd_plan` para usar `chat/record.py` | boa ideia, risco desnecessário agora (§11) |
| 14 | FastAPI/uvicorn | §9.2 |
| 15 | `--host 0.0.0.0` | §7.6 |

---

# 24. Plano de rollout incremental

Cinco degraus. **Cada um deixa o repositório verde e utilizável.** Se a M4.1 parar em qualquer um deles, o que existe funciona.

**R0 — Fundação (A, onda 1).** `contracts/chat.py`. Nada usa ainda; a suíte roda. *Valor:* os tipos existem e as outras frentes destravam.

**R1 — Atribuição e contexto (A, onda 2, passo 2).** M1 + M2 num commit só. *Valor imediato, independente do chat:* `pat ask --writer` passa a produzir prosa que nomeia a empresa. Testável hoje, sem nenhuma casca HTTP.

**R2 — Turno headless (A + C).** `chat/turn.py`, `session.py`, `record.py`, `view.py`. Sem HTTP. *Valor:* uma conversa de N turnos roda dentro de um teste, ponta a ponta, com `FakeLLMClient`. **É aqui que o desenho é provado.** Se E2E-2 passa em R2, a parte difícil acabou.

**R3 — HTTP (B).** `chat/http.py` + `pat serve` com a UI mais crua possível: mensagem, resposta, erro. *Valor:* dá para conversar no browser.

**R4 — UI completa (B).** Painéis de plano e fontes, warnings, recusas, chips de pino, loading. *Valor:* a auditabilidade fica visível, que é o ponto do produto.

**Marco de decisão entre R2 e R3:** rodar R2 com a `ANTHROPIC_API_KEY` de verdade, por script, e ler as quatro respostas. Se o planejador não usar bem o contexto conversacional, o problema é de prompt e se conserta em `planner.py` — barato. Descobrir isso depois de R4 significa depurar prompt através de uma UI, que é o caminho caro.

---

# 25. Prompt exato para cada sub-agent

> **Preâmbulo comum — colar no topo de todos os quatro prompts:**
>
> Você está implementando a Milestone 4.1 do PAT (Personal Investment Research Agent) em `/Users/antoniobarcinski/Desktop/personal-pat`.
>
> **Leia antes de escrever qualquer linha:** `CLAUDE.md`, `docs/m41_chat_proposal.md` (a especificação desta milestone — ela é normativa), `src/pat/research/__init__.py`, `src/pat/contracts/research.py`.
>
> **Convenções que não se negociam:** identificadores em inglês, docstrings e comentários em **português**. Comentário explica *por quê*, não *o quê* — o valor está em registrar a razão de uma restrição para que ninguém a remova depois por parecer excesso de zelo. `Decimal` para dinheiro, nunca `float`. Datetime sempre timezone-aware. Contratos herdam de `Frozen` (imutável, `extra="forbid"`).
>
> **Proibições absolutas:** não adicione dependência nenhuma (`pyproject.toml` e `uv.lock` estão congelados). Não altere `src/pat/semantics/**`, `src/pat/store/**`, `src/pat/query/**`, `src/pat/contracts/research.py` nem nenhum contrato existente. Não toque em `data/**`. Não faça commit sem que `pytest` esteja verde. Não crie nem modifique arquivo que não esteja na sua lista de propriedade — outros agentes estão trabalhando em paralelo, e um arquivo tem exatamente um dono.
>
> Rode `pytest` antes de terminar. Se algo na especificação estiver errado ou impossível, **pare e reporte** em vez de improvisar: a especificação foi escrita a partir de uma auditoria do código real, e uma divergência significa que a auditoria errou.

---

## Agent A — Contratos, pipeline e integração

```
Você é o Agent A da M4.1. É o caminho crítico e o único agente autorizado a
tocar em src/pat/research/. Seu trabalho tem três ondas.

ARQUIVOS QUE VOCÊ PODE CRIAR OU MODIFICAR (nenhum outro):
  ONDA 1: src/pat/contracts/chat.py
          tests/chat/__init__.py, tests/chat/test_contracts_chat.py
  ONDA 2: src/pat/research/planner.py
          src/pat/research/render.py
          src/pat/research/__init__.py
          src/pat/chat/__init__.py, turn.py, record.py, view.py
          tests/chat/test_turn.py, tests/chat/test_view.py
          tests/research/test_planner.py, tests/research/test_render_and_answer.py
  ONDA 3: src/pat/cli.py
          README.md

READ-ONLY: todo o resto, em especial src/pat/chat/session.py e http.py
(Agents C e B), tests/chat/conftest.py (Agent D) e tests/chat/test_layering_chat.py.

--- ONDA 1: contratos (faça primeiro, entregue, avise) ---

Implemente src/pat/contracts/chat.py exatamente como a §13 da especificação:
TurnPlanSummary, ConversationContext, ChatRequest, RefusalKind, TurnRefusal,
ChatTurn. Todos Frozen. ChatTurn com model_validator exigindo exatamente um de
{answer, refusal}.

Restrição estrutural: NENHUM campo de ConversationContext ou TurnPlanSummary
pode aceitar Decimal, e nenhum pode derivar de ComputationResult. Escreva o
docstring do módulo explicando que essa é a propriedade que impede número de
turno anterior de chegar ao planejador — no mesmo espírito do docstring de
contracts/research.py.

contracts/chat.py pode importar contracts/common.py, contracts/semantics.py e
contracts/research.py. NADA MAIS — nem pat.store, nem pat.query, nem
pat.semantics, nem pat.research.*, nem duckdb, nem httpx.

Escreva tests/chat/test_contracts_chat.py com C-1 a C-5 da §18.1. C-1 é por
AST, copiando a técnica de test_a_gramatica_de_plano_nao_tem_onde_por_um_numero
em tests/research/test_layering_research.py.

PARE AQUI, rode pytest, e reporte. Os Agents B, C e D dependem deste arquivo.

--- ONDA 2: pipeline ---

(2a) render.py — describe() passa a nomear a empresa. Mudança M1 da §3.3.
Use metric.display_name or metric.entity_id. Comentário explicando por quê:
sem isso, uma comparação de N empresas dá N descrições idênticas ao escritor,
e a atribuição no texto passa a depender de o planejador ter escolhido bons
step_id — o que é sorte, não propriedade. Atualize
tests/research/test_render_and_answer.py. Confira que
tests/research/test_writer.py (nenhum valor renderizado no prompt) continua
passando: display_name não é valor.

(2b) planner.py — parâmetro `context: ConversationContext | None = None` em
build_user_prompt, build_request e plan_question; e research/__init__.py
repassa em plan_for_question.

REQUISITO DURO: com context=None, o prompt tem que sair BYTE A BYTE igual ao
de hoje. Escreva dois testes: (i) build_user_prompt(q,s) == build_user_prompt(q,s,None);
(ii) hash congelado — prompt_sha256 de uma pergunta fixa com context=None
comparado a um literal no teste. Se você mudar isso sem querer, esses testes
pegam.

Com contexto, acrescente ao final do prompt do usuário um bloco
"CONVERSA ATE AQUI" com canonical_bytes(context).

Acrescente ao SYSTEM_PROMPT as seções CONVERSA e ATRIBUIÇÃO, texto exato da
§17.3. A de ATRIBUIÇÃO não é opcional: sem ela o modelo produzirá um `min` de
três delta_pct para "qual teve a maior queda", e o sistema devolverá um número
correto para uma pergunta que ninguém fez.

planner.py continua não podendo importar os, pathlib, duckdb, pat.store,
pat.query, pat.semantics — só ganha pat.contracts.chat. Rode
tests/research/test_layering_research.py e confirme.

(2c) src/pat/chat/turn.py, record.py, view.py, __init__.py conforme §12.2-12.6.

turn.py: run_turn(request, state, *, paths, llm, model, source) -> ChatTurn.
Ordem fixa: ResearchQuestion -> build_context (importado de chat.session, que
o Agent C está escrevendo — programe contra a assinatura da §12.1) ->
plan_for_question -> grava chamada do planejador -> run_plan -> grava manifesto
e chamada do escritor -> ChatTurn. Traduz PlannerError, WriterError,
violações, pendências e ComputationFailure em TurnRefusal conforme §14.1.
run_turn NÃO levanta por dado; só LLMError escapa.

record.py: porte a lógica de cli.py:_record_manifest e cli.py:_record_call,
recebendo Paths em vez de args. Duplicação deliberada — não altere cli.py
nesta onda.

view.py: ChatTurn -> dict exatamente conforme §7.3. Não formate número nenhum:
consuma NumericClaim.rendered_value, que já é string. Enriqueça cada
MetricStep com entity_name a partir do EntityCard do snapshot.

__init__.py: ChatService, a raiz de composição. Guarda Paths, SessionStore,
LLMClient JÁ MONTADO (injetado, nunca construído aqui — mesma regra de
research/__init__.py: construir o adapter aqui daria saída de rede no import),
modelo e um threading.Lock. Expõe create_session, get_session, send_message.

Testes T-1..T-8 e V-1..V-4 da §18.

--- ONDA 3: integração (só depois de B, C e D entregarem) ---

`pat serve` em cli.py conforme §12.7. Reuse cli.py:_llm_client — não monte
outro cliente. Bind FIXO em 127.0.0.1, sem flag de host. Recuse na partida se
o warehouse não existir (mesma mensagem de _open_readonly) ou se
ANTHROPIC_API_KEY faltar.

Faça o merge das quatro branches e rode a suíte inteira. Atualize o README com
uma seção sobre `pat serve` no estilo das seções existentes de Pesquisa (L3).
```

---

## Agent B — HTTP e frontend

```
Você é o Agent B da M4.1: a casca HTTP e a página. Você NÃO toca no pipeline
de pesquisa nem no estado de conversa.

ARQUIVOS QUE VOCÊ PODE CRIAR OU MODIFICAR (nenhum outro):
  src/pat/chat/http.py
  src/pat/chat/static/index.html
  tests/chat/test_http.py

READ-ONLY: todo o resto. Em especial: src/pat/chat/{turn,view,session,record}.py
e __init__.py são de outros agentes. Você consome ChatService pela interface
descrita na §12.3 e o TurnView pelo formato congelado da §7.3.

Comece pela §7 da especificação. O TurnView da §7.3 é um contrato congelado —
programe contra ele; o Agent D produz um fixture com um TurnView de exemplo em
tests/chat/conftest.py que você usa para desenvolver antes do pipeline existir.

--- BACKEND: src/pat/chat/http.py ---

ThreadingHTTPServer + BaseHTTPRequestHandler da BIBLIOTECA PADRÃO. Zero
dependências. NÃO use FastAPI, Flask, Starlette ou uvicorn — a §9.2 explica
por quê, e a decisão está tomada.

Rotas da §7.1. Códigos HTTP da §7.4 — atenção especial: turno RECUSADO devolve
200 com `refusal` preenchido, não 4xx. A recusa é a resposta correta do
sistema; um 4xx faria a UI tratar como erro de cliente o que é o comportamento
projetado. Escreva esse comentário no código.

Regras:
- Todo handler dentro de try/except Exception, devolvendo 500 JSON. Sem isso a
  exceção sai como HTML e mata a thread.
- Corpo de requisição validado por Pydantic (ChatRequest). Campo desconhecido
  falha na fronteira com 400.
- Estático servido de Path(__file__).parent / "static", com LISTA BRANCA de
  nomes. Nunca os.path.join com entrada do usuário.
- Bind fixo em 127.0.0.1. Não implemente flag de host.
- GET / e GET /api/capability não pegam o lock do ChatService, para que a UI
  carregue enquanto um turno roda. POST /api/chat pega.

--- FRONTEND: src/pat/chat/static/index.html ---

UM arquivo: HTML + CSS + JS vanilla. Sem build, sem CDN, sem framework, sem
dependência externa de nenhum tipo (a página tem que funcionar sem rede além
do próprio localhost).

Layout da §8.2. Painel lateral para "Ver plano" (§16) e "Ver fontes" (§8.4).

REGRAS QUE SÃO VERIFICADAS POR TESTE (tests/chat/test_layering_chat.py, guard
L-9) — o teste vai falhar se você as violar:
1. A prosa vai para o DOM com textContent, NUNCA innerHTML. A prosa vem de um
   modelo; renderizá-la como markup é injeção com passos extras.
2. ZERO aritmética em JavaScript. Sem parseFloat, sem Number(, sem toFixed,
   sem Math., sem sort() por valor. Os números chegam como strings prontas em
   rendered_value e vão para a tela como vieram. O único lugar do sistema que
   formata número é src/pat/research/render.py, e isso é o que torna
   verificável a regra de que o modelo nunca emite dígito.
3. Sem eval(.

Mais:
4. Claims interpretativos em bloco visualmente separado, rotulado "leitura do
   modelo, não medição". Nunca no mesmo parágrafo da prosa — a distinção entre
   "isto foi medido" e "isto é uma leitura" só vale se sobreviver até a tela.
5. Warnings sempre visíveis, nunca atrás de clique.
6. Recusa (§14.1) com estilo próprio, distinto de resposta e distinto de erro
   de infra. Candidatos de ambiguidade viram botões que criam chips de pino.
7. Loading indeterminado com rótulos "planejando… / calculando… / redigindo…".
   Não invente percentual de progresso.
8. Tema via prefers-color-scheme, sem toggle.

--- TESTES: tests/chat/test_http.py ---

H-1..H-8 da §18.5. Suba o servidor numa thread contra um ChatService falso ou
contra fixtures. H-7 (round-trip do plano por load_envelope) e H-8 (exceção
não prevista não derruba o servidor) são os dois que mais importam.
```

---

## Agent C — Estado de conversa

```
Você é o Agent C da M4.1: o estado de conversa e a construção do contexto. É a
peça onde o invariante da milestone mora, e ela é pequena.

ARQUIVOS QUE VOCÊ PODE CRIAR OU MODIFICAR (nenhum outro):
  src/pat/chat/session.py
  src/pat/config.py   (adicionar SÓ a propriedade `chat` a Paths e incluí-la
                       em ensure() — não mexa em mais nada)
  tests/chat/test_session.py

READ-ONLY: todo o resto.

Depende de: src/pat/contracts/chat.py (Agent A, onda 1). Não comece antes.

Leia as §5 e §12.1 da especificação inteiras antes de escrever. A §5.1 e a
§5.4 contêm as decisões que você tem que implementar exatamente.

--- O QUE IMPLEMENTAR ---

ConversationState (dataclass): session_id, as_of, created_at, turns.

SessionStore: create(as_of), get(session_id), append(state, turn). Estado em
memória; log append-only em JSONL sob paths.chat / f"{session_id}.jsonl".

session_id gerado pelo SERVIDOR: sha256 de (created_at, nonce) truncado em 16
hex, validado contra ^[0-9a-f]{16}$ antes de virar caminho de arquivo. Nunca
aceite session_id do cliente sem essa validação — é a diferença entre um id e
um path traversal.

build_context(state, *, window=4) -> ConversationContext | None.

AS QUATRO REGRAS QUE NÃO SE NEGOCIAM:

1. Sessão nova devolve None, não contexto vazio. Contexto vazio mudaria o
   prompt do primeiro turno em relação ao de `pat plan`, e as duas coisas são
   a mesma pergunta. Escreva esse comentário no código — o Agent A tem um
   teste de hash congelado que depende disso.

2. NENHUM valor entra no contexto. Nem Decimal, nem rendered_value, nem
   result_id, nem manifest_id, nem mensagem de ResearchWarning (as de
   CHECK_FAILED carregam observed/expected, que são números do motor — o mesmo
   recorte que writer.evidence_payload já faz em research/writer.py:296).
   Só os campos listados na §5.2. Escreva o teste S-4: varredura textual sobre
   canonical_bytes(context) procurando qualquer rendered_value dos turnos.

3. Contexto NÃO vira pino. pinned_periods é whitelist em validate.py:245 —
   carregá-lo adiante recusaria "E como isso mudou desde 2023?" com
   PIN_CONTRADICTED_PERIOD. Pinos são o que o USUÁRIO fixou, vêm do request,
   e você não os infere de turno nenhum. Registre isso em comentário; é a
   armadilha mais provável de alguém reintroduzir depois.

4. as_of é da SESSÃO e imutável. §5.4: numa conversa que atravessa a
   meia-noite, as_of variável faria "isso mudou?" comparar duas visões do
   mundo diferentes sem avisar ninguém. Trocar as_of abre sessão nova.

Turnos recusados ENTRAM no contexto, com outcome="refused" e os códigos de
recusa. É o mecanismo mais barato de correção de curso e não custa chamada
nenhuma.

--- TESTES ---

S-1..S-7 da §18.2. S-4 é o que importa mais.
```

---

## Agent D — Testes, guards e auditoria

```
Você é o Agent D da M4.1: os guards de camada, os fixtures e a auditoria
final. Seu trabalho é o que impede a M4.1 de erodir depois.

ARQUIVOS QUE VOCÊ PODE CRIAR OU MODIFICAR (nenhum outro):
  tests/chat/conftest.py
  tests/chat/test_layering_chat.py
  tests/chat/test_conversation_e2e.py
  tests/research/test_layering_research.py   (SÓ estender, nunca afrouxar)

READ-ONLY: todo o src/. Se um guard seu falhar, o conserto é do dono do
arquivo — reporte, não conserte.

Leia tests/research/test_layering_research.py inteiro antes de começar. Ele é
o modelo do que você vai escrever: verificação por AST, conjuntos EXATOS e
não listas negras, e docstrings que explicam por que a regra existe. A nota
sobre guards em test_existe_exatamente_um_adapter_concreto é a filosofia do
projeto: um guard é ESTREITADO a cada milestone, nunca removido nem afrouxado.

--- ONDA 2a: conftest (faça primeiro — B, A e C dependem) ---

tests/chat/conftest.py com:
- warehouse temporário (copie a técnica de tests/research/conftest.py)
- FakeLLMClient pré-carregado com respostas de planejador e escritor para a
  conversa de 4 turnos da §17.1
- um fixture com um TurnView de exemplo (§7.3) completo, para o Agent B
  desenvolver o frontend antes do pipeline existir
- fixtures de sessão

--- ONDA 2b: guards de camada ---

tests/chat/test_layering_chat.py com L-1..L-10 da §18.4. Os que mais importam:

L-1: conjunto EXATO de arquivos em src/pat/chat/ contendo "llm.complete(" é
     VAZIO. O chat não fala com o modelo — research/ fala. Uma terceira
     chamada de LLM (roteador de intenção, reescritor de pergunta,
     classificador) apareceria aqui.
L-2: "quantize(" não aparece em src/pat/chat/. O único formatador de número do
     sistema é research/render.py.
L-9: src/pat/chat/static/index.html não contém parseFloat, Number(, toFixed,
     Math., eval(, innerHTML. O frontend não calcula e não injeta.
L-10: [project].dependencies de pyproject.toml inalterado. Uma dependência
     nova na M4.1 é uma decisão que tem que ser explícita.

Em tests/research/test_layering_research.py, ESTENDA (não afrouxe):
L-7: nenhum arquivo em src/pat/research/ importa pat.chat — a Fase 3 continua
     utilizável sem a camada de conversa, do mesmo jeito que a Fase 2 continua
     utilizável sem a Fase 3.
L-8: a saída de rede do projeto fora de sources/ cabe no conjunto EXATO
     {research/llm/anthropic.py, chat/http.py}.

Escreva os guards ANTES de o código existir. Vermelho é o estado correto nessa
hora.

--- ONDA 3: ponta a ponta ---

tests/chat/test_conversation_e2e.py com E2E-1..E2E-6 da §18.6.

E2E-2 É O PORTÃO DA MILESTONE. Rode a conversa de 4 turnos com FakeLLMClient,
capture TODOS os LLMRequest de TODOS os turnos (FakeLLMClient.calls existe
para isso), e afirme que nenhum rendered_value de nenhum turno anterior
aparece em nenhum prompt de nenhum turno posterior. É o análogo conversacional
de test_o_escritor_nao_le_valor_nenhum, e é a única afirmação da M4.1 que não
dá para fazer olhando um turno isolado.

E2E-5 é o segundo mais importante: pegue o PlanEnvelope de um turno, grave em
arquivo, rode `pat ask --plan-file p.json` SEM --writer, e confirme que os
rendered_value batem. Prova que a conversa não é fonte de verdade — é uma
casca sobre um pipeline que roda sem ela.

--- AUDITORIA FINAL ---

Percorra os 12 critérios da §21 e reporte cada um como passa/falha com a
evidência (comando + saída). O 21.9 é manual, com ANTHROPIC_API_KEY e browser:
reporte o que você conseguir automatizar e liste explicitamente o que precisa
de olho humano. Não declare a milestone pronta com item pendente não listado.
```

---

## Apêndice — referências de código citadas

| Referência | Arquivo:linha |
|---|---|
| `run_plan` | `src/pat/research/__init__.py:132` |
| `plan_for_question` | `src/pat/research/__init__.py:269` |
| `review_plan` | `src/pat/research/__init__.py:104` |
| `describe()` sem entidade — Achado 1 | `src/pat/research/render.py:77` |
| `MetricResult.entity_id` / `display_name` | `src/pat/contracts/semantics.py:447,453` |
| `min`/`max` sem atribuição — Achado 2 | `src/pat/research/derive.py:184` |
| `pinned_periods` como whitelist — Achado 3 | `src/pat/research/validate.py:245` |
| `check_prose` (regra do dígito) | `src/pat/research/answer.py:61` |
| `evidence_payload` (o recorte do escritor) | `src/pat/research/writer.py:263` |
| `SnapshotLimits` | `src/pat/contracts/research.py:284` |
| `ResearchQuestion` (pinos ordenados) | `src/pat/contracts/research.py:183` |
| `_llm_client` (ponto de montagem) | `src/pat/cli.py:939` |
| `_record_manifest` / `_record_call` | `src/pat/cli.py:746,1005` |
| Sem fallback do escritor | `src/pat/cli.py:828` |
| Leituras separadas dos números no terminal | `src/pat/cli.py:907` |
| `_open_readonly` (mensagem de warehouse ausente) | `src/pat/cli.py:84` |
| `Paths.llm` (procedências paralelas) | `src/pat/config.py:35` |
| Guards de camada da Fase 3 | `tests/research/test_layering_research.py` |

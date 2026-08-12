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

## Testes

- `pytest` — suite padrão, sem rede.
- `pytest -m network` — contra fontes públicas reais. Detecta mudança de layout de
  URL na origem; não roda em CI por depender de terceiro.

Ao adicionar um provider, escreva os testes de `resolve()` sem rede (construção de
URL, validação de parâmetro) e deixe só a verificação de layout em `-m network`.

## Estilo

- Identificadores em inglês, docstrings e comentários em português.
- Comentário explica *por que*, não *o quê*. A maior parte do valor dos comentários
  aqui está em registrar a razão de uma restrição, para que ninguém a remova depois
  por parecer excesso de zelo.
- Dependências mínimas e pinadas. Evite frameworks de agente: o valor do desenho está
  no pipeline explícito e inspecionável, e frameworks escondem exatamente o que
  precisa ser auditado.

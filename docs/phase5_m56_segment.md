# M5.6 frente 4 — o que a DERA revelou sobre o eixo SEGMENT

Registro do que foi verificado contra a fonte real, antes de qualquer
implementação. Escrito porque as três questões abaixo não têm resposta nos
dados, e adivinhar produziria uma decomposição plausível e errada — exatamente
o modo de falha que a Fase 5 existe para impedir.

## A fonte existe, e é estruturada

Confirmado. `sec.financial_statements` (dataset trimestral da DERA, 2025q1,
127 MB) traz a coluna `segments` em `num.txt`, e a Intel publica receita por
segmento de negócio ali. Não há PDF envolvido, e a decisão A permanece intacta.

Ingerido e no bronze. `parse_dera_num` lê as linhas dimensionais.

## Problema 1 — os membros não formam uma partição

Receita por segmento da Intel, FY2024, todos com
`ConsolidationItems=OperatingSegments`:

| membro | US$ MM |
|---|---|
| `ClientComputingGroupDatacenterAndAIAndNetworkAndEdge` | 48.949 |
| `ClientComputingGroup` | 30.290 |
| `IntelFoundry` | 17.543 |
| `DatacenterAndAI` | 12.817 |
| `NetworkAndEdge` | 5.842 |
| `AllOtherSegments` | 3.824 |
| `IntelFoundry` + `ProductOrService=AssemblyAndTest` | 385 |

O primeiro é um **roll-up**: 30.290 + 12.817 + 5.842 = 48.949. Somar todos os
membros contaria esses três segmentos duas vezes, inflando a soma em 48.949.

O último tem uma **terceira dimensão** (`ProductOrService`) e é um recorte
*dentro* de `IntelFoundry`, não um segmento irmão.

Nada nos dados marca quem é folha e quem é agregado. A hierarquia está no
*linkbase* de apresentação do XBRL, que a DERA não publica em `num.txt`.

## Problema 2 — os segmentos não somam o consolidado, e o resíduo não é resíduo

Folhas plausíveis: 30.290 + 12.817 + 5.842 + 17.543 + 3.824 = **70.316**
Receita consolidada FY2024 (companyfacts): **53.101**

Diferença: **17.215**, quase exatamente a receita da Intel Foundry (17.543).
A explicação é conhecida — a receita da Foundry é majoritariamente
*intersegmento*, vendida internamente a CCG/DCAI e eliminada na consolidação.

Mas a **linha de eliminação não está** no conjunto que a DERA devolveu para
`BusinessSegments`. Ou seja: uma decomposição da receita consolidada por
segmento fecharia com resíduo de −17.215, e esse resíduo **não seria "não
explicado"** — seria uma eliminação conhecida que a fonte não forneceu.
Apresentá-lo como resíduo mentiria sobre o que ele é.

## Problema 3 — as datas não se encontram

| fonte | fim do exercício FY2024 |
|---|---|
| `companyfacts` | **2024-12-28** (fiscal real, 52/53 semanas) |
| DERA `ddate` | **2024-12-31** (normalizada para fim de mês) |

O fato consolidado e o fato por segmento nunca casam por `period_end`. Juntá-los
exige uma regra de reconciliação de período que não existe no sistema — e
`period_end` é chave lógica em toda a arquitetura, incluindo `AS OF`.

## O que isso significa

O eixo `SEGMENT` **não** está bloqueado por falta de fonte estruturada — a
fonte existe. Está bloqueado por três decisões semânticas que os dados não
resolvem:

1. quais membros formam a partição (folha vs. roll-up vs. sub-dimensão);
2. como tratar a eliminação intersegmento ausente;
3. como reconciliar `ddate` normalizada com `period_end` fiscal.

Cada uma tem mais de uma resposta defensável, e a escolha errada produz um
número plausível. Por isso estão registradas em vez de decididas.

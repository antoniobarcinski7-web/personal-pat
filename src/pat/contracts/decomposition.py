"""Contratos de decomposicao (Fase 5, M5.2): de quanto mudou para o que mudou.

Nada neste modulo importa `pat.semantics.*`, `pat.query`, `pat.store` ou
`pat.research.*`. Ele so conhece `contracts.common` e `contracts.semantics`.

O que a decomposicao faz, e o que ela nunca faz
-----------------------------------------------
`delta_pct` responde *quanto* uma grandeza mudou. Uma decomposicao responde
*de onde* a mudanca veio:

    delta_total  =  contribuicao_A + contribuicao_B + ... + residual

E o primeiro passo de toda resposta causal, e ele e QUANTITATIVO. Um sistema
que pula direto para "o release diz que foi o Brent" esta fazendo jornalismo,
nao research - a citacao explica a decomposicao, nunca a substitui.

O residual e obrigatorio, e por que
------------------------------------
`residual` NAO e um campo opcional que fica em zero quando tudo fecha. Ele e
definido como `delta_total - soma(contribuicoes)` e o contrato exige a
igualdade EXATA. Isso tem uma consequencia que e o ponto inteiro do desenho:
nao existe forma de montar um `DecompositionResult` cujas partes nao somem o
todo. Ou elas somam, ou a diferenca aparece com nome.

A alternativa - deixar a soma "aproximadamente" fechar - produziria o pior
resultado possivel: uma decomposicao que parece completa e explica 94% da
variacao, com 6% escondido num arredondamento que ninguem foi conferir.

Membro presente em um so periodo nao vira zero
----------------------------------------------
Se uma companhia reportava um componente em FY2023 e parou em FY2024, tratar a
ausencia como zero atribuiria o valor inteiro do ano anterior como
"contribuicao" - um driver fabricado, com cara de medido. A decomposicao
recusa, nomeando o membro. E a mesma regra de `MetricUnavailable`: nunca zero,
nunca parcial, nunca `None` que se le como zero.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import Frozen, PeriodType, Sha256
from pat.contracts.semantics import Fidelity, InputRef, ReportingScope

# ---------------------------------------------------------------------------
# Eixos
# ---------------------------------------------------------------------------


class BreakdownAxis(StrEnum):
    """Por qual dimensao um total se reparte.

    Universal e sem jurisdicao, como `Concept`. O eixo e a IDEIA (repartir por
    segmento operacional); o membro e o ENDERECO daquela ideia num regime
    ("E&P" e o segmento da Petrobras, nao um conceito universal). E a mesma
    separacao de tres eixos da Fase 2, aplicada a uma dimensao nova.
    """

    COMPONENT = "component"
    """Partes de uma identidade contabil publicada: receita, custo, despesa.

    E o unico eixo com fonte deterministica no regime da CVM hoje, porque as
    partes sao linhas do proprio plano padronizado - ja mapeadas, ja com
    fato no gold, ja com linhagem ate o byte."""

    SEGMENT = "segment"
    """Segmento operacional (IFRS 8). Sem fonte estruturada na CVM: o plano
    padronizado nao tem segmentos, e a nota explicativa e PDF."""

    GEOGRAPHY = "geography"
    PRODUCT = "product"


class DecompositionFailureReason(StrEnum):
    """Por que uma variacao nao pode ser decomposta.

    Enum proprio, e nao membros novos em `UnavailableReason`: o motivo de um
    conceito nao existir e assunto da camada semantica; o motivo de uma
    identidade nao poder ser aberta entre dois periodos e assunto daqui.
    """

    NO_BREAKDOWN_SOURCE = "no_breakdown_source"
    """O eixo pedido nao tem fonte estruturada neste regime. Distinto de
    'a empresa nao reporta': e o SISTEMA que nao tem por onde ler."""

    UNKNOWN_DECOMPOSITION = "unknown_decomposition"
    TARGET_UNAVAILABLE = "target_unavailable"
    MEMBER_UNAVAILABLE = "member_unavailable"
    MEMBER_ONLY_IN_ONE_PERIOD = "member_only_in_one_period"
    """Membro novo ou encerrado. Recusa, nunca zero implicito."""

    PERIOD_ORDER = "period_order"
    PERIOD_KIND_MISMATCH = "period_kind_mismatch"
    """Comparar um trimestre com um exercicio. A variacao existiria e nao
    significaria nada."""

    CURRENCY_MISMATCH = "currency_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    IDENTITY_DOES_NOT_HOLD = "identity_does_not_hold"
    """Os termos nao fecham nem dentro de um periodo isolado. Sinal de
    mapeamento errado - as partes nao vieram da mesma cascata."""


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


class Contribution(Frozen):
    """Quanto um membro contribuiu para a variacao do total.

    `contribution` ja carrega o sinal da identidade: num `ebit = receita -
    custo`, um custo que SOBE contribui NEGATIVAMENTE para o EBIT. Guardar o
    delta cru e deixar o leitor aplicar o sinal seria transferir para a tela
    uma conta que erra em silencio.
    """

    member_id: str = Field(min_length=1)
    member_label: str = Field(min_length=1)
    sign: Literal[-1, 1]

    value_from: Decimal
    value_to: Decimal
    delta: Decimal = Field(description="value_to - value_from, sem sinal da identidade")
    contribution: Decimal = Field(description="sign * delta: efeito sobre o total")

    share: Decimal | None = None
    """Fracao da variacao do total. `None` quando o total nao variou - divisao
    por zero nao vira infinito nem 100%, vira ausencia declarada."""

    fidelity: Fidelity
    inputs: tuple[InputRef, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "Contribution":
        if self.delta != self.value_to - self.value_from:
            raise ValueError(
                f"{self.member_id}: delta={self.delta} nao e "
                f"{self.value_to} - {self.value_from}"
            )
        if self.contribution != self.sign * self.delta:
            raise ValueError(
                f"{self.member_id}: contribuicao={self.contribution} nao e "
                f"{self.sign} * {self.delta}"
            )
        for name in ("value_from", "value_to", "delta", "contribution"):
            if not getattr(self, name).is_finite():
                raise ValueError(f"{self.member_id}: {name} nao finito")
        return self


class DecompositionResult(Frozen):
    """A variacao de um total, aberta em partes, com o residual explicito.

    A invariante que este contrato existe para tornar inviolavel:

        target_delta == soma(contribuicoes) + residual

    Exata, em `Decimal`, conferida na construcao. Nao ha tolerancia AQUI - a
    tolerancia decide se a decomposicao FECHA (`closes`), nao se ela pode ser
    montada. Uma decomposicao que nao fecha continua sendo um resultado
    valido e publicavel; ela so diz, no proprio corpo, quanto sobrou.
    """

    result_version: Literal["v1"] = "v1"
    decomposition_id: str = Field(min_length=1)
    decomposition_version: str = Field(min_length=1)
    axis: BreakdownAxis
    target_id: str = Field(min_length=1)
    target_label: str = Field(min_length=1)

    entity_id: str = Field(min_length=1)
    scope: ReportingScope
    period_type: PeriodType
    period_from: date
    period_to: date
    as_of: date

    target_from: Decimal
    target_to: Decimal
    target_delta: Decimal
    target_delta_pct: Decimal | None = None

    contributions: tuple[Contribution, ...] = Field(min_length=1)

    residual: Decimal
    residual_share: Decimal | None = None
    closes: bool
    tolerance_abs: Decimal

    currency: str = Field(min_length=1)
    fidelity: Fidelity
    knowledge_date: date
    mapping_sha256: Sha256

    @model_validator(mode="after")
    def _check(self) -> "DecompositionResult":
        if self.period_from >= self.period_to:
            raise ValueError(
                f"periodos fora de ordem: {self.period_from} nao e anterior a "
                f"{self.period_to}"
            )
        if self.target_delta != self.target_to - self.target_from:
            raise ValueError("target_delta nao bate com os dois valores do alvo")

        soma = sum((c.contribution for c in self.contributions), Decimal(0))
        if soma + self.residual != self.target_delta:
            raise ValueError(
                f"as partes nao somam o todo: contribuicoes={soma}, "
                f"residual={self.residual}, total={self.target_delta}. "
                "O residual existe justamente para absorver a diferenca; se "
                "esta igualdade falha, quem montou o resultado calculou o "
                "residual errado."
            )
        if (abs(self.residual) <= self.tolerance_abs) != self.closes:
            raise ValueError(
                f"closes={self.closes} nao bate com residual={self.residual} e "
                f"tolerancia={self.tolerance_abs}"
            )

        ids = [c.member_id for c in self.contributions]
        if len(ids) != len(set(ids)):
            raise ValueError(f"membro repetido: {sorted({i for i in ids if ids.count(i) > 1})}")
        return self

    @property
    def explained(self) -> Decimal:
        """Quanto das contribuicoes soma - o complemento do residual."""
        return self.target_delta - self.residual

    def ranked(self) -> tuple[Contribution, ...]:
        """Contribuicoes por magnitude, maior primeiro; id como desempate.

        Ordem total, para que duas execucoes identicas apresentem a mesma
        coisa na mesma ordem.
        """
        return tuple(
            sorted(self.contributions, key=lambda c: (-abs(c.contribution), c.member_id))
        )


class DecompositionUnavailable(Frozen):
    """A resposta honesta quando nao da para abrir a variacao.

    Nunca uma decomposicao parcial, nunca uma lista de partes que nao somam o
    todo, nunca narrativa no lugar do numero. Carrega o suficiente para
    consertar - inclusive quando o conserto e "escrever um provider".
    """

    reason: DecompositionFailureReason
    message: str = Field(min_length=1)
    decomposition_id: str | None = None
    axis: BreakdownAxis | None = None
    entity_id: str | None = None
    member_id: str | None = None
    period_from: date | None = None
    period_to: date | None = None
    as_of: date | None = None
    remedy: str | None = None

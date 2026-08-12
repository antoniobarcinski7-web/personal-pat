"""Tipos base compartilhados por todos os contratos.

Regra do projeto: nada atravessa uma fronteira de camada sem passar por um
modelo definido aqui ou em um dos modulos irmaos de `contracts`.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _check_sha256(v: str) -> str:
    if not _SHA256_RE.match(v):
        raise ValueError(f"sha256 invalido (esperado 64 hex minusculos): {v!r}")
    return v


def _check_aware(v: datetime) -> datetime:
    """Exige timezone explicita.

    Timestamp ingenuo e a origem mais comum de erro de point-in-time: o mesmo
    instante vira dois valores diferentes dependendo de onde o codigo roda,
    o que quebra a reprodutibilidade (I3).
    """
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("datetime precisa ser timezone-aware")
    return v


Sha256 = Annotated[str, AfterValidator(_check_sha256)]
AwareDatetime = Annotated[datetime, AfterValidator(_check_aware)]


class SourceTier(StrEnum):
    """Procedencia do dado, do mais forte para o mais fraco.

    Gravado em todo retrieval. Permite que a camada analitica prefira ou
    exija um nivel minimo de procedencia, e permite auditar depois quanto de
    uma conclusao dependeu de fonte fraca.
    """

    PRIMARY_OFFICIAL = "primary_official"
    """Documento oficial do regulador ou da bolsa (CVM, BCB, IBGE, B3)."""

    ISSUER = "issuer"
    """Publicado pela propria companhia (RI, release de resultados)."""

    PUBLIC_WEB = "public_web"
    """Web publica sem carater oficial (imprensa, agregadores)."""

    VENDOR_LICENSED = "vendor_licensed"
    """Provedor pago sob licenca. Nao implementado na Fase 0."""

    DERIVED = "derived"
    """Produzido pelo proprio PAT a partir de outras fontes."""


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PeriodType(StrEnum):
    QUARTER = "quarter"
    YEAR = "year"
    SEMESTER = "semester"
    MONTH = "month"
    DAY = "day"
    INSTANT = "instant"
    """Saldo pontual (contas patrimoniais), diferente de fluxo acumulado."""


class Frozen(BaseModel):
    """Base para todos os contratos: imutavel e estrita.

    `extra="forbid"` faz um campo inesperado falhar na fronteira de entrada,
    em vez de se propagar silenciosamente ate um numero errado no relatorio.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

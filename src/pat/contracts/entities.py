"""Identidade de entidades cobertas pelo sistema.

O identificador canonico no Brasil e o CNPJ; `cod_cvm` e a chave usada nos
arquivos da CVM; tickers mudam e por isso nunca sao chave primaria.
"""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from pat.contracts.common import Frozen

_CNPJ_RE = re.compile(r"^\d{14}$")


class Company(Frozen):
    entity_id: str = Field(description="Chave estavel interna, ex. 'br:cnpj:33000167000101'")
    legal_name: str
    cnpj: str | None = Field(default=None, description="14 digitos, sem pontuacao")
    cod_cvm: int | None = Field(default=None, description="Codigo CVM da companhia aberta")
    tickers: tuple[str, ...] = ()
    country: str = Field(default="BR", description="ISO 3166-1 alpha-2")

    @field_validator("cnpj")
    @classmethod
    def _clean_cnpj(cls, v: str | None) -> str | None:
        if v is None:
            return None
        digits = re.sub(r"\D", "", v)
        if not _CNPJ_RE.match(digits):
            raise ValueError(f"CNPJ deve ter 14 digitos: {v!r}")
        return digits

    @staticmethod
    def entity_id_from_cnpj(cnpj: str) -> str:
        digits = re.sub(r"\D", "", cnpj)
        if not _CNPJ_RE.match(digits):
            raise ValueError(f"CNPJ deve ter 14 digitos: {cnpj!r}")
        return f"br:cnpj:{digits}"

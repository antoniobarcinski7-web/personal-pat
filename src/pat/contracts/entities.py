"""Identidade de entidades cobertas pelo sistema.

O identificador canonico no Brasil e o CNPJ; `cod_cvm` e a chave usada nos
arquivos da CVM; tickers mudam e por isso nunca sao chave primaria.

A separacao que a Fase 5 introduziu
-----------------------------------
Ate a M5.6, `gold_fact` carregava `cod_cvm` e `denom_cia` como colunas
obrigatorias. Isso era o mesmo erro de categoria que citar `cd_conta` em
`concepts.py` seria: um endereco de REGIME morando na camada que nao pode ter
jurisdicao. Funcionava enquanto so existia o Brasil, e quebrava na primeira
companhia americana - que nao tem `cod_cvm` nenhum.

Agora o fato guarda so `entity_id`, opaco, e os identificadores locais vivem
em `EntityIdentity`, um por jurisdicao. Consequencias:

- Uma empresa nova de outra jurisdicao nao pede coluna nova em `gold_fact`.
  A terceira jurisdicao tambem nao. Era a terceira coluna que denunciava o
  desenho anterior.
- `entity_id` continua opaco: quem consulta nao deve derivar jurisdicao dele
  por prefixo, e sim ler `EntityIdentity.jurisdiction`. O prefixo e uma
  conveniencia de leitura humana, nao um contrato.
"""

from __future__ import annotations

import re

from pydantic import Field, field_validator, model_validator

from pat.contracts.common import Frozen

_CNPJ_RE = re.compile(r"^\d{14}$")
_CIK_RE = re.compile(r"^\d{10}$")


class EntityIdentity(Frozen):
    """Como uma entidade e chamada num regime concreto.

    `scheme` e o vocabulario do identificador (`cnpj`, `cod_cvm`, `cik`), e
    `local_id` e sempre STRING - mesmo quando o regime usa numero. `cod_cvm` e
    inteiro na CVM e CIK tem zeros a esquerda que importam; guardar os dois no
    mesmo tipo obrigaria um deles a ser convertido, e conversao silenciosa de
    identificador e como se perde um zero a esquerda sem ninguem notar.
    """

    entity_id: str = Field(min_length=1, description="Chave interna, opaca")
    jurisdiction: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2")
    scheme: str = Field(min_length=1, description="'cnpj', 'cod_cvm', 'cik', 'ticker'")
    local_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    is_primary: bool = False
    """Verdadeiro no identificador canonico da jurisdicao (CNPJ no Brasil, CIK
    nos EUA). Os demais sao apelidos uteis para quem digita na linha de
    comando - `cod_cvm` e um deles."""

    @model_validator(mode="after")
    def _check(self) -> "EntityIdentity":
        if self.jurisdiction != self.jurisdiction.upper():
            raise ValueError(f"jurisdicao em maiusculas: {self.jurisdiction!r}")
        if self.scheme != self.scheme.lower():
            raise ValueError(f"scheme em minusculas: {self.scheme!r}")
        return self


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

    cik: str | None = Field(
        default=None, description="Central Index Key da SEC, 10 digitos com zeros"
    )

    @field_validator("cik")
    @classmethod
    def _clean_cik(cls, v: str | None) -> str | None:
        if v is None:
            return None
        digits = re.sub(r"\D", "", str(v)).lstrip("0") or "0"
        if len(digits) > 10:
            raise ValueError(f"CIK longo demais: {v!r}")
        return digits.zfill(10)

    @staticmethod
    def entity_id_from_cnpj(cnpj: str) -> str:
        digits = re.sub(r"\D", "", cnpj)
        if not _CNPJ_RE.match(digits):
            raise ValueError(f"CNPJ deve ter 14 digitos: {cnpj!r}")
        return f"br:cnpj:{digits}"

    @staticmethod
    def entity_id_from_cik(cik: str) -> str:
        """`us:cik:0000050863`. Simetrico ao do CNPJ, e opaco do mesmo jeito.

        Os zeros a esquerda ficam: a SEC os usa em `CIK0000050863.json`, e um
        identificador que muda de forma conforme quem o escreveu deixa de ser
        identificador.
        """
        digits = re.sub(r"\D", "", str(cik)).lstrip("0") or "0"
        if len(digits) > 10:
            raise ValueError(f"CIK longo demais: {cik!r}")
        return f"us:cik:{digits.zfill(10)}"

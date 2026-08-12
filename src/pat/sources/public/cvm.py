"""CVM - Portal de Dados Abertos.

Fonte primaria oficial das demonstracoes financeiras de companhias abertas
brasileiras. Fase 0 cobre tres datasets:

    cvm.dfp             Demonstracoes Financeiras Padronizadas (anuais)
    cvm.itr             Informacoes Trimestrais
    cvm.cad_cia_aberta  Cadastro de companhias abertas

Ponto de atencao arquitetural
-----------------------------
A CVM publica um ZIP por ano e **reescreve o mesmo arquivo** quando uma
companhia reapresenta demonstracoes. A URL de `dfp_cia_aberta_2024.zip` e
estavel, o conteudo nao. Em consequencia:

- anos ja ingeridos precisam ser re-buscados periodicamente, nao tratados
  como imutaveis;
- o bronze guarda cada versao dos bytes separadamente, e nunca sobrescreve;
- a diferenca entre duas versoes do mesmo recurso e o registro primario de
  uma reapresentacao, e alimenta o `knowledge_date` da camada gold.

Por isso `resource_key` e o ano (o recurso logico, estavel), enquanto a
identidade do conteudo e o SHA-256 (o recurso fisico, variavel).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import ClassVar

from pat.contracts.common import SourceTier
from pat.contracts.documents import ResourceRef
from pat.sources.base import DatasetSpec, PublicSourceProvider, SourceError

_BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA"
_HOMEPAGE = "https://dados.cvm.gov.br/dataset"
_LICENSE = "Dados abertos governamentais (Lei 12.527/2011)"


class CVMProvider(PublicSourceProvider):
    provider_id: ClassVar[str] = "cvm"
    tier: ClassVar[SourceTier] = SourceTier.PRIMARY_OFFICIAL
    version: ClassVar[str] = "1.0.0"

    min_interval_s: ClassVar[float] = 2.0

    DFP_MIN_YEAR: ClassVar[int] = 2010
    ITR_MIN_YEAR: ClassVar[int] = 2011

    def datasets(self) -> tuple[DatasetSpec, ...]:
        return (
            DatasetSpec(
                dataset_id="cvm.dfp",
                title="Demonstracoes Financeiras Padronizadas (anual)",
                tier=self.tier,
                params=("year",),
                homepage=f"{_HOMEPAGE}/cia_aberta-doc-dfp",
                license=_LICENSE,
                notes="Arquivo do ano e reescrito pela CVM em caso de reapresentacao.",
            ),
            DatasetSpec(
                dataset_id="cvm.itr",
                title="Informacoes Trimestrais",
                tier=self.tier,
                params=("year",),
                homepage=f"{_HOMEPAGE}/cia_aberta-doc-itr",
                license=_LICENSE,
                notes="Arquivo do ano e reescrito pela CVM em caso de reapresentacao.",
            ),
            DatasetSpec(
                dataset_id="cvm.cad_cia_aberta",
                title="Cadastro de companhias abertas",
                tier=self.tier,
                params=(),
                homepage=f"{_HOMEPAGE}/cia_aberta-cad",
                license=_LICENSE,
                notes="Snapshot corrente, sem historico. Buscar periodicamente.",
            ),
        )

    def resolve(self, dataset_id: str, **params: str) -> tuple[ResourceRef, ...]:
        spec = self.spec(dataset_id)

        if dataset_id == "cvm.cad_cia_aberta":
            if params:
                raise SourceError(f"{dataset_id} nao aceita parametros; recebeu {sorted(params)}")
            return (
                ResourceRef(
                    provider_id=self.provider_id,
                    dataset_id=dataset_id,
                    resource_key="current",
                    url=f"{_BASE}/CAD/DADOS/cad_cia_aberta.csv",
                    media_type="text/csv",
                ),
            )

        unexpected = set(params) - set(spec.params)
        if unexpected:
            raise SourceError(f"{dataset_id}: parametros desconhecidos {sorted(unexpected)}")
        if "year" not in params:
            raise SourceError(f"{dataset_id} exige o parametro 'year' (ex. 2024, 2020-2024)")

        kind = "dfp" if dataset_id == "cvm.dfp" else "itr"
        min_year = self.DFP_MIN_YEAR if kind == "dfp" else self.ITR_MIN_YEAR
        years = self._parse_years(params["year"], min_year)

        return tuple(
            ResourceRef(
                provider_id=self.provider_id,
                dataset_id=dataset_id,
                resource_key=str(year),
                url=f"{_BASE}/DOC/{kind.upper()}/DADOS/{kind}_cia_aberta_{year}.zip",
                media_type="application/zip",
                params=(("year", str(year)),),
            )
            for year in years
        )

    @staticmethod
    def _parse_years(raw: str, min_year: int) -> tuple[int, ...]:
        """Aceita '2024', '2020-2024' e '2020,2022,2024'."""
        max_year = datetime.now(UTC).year
        years: set[int] = set()

        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            if m := re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", part):
                start, end = int(m.group(1)), int(m.group(2))
                if start > end:
                    raise SourceError(f"intervalo invertido: {part!r}")
                years.update(range(start, end + 1))
            elif re.fullmatch(r"\d{4}", part):
                years.add(int(part))
            else:
                raise SourceError(f"ano invalido: {part!r}")

        if not years:
            raise SourceError("nenhum ano informado")

        for year in sorted(years):
            if year < min_year:
                raise SourceError(f"CVM nao publica este dataset antes de {min_year}; pedido: {year}")
            if year > max_year:
                raise SourceError(f"ano futuro: {year}")

        return tuple(sorted(years))

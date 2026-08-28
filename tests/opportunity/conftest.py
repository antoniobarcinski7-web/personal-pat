"""Fixtures do Opportunity.

Duas empresas, uma em cada jurisdicao, porque quase toda regra desta camada so
e testavel de verdade com duas: uma jurisdicao so nao distingue "generico" de
"brasileiro por acidente".
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pat.contracts.opportunity import CompanyProfile

AS_OF = date(2025, 6, 30)
CREATED_AT = datetime(2025, 7, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def gpa_profile() -> CompanyProfile:
    return CompanyProfile(
        entity_id="br:cnpj:47508411000156",
        display_name="CIA BRASILEIRA DE DISTRIBUICAO",
        jurisdiction="BR",
        identifiers=(("cnpj", "47508411000156"), ("cod_cvm", "14826")),
    )


@pytest.fixture
def intel_profile() -> CompanyProfile:
    return CompanyProfile(
        entity_id="us:cik:0000050863",
        display_name="INTEL CORP",
        jurisdiction="US",
        identifiers=(("cik", "0000050863"), ("ticker", "INTC")),
    )


@pytest.fixture
def root(tmp_path):
    """Raiz dos workspaces. Equivalente a `paths.opportunity`."""
    return tmp_path / "opportunity"

"""L2 - camada semantica: conceitos universais, mapeamentos por regime, metricas.

Montagem tipica:

    from pat.semantics import build_engine
    engine = build_engine(conn)
    result = engine.compute("ebitda@v1", entity_id=..., period_end=..., scope=..., as_of=...)
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from pat.contracts.semantics import TaxonomyId
from pat.semantics.engine import Engine
from pat.semantics.frameworks import install_builtins
from pat.semantics.loader import MappingSet, load_dir
from pat.semantics.registry import MetricRegistry, default_registry

DEFAULT_SOURCE = "cvm.dfp"

# Raiz de composicao: e aqui, e so aqui, que o sistema decide quais regimes
# concretos existem. Trocar a CVM pela SEC e mexer nesta linha e no
# `build_engine` abaixo - nao em conceito, metrica ou contrato.
install_builtins()


def build_engine(
    conn: duckdb.DuckDBPyConnection,
    *,
    mappings_dir: Path | None = None,
    mappings: MappingSet | None = None,
    registry: MetricRegistry | None = None,
    source: str = DEFAULT_SOURCE,
    git_sha: str | None = None,
) -> Engine:
    """Motor ligado ao warehouse local.

    Este e o unico ponto onde a camada semantica encontra uma fonte concreta:
    aqui se escolhem os resolvers. Plugar um regime novo e acrescentar uma
    linha neste dicionario - e foi literalmente isso que a M5.6 fez com o
    us-gaap. Nenhum conceito, nenhuma metrica e nenhuma decomposicao mudou.

    Os dois convivem no mesmo motor de proposito: o `entity_id` decide qual
    mapeamento resolve, o mapeamento decide qual taxonomia, e a taxonomia
    decide qual resolver. Uma pergunta sobre a Petrobras e outra sobre a Intel
    passam pelo mesmo caminho e chegam a fontes diferentes sem que nada acima
    saiba disso.
    """
    from pat import __version__
    from pat.query.asof import AsOf
    from pat.semantics.frameworks.cvm_dfp.resolver import CvmDfpResolver
    from pat.semantics.frameworks.us_gaap.resolver import UsGaapResolver

    asof = AsOf(conn)
    return Engine(
        resolvers={
            TaxonomyId.CVM_PLANO_PADRONIZADO: CvmDfpResolver(asof),
            TaxonomyId.US_GAAP_XBRL: UsGaapResolver(asof),
        },
        mappings=mappings if mappings is not None else load_dir(mappings_dir or _default_dir()),
        registry=registry if registry is not None else default_registry(),
        source=source,
        pat_version=__version__,
        git_sha=git_sha,
    )


def _default_dir() -> Path:
    from pat.semantics.loader import MAPPINGS_DIR

    return MAPPINGS_DIR


__all__ = ["Engine", "MetricRegistry", "build_engine", "default_registry", "load_dir"]

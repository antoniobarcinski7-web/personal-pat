"""Definicoes de metrica. Uma por modulo, versionada.

Regra de camada, testada em `tests/semantics/test_layering.py`: nenhum modulo
daqui pode importar `pat.semantics.frameworks`, `pat.query` ou `pat.store`.
Metrica fala de conceito; onde o conceito mora e assunto do mapeamento.

O registro e explicito - lista abaixo, na ordem de dependencia. Nao ha
autodescoberta por varredura de diretorio: metrica que existe no disco mas nao
foi registrada aqui nao deve calcular, e um `import *` magico transformaria um
arquivo esquecido numa metrica fantasma.
"""

from __future__ import annotations

from pat.semantics.definitions import (
    d_and_a,
    ebit,
    ebitda,
    lucro_liquido,
    margem_ebitda,
    receita_liquida,
)
from pat.semantics.registry import MetricRegistry

MODULES = (receita_liquida, ebit, d_and_a, ebitda, margem_ebitda, lucro_liquido)


def register_all(registry: MetricRegistry) -> MetricRegistry:
    for module in MODULES:
        module.register(registry)
    return registry


__all__ = ["MODULES", "register_all"]

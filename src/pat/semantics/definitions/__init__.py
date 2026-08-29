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
    aliquota_efetiva,
    caixa_e_equivalentes,
    capex,
    capital_investido,
    d_and_a,
    divida_bruta,
    divida_bruta_com_arrendamento,
    divida_liquida,
    ebit,
    ebitda,
    fcf,
    fluxo_de_caixa_operacional,
    dividendos_pagos,
    lucro_liquido,
    margem_ebitda,
    patrimonio_liquido,
    receita_liquida,
    recompras,
    retorno_ao_acionista,
    roe,
    roic,
)
from pat.semantics.registry import MetricRegistry

MODULES = (
    # DRE
    receita_liquida,
    ebit,
    d_and_a,
    ebitda,
    margem_ebitda,
    lucro_liquido,
    # Balanco
    caixa_e_equivalentes,
    divida_bruta,
    divida_bruta_com_arrendamento,
    divida_liquida,
    # Fluxo de caixa
    fluxo_de_caixa_operacional,
    capex,
    fcf,
    # Patrimonio e retorno ao acionista
    patrimonio_liquido,
    dividendos_pagos,
    recompras,
    retorno_ao_acionista,
    # Retorno sobre o capital
    roe,
    aliquota_efetiva,
    capital_investido,
    roic,
)


def register_all(registry: MetricRegistry) -> MetricRegistry:
    for module in MODULES:
        module.register(registry)
    return registry


__all__ = ["MODULES", "register_all"]

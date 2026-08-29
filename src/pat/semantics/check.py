"""Conferencia de um mapeamento contra o que a fonte realmente publicou.

Responde a pergunta que o `fidelity` nao responde: as linhas que este
mapeamento afirma existir ainda existem, e ainda se chamam o que ele diz?

Duas falhas distintas, contadas em separado porque pedem acoes diferentes:

    NAO_RESOLVE      a conta sumiu, foi renumerada, ou a companhia nao a publica
    ROTULO_MUDOU     a conta esta la com outro nome - candidata a ter mudado de
                     significado, que e a falha perigosa: continua resolvendo,
                     em silencio, para outra coisa
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pat.contracts.semantics import ReportingScope
from pat.semantics import concepts
from pat.semantics.loader import MappingChain
from pat.semantics.resolver import FactResolver, ResolutionFailure

OK = "ok"
NAO_RESOLVE = "nao_resolve"
ROTULO_MUDOU = "rotulo_mudou"


@dataclass(frozen=True)
class BindingCheck:
    concept_id: str
    mapping_id: str
    address: str
    status: str
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.status != OK


def check_chain(
    chain: MappingChain,
    resolver: FactResolver,
    *,
    entity_id: str,
    period_end: date,
    as_of: date,
    scope: ReportingScope = ReportingScope.CONSOLIDATED,
    resolvers: dict | None = None,
) -> list[BindingCheck]:
    """Confere todo binding alcancavel pela cadeia, do mais especifico ao menos.

    `resolvers` permite conferir um mapeamento que cruza taxonomias - o preco
    de mercado ao lado do resultado do arquivamento. Sem ele, a conferencia
    usaria o resolver da cadeia para toda linha e quebraria na primeira que
    aponta para outro regime, dizendo que o mapeamento esta errado quando quem
    esta errado e a conferencia.
    """
    out: list[BindingCheck] = []
    vistos: set[str] = set()

    for mapping in chain.chain:
        for binding in mapping.bindings:
            # Binding sombreado por um mais especifico nao esta em uso.
            if binding.concept_id in vistos:
                continue
            vistos.add(binding.concept_id)

            concept = concepts.get(binding.concept_id)
            for line in binding.lines:
                endereco = line.address.as_str()
                da_linha = (resolvers or {}).get(line.address.taxonomy, resolver)
                outcome = da_linha.resolve(
                    entity_id=entity_id,
                    address=line.address,
                    period_end=period_end,
                    period_kind=concept.period_kind,
                    scope=scope,
                    as_of=as_of,
                )
                if isinstance(outcome, ResolutionFailure):
                    out.append(
                        BindingCheck(
                            binding.concept_id, mapping.mapping_id, endereco,
                            NAO_RESOLVE, outcome.message,
                        )
                    )
                    continue

                esperado = line.address.label_as_reported
                if esperado and outcome.label_as_reported != esperado:
                    out.append(
                        BindingCheck(
                            binding.concept_id, mapping.mapping_id, endereco, ROTULO_MUDOU,
                            f"mapeamento diz {esperado!r}, fonte diz "
                            f"{outcome.label_as_reported!r}",
                        )
                    )
                    continue

                out.append(BindingCheck(binding.concept_id, mapping.mapping_id, endereco, OK))
    return out

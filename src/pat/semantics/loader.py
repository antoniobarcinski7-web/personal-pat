"""Carrega mapeamentos TOML e resolve a cadeia de heranca.

Por que TOML e nao Python: mapeamento e *dado*, revisavel linha a linha num
diff, e nao deve poder executar nada. `tomllib` e stdlib, entao isso nao custa
dependencia nova.

Resolucao, do mais especifico para o menos:

    1. mapeamento da propria empresa   (entity_id bate)
    2. cadeia de `parent` desse mapeamento
    3. familia default da fonte        -> mapping_confirmed=False

O passo 1 exige conteudo: um arquivo de empresa **sem nenhum binding** e
recusado no load. Ele confirmaria a cadeia sem que ninguem tivesse conferido
uma linha, e `mapping_confirmed` passaria a significar "alguem criou um
arquivo" em vez de "alguem conferiu".

O passo 3 nunca e silencioso: quando um numero sai da familia default sem
mapeamento conferido para a empresa, o resultado carrega
`mapping_confirmed=False` ate o fim. Cair no default e razoavel para explorar;
publicar sem saber que caiu, nao.

Reprodutibilidade: cada arquivo entra com o sha256 dos seus bytes, e o
resultado guarda o hash da cadeia inteira. Dois calculos que discordam tem que
ser explicaveis por versao de metrica, data de conhecimento ou hash de
mapeamento - nunca por "alguem editou um TOML".
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pat.contracts.semantics import (
    AccountingFramework,
    BoundLine,
    ConceptBinding,
    Fidelity,
    Mapping,
    TaxonomyId,
)
from pat.semantics import concepts
from pat.semantics.frameworks import get as get_adapter

MAPPINGS_DIR = Path(__file__).parent / "mappings"


class MappingError(ValueError):
    pass


def _require(data: dict, key: str, where: str) -> object:
    if key not in data:
        raise MappingError(f"{where}: campo obrigatorio ausente: {key!r}")
    return data[key]


def _parse_segments(raw, *, where: str):
    """Blocos `[[segment]]` -> membros declarados.

    Campo desconhecido levanta, pela mesma razao dos enderecos: um `member`
    no singular passaria batido e viraria membro ausente na decomposicao, que
    parece problema de dado quando na verdade e erro de digitacao.
    """
    from pat.contracts.semantics import SegmentMember

    permitidos = {"axis", "member_id", "label", "is_elimination", "source_key"}
    saida = []
    for bloco in raw:
        desconhecidos = set(bloco) - permitidos
        if desconhecidos:
            raise MappingError(
                f"{where}: campos desconhecidos em [[segment]]: {sorted(desconhecidos)}. "
                f"Aceitos: {sorted(permitidos)}"
            )
        saida.append(SegmentMember(**bloco))

    ids = [(m.axis, m.member_id) for m in saida]
    if len(ids) != len(set(ids)):
        raise MappingError(f"{where}: membro de segmento repetido")
    return tuple(saida)


def parse_mapping(raw: bytes, *, where: str) -> Mapping:
    """Bytes de um TOML -> `Mapping` validado, com sha256 dos proprios bytes."""
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise MappingError(f"{where}: TOML invalido: {exc}") from exc

    try:
        taxonomy = TaxonomyId(str(_require(data, "taxonomy", where)))
    except ValueError as exc:
        raise MappingError(f"{where}: taxonomia desconhecida: {exc}") from exc
    try:
        framework = AccountingFramework(str(_require(data, "framework", where)))
    except ValueError as exc:
        raise MappingError(f"{where}: framework desconhecido: {exc}") from exc

    adapter = get_adapter(taxonomy)

    bindings: list[ConceptBinding] = []
    for block in data.pop("binding", []):
        concept_id = str(_require(block, "concept_id", where))
        if not concepts.exists(concept_id):
            raise MappingError(
                f"{where}: binding para conceito inexistente {concept_id!r}. "
                "O catalogo de conceitos e a autoridade; adicione-o la primeiro."
            )
        lines_raw = block.get("line") or []
        if not lines_raw:
            raise MappingError(f"{where}: binding de {concept_id} sem nenhuma linha")

        lines: list[BoundLine] = []
        for line_raw in lines_raw:
            line_raw = dict(line_raw)
            sign = int(line_raw.pop("sign", 1))
            try:
                address = adapter.parse_address(line_raw)
            except ValueError as exc:
                raise MappingError(f"{where}: binding de {concept_id}: {exc}") from exc
            lines.append(BoundLine(address=address, sign=sign))

        try:
            fidelity = Fidelity(str(_require(block, "fidelity", where)))
        except ValueError as exc:
            raise MappingError(f"{where}: fidelity invalida em {concept_id}: {exc}") from exc

        bindings.append(
            ConceptBinding(
                concept_id=concept_id,
                lines=tuple(lines),
                fidelity=fidelity,
                equivalence_basis=str(_require(block, "equivalence_basis", where)),
                divergence_note=block.get("divergence_note"),
            )
        )

    entity_id = data.get("entity_id")
    if entity_id and not bindings:
        # Sem esta recusa, um arquivo com so `entity_id` e `parent` marcaria a
        # cadeia como `confirmed=True` sem que ninguem tivesse conferido uma
        # unica linha - e `mapping_confirmed` deixaria de significar "um humano
        # olhou". A confirmacao tem que custar pelo menos um binding escrito.
        raise MappingError(
            f"{where}: mapeamento de empresa sem nenhum binding. Um arquivo que so "
            "declara entity_id e parent confirmaria a cadeia sem conferencia "
            "nenhuma. Escreva ao menos um binding com equivalence_basis, ou apague "
            "o arquivo e deixe a empresa cair na familia default (que sai com "
            "mapping_confirmed=False, como deve)."
        )

    return Mapping(
        mapping_id=str(_require(data, "mapping_id", where)),
        mapping_version=str(_require(data, "mapping_version", where)),
        framework=framework,
        taxonomy=taxonomy,
        jurisdiction=str(_require(data, "jurisdiction", where)),
        source=str(_require(data, "source", where)),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        parent=data.get("parent"),
        entity_id=entity_id,
        is_default_for_source=bool(data.get("is_default_for_source", False)),
        verified_by=data.get("verified_by"),
        verified_against=data.get("verified_against"),
        bindings=tuple(bindings),
        segments=_parse_segments(data.get("segment", []), where=where),
    )


@dataclass(frozen=True)
class MappingChain:
    """A cadeia efetiva para uma empresa, do mais especifico ao menos."""

    chain: tuple[Mapping, ...]
    confirmed: bool

    @property
    def head(self) -> Mapping:
        return self.chain[0]

    def binding_for(self, concept_id: str) -> tuple[ConceptBinding, Mapping] | None:
        """Primeiro binding encontrado na cadeia. Mais especifico vence."""
        for mapping in self.chain:
            if (binding := mapping.binding_for(concept_id)) is not None:
                return binding, mapping
        return None

    @property
    def sha256(self) -> str:
        """Hash da cadeia inteira, na ordem.

        Nao basta o hash do mapeamento da empresa: se a familia herdada mudar,
        os numeros mudam sem que o arquivo da empresa tenha sido tocado.
        """
        material = "|".join(f"{m.mapping_id}:{m.source_sha256}" for m in self.chain)
        return hashlib.sha256(material.encode()).hexdigest()


class MappingSet:
    """Todos os mapeamentos conhecidos, indexados por id e por empresa."""

    def __init__(self, mappings: tuple[Mapping, ...]) -> None:
        self._by_id: dict[str, Mapping] = {}
        for mapping in mappings:
            if mapping.mapping_id in self._by_id:
                raise MappingError(f"mapping_id duplicado: {mapping.mapping_id}")
            self._by_id[mapping.mapping_id] = mapping

        self._by_entity: dict[str, Mapping] = {}
        for mapping in mappings:
            if mapping.entity_id:
                if mapping.entity_id in self._by_entity:
                    raise MappingError(
                        f"duas empresas mapeadas para {mapping.entity_id}: "
                        f"{self._by_entity[mapping.entity_id].mapping_id} e {mapping.mapping_id}"
                    )
                self._by_entity[mapping.entity_id] = mapping

        self._defaults: dict[str, Mapping] = {}
        for mapping in mappings:
            if mapping.is_default_for_source:
                if mapping.source in self._defaults:
                    raise MappingError(f"dois mapeamentos default para a fonte {mapping.source}")
                self._defaults[mapping.source] = mapping

        for mapping in mappings:
            if mapping.parent and mapping.parent not in self._by_id:
                raise MappingError(
                    f"{mapping.mapping_id}: parent inexistente {mapping.parent!r}"
                )
        for mapping in mappings:
            self._chain_from(mapping)  # detecta ciclo no load

    def __len__(self) -> int:
        return len(self._by_id)

    def all(self) -> tuple[Mapping, ...]:
        return tuple(self._by_id[k] for k in sorted(self._by_id))

    def by_id(self, mapping_id: str) -> Mapping | None:
        return self._by_id.get(mapping_id)

    def _chain_from(self, mapping: Mapping) -> tuple[Mapping, ...]:
        chain: list[Mapping] = []
        seen: set[str] = set()
        current: Mapping | None = mapping
        while current is not None:
            if current.mapping_id in seen:
                raise MappingError(
                    f"ciclo de heranca em mapeamentos: {' -> '.join([*seen, current.mapping_id])}"
                )
            seen.add(current.mapping_id)
            chain.append(current)
            current = self._by_id.get(current.parent) if current.parent else None
        return tuple(chain)

    def resolve(self, entity_id: str, *, source: str) -> MappingChain | None:
        """Cadeia efetiva para uma empresa. None se nao ha nem default."""
        if (own := self._by_entity.get(entity_id)) is not None:
            return MappingChain(chain=self._chain_from(own), confirmed=True)
        if (default := self._defaults.get(source)) is not None:
            return MappingChain(chain=self._chain_from(default), confirmed=False)
        return None


def load_dir(path: Path = MAPPINGS_DIR) -> MappingSet:
    """Carrega todo `*.toml` sob `path`, recursivamente e em ordem estavel."""
    files = sorted(path.rglob("*.toml"))
    mappings = tuple(parse_mapping(f.read_bytes(), where=str(f.relative_to(path))) for f in files)
    return MappingSet(mappings)

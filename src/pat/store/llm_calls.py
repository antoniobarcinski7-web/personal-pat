"""Persistencia da procedencia de chamada de modelo.

Fica em `store/` pela mesma razao que `write_manifest` fica: quem calcula nao
grava. A camada de pesquisa produz um `PlanProvenance` puro e entrega; a
decisao de persistir e de quem tem a conexao de escrita. E isso que permite
planejar e executar inteiro em memoria, num teste, sem banco nenhum.

Indice, nao identidade
----------------------
Os bytes moram em `data/llm/`, enderecados por `call_sha256`. Esta tabela
existe para que auditoria seja consulta em vez de varredura de diretorio - e
para ligar chamada a manifesto, ligacao que o cache nao pode fazer.

O cache nao conhece `manifest_id`, e nao e preferencia: `manifest_id` embute
`executed_at`, entao inclui-lo na identidade da chamada faria toda consulta ser
MISS e o cache jamais acertaria. A ligacao existe aqui, onde ela e um fato
sobre uma corrida, e nao ali, onde seria parte de uma chave.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import duckdb

from pat.contracts.research import PlanProvenance

CallKind = Literal["planner", "writer"]

_COLUMNS = (
    "call_sha256",
    "kind",
    "recorded_at",
    "model_id",
    "client_fingerprint",
    "system_prompt_sha256",
    "prompt_sha256",
    "response_sha256",
    "capability_sha256",
    "temperature",
    "max_tokens",
    "called_at",
    "cached",
    "manifest_id",
)

_INSERT = (
    f"INSERT INTO llm_call ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))}) "
    "ON CONFLICT (call_sha256, recorded_at) DO NOTHING"
)


@dataclass(frozen=True)
class LLMCallRow:
    """A chamada como saiu do banco.

    Tipo proprio, e nao `PlanProvenance` reconstruido: a linha carrega
    `call_sha256`, `kind` e `manifest_id`, que nao sao campos do contrato -
    devolver o contrato daria a impressao de que a leitura recupera exatamente
    o que a camada de pesquisa produziu.
    """

    call_sha256: str
    kind: str
    recorded_at: datetime
    model_id: str
    client_fingerprint: str
    system_prompt_sha256: str
    prompt_sha256: str
    response_sha256: str
    capability_sha256: str
    temperature: Decimal | None
    max_tokens: int
    called_at: datetime
    cached: bool
    manifest_id: str | None
    """Nulo quando a chamada aconteceu e nenhum manifesto foi produzido - um
    plano recusado pelo validador e o caso concreto (M-3). Nulo nao e ausencia
    de informacao: e a informacao de que nada executou."""


def write_call(
    conn: duckdb.DuckDBPyConnection,
    provenance: PlanProvenance,
    *,
    call_sha256: str,
    kind: CallKind,
    manifest_id: str | None = None,
    recorded_at: datetime | None = None,
) -> bool:
    """Grava a chamada. True se entrou, False se ja estava la.

    `manifest_id` e opcional e o default e nulo, e nao o contrario: a chamada
    acontece antes de existir manifesto, e forcar quem chama a inventar um
    valor seria fabricar procedencia.

    `recorded_at` e o instante em que ESTA linha foi gravada, distinto de
    `called_at` (quando o modelo respondeu). Os dois sao iguais numa chamada
    fresca e diferentes num acerto de cache - e e essa diferenca que permite
    contar quantas vezes uma resposta gravada foi reutilizada, sem que o
    `called_at` original seja perdido.
    """
    before = count(conn)
    conn.execute(
        _INSERT,
        [
            call_sha256,
            kind,
            recorded_at or datetime.now(UTC),
            provenance.model_id,
            provenance.client_fingerprint,
            provenance.system_prompt_sha256,
            provenance.prompt_sha256,
            provenance.response_sha256,
            provenance.capability_sha256,
            provenance.temperature,
            provenance.max_tokens,
            provenance.called_at,
            provenance.cached,
            manifest_id,
        ],
    )
    return count(conn) > before


def _to_row(row: tuple) -> LLMCallRow:
    return LLMCallRow(*row)


def read_calls(
    conn: duckdb.DuckDBPyConnection, manifest_id: str
) -> list[LLMCallRow]:
    """As chamadas de uma corrida, na ordem em que foram gravadas."""
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM llm_call WHERE manifest_id = ? "
        "ORDER BY recorded_at ASC, call_sha256 ASC",
        [manifest_id],
    ).fetchall()
    return [_to_row(row) for row in rows]


def orphan_calls(conn: duckdb.DuckDBPyConnection, limit: int = 20) -> list[LLMCallRow]:
    """Chamadas sem manifesto: custaram dinheiro e nao viraram corrida.

    E a consulta que torna o M-3 util em vez de so correto - sem ela, o custo
    de planos recusados seria invisivel ate a fatura chegar.
    """
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM llm_call WHERE manifest_id IS NULL "
        "ORDER BY recorded_at DESC, call_sha256 ASC LIMIT ?",
        [limit],
    ).fetchall()
    return [_to_row(row) for row in rows]


def count(conn: duckdb.DuckDBPyConnection) -> int:
    return conn.execute("SELECT COUNT(*) FROM llm_call").fetchone()[0]

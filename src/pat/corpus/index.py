"""Indice lexico do corpus: tokenizacao versionada e BM25 deterministico.

Por que um indice invertido explicito, e nao a extensao FTS do DuckDB
---------------------------------------------------------------------
Tres razoes, na ordem em que importam:

1. **Reproduzibilidade.** Mesma consulta + mesmo `index_version` + mesmo
   `as_of` tem que devolver exatamente os mesmos `unit_id`, na mesma ordem,
   hoje e daqui a um ano. Uma extensao carregada em runtime traz a versao que
   estiver disponivel na maquina, e o ranking passa a depender disso.

2. **Auditabilidade.** "Por que este trecho voltou em primeiro" precisa ter
   resposta. Aqui a resposta e uma tabela de tokens que da para consultar e um
   escore de dez linhas que da para conferir a mao.

3. **Nenhuma dependencia nova.** `INSTALL fts` baixa binario da internet na
   primeira execucao. Um sistema de research local que precisa de rede para
   *indexar* o que ja esta em disco trocaria uma garantia por conveniencia.

O indice e derivado e descartavel: apagar a tabela e reconstruir a partir das
unidades nao perde nada. As unidades e que sao a fonte, e o bronze e que e a
fonte das unidades.

O escore nao e grandeza financeira
-----------------------------------
`relevance` ordena texto. Nao tem unidade, nao tem moeda, nunca entra num
calculo e nunca aparece na prosa. Ele e `Decimal` pela mesma razao que todo o
resto do sistema e - float nao reproduz entre plataformas, e um ranking que
muda de ordem conforme a maquina nao e um ranking auditavel.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from decimal import Decimal, localcontext

import duckdb

__all__ = [
    "INDEX_VERSION",
    "SCORING_VERSION",
    "TOKENIZER_VERSION",
    "bm25",
    "build_index",
    "index_status",
    "tokenize",
]

TOKENIZER_VERSION = "1"
SCORING_VERSION = "bm25/v1"
INDEX_VERSION = f"lexical/v1+tok{TOKENIZER_VERSION}+{SCORING_VERSION}"
"""Viaja em todo `EvidenceResult`.

Mudar a lista de stopwords, a regra de token ou as constantes do BM25 muda o
conjunto devolvido sem que a pergunta tenha mudado - entao muda a versao, do
mesmo jeito que mudar a aritmetica de uma metrica muda a versao dela."""

_K1 = Decimal("1.2")
_B = Decimal("0.75")
_SCORING_PRECISION = 28
"""Precisao fixa do contexto decimal durante o escore.

Fixa e nao herdada: se o escore usasse a precisao ambiente, um processo que
tivesse mexido no contexto por outro motivo produziria outro ranking, e o
`EvidenceResult` deixaria de ser reproduzivel sem que nada visivel tivesse
mudado."""

MIN_TOKEN_CHARS = 2
_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")

_STOPWORDS = frozenset(
    """
    a ao aos as até com como da das de dela delas dele deles do dos e em entre era eram
    essa essas esse esses esta estas este estes eu foi foram há isso isto já lhe lhes
    mais mas me mesmo meu meus muito na nas nem no nos nós num numa o os ou para pela
    pelas pelo pelos por qual quando que quem se sem ser seu seus só sua suas também te
    tem tém ter teu teus tu um uma umas uns você vocês
    """.split()
)
"""Stopwords do portugues, versionadas junto do tokenizador.

Deliberadamente curta e sem negacao: "nao" continua sendo token. Numa base de
research, "nao recorrente" e "sem impacto" sao justamente o tipo de trecho que
importa, e um tokenizador que descartasse a negacao casaria a frase com o
oposto do que ela diz."""


def tokenize(text: str) -> tuple[str, ...]:
    """Texto -> tokens, deterministicamente.

    Minusculas, acento removido por decomposicao NFD, quebra em tudo que nao
    for letra ASCII ou digito. Remover acento e o que faz "produção" casar com
    "producao" - digitar sem acento e o caso normal numa linha de comando, e
    exigir acento faria a busca falhar por teclado.

    Digitos sobrevivem ao tokenizador de proposito: "3T24" e "2024" sao termos
    de busca legitimos. Isso NAO abre porta para numero de documento virar
    insumo de calculo - o que casa aqui e o token da consulta com o token do
    texto; nenhum valor e lido como quantidade em lugar nenhum deste modulo.
    """
    folded = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    return tuple(
        token
        for token in _TOKEN_SPLIT.split(stripped)
        if len(token) >= MIN_TOKEN_CHARS and token not in _STOPWORDS
    )


def term_frequencies(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in tokenize(text):
        counts[token] = counts.get(token, 0) + 1
    return counts


def build_index(
    conn: duckdb.DuckDBPyConnection, *, unit_ids: Sequence[str] | None = None
) -> int:
    """(Re)constroi o indice das unidades ainda nao indexadas nesta versao.

    Idempotente: unidade ja indexada sob o mesmo `INDEX_VERSION` e ignorada.
    Indexar sob versao nova nao apaga a anterior - a consulta escolhe a versao
    que quer, e um `EvidenceResult` antigo continua explicavel.
    """
    if unit_ids is None:
        rows = conn.execute(
            """
            SELECT u.unit_id, u.text
            FROM document_unit u
            WHERE NOT EXISTS (
                SELECT 1 FROM document_unit_token t
                WHERE t.unit_id = u.unit_id AND t.index_version = ?
            )
            ORDER BY u.unit_id
            """,
            [INDEX_VERSION],
        ).fetchall()
    else:
        if not unit_ids:
            return 0
        placeholders = ", ".join("?" * len(unit_ids))
        rows = conn.execute(
            f"""
            SELECT u.unit_id, u.text
            FROM document_unit u
            WHERE u.unit_id IN ({placeholders})
              AND NOT EXISTS (
                SELECT 1 FROM document_unit_token t
                WHERE t.unit_id = u.unit_id AND t.index_version = ?
            )
            ORDER BY u.unit_id
            """,
            [*unit_ids, INDEX_VERSION],
        ).fetchall()

    payload: list[tuple[str, str, str, int, int]] = []
    for unit, text in rows:
        counts = term_frequencies(text)
        length = sum(counts.values())
        for token, frequency in sorted(counts.items()):
            payload.append((unit, INDEX_VERSION, token, frequency, length))

    if payload:
        conn.executemany(
            "INSERT INTO document_unit_token "
            "(unit_id, index_version, token, tf, unit_length) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (unit_id, index_version, token) DO NOTHING",
            payload,
        )
    return len(rows)


def index_status(conn: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """(unidades indexadas nesta versao, unidades existentes)."""
    indexed = conn.execute(
        "SELECT COUNT(DISTINCT unit_id) FROM document_unit_token WHERE index_version = ?",
        [INDEX_VERSION],
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM document_unit").fetchone()[0]
    return int(indexed), int(total)


def bm25(
    *,
    term_hits: Iterable[tuple[str, int]],
    unit_length: int,
    average_length: Decimal,
    documents_in_scope: int,
    document_frequencies: dict[str, int],
) -> Decimal:
    """BM25 classico, em `Decimal`, com precisao fixa.

    `term_hits` sao pares (token, frequencia na unidade). O IDF usa a variante
    com `+1` dentro do log, que nunca fica negativa - a variante sem ela da
    escore negativo para termo presente em mais da metade do escopo, e um
    trecho que casa o termo procurado ficando atras de um que nao casa e o
    tipo de comportamento que ninguem consegue auditar depois.
    """
    with localcontext() as context:
        context.prec = _SCORING_PRECISION
        total = Decimal(0)
        norm = (
            Decimal(1) - _B + _B * (Decimal(unit_length) / average_length)
            if average_length > 0
            else Decimal(1)
        )
        for token, frequency in term_hits:
            df = Decimal(document_frequencies.get(token, 0))
            n = Decimal(documents_in_scope)
            idf = ((n - df + Decimal("0.5")) / (df + Decimal("0.5")) + Decimal(1)).ln()
            tf = Decimal(frequency)
            total += idf * (tf * (_K1 + Decimal(1))) / (tf + _K1 * norm)
        return +total

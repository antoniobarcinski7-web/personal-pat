"""Persistencia da auditoria de um turno: `research_run` e `llm_call`.

A conversa nao e um universo paralelo. Um turno grava exatamente nas mesmas
tabelas que `pat plan` e `pat ask --writer` gravam, com as mesmas chaves - e e
por isso que todo `manifest_id` mostrado na tela e consultavel por
`pat runs --research <id>`.

Duplicacao deliberada em relacao a `cli.py:_record_manifest` e
`cli.py:_record_call`: aquelas funcoes recebem `args` do argparse e sao o
caminho que os testes de M3/M4 exercitam. Unificar hoje mexeria em `cmd_ask` e
`cmd_plan` para ganhar uma funcao a menos; e item de M4.2, com a suite verde
como rede. O que NAO pode divergir e a chave da chamada - `call_sha256_of` mora
num lugar so, e as duas pontas a chamam.
"""

from __future__ import annotations

from pat.config import Paths
from pat.contracts.research import PlanProvenance, ResearchRunManifest
from pat.store.db import connect, migrate


def record_planner_call(paths: Paths, provenance: PlanProvenance, *, fingerprint: str) -> None:
    """Grava a chamada do planejador, com `manifest_id` nulo.

    Nulo e o caso NORMAL aqui, e nao uma lacuna: quando o plano e recusado nada
    executa e manifesto nenhum existe. E precisamente a situacao que fez a
    coluna ser nulavel - a chamada custou dinheiro e tem que ter rastro mesmo
    assim. Ela sai em `orphan_calls`.
    """
    _write_call(paths, provenance, fingerprint=fingerprint, kind="planner", manifest_id=None)


def record_turn(
    paths: Paths,
    manifest: ResearchRunManifest | None,
    writer_provenance: PlanProvenance | None,
    *,
    fingerprint: str,
) -> bool | None:
    """Grava manifesto e, se houve escritor, a chamada dele ligada ao manifesto.

    Uma conexao de escrita so para os dois: abrir duas seria pagar o lock do
    DuckDB duas vezes por turno para gravar dois registros da mesma corrida.
    """
    if manifest is None:
        return None

    from pat.store.llm_calls import write_call
    from pat.store.research import write_manifest

    conn = connect(paths.warehouse)
    try:
        migrate(conn)  # as duas tabelas sao aditivas: warehouse antigo migra sozinho
        gravado = write_manifest(conn, manifest)
        if writer_provenance is not None:
            write_call(
                conn,
                writer_provenance,
                call_sha256=_key(writer_provenance, fingerprint),
                kind="writer",
                manifest_id=manifest.manifest_id,
            )
        return gravado
    finally:
        conn.close()


def _write_call(
    paths: Paths,
    provenance: PlanProvenance,
    *,
    fingerprint: str,
    kind: str,
    manifest_id: str | None,
) -> None:
    from pat.store.llm_calls import write_call

    conn = connect(paths.warehouse)
    try:
        migrate(conn)
        write_call(
            conn,
            provenance,
            call_sha256=_key(provenance, fingerprint),
            kind=kind,
            manifest_id=manifest_id,
        )
    finally:
        conn.close()


def _key(provenance: PlanProvenance, fingerprint: str) -> str:
    """A identidade da chamada, do mesmo jeito que o CLI a calcula.

    `prompt_sha256` ja esta na procedencia; remontar o `LLMRequest` para chegar
    a mesma chave seria um segundo caminho ate o mesmo valor, e o dia em que os
    dois divergissem o sintoma seria uma chamada indexada sob uma chave que nao
    existe no cache.
    """
    from pat.research.llm.cache import call_sha256_of

    return call_sha256_of(provenance.prompt_sha256, fingerprint)

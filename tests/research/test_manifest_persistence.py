"""Persistencia do manifesto de pesquisa em `research_run`.

Um manifesto que so e impresso na tela prova a corrida para quem estava
olhando. A tabela e o que permite, meses depois, responder "de onde veio este
numero no relatorio de agosto" - que e a pergunta que a Fase 3 existe para
poder responder.

O que estes testes defendem:

1. o que entra e o que sai sao a mesma coisa, tuplas na mesma ordem;
2. regravar e no-op, e corrida antiga nunca e reescrita;
3. a tabela nasce em warehouse antigo sem migracao manual;
4. corrida sem saida disponivel tambem e registrada - recusa e auditoria.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pat.contracts.research import ResearchRunManifest
from pat.store.db import connect, migrate
from pat.store.research import count, read_manifest, recent_manifests, write_manifest

QUANDO = datetime(2025, 6, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def banco(tmp_path):
    """Esquema vazio.

    Nao usa o fixture `warehouse` deste pacote de proposito: aquele carrega o
    GPA inteiro, e persistir manifesto nao depende de haver um unico fato.
    """
    conn = connect(tmp_path / "runs.duckdb")
    migrate(conn)
    yield conn
    conn.close()


def _manifesto(**overrides) -> ResearchRunManifest:
    base = dict(
        manifest_id="a" * 64,
        question_id="b" * 64,
        plan_id="c" * 64,
        capability_sha256="d" * 64,
        as_of=date(2025, 6, 30),
        executed_at=QUANDO,
        outputs_available=True,
        result_ids=("e" * 64, "f" * 64),
        metric_versions=("margem_ebitda@v1", "receita_liquida@v1"),
        mapping_sha256s=("1" * 64,),
        fact_ids=("fato-1", "fato-2", "fato-3"),
        pat_version="0.1.0",
        python_version="3.14.5",
        git_sha="3cf93d3",
    )
    return ResearchRunManifest(**(base | overrides))


def test_o_que_entra_e_o_que_sai_sao_a_mesma_corrida(banco):
    manifesto = _manifesto()

    assert write_manifest(banco, manifesto) is True

    row = read_manifest(banco, manifesto.manifest_id)
    assert row.question_id == manifesto.question_id
    assert row.plan_id == manifesto.plan_id
    assert row.capability_sha256 == manifesto.capability_sha256
    assert row.as_of == manifesto.as_of
    assert row.executed_at == QUANDO
    assert row.outputs_available is True
    assert row.pat_version == "0.1.0"
    assert row.python_version == "3.14.5"
    assert row.git_sha == "3cf93d3"


def test_a_ordem_das_tuplas_sobrevive_ao_banco(banco):
    """`result_ids` segue a ordem dos passos do plano, e ordem e significado:
    a citacao do primeiro output tem que continuar sendo o primeiro."""
    manifesto = _manifesto(
        result_ids=("9" * 64, "1" * 64, "5" * 64),
        fact_ids=("z-ultimo", "a-primeiro"),
    )
    write_manifest(banco, manifesto)

    row = read_manifest(banco, manifesto.manifest_id)
    assert row.result_ids == ("9" * 64, "1" * 64, "5" * 64)
    assert row.fact_ids == ("z-ultimo", "a-primeiro")
    assert row.metric_versions == ("margem_ebitda@v1", "receita_liquida@v1")


def test_regravar_o_mesmo_manifesto_e_no_op(banco):
    """Append-only: um `manifest_id` ja gravado nao e reescrito."""
    manifesto = _manifesto()

    assert write_manifest(banco, manifesto) is True
    assert write_manifest(banco, manifesto) is False
    assert count(banco) == 1


def test_corrida_antiga_nao_e_sobrescrita_por_uma_nova(banco):
    """Duas execucoes do mesmo plano sao corridas distintas e coexistem - a
    mesma regra do gold, e pelo mesmo motivo."""
    primeira = _manifesto(manifest_id="a" * 64, executed_at=QUANDO)
    segunda = _manifesto(
        manifest_id="b" * 64, executed_at=datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
    )
    write_manifest(banco, primeira)
    write_manifest(banco, segunda)

    assert count(banco) == 2
    # Mesmo plano nas duas: o que muda e a identidade da corrida.
    assert {row.plan_id for row in recent_manifests(banco)} == {"c" * 64}
    # Da mais recente para a mais antiga.
    assert [row.manifest_id for row in recent_manifests(banco)] == ["b" * 64, "a" * 64]


def test_corrida_sem_saida_tambem_e_registrada(banco):
    """Uma recusa e uma corrida: a auditoria de por que nao houve resposta vale
    tanto quanto a de por que houve."""
    write_manifest(banco, _manifesto(outputs_available=False, result_ids=(), fact_ids=()))

    (row,) = recent_manifests(banco)
    assert row.outputs_available is False
    assert row.result_ids == ()
    assert row.fact_ids == ()


def test_manifesto_desconhecido_devolve_nulo(banco):
    assert read_manifest(banco, "nao-existe") is None
    assert recent_manifests(banco) == []


def test_git_sha_ausente_e_nulo_e_nao_string_vazia(banco):
    """`None` e "nao sei"; "" seria um sha vazio. A diferenca importa numa
    tabela cuja finalidade e provar reprodutibilidade."""
    write_manifest(banco, _manifesto(git_sha=None))

    assert read_manifest(banco, "a" * 64).git_sha is None


def test_a_tabela_e_aditiva_em_warehouse_antigo(tmp_path):
    """`migrate()` e CREATE TABLE IF NOT EXISTS: um warehouse da Fase 1 ganha
    `research_run` sem migracao manual e sem perder nada."""
    caminho = tmp_path / "antigo.duckdb"
    antigo = connect(caminho)
    migrate(antigo)
    antigo.execute("CREATE TABLE marcador (x INTEGER)")
    antigo.close()

    novo = connect(caminho)
    migrate(novo)  # roda o esquema de novo, como faz `pat init`
    try:
        assert write_manifest(novo, _manifesto()) is True
        assert novo.execute("SELECT COUNT(*) FROM marcador").fetchone()[0] == 0
    finally:
        novo.close()


def test_persistencia_nao_entra_no_nucleo_deterministico():
    """`pat.research` nao importa `pat.store`: quem calcula nao grava.

    E o que permite executar um plano inteiro em memoria, sem banco - e o que
    impede a camada de pesquisa de ganhar, de fininho, uma porta de escrita.
    """
    import ast
    from pathlib import Path

    research = Path(__file__).resolve().parents[2] / "src" / "pat" / "research"
    for path in research.rglob("*.py"):
        arvore = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(arvore):
            nomes: list[str] = []
            if isinstance(node, ast.Import):
                nomes = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                nomes = [node.module]
            for nome in nomes:
                assert not nome.startswith("pat.store"), (
                    f"{path.name} importa {nome}: a camada de pesquisa passou a gravar"
                )


@pytest.mark.parametrize("campo", ["result_ids", "metric_versions", "mapping_sha256s", "fact_ids"])
def test_tuplas_vazias_sao_preservadas_como_vazias(banco, campo):
    """Lista vazia nao pode virar NULL: "nao houve" e diferente de "nao sei"."""
    write_manifest(banco, _manifesto(**{campo: ()}))

    assert getattr(read_manifest(banco, "a" * 64), campo) == ()

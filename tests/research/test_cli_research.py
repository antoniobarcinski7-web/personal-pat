"""A linha de comando do Milestone 1.

Duas coisas para provar: que o caminho deterministico roda de ponta a ponta a
partir de um arquivo, e que o plano congelado em disco continua sendo o mesmo
plano - se alguem mexer na gramatica sem migrar o arquivo, o `plan_id` muda e
este teste quebra.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pat.cli import main
from pat.research import load_envelope
from pat.research.canonical import plan_id

PLANO = Path(__file__).parent / "plans" / "gpa_margin_fy24_fy23.json"
PLAN_ID = "ebada2ffa3f7f0d392441e660216ac400660a03f1de7f9cd0eb85d7705eb7bb8"


def test_o_plano_congelado_tem_identidade_estavel():
    """O arquivo do golden e o plano do fixture sao o mesmo plano."""
    from tests.research.conftest import make_plan, make_question

    envelope = load_envelope(PLANO.read_bytes())
    assert plan_id(envelope.plan) == PLAN_ID
    assert plan_id(make_plan(make_question())) == PLAN_ID


def test_capability_roda_sem_warehouse(tmp_path, capsys):
    """Estado normal de quem acabou de clonar: instrucao, nao stack trace."""
    assert main(["--home", str(tmp_path), "capability"]) == 0
    saida = capsys.readouterr().out
    assert "capability_sha256" in saida
    assert "ebitda@v1" in saida
    assert "pat build" in saida, "sem entidades, precisa dizer o que fazer"


def test_capability_json_e_canonico(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "capability", "--json"]) == 0
    saida = capsys.readouterr().out.strip()
    assert saida.startswith("{") and '", "' not in saida


def test_capability_nao_vaza_valor_financeiro(tmp_path, capsys):
    """O snapshot diz o que existe, nunca quanto vale."""
    main(["--home", str(tmp_path), "capability", "--json"])
    saida = capsys.readouterr().out
    for numero in ("19250000", "1136000", "1813000", "0.100939"):
        assert numero not in saida


def test_ask_exige_plano_no_milestone_1(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["--home", str(tmp_path), "ask"])


def test_ask_sem_warehouse_orienta(tmp_path, capsys):
    codigo = main(["--home", str(tmp_path), "ask", "--plan-file", str(PLANO)])
    assert codigo == 1
    assert "pat init" in capsys.readouterr().err


# -- com warehouse -----------------------------------------------------------


@pytest.fixture
def home(tmp_path, warehouse):
    """PAT_HOME apontando para o warehouse do GPA ja materializado."""
    import shutil

    from pat.config import resolve_paths

    warehouse.conn.close()
    paths = resolve_paths(tmp_path / "home").ensure()
    shutil.copy(tmp_path / "research.duckdb", paths.warehouse)
    shutil.copytree(tmp_path / "bronze", paths.bronze, dirs_exist_ok=True)
    return str(paths.home)


def test_dry_run_valida_e_nao_executa(home, capsys):
    codigo = main(["--home", home, "ask", "--plan-file", str(PLANO), "--dry-run"])
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "VALIDACAO  ok" in saida
    assert "nao executado" in saida
    assert "RESPOSTA" not in saida
    assert "MANIFESTO" not in saida


def test_ask_executa_o_caminho_deterministico(home, capsys):
    codigo = main(["--home", home, "ask", "--plan-file", str(PLANO)])
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "RESPOSTA" in saida
    assert "CITACOES" in saida
    assert "MANIFESTO" in saida
    # As duas margens do fixture, formatadas pelo renderer.
    assert "10.09%" in saida
    assert "3.89%" in saida
    assert "(sem ressalvas)" in saida
    assert "margem_ebitda@v1" in saida


# -- a corrida fica registrada -----------------------------------------------


def _manifest_id(saida: str) -> str:
    for linha in saida.splitlines():
        if "manifest_id" in linha:
            return linha.split()[-1]
    raise AssertionError("manifest_id nao apareceu na saida")


def test_ask_registra_a_corrida_no_warehouse(home, capsys):
    """A prova de que o numero existiu tem que sobreviver ao terminal."""
    main(["--home", home, "ask", "--plan-file", str(PLANO)])
    manifest_id = _manifest_id(capsys.readouterr().out)

    assert main(["--home", home, "runs", "--research"]) == 0
    listagem = capsys.readouterr().out

    assert manifest_id in listagem
    assert PLAN_ID[:12] in listagem
    assert "margem_ebitda@v1" in listagem
    assert "ok" in listagem


def test_runs_research_aceita_um_manifest_id(home, capsys):
    main(["--home", home, "ask", "--plan-file", str(PLANO)])
    manifest_id = _manifest_id(capsys.readouterr().out)

    assert main(["--home", home, "runs", "--research", manifest_id]) == 0
    assert manifest_id in capsys.readouterr().out

    assert main(["--home", home, "runs", "--research", "f" * 64]) == 1
    assert "desconhecido" in capsys.readouterr().err


def test_runs_research_sem_corrida_nenhuma_orienta(home, capsys):
    assert main(["--home", home, "runs", "--research"]) == 0
    assert "pat ask" in capsys.readouterr().out


def test_dry_run_nao_registra_corrida(home, capsys):
    """Nada foi executado, entao nao ha corrida para auditar."""
    main(["--home", home, "ask", "--plan-file", str(PLANO), "--dry-run"])
    capsys.readouterr()

    main(["--home", home, "runs", "--research"])
    assert "nenhuma corrida" in capsys.readouterr().out


def test_reexecutar_o_mesmo_plano_gera_duas_corridas(home, capsys):
    """Mesmo plano, mesmos numeros, corridas distintas: `executed_at` entra no
    `manifest_id` de proposito. O que tem que bater entre elas sao os
    `result_id`, e o teste de determinismo cuida disso."""
    main(["--home", home, "ask", "--plan-file", str(PLANO)])
    primeira = _manifest_id(capsys.readouterr().out)
    main(["--home", home, "ask", "--plan-file", str(PLANO)])
    segunda = _manifest_id(capsys.readouterr().out)

    main(["--home", home, "runs", "--research"])
    listagem = capsys.readouterr().out

    assert primeira != segunda
    assert primeira in listagem and segunda in listagem
    # O plano e o mesmo nas duas.
    assert listagem.count(PLAN_ID[:12]) == 2


def test_o_warehouse_continua_legivel_depois_de_gravar(home, capsys):
    """A gravacao abre uma conexao de escrita propria; se ela vazasse o lock,
    a proxima leitura falharia."""
    main(["--home", home, "ask", "--plan-file", str(PLANO)])
    capsys.readouterr()

    assert main(["--home", home, "capability"]) == 0
    assert "CIA BRASILEIRA DE DISTRIBUICAO" in capsys.readouterr().out

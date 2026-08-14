"""Comandos de linha da Fase 2.

Os tres primeiros nao tocam o warehouse - listam o que o sistema *sabe*, nao o
que ele calculou. Sao a documentacao executavel do catalogo, e o caminho por
onde alguem descobre que conceito existe antes de escrever um mapeamento.
"""

from __future__ import annotations

import pytest

from pat.cli import main


@pytest.mark.parametrize("comando", ["concepts", "metrics", "mappings"])
def test_comandos_de_catalogo_rodam_sem_warehouse(comando, capsys):
    assert main([comando]) == 0
    saida = capsys.readouterr().out
    assert saida.strip()


def test_concepts_mostra_a_convencao_de_sinal(capsys):
    """Sem ela, quem escreve mapeamento nao tem como saber que sinal usar."""
    main(["concepts"])
    saida = capsys.readouterr().out
    assert "d_and_a_pnl" in saida
    assert "d_and_a_retained" in saida
    assert "positivo = despesa" in saida


def test_metrics_mostra_versao_e_dependencia_pinada(capsys):
    main(["metrics"])
    saida = capsys.readouterr().out
    assert "ebitda@v1" in saida
    assert "ebit@v1, d_and_a@v1" in saida


def test_mappings_destaca_binding_aproximado(capsys):
    """A aproximacao da familia default precisa saltar aos olhos na listagem."""
    main(["mappings"])
    saida = capsys.readouterr().out
    assert "cvm.plano_padronizado/nao_financeiro" in saida
    assert "approximate" in saida


def test_metric_sem_dados_falha_dizendo_o_que_fazer(capsys, tmp_path):
    codigo = main(
        [
            "--home", str(tmp_path),
            "metric", "ebitda@v1",
            "--cod-cvm", "14826",
            "--period-end", "2023-12-31",
            "--as-of", "2024-06-30",
        ]
    )
    assert codigo == 1
    assert "pat build" in capsys.readouterr().err

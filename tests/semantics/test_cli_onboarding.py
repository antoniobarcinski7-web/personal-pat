"""O caminho de ONBOARDING de uma companhia, pela linha de comando.

O README define tres passos para adicionar uma empresa:

    pat concepts                    que conceitos existem
    pat accounts --...              o que a empresa publicou; um humano escolhe
    pat mapping-check --...         os bindings resolvem?

Esses comandos nao tinham teste nenhum, e `pat accounts` estava QUEBRADO: a
M5.6 trocou a chave de `cod_cvm` por `entity_id` em `AsOf.accounts`, e a
chamada na CLI continuou passando `cod_cvm=` - `TypeError` em toda invocacao,
nas duas jurisdicoes. A suite inteira passava porque nada exercia o comando.

E a classe de falha mais cara possivel: o comando quebrado e justamente o que
alguem roda no PRIMEIRO minuto de contato com uma companhia nova. Estes testes
existem para que o caminho de entrada tenha o mesmo cuidado que o de calculo.
"""

from __future__ import annotations

import pytest

from pat.cli import main
from pat.store.bronze import BronzeStore
from pat.store.db import connect, migrate
from pat.config import resolve_paths
from tests.semantics import golden_gpa as gpa
from tests.semantics.conftest import load_zips

COD_CVM_GPA = "14826"
PERIOD_END = "2023-12-31"
AS_OF = "2025-06-30"


@pytest.fixture
def home(tmp_path):
    """`PAT_HOME` com o GPA ja no gold, pelo caminho de producao."""
    paths = resolve_paths(tmp_path).ensure()
    conn = connect(paths.warehouse)
    migrate(conn)
    load_zips(conn, BronzeStore(paths.bronze), gpa.zips())
    conn.close()
    return tmp_path


def _run(home, *args) -> int:
    return main(["--home", str(home), *args])


# -- pat accounts -------------------------------------------------------------


def test_accounts_lista_o_plano_de_contas_publicado(home, capsys):
    """O REGRESSION TEST do bug: antes disto, `TypeError`.

    Nao confere valor - confere que o comando roda e mostra a linha que um
    humano precisa ver para escrever o binding. O valor certo e assunto dos
    golden tests.
    """
    codigo = _run(
        home, "accounts",
        "--cod-cvm", COD_CVM_GPA,
        "--statement", "DRE",
        "--period-end", PERIOD_END,
        "--as-of", AS_OF,
    )
    assert codigo == 0

    saida = capsys.readouterr().out
    assert "3.01" in saida, "a receita liquida tem que aparecer para ser escolhida"
    assert "contas." in saida
    assert "equivalence_basis" in saida, (
        "o comando existe para levar a escrever o binding; sem essa instrucao "
        "ele vira uma listagem que nao diz o que fazer com ela"
    )


def test_accounts_identifica_a_empresa_pelo_entity_id(home, capsys):
    """O cabecalho nao pode imprimir o identificador LOCAL.

    Imprimir `args.cod_cvm` sairia como `None` para quem entrou por `--cik` -
    e um cabecalho que diz `(None)` e pior que um sem identificador nenhum.
    """
    _run(
        home, "accounts",
        "--cod-cvm", COD_CVM_GPA,
        "--statement", "DRE",
        "--period-end", PERIOD_END,
        "--as-of", AS_OF,
    )
    saida = capsys.readouterr().out
    assert "br:cnpj:" in saida
    assert "(None)" not in saida


def test_accounts_sem_identificador_pede_um(home, capsys):
    """Nenhum dos dois e `required` no argparse; quem cobra e a porta unica."""
    codigo = _run(
        home, "accounts",
        "--statement", "DRE",
        "--period-end", PERIOD_END,
        "--as-of", AS_OF,
    )
    assert codigo == 1
    assert "--cod-cvm ou --cik" in capsys.readouterr().err


# -- as duas jurisdicoes entram pela mesma porta ------------------------------


@pytest.mark.parametrize(
    "comando",
    [
        ["accounts", "--statement", "DRE"],
        ["mapping-check"],
        ["metric", "ebitda@v1"],
    ],
)
def test_os_comandos_de_onboarding_aceitam_cik(home, capsys, comando):
    """`--cik` existe em todo comando do caminho, e nao so em `company`.

    Ate esta mudanca, `metric`, `accounts` e `mapping-check` tinham
    `--cod-cvm` como `required=True`: uma companhia americana nao tinha como
    ser consultada por eles, apesar de a M5.6 ter trazido a segunda
    jurisdicao inteira. A entidade nao existe neste warehouse, entao a
    resposta certa e a recusa NOMEADA - o que importa aqui e que o argumento
    seja aceito e chegue na porta unica.
    """
    codigo = _run(
        home, *comando,
        "--cik", "50863",
        "--period-end", PERIOD_END,
        "--as-of", AS_OF,
    )
    assert codigo == 1
    erro = capsys.readouterr().err
    assert "cik=50863" in erro
    assert "nenhuma entidade conhecida" in erro


@pytest.mark.parametrize("comando", ["docs", "evidence"])
def test_os_comandos_de_corpus_aceitam_cik(home, capsys, comando):
    """`docs` e `evidence` liam a entidade por um SEGUNDO caminho.

    `_entity_for_cod_cvm` fixava `cik=None`, o que fazia a promessa de "porta
    unica" do `_resolve_entity_arg` valer so para metade dos comandos.
    """
    extra = ["--query", "receita"] if comando == "evidence" else []
    codigo = _run(home, comando, "--cik", "50863", *extra, "--as-of", AS_OF)
    assert codigo == 1
    assert "cik=50863" in capsys.readouterr().err


# -- pat mapping-check --------------------------------------------------------


def test_mapping_check_confere_os_bindings_do_gpa(home, capsys):
    """Sai 1, e isso e o comportamento CERTO.

    O fixture golden do GPA transcreve as linhas que a Fase 2 precisava; ele
    nao tem `3.11.01`, que so passou a ser exigida quando `lucro_liquido@v1`
    entrou. O comando reporta o binding que nao resolve, com nome e endereco,
    em vez de omiti-lo - que e exatamente para isso que ele existe. Um
    `mapping-check` que saisse 0 escondendo um binding morto seria pior que
    nao existir.
    """
    codigo = _run(
        home, "mapping-check",
        "--cod-cvm", COD_CVM_GPA,
        "--period-end", PERIOD_END,
        "--as-of", AS_OF,
    )
    assert codigo == 1

    saida = capsys.readouterr().out
    assert "br:cnpj:" in saida
    assert "(None)" not in saida, "o cabecalho nao pode imprimir o id local cru"
    assert "cadeia" in saida
    assert "[ok  ] revenue_net" in saida
    assert "net_income_controlling" in saida
    assert "nao_resolve" in saida


# -- a fonte segue a jurisdicao, e nao um literal ------------------------------


def test_a_fonte_default_segue_a_jurisdicao_da_entidade(home):
    """`_default_source_for` e o que impede uma companhia americana sem
    mapeamento proprio de ser avaliada contra a familia da CVM.

    O mapeamento proprio sempre vence a familia, entao a Intel funcionava por
    SORTE - o caso que quebrava era a companhia sem TOML ainda, que e
    exatamente o estado de toda empresa nova no primeiro dia.
    """
    from pat.cli import _default_source_for

    paths = resolve_paths(home)
    conn = connect(paths.warehouse, read_only=True)
    try:
        assert _default_source_for(conn, "br:cnpj:47508411000156") == "cvm.dfp"
        # Entidade desconhecida mantem o default historico, em vez de adivinhar
        # jurisdicao pelo prefixo do identificador - que e opaco de proposito.
        assert _default_source_for(conn, "xx:zzz:000") == "cvm.dfp"
    finally:
        conn.close()

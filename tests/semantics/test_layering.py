"""A regra de camada, verificada no codigo e nao so no README.

Conceitos e metricas sao universais. Se um dia alguem importar o resolver da
CVM dentro de `definitions/ebitda.py` para "resolver rapidinho um caso", a
arquitetura acabou - e ninguem vai notar, porque os testes de valor continuam
passando. Este arquivo e o que faz notar.

A checagem e sintatica (AST), de proposito: nao depende de o import ser
executado, entao pega tambem o import morto dentro de um `if` ou de uma
funcao.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
SEMANTICS = SRC / "pat" / "semantics"

# Modulos que precisam permanecer sem jurisdicao.
UNIVERSAL = [
    SRC / "pat" / "contracts" / "semantics.py",
    SEMANTICS / "concepts.py",
    SEMANTICS / "engine.py",
    SEMANTICS / "registry.py",
    SEMANTICS / "resolver.py",
    *sorted((SEMANTICS / "definitions").glob("*.py")),
]

PROIBIDOS = (
    "pat.semantics.frameworks",
    "pat.query",
    "pat.store",
    "pat.parse",
    "pat.sources",
    "pat.build",
    "pat.ingest",
)


def _collect(nodes) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _imports(path: Path) -> set[str]:
    """Todos os imports, inclusive os escondidos dentro de funcao."""
    return _collect(ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))


def _module_level_imports(path: Path) -> set[str]:
    """So os que acontecem ao importar o modulo.

    Distincao necessaria: um import dentro de funcao adia o acoplamento, e ha
    casos em que isso e exatamente o desenho - registrar um plugin sob demanda,
    por exemplo.
    """
    return _collect(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body)


@pytest.mark.parametrize("path", UNIVERSAL, ids=lambda p: p.name)
def test_camada_universal_nao_conhece_regime_nem_armazenamento(path: Path):
    for imported in _imports(path):
        for proibido in PROIBIDOS:
            assert not imported.startswith(proibido), (
                f"{path.relative_to(SRC)} importa {imported}. A camada universal nao "
                "pode conhecer fonte, taxonomia nem armazenamento - e o que permite "
                "que a mesma metrica valha em outro regime."
            )


@pytest.mark.parametrize("path", UNIVERSAL, ids=lambda p: p.name)
def test_camada_universal_nao_cita_plano_de_contas(path: Path):
    """Nem em string, nem em comentario. Codigo de conta em metrica e o
    sintoma exato que a Fase 2 foi redesenhada para eliminar."""
    texto = path.read_text(encoding="utf-8")
    for termo in ("cd_conta", "cod_cvm", "us-gaap:", "CD_CONTA"):
        assert termo not in texto, f"{path.relative_to(SRC)} cita {termo!r}"


def test_o_loader_conhece_o_registro_de_adapters_mas_nao_um_regime():
    """O loader precisa despachar para adapters - isso e o mecanismo de plugin.
    O que ele nao pode e conhecer um plugin especifico."""
    imports = _imports(SEMANTICS / "loader.py")
    assert "pat.semantics.frameworks" in imports
    assert not any(i.startswith("pat.semantics.frameworks.") for i in imports)


def test_so_o_resolver_e_a_raiz_de_composicao_tocam_a_camada_de_consulta():
    """`pat.query` e a porta do gold.

    So podem atravessa-la os RESOLVERS - um por regime, que e o papel de
    adaptador - e `__init__.py`, que e a raiz de composicao, o lugar onde o
    sistema escolhe com que fontes concretas vai falar.

    O conjunto cresceu na M5.6 com `us_gaap/resolver.py` e de novo com
    `market/resolver.py`, e crescer assim e o comportamento esperado: cada
    REGIME novo traz UM resolver. O de mercado nao e uma jurisdicao - preco nao
    e peca contabil -, e a regra continua a mesma porque ela nunca foi sobre
    jurisdicao: e sobre quem tem licenca para ler o gold.

    O que o teste impede e um arquivo que nao seja resolver nem raiz aparecer
    aqui - seria a camada semantica lendo o gold por fora.
    """
    atravessam = {
        path.relative_to(SEMANTICS).as_posix()
        for path in SEMANTICS.rglob("*.py")
        if any(i.startswith("pat.query") for i in _imports(path))
    }
    assert atravessam == {
        "__init__.py",
        "frameworks/cvm_dfp/resolver.py",
        "frameworks/us_gaap/resolver.py",
        "frameworks/market/resolver.py",
    }
    # E a regra por tras do conjunto, dita de forma que sobrevive ao proximo
    # regime: todo arquivo que le `pat.query` ou e a raiz, ou e um resolver.
    assert all(
        caminho == "__init__.py" or caminho.endswith("/resolver.py")
        for caminho in atravessam
    )


def test_registro_de_adapters_nao_arrasta_regime_na_importacao():
    """Importar o registro de taxonomias nao pode importar o Brasil junto.

    O import do adapter concreto vive dentro de `install_builtins()`, e por
    isso so acontece quando alguem decide instalar o regime.
    """
    imports = _module_level_imports(SEMANTICS / "frameworks" / "__init__.py")
    assert not any(i.startswith("pat.semantics.frameworks.cvm") for i in imports)

"""A regra de camada do Opportunity, verificada no codigo.

Duas regras, e as duas existem pela mesma razao que a checagem da camada
semantica existe: elas continuariam valendo no README depois de terem parado
de valer no codigo, e nenhum teste de valor notaria.

1. **O Opportunity nao tem jurisdicao.** Nada em `pat/opportunity/` cita CVM,
   SEC, `cod_cvm`, `cik` ou `cd_conta` - nem em comentario. A empresa chega
   como `CompanyProfile`, com `jurisdiction` e `identifiers` opacos. A terceira
   jurisdicao nao deve pedir uma linha aqui.

   `company.py` e a excecao declarada, e mesmo ela so pode citar `scheme` -
   nunca um valor concreto de scheme.

2. **O Opportunity nao calcula.** Nada nesta camada importa o motor semantico
   nem o gold diretamente para fazer conta. Os numeros entram por
   `pat.opportunity.tools` (O3), que devolve resultado do PAT ja pronto, com
   procedencia. Um `Decimal` construido dentro do Opportunity a partir de
   pedacos de fato seria numero produzido por quem nao deve produzir numero.

   A camada de valuation (O8) e a excecao declarada da regra 2: ela calcula, e
   por isso vive em `valuation/`, isolada e testada contra numeros conferidos a
   mao.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
OPPORTUNITY = SRC / "pat" / "opportunity"

MODULOS = sorted(p for p in OPPORTUNITY.rglob("*.py") if "__pycache__" not in p.parts)

# Termos de regime. Procurados no texto inteiro do arquivo, comentarios e
# docstrings inclusive: um comentario que explica "aqui e o codigo da CVM" ja
# denuncia que a decisao de regime vazou para esta camada.
TERMOS_DE_REGIME = (
    r"\bcod_cvm\b",
    r"\bcd_conta\b",
    r"\bcvm\b",
    r"\bsec\b",
    r"\bdfp\b",
    r"\bedgar\b",
    r"\bus_gaap\b",
    r"\bus-gaap\b",
    r"\bifrs\b",
    r"\bcnpj\b",
    r"\bcik\b",
    r"\b10-k\b",
    r"\b10-q\b",
)

# Arquivos autorizados a citar um termo de regime, com a razao.
# `company.py` traduz o catalogo em perfil e precisa nomear os apelidos que
# um humano digita; `providers.py` (O4) e a raiz de composicao dos adapters
# de documento, que e onde os regimes concretos podem ser escolhidos.
EXCECOES = {
    "company.py": "traduz identidade do catalogo; cita scheme, nunca semantica de regime",
    "providers.py": "raiz de composicao dos adapters de documento",
    "tools.py": "traduz o endereco de conta do regime em codigo opaco, uma vez",
}

PROIBIDO_IMPORTAR = (
    "pat.semantics.frameworks",
    "pat.parse",
    "pat.sources",
    "pat.build",
    "pat.build_sec",
    "pat.ingest",
)


def _texto(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports(path: Path) -> set[str]:
    encontrados: set[str] = set()
    for node in ast.walk(ast.parse(_texto(path), filename=str(path))):
        if isinstance(node, ast.Import):
            encontrados.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            encontrados.add(node.module)
    return encontrados


@pytest.mark.parametrize("path", MODULOS, ids=lambda p: p.name)
def test_opportunity_nao_cita_regime(path: Path):
    if path.name in EXCECOES:
        pytest.skip(f"excecao declarada: {EXCECOES[path.name]}")
    texto = _texto(path).lower()
    for termo in TERMOS_DE_REGIME:
        achado = re.search(termo, texto)
        assert achado is None, (
            f"{path.relative_to(SRC)} cita {achado.group(0)!r}. O Opportunity fala "
            "com o mundo por `CompanyProfile` e pelos adapters; citar um regime aqui "
            "torna a camada permanentemente brasileira (ou permanentemente americana)."
        )


@pytest.mark.parametrize("path", MODULOS, ids=lambda p: p.name)
def test_opportunity_nao_importa_ingestao_nem_taxonomia(path: Path):
    for importado in _imports(path):
        for proibido in PROIBIDO_IMPORTAR:
            assert not importado.startswith(proibido), (
                f"{path.relative_to(SRC)} importa {importado}. Ingestao, parsing e "
                "taxonomia concreta ficam abaixo do PAT; o Opportunity so ve a "
                "interface publica."
            )


def test_a_excecao_de_tools_e_estreita():
    """`tools.py` pode nomear o campo de endereco de conta que o PAT devolve;
    nao pode conhecer taxonomia, fonte nem tipo de arquivo de regime.

    A traducao acontece UMA vez, em `accounts()`, e o que sai dali e
    `account_code`, opaco. Se um segundo lugar do arquivo precisar do nome
    original, a traducao vazou.
    """
    texto = _texto(OPPORTUNITY / "tools.py").lower()
    for proibido in (r"\bdfp\b", r"\bedgar\b", r"\bus_gaap\b", r"\bcvm\b", r"\bsec\b"):
        assert re.search(proibido, texto) is None, (
            f"tools.py cita {proibido}: isso e regime, e a mesa nao pode ter um."
        )
    assert texto.count("cd_conta") == 1, (
        "o endereco de conta do regime aparece mais de uma vez em tools.py. "
        "A traducao para `account_code` e um ponto so; dois pontos ja e a "
        "taxonomia vazando para a camada que nao pode ter jurisdicao."
    )


def test_a_excecao_de_company_e_estreita():
    """`company.py` pode citar `scheme`; nao pode conter a logica de um regime.

    A diferenca e o que separa "traduz identificador" de "sabe o que a CVM e".
    """
    texto = _texto(OPPORTUNITY / "company.py").lower()
    for proibido in (r"\bcd_conta\b", r"\bdfp\b", r"\bedgar\b", r"\bus_gaap\b"):
        assert re.search(proibido, texto) is None, (
            f"company.py cita {proibido}: isso e semantica de regime, nao identidade."
        )

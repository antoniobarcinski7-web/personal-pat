"""As regras de camada da Fase 5, verificadas por AST.

Mesma tecnica de `tests/research/test_layering_research.py`, e pela mesma
razao: um import morto dentro de uma funcao continua sendo acoplamento, e o
dia em que alguem chamar um modelo de dentro da recuperacao "so para melhorar
o ranking" os testes de valor continuariam passando.

A regra mais importante do arquivo e a ultima: o texto de um documento nao
tem caminho ate o gold. Ela e o portao mecanico contra a lavagem de numero do
emissor, e vale sobre a arvore inteira, nao sobre uma lista de arquivos.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
CORPUS = SRC / "pat" / "corpus"
CONTRATO = SRC / "pat" / "contracts" / "corpus.py"


def _collect(nodes) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _imports(path: Path) -> set[str]:
    return _collect(ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))


def _corpus_files() -> list[Path]:
    return sorted(CORPUS.rglob("*.py"))


# -- C1: o contrato continua universal ---------------------------------------


def test_contrato_de_corpus_nao_conhece_implementacao():
    proibidos = (
        "pat.corpus",
        "pat.research",
        "pat.semantics",
        "pat.query",
        "pat.store",
        "pat.parse",
        "pat.sources",
        "httpx",
        "duckdb",
        "pypdf",
    )
    for imported in _imports(CONTRATO):
        assert not imported.startswith(proibidos), (
            f"contracts/corpus.py importa {imported}. Contrato depende so de "
            "contrato - e o que permite conferir a forma de uma citacao sem "
            "banco, sem rede e sem biblioteca de PDF."
        )


def test_o_contrato_de_corpus_so_conhece_contratos_do_proprio_projeto():
    internos = {i for i in _imports(CONTRATO) if i.startswith("pat.")}
    assert internos <= {"pat.contracts.common"}, (
        f"contracts/corpus.py importa {sorted(internos)}; so `contracts.common` "
        "e permitido."
    )


# -- C2: nenhum modelo no caminho da evidencia -------------------------------


def test_nenhum_modulo_de_corpus_chama_llm():
    """A M5.1 inteira roda sem credencial, e isso e estrutural.

    Nao ha `pat.research.llm` nem `anthropic` em lugar nenhum de `pat/corpus/`.
    Se a recuperacao nao presta sem modelo, ela nao vai prestar com um - vai
    so ficar dificil de perceber.
    """
    proibidos = ("anthropic", "pat.research.llm", "openai")
    for path in _corpus_files():
        for imported in _imports(path):
            assert not imported.startswith(proibidos), (
                f"{path.name} importa {imported}: a camada de evidencia da M5.1 "
                "e deterministica de ponta a ponta."
            )


def test_corpus_nao_depende_da_camada_de_pesquisa():
    """L1.5 esta ABAIXO de L3. Um import daqui para la viraria ciclo.

    E a razao pela qual o serializador canonico foi movido para
    `pat.canonical` em vez de a camada de corpus importar
    `pat.research.canonical`.
    """
    for path in _corpus_files():
        for imported in _imports(path):
            assert not imported.startswith(("pat.research", "pat.semantics")), (
                f"{path.name} importa {imported}: corpus e camada de baixo."
            )


def test_so_o_extrator_conhece_a_biblioteca_de_pdf():
    """`pypdf` fica confinado a um arquivo.

    E o que torna a decisao reversivel: trocar de biblioteca no futuro e
    escrever um extrator novo com versao propria, e nao caçar imports pela
    arvore.
    """
    com_pypdf = {
        path.name for path in _corpus_files() if any(i.startswith("pypdf") for i in _imports(path))
    }
    assert com_pypdf == {"extract.py"}, (
        f"pypdf aparece em {sorted(com_pypdf)}; deveria estar so em extract.py"
    )


# -- C3: o indice e derivado --------------------------------------------------


def test_a_recuperacao_le_o_indice_mas_a_citacao_vem_da_unidade():
    """O indice ordena; ele nao e fonte do texto.

    `_quote` monta a citacao a partir de `document_unit`, nunca da tabela de
    tokens - que e lossy por construcao (minusculas, sem acento, sem
    stopword). Uma citacao montada do indice seria uma parafrase com cara de
    verbatim.
    """
    fonte = (CORPUS / "retrieve.py").read_text(encoding="utf-8")
    corpo = fonte[fonte.index("def _quote(") :]
    assert "document_unit_token" not in corpo
    assert "unit.text" in corpo


# -- C4: o portao contra a lavagem de numero do emissor ----------------------


def test_o_texto_de_documento_nao_tem_caminho_ate_o_gold():
    """Nenhum modulo que le corpus escreve fato.

    E a barreira que impede "receita de R$ 511,9 bilhoes" lido de um release
    de virar um `Fact`. O numero do emissor pode ser CITADO; ele nao pode ser
    INGERIDO, porque nao existe codigo que faca a travessia.
    """
    escrevem_gold = {
        path.name
        for path in _corpus_files()
        if any(i.startswith(("pat.store.gold", "pat.build")) for i in _imports(path))
    }
    assert not escrevem_gold, (
        f"{sorted(escrevem_gold)} escreve(m) no gold a partir da camada de texto. "
        "Numero de documento e citacao, nunca fato."
    )


def test_o_store_de_corpus_nao_escreve_em_tabela_de_fato():
    fonte = (SRC / "pat" / "store" / "corpus.py").read_text(encoding="utf-8")
    for tabela in ("gold_fact", "silver_line"):
        assert tabela not in fonte, (
            f"store/corpus.py menciona {tabela}: as duas camadas nao se tocam."
        )


def _sql_literais(path: Path) -> list[str]:
    """Todo literal de string que chega a `execute`/`executemany`.

    Por AST, e nao por busca no texto: a docstring deste modulo fala de
    UPDATE para explicar por que ele nao faz UPDATE, e uma checagem por
    substring acusaria a explicacao junto com o crime.
    """
    arvore = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    encontrados: list[str] = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call) or not isinstance(no.func, ast.Attribute):
            continue
        if no.func.attr not in {"execute", "executemany"}:
            continue
        for argumento in no.args:
            for parte in ast.walk(argumento):
                if isinstance(parte, ast.Constant) and isinstance(parte.value, str):
                    encontrados.append(parte.value)
    return encontrados


def test_o_store_de_corpus_e_append_only():
    """Nao ha UPDATE nem DELETE no SQL, e nao deve passar a haver.

    Documento reapresentado tem bytes diferentes e portanto `document_id`
    diferente: ele entra como linha nova, ao lado da anterior, e as duas
    continuam consultaveis pela data em que cada uma era verdade.
    """
    sql = " ".join(_sql_literais(SRC / "pat" / "store" / "corpus.py")).upper()
    assert sql, "nenhum SQL encontrado - o teste deixou de olhar para o lugar certo"
    for proibido in ("UPDATE ", "DELETE ", "DROP ", " SET "):
        assert proibido not in sql, f"SQL de store/corpus.py contem {proibido!r}"
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql, (
        "a idempotencia tem que ser por DO NOTHING; um DO UPDATE sobrescreveria "
        "silenciosamente o que ja estava gravado"
    )

"""A regra de camada da Fase 3, verificada por AST.

Mesma tecnica de `tests/semantics/test_layering.py`, e pela mesma razao: um
import morto dentro de uma funcao continua sendo acoplamento, e o dia em que
alguem ler o warehouse de dentro do planejador "so para conferir" os testes de
valor continuariam passando.

As regras que importam sao as de conjunto EXATO, nao as de lista negra: um
arquivo novo que atravesse a fronteira aparece, e uma lista negra nunca
pegaria isso.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
RESEARCH = SRC / "pat" / "research"
SEMANTICS = SRC / "pat" / "semantics"


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


def _files_importing(root: Path, prefixes: tuple[str, ...]) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if any(i.startswith(prefixes) for i in _imports(path))
    }


# -- R1: contratos continuam universais --------------------------------------


def test_contratos_de_pesquisa_nao_conhecem_implementacao():
    proibidos = ("pat.semantics", "pat.query", "pat.store", "pat.research", "pat.parse", "httpx")
    for imported in _imports(SRC / "pat" / "contracts" / "research.py"):
        assert not imported.startswith(proibidos), (
            f"contracts/research.py importa {imported}. Contrato depende so de "
            "contrato - e o que mantem a gramatica de plano conferivel sem banco."
        )


def test_a_gramatica_de_plano_nao_tem_onde_por_um_numero():
    """A propriedade estrutural da Fase 3, conferida no texto do contrato.

    Se um `Decimal` aparecer num passo de plano, um planejador passa a poder
    escrever um valor literal - e a resposta ganha um numero que nao veio do
    motor. O diff que fizer isso tem que quebrar aqui.
    """
    fonte = (SRC / "pat" / "contracts" / "research.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)

    for node in ast.walk(arvore):
        if not isinstance(node, ast.ClassDef) or node.name not in ("MetricStep", "DerivationStep"):
            continue
        for campo in node.body:
            if isinstance(campo, ast.AnnAssign):
                anotacao = ast.unparse(campo.annotation)
                assert "Decimal" not in anotacao, (
                    f"{node.name}.{ast.unparse(campo.target)} aceita Decimal: a gramatica "
                    "passou a permitir numero literal no plano"
                )


# -- R2: a Fase 2 nao sabe que a Fase 3 existe -------------------------------


def test_semantics_nao_importa_research():
    atravessam = _files_importing(SEMANTICS, ("pat.research",))
    assert atravessam == set(), (
        f"{sorted(atravessam)} importam pat.research. A camada semantica tem que "
        "continuar utilizavel sem a camada de pesquisa."
    )


# -- R4/R5: um dono para o Engine, um para o AsOf ----------------------------


def _files_containing(root: Path, needle: str) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if needle in path.read_text(encoding="utf-8")
    }


def test_so_a_raiz_de_composicao_constroi_o_motor():
    """Quem escolhe a fonte concreta e um lugar so."""
    assert _files_containing(RESEARCH, "build_engine") == {"__init__.py"}


def test_so_o_executor_chama_o_motor():
    """O motor chega ao executor por injecao - ele nem importa `pat.semantics.engine`.

    Isso e mais forte do que a regra pedia: o executor nao consegue construir
    um motor, so usar o que lhe deram.
    """
    assert _files_containing(RESEARCH, "engine.compute") == {"execute.py"}
    assert _files_importing(RESEARCH, ("pat.semantics.engine",)) == set()


def test_so_resolve_capability_e_a_raiz_tocam_a_camada_de_consulta():
    atravessam = _files_importing(RESEARCH, ("pat.query",))
    assert atravessam == {"resolve.py", "capability.py"}


# -- R6: o validador e puro ---------------------------------------------------


def test_o_validador_nao_toca_banco_relogio_nem_rede():
    proibidos = ("pat.query", "pat.store", "pat.research.llm", "httpx", "random", "socket")
    imports = _imports(RESEARCH / "validate.py")
    for imported in imports:
        assert not imported.startswith(proibidos), f"validate.py importa {imported}"

    fonte = (RESEARCH / "validate.py").read_text(encoding="utf-8")
    assert "datetime.now" not in fonte and "date.today" not in fonte, (
        "validacao que le o relogio nao e reproduzivel"
    )


def test_a_derivacao_e_pura():
    proibidos = ("pat.query", "pat.store", "pat.semantics.engine", "httpx")
    for imported in _imports(RESEARCH / "derive.py"):
        assert not imported.startswith(proibidos), f"derive.py importa {imported}"


# -- R7/R8: sem rede, e um so formatador -------------------------------------


def test_a_rede_entra_por_um_arquivo_so():
    """Guard ESTREITADO no M2.3: ate aqui a camada de pesquisa nao tinha
    nenhuma saida de rede, e o teste exigia conjunto vazio. O adapter e a
    primeira - e continua sendo conjunto EXATO, entao um segundo arquivo com
    saida de rede aparece aqui em vez de passar despercebido.

    `anthropic` entra na lista junto com os clientes HTTP crus: e por ele que a
    rede sai, e um guard que so olhasse `httpx` nao veria nada.
    """
    atravessam = _files_importing(
        RESEARCH, ("httpx", "urllib", "socket", "requests", "anthropic")
    )
    assert atravessam == {"llm/anthropic.py"}, (
        f"{sorted(atravessam)}: a saida de rede da camada de pesquisa tem que "
        "caber num arquivo so"
    )


def test_so_o_adapter_le_o_ambiente():
    """Chave de API entra por um lugar. `config.py` continua sendo o unico
    lugar do resto do projeto que le `os.environ`."""
    lendo = {
        path.relative_to(RESEARCH).as_posix()
        for path in RESEARCH.rglob("*.py")
        if "os.environ" in path.read_text(encoding="utf-8")
        or "getenv" in path.read_text(encoding="utf-8")
    }
    assert lendo == {"llm/anthropic.py"}


def test_o_adapter_desliga_a_retentativa_do_sdk():
    """O SDK tenta duas vezes por default, e uma segunda tentativa e um segundo
    caminho de influencia: teria que ser hasheada e manifestada por conta
    propria. Conferido no texto porque um default some numa leitura distraida.
    """
    fonte = (RESEARCH / "llm" / "anthropic.py").read_text(encoding="utf-8")
    assert "max_retries=0" in fonte


def test_nao_existe_execucao_de_codigo_arbitrario():
    """Sem sandbox porque sem codigo gerado - e isso e conferivel."""
    proibidos = ("subprocess", "os.system", "eval(", "exec(")
    for path in RESEARCH.rglob("*.py"):
        fonte = path.read_text(encoding="utf-8")
        for termo in proibidos:
            assert termo not in fonte, f"{path.name} contem {termo!r}"


def test_so_o_renderer_formata_numero_para_apresentacao():
    formatadores = {
        path.relative_to(RESEARCH).as_posix()
        for path in RESEARCH.rglob("*.py")
        if "quantize(" in path.read_text(encoding="utf-8")
    }
    assert formatadores == {"render.py"}, (
        f"{sorted(formatadores)} formatam numero. Um lugar so e o que torna a regra "
        "do digito verificavel."
    )


# -- o que ainda nao comecou -------------------------------------------------
#
# Ate o Milestone 2.1 este teste tambem exigia que `research/llm/` nao
# existisse. O M2.1 criou o modulo - a porta e os contratos - de proposito, e o
# guard foi estreitado para o que continua nao iniciado, em vez de removido.


@pytest.mark.parametrize("modulo", ["writer.py"])
def test_o_escritor_nao_foi_comecado(modulo):
    """O M2.2 criou `planner.py`; o escritor e Milestone 3."""
    assert not (RESEARCH / modulo).exists(), (
        f"{modulo} pertence ao Milestone 3 e nao deveria existir ainda"
    )


# -- o planejador: fala com modelo, nunca com dado ---------------------------


def test_o_planejador_nao_alcanca_dado_persistencia_nem_rede():
    """A fronteira que sustenta a Fase 3: o modelo nao tem caminho ate o
    warehouse. Conjunto exato de imports - qualquer travessia nova aparece.
    """
    proibidos = (
        "pat.store",
        "pat.sources",
        "pat.parse",
        "pat.query",
        "pat.ingest",
        "pat.build",
        "pat.semantics",
        "pat.research.capability",
        "pat.research.execute",
        "pat.research.resolve",
        "httpx",
        "urllib",
        "socket",
        "requests",
        "duckdb",
        "pathlib",
        "os",
    )
    for imported in _imports(RESEARCH / "planner.py"):
        assert not imported.startswith(proibidos), (
            f"planner.py importa {imported}: o planejador ganhou acesso a "
            "dado, disco, rede ou execucao"
        )


def test_o_planejador_nao_conhece_provider_nenhum():
    codigo = _codigo_sem_texto(RESEARCH / "planner.py")
    for provider in ("anthropic", "openai", "bedrock", "vertex", "api_key", "getenv"):
        assert provider not in codigo, f"o codigo do planejador menciona {provider!r}"


def test_o_planejador_nao_executa_nem_valida():
    """Ele monta o plano e para. Executar seria apagar a fronteira; validar
    seria uma segunda implementacao das regras do validador."""
    fonte = (RESEARCH / "planner.py").read_text(encoding="utf-8")

    for proibido in ("engine.compute", "execute_plan", "validate_plan", "resolve_plan"):
        assert proibido not in fonte, f"planner.py chama {proibido}"


def test_existe_exatamente_um_adapter_concreto():
    """Guard ESTREITADO a cada milestone, nunca removido nem afrouxado.

    Historia: ate o M2.1 exigia que `llm/` nao existisse; o M2.3 admitiu cache
    e persistencia; agora admite um adapter. Continua sendo conjunto EXATO -
    `openai.py` aparece aqui no dia em que alguem o criar, e a decisao de ter
    um segundo provider passa a ser explicita em vez de silenciosa. Uma lista
    negra nunca pegaria isso.
    """
    llm = RESEARCH / "llm"
    assert {p.name for p in llm.glob("*.py")} == {
        "__init__.py",
        "anthropic.py",
        "cache.py",
        "store.py",
    }


def test_o_cache_nao_alcanca_dado_nem_persistencia_do_warehouse():
    """O cache fala com o armazenamento por Protocol, do mesmo jeito que a
    camada semantica fala com os dados por `FactResolver`. Um import de
    `pat.store` aqui daria ao modelo um caminho ate o warehouse."""
    proibidos = (
        "pat.store",
        "pat.sources",
        "pat.parse",
        "pat.query",
        "pat.semantics",
        "pat.build",
        "pat.ingest",
        "duckdb",
        "httpx",
        "urllib",
        "socket",
        "requests",
    )
    for modulo in ("cache.py", "store.py"):
        for imported in _imports(RESEARCH / "llm" / modulo):
            assert not imported.startswith(proibidos), (
                f"llm/{modulo} importa {imported}: o cache ganhou caminho ate "
                "dado, warehouse ou rede"
            )


def test_o_cache_e_a_persistencia_nao_conhecem_provider_nenhum():
    for modulo in ("cache.py", "store.py"):
        codigo = _codigo_sem_texto(RESEARCH / "llm" / modulo)
        for provider in ("anthropic", "openai", "bedrock", "vertex", "api_key", "getenv"):
            assert provider not in codigo, f"llm/{modulo} menciona {provider!r}"


def test_so_o_cache_decide_a_identidade_da_chamada():
    """`call_sha256` num lugar so. Duas implementacoes de identidade divergem
    em silencio - foi o argumento que fez `LLMRequest` ser Pydantic e nao
    dataclass, e vale igual aqui."""
    assert _files_containing(RESEARCH, "def call_sha256") == {"llm/cache.py"}


def _codigo_sem_texto(path: Path) -> str:
    """Fonte com comentarios e literais de string removidos.

    Necessario porque a docstring do modulo *enuncia* a regra ("nada aqui
    conhece Anthropic"), e uma busca no texto cru acusaria a documentacao da
    proibicao como se fosse a violacao dela.
    """
    import io
    import tokenize

    pedacos = []
    for token in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        pedacos.append(token.string)
    return " ".join(pedacos).lower()


def test_a_porta_de_llm_nao_conhece_provider_nenhum():
    """Se um nome de fornecedor aparecer no *codigo*, a porta deixou de ser porta."""
    codigo = _codigo_sem_texto(RESEARCH / "llm" / "__init__.py")
    for provider in ("anthropic", "openai", "bedrock", "vertex", "api_key", "getenv"):
        assert provider not in codigo, f"o codigo da porta de LLM menciona {provider!r}"


def test_a_porta_de_llm_nao_alcanca_dado_nem_persistencia():
    """O modelo nao tem caminho ate o warehouse, e a porta e onde isso comeca.

    Conjunto exato de imports: qualquer coisa nova que atravesse aparece aqui.
    """
    proibidos = (
        "pat.store",
        "pat.sources",
        "pat.parse",
        "pat.query",
        "pat.semantics",
        "pat.build",
        "pat.ingest",
        "httpx",
        "urllib",
        "socket",
        "requests",
    )
    for imported in _imports(RESEARCH / "llm" / "__init__.py"):
        assert not imported.startswith(proibidos), (
            f"a porta de LLM importa {imported}: o modelo ganhou um caminho "
            "ate dado, persistencia ou rede"
        )

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
    """`engine.compute` mora num modulo so.

    Continua valendo depois da M5.2: `decompose.py` fala com o motor, mas pela
    porta de CONCEITO (`resolve_concept`), nao pela de metrica. Sao coisas
    diferentes - uma decomposicao abre os termos de uma identidade contabil, e
    esses termos nunca foram metricas registradas.
    """
    assert _files_containing(RESEARCH, "engine.compute") == {"execute.py"}


def test_nenhum_modulo_de_pesquisa_constroi_um_motor():
    """O motor chega por injecao, sempre.

    Esta e a regra que importa, e ela e mais forte do que "so um arquivo
    importa `engine`": nenhum modulo de L3 consegue CONSTRUIR um motor, entao
    nenhum deles escolhe sozinho contra que warehouse, que mapeamento ou que
    fonte vai falar. Quem monta essa ligacao e a raiz de composicao.

    Ate a M5.1 a regra era conferida como "ninguem importa
    `pat.semantics.engine`", o que funcionava por acidente: nenhum modulo
    precisava do TIPO. `decompose.py` precisa, para anotar o parametro que
    recebe. Trocar a checagem pela construcao e o que mantem a intencao
    original em vez da consequencia dela.
    """
    # `research/__init__.py` E a raiz de composicao desta camada: e nele que
    # `run_plan` liga conexao, mapeamentos e registro num motor. A excecao e
    # nominal, e nao um prefixo - um arquivo novo que quisesse o mesmo direito
    # teria que ser acrescentado aqui, num diff que aparece.
    RAIZ_DE_COMPOSICAO = {"__init__.py"}

    for path in sorted(RESEARCH.rglob("*.py")):
        if path.name in RAIZ_DE_COMPOSICAO and path.parent == RESEARCH:
            continue
        fonte = path.read_text(encoding="utf-8")
        assert "build_engine" not in fonte, (
            f"{path.name} constroi um motor. Ele tem que receber um pronto - "
            "e a raiz de composicao que decide contra o que o sistema fala."
        )
        assert "Engine(" not in fonte, f"{path.name} instancia um Engine diretamente"


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


# -- o escritor: fala de numero, nunca com numero ----------------------------
#
# Ate o M2.1 esta secao exigia que `research/llm/` nao existisse; ate o M3
# exigia que `writer.py` nao existisse. Nenhuma das duas foi removida por o
# milestone ter chegado - as duas foram ESTREITADAS para a regra que o
# codigo novo passou a ter que cumprir. Um guard que so diz "ainda nao
# comecou" nao protege nada no dia seguinte.


def test_o_escritor_nao_alcanca_dado_persistencia_nem_rede():
    """A mesma fronteira do planejador, pela mesma razao: o modelo nao tem
    caminho ate o warehouse. Conjunto exato de imports."""
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
    for imported in _imports(RESEARCH / "writer.py"):
        assert not imported.startswith(proibidos), (
            f"writer.py importa {imported}: o escritor ganhou acesso a dado, "
            "disco, rede ou execucao"
        )


def test_o_escritor_nao_conhece_provider_nenhum():
    codigo = _codigo_sem_texto(RESEARCH / "writer.py")
    for provider in ("anthropic", "openai", "bedrock", "vertex", "api_key", "getenv"):
        assert provider not in codigo, f"o codigo do escritor menciona {provider!r}"


def test_o_escritor_nao_le_valor_nenhum():
    """A regra central do M3, conferida por AST e nao por promessa.

    `ComputationResult.value` e o numero autoritativo e `NumericClaim.
    rendered_value` e a representacao dele. O escritor nao acessa nenhum dos
    dois: ele monta o prompt com `describe()` e nomes de token, e devolve
    texto com token. Se um destes atributos aparecer aqui, o escritor passou a
    ter um numero em maos - e o proximo passo natural de quem escreve o codigo
    seria manda-lo para o modelo.
    """
    fonte = (RESEARCH / "writer.py").read_text(encoding="utf-8")
    atributos = {
        node.attr
        for node in ast.walk(ast.parse(fonte))
        if isinstance(node, ast.Attribute)
    }

    for proibido in ("value", "rendered_value"):
        assert proibido not in atributos, (
            f"writer.py acessa .{proibido}: o escritor passou a ter acesso ao "
            "numero, e o desenho e que ele nunca o veja"
        )


def test_o_escritor_nao_faz_aritmetica():
    """Nenhum operador aritmetico no modulo inteiro.

    Mais forte do que "nao importa Decimal": o escritor nao soma, nao divide e
    nao compara grandezas nem por acidente. `+` fica de fora junto - concatenar
    string e legitimo, mas permitir `+` deixaria a checagem sem valor, e o
    modulo consegue viver sem ele.
    """
    aritmeticos = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Pow, ast.Mod)
    fonte = (RESEARCH / "writer.py").read_text(encoding="utf-8")

    for node in ast.walk(ast.parse(fonte)):
        if isinstance(node, ast.BinOp):
            assert not isinstance(node.op, aritmeticos), (
                f"writer.py faz aritmetica ({ast.unparse(node)}): calcular e do "
                "motor, e o escritor nao produz numero"
            )


def test_o_escritor_nao_monta_a_resposta_final():
    """`build_answer` e de quem tem o `manifest_id`, que so fecha depois da
    procedencia do escritor existir. Se o escritor a chamasse, ou o manifesto
    passaria a nao registrar o autor do proprio texto, ou apareceria um
    segundo lugar decidindo a ordem - e as duas ordens divergiriam."""
    montam = {
        path.relative_to(RESEARCH).as_posix()
        for path in RESEARCH.rglob("*.py")
        if "build_answer" in _importados_por_nome(path)
    }
    assert montam == {"__init__.py"}, (
        f"{sorted(montam)} importam build_answer. Montar a resposta e da raiz "
        "de composicao, que e quem conhece o manifest_id."
    )


def _importados_por_nome(path: Path) -> set[str]:
    """Nomes trazidos por `from ... import X`. Diferente de `_imports`, que
    coleta modulos: aqui a pergunta e sobre a funcao, nao sobre o pacote."""
    arvore = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.name
        for node in ast.walk(arvore)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def test_so_o_escritor_e_os_planejadores_falam_com_o_modelo():
    """Conjunto EXATO dos modulos que constroem uma chamada de LLM.

    Um modulo novo montando `LLMRequest` aparece aqui em vez de passar
    despercebido - e a afirmacao "o modelo entra em pontos contados" volta a
    ser verificavel a cada suite.

    `program_planner.py` entrou na M5.3 com DUAS chamadas, e elas nao sao uma
    retentativa: sao dois papeis, com prompts e entradas diferentes, cada um
    com sua propria `PlanProvenance` e sua propria linha em `llm_call`. O
    estagio 1 escolhe o que medir e decompor; o estagio 2 ve a FORMA dos
    resultados - direcao, faixa de magnitude, ordem dos contribuidores - e
    escolhe o que procurar no corpus. Nenhum dos dois ve um valor.
    """
    assert _files_containing(RESEARCH, "llm.complete(") == {
        "planner.py",
        "program_planner.py",
        "writer.py",
    }


def test_o_planejador_de_programa_nao_alcanca_dado_nem_persistencia():
    """O estagio 2 ve forma, e nao warehouse.

    Se `program_planner.py` pudesse ler o banco, a fronteira que `ResultShape`
    existe para manter viraria decorativa: bastaria uma consulta para o valor
    chegar ao prompt por outro caminho.
    """
    proibidos = ("pat.query", "pat.store", "pat.semantics.engine", "duckdb", "httpx")
    for imported in _imports(RESEARCH / "program_planner.py"):
        assert not imported.startswith(proibidos), (
            f"program_planner.py importa {imported}: o planejador fala com o "
            "modelo, nunca com o dado."
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


# -- o custo nao pode escapar ------------------------------------------------
#
# A afirmacao "`pytest` nao gasta credencial" repousava numa linha de
# configuracao que nenhum teste lia. Apagar `not llm` do `addopts` e um diff de
# uma palavra, passaria em revisao sem chamar atencao, e o sintoma seria uma
# fatura - nao um teste vermelho.


def _pyproject() -> str:
    return (SRC.parent / "pyproject.toml").read_text(encoding="utf-8")


def test_a_suite_padrao_desliga_os_marcadores_que_custam():
    """`network` depende de terceiro, `llm` gasta token. Nenhum dos dois pode
    rodar por acidente em `pytest`."""
    linha = next(
        linha for linha in _pyproject().splitlines() if linha.startswith("addopts")
    )

    for marcador in ("not network", "not llm"):
        assert marcador in linha, (
            f"`{marcador}` saiu do addopts: `pytest` puro passou a poder "
            f"disparar chamadas que custam. Linha atual: {linha}"
        )


def test_os_dois_marcadores_estao_declarados():
    """Marcador nao declarado vira warning e, sob `--strict-markers`, erro -
    mas o modo de falha silencioso e pior: um `pytest.mark.llm` com typo nao
    marca nada, e o teste passa a rodar na suite padrao."""
    texto = _pyproject()

    for marcador in ("network:", "llm:"):
        assert marcador in texto, f"marcador {marcador!r} nao declarado em pyproject"


def test_todo_teste_que_monta_um_cliente_real_esta_marcado_como_llm():
    """Quem constroi o adapter SEM injetar `sdk` tem que estar sob `llm`.

    A distincao e a que importa, e por isso e feita por AST e nao por texto:
    `AnthropicClient(sdk=duplo)` nao le credencial e nao fala com a rede - e
    como `test_llm_anthropic.py` exercita o mapeamento de erro de graca.
    `AnthropicClient()` le `ANTHROPIC_API_KEY` e abre conexao.

    Uma busca por "AnthropicClient(" acusaria os dois e o guard seria
    desligado no primeiro falso positivo, que e como um guard morre.
    """
    testes = Path(__file__).resolve().parents[1]
    desmarcados: list[str] = []

    for path in testes.rglob("test_*.py"):
        fonte = path.read_text(encoding="utf-8")
        if "pytest.mark.llm" in fonte:
            continue

        arvore = ast.parse(fonte, filename=str(path))
        # Construcao dentro de `pytest.raises` nao gasta nada: o teste afirma
        # que ela FALHA. E o caso de `test_sem_chave_falha_em_vez_de_degradar`,
        # que apaga a variavel de ambiente e espera a recusa - exatamente o
        # comportamento que este guard existe para proteger.
        esperando_falha = {
            linha
            for node in ast.walk(arvore)
            if isinstance(node, ast.With)
            and any("raises" in ast.unparse(item.context_expr) for item in node.items)
            for linha in range(node.lineno, (node.end_lineno or node.lineno) + 1)
        }

        for node in ast.walk(arvore):
            if not isinstance(node, ast.Call):
                continue
            alvo = node.func
            nome = alvo.attr if isinstance(alvo, ast.Attribute) else getattr(alvo, "id", None)
            if nome != "AnthropicClient" or node.lineno in esperando_falha:
                continue
            if not any(kw.arg == "sdk" for kw in node.keywords):
                desmarcados.append(f"{path.relative_to(testes).as_posix()}:{node.lineno}")

    assert desmarcados == [], (
        f"{desmarcados} montam um AnthropicClient real sem estar sob o marcador "
        "`llm`: rodariam em `pytest` puro, gastando credencial"
    )


# -- M4.1: a camada de conversa nao inverte nenhuma seta ---------------------
#
# Ate a M4.1 nao existia `pat.chat`. Estes dois guards nascem junto com ela, e
# seguem a disciplina de sempre: conjunto EXATO, para que a proxima travessia
# apareca em vez de passar despercebida.


def test_research_nao_importa_chat():
    """A seta aponta para baixo, e so.

    A Fase 3 continua utilizavel sem a camada de conversa, do mesmo jeito que a
    Fase 2 continua utilizavel sem a Fase 3 (`test_semantics_nao_importa_
    research`). `pat plan` e `pat ask` nao podem passar a depender de um
    servidor HTTP existir.

    Cuidado ao ler uma falha aqui: `planner.py` importa `pat.contracts.chat`, e
    isso esta CERTO - contrato depende de contrato. O que nao pode e importar
    `pat.chat`, que e implementacao.
    """
    atravessam = _files_importing(RESEARCH, ("pat.chat",))
    assert atravessam == set(), (
        f"{sorted(atravessam)} importam pat.chat. A camada de pesquisa tem que "
        "continuar utilizavel sem a camada de conversa."
    )


def test_a_rede_do_projeto_inteiro_cabe_em_dois_arquivos():
    """Guard ESTREITADO na M4.1, e para o pacote inteiro.

    `test_a_rede_entra_por_um_arquivo_so` olha so `research/`. A partir do
    momento em que existe um servidor local, a pergunta que interessa deixou de
    ser "a camada de pesquisa abre socket?" e passou a ser "quantos lugares do
    PAT falam com a rede?". A resposta e dois, e cada um por uma razao distinta:
    um chama o modelo, o outro escuta em localhost.

    `sources/` fica de fora porque baixar bytes da CVM e a funcao declarada
    dele - a Fase 1 inteira existe para isso.
    """
    pat = SRC / "pat"
    atravessam = {
        path.relative_to(pat).as_posix()
        for path in pat.rglob("*.py")
        if not path.relative_to(pat).as_posix().startswith("sources/")
        and any(
            i.startswith(("httpx", "urllib", "socket", "requests", "anthropic", "http.server"))
            for i in _imports(path)
        )
    }
    assert atravessam == {"research/llm/anthropic.py", "chat/http.py"}, (
        f"{sorted(atravessam)}: a saida de rede do PAT fora de `sources/` tem que "
        "caber em dois arquivos - o adapter do modelo e o servidor local."
    )

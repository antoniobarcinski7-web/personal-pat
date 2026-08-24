"""A regra de camada da M4.1, verificada por AST e por texto.

Mesma tecnica de `tests/research/test_layering_research.py`, e pela mesma razao:
um import morto dentro de uma funcao continua sendo acoplamento, e o dia em que
alguem chamar o modelo de dentro da camada de conversa "so para desambiguar" os
testes de valor continuariam passando.

O que esta camada existe para nao fazer
---------------------------------------
A M4.1 poe uma caixa de texto na frente de um sistema cuja propriedade central e
que o LLM nunca produz um numero. A partir do momento em que existe uma caixa de
texto, a pressao e toda na mesma direcao: "so deixar o modelo responder direto
quando nao tem dado", "so cachear a resposta anterior", "so deixar ele estimar".
Cada uma isolada parece razoavel. Os guards deste arquivo sao o que faz cada uma
delas aparecer como um teste vermelho em vez de um diff que passa em revisao.

As regras que importam sao as de conjunto EXATO, nao as de lista negra: um
arquivo novo que atravesse a fronteira aparece, e uma lista negra nunca pegaria
isso.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SRC = RAIZ / "src"
CHAT = SRC / "pat" / "chat"
INDEX_HTML = CHAT / "static" / "index.html"


def _fontes() -> list[Path]:
    """Todo `.py` da camada de chat. Nao ha lista fixa de proposito: arquivo
    novo entra no guard sozinho."""
    return sorted(CHAT.rglob("*.py"))


def _texto(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports(path: Path) -> set[str]:
    achados: set[str] = set()
    for node in ast.walk(ast.parse(_texto(path), filename=str(path))):
        if isinstance(node, ast.Import):
            achados.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            achados.add(node.module)
    return achados


def _arquivos_contendo(agulha: str) -> set[str]:
    return {p.relative_to(CHAT).as_posix() for p in _fontes() if agulha in _texto(p)}


def _arquivos_importando(prefixos: tuple[str, ...]) -> set[str]:
    return {
        p.relative_to(CHAT).as_posix()
        for p in _fontes()
        if any(i.startswith(prefixos) for i in _imports(p))
    }


# -- L-1: o chat nao fala com o modelo ---------------------------------------


def test_a_camada_de_chat_nao_chama_o_modelo():
    """Conjunto EXATO, e ele e VAZIO.

    Quem fala com o modelo sao `research/planner.py` e `research/writer.py`, e o
    desenho da Fase 3 diz "no maximo uma chamada por planejador e uma por
    escritor". A M4.1 nao acrescentou uma terceira: o contexto conversacional
    entra no prompt do planejador, que ja existia.

    Um roteador de intencao, um reescritor de pergunta ou um classificador de
    follow-up seriam um terceiro caminho de influencia - teria que ser hasheado
    e manifestado por conta propria, ou a procedencia passa a sub-relatar o que
    aconteceu. Ele apareceria aqui.
    """
    assert _arquivos_contendo("llm.complete(") == set(), (
        "a camada de chat passou a chamar o modelo diretamente"
    )


# -- L-2: um formatador de numero, e ele nao e este --------------------------


def test_o_chat_nao_formata_numero():
    """`quantize(` so existe em `research/render.py`, no projeto inteiro.

    Ter um formatador so e o que torna conferivel a regra de que o modelo nunca
    emite digito: se a camada de apresentacao pudesse arredondar, "o numero na
    tela e o numero do motor" viraria promessa em vez de propriedade. `view.py`
    copia `rendered_value`, que ja e string pronta.
    """
    assert _arquivos_contendo("quantize(") == set()


# -- L-3: o chat usa o pipeline, nao o reimplementa --------------------------


def test_o_chat_nao_alcanca_o_motor_nem_reimplementa_o_pipeline():
    """Ele chama `run_plan` e para.

    Construir o motor aqui daria a camada de conversa uma segunda fonte de
    numeros; chamar validador, resolver ou executor por fora daria uma segunda
    ordem de operacoes. As duas divergiriam da primeira - normalmente na direcao
    de a mais permissiva deixar passar o que a outra recusaria.
    """
    assert _arquivos_importando(("pat.semantics.engine",)) == set()

    for proibido in ("build_engine", "execute_plan", "validate_plan", "resolve_plan"):
        assert _arquivos_contendo(proibido) == set(), (
            f"a camada de chat menciona {proibido!r}: ela deixou de ser uma casca"
        )


# -- L-4: sem execucao de codigo arbitrario ----------------------------------


def test_nao_existe_execucao_de_codigo_arbitrario_no_chat():
    """Sem sandbox porque sem codigo gerado - e isso continua conferivel na
    camada nova, que e justamente a que recebe texto de fora."""
    for proibido in ("subprocess", "os.system", "eval(", "exec("):
        assert _arquivos_contendo(proibido) == set(), f"a camada de chat contem {proibido!r}"


# -- L-5: a rede entra por um arquivo so -------------------------------------


def test_a_rede_da_camada_de_chat_cabe_num_arquivo_so():
    """Conjunto EXATO. Um segundo arquivo abrindo socket aparece aqui em vez de
    passar despercebido - a mesma disciplina que mantem a saida de rede da
    camada de pesquisa em `llm/anthropic.py`."""
    assert _arquivos_importando(("http.server", "socketserver", "socket")) == {"http.py"}


# -- L-6: quem calcula nao grava ---------------------------------------------


def test_a_escrita_em_disco_cabe_em_dois_arquivos():
    """Conjunto EXATO: `session.py` grava o log da conversa, `record.py` grava a
    auditoria. Mais ninguem.

    E a mesma razao pela qual `write_facts` mora em `store/` e nao em quem
    calcula: separar quem produz de quem persiste e o que permite rodar um turno
    inteiro em memoria, num teste, e o que impede uma camada de apresentacao de
    ganhar um caminho de escrita ate o warehouse.
    """
    agulhas = ("open(", "write_text", "write_call", "write_manifest", "mkdir")
    escrevem = {nome for agulha in agulhas for nome in _arquivos_contendo(agulha)}
    assert escrevem == {"session.py", "record.py"}, (
        f"{sorted(escrevem)} escrevem. Quem calcula nao grava."
    )


# -- L-9: o frontend nao calcula e nao injeta --------------------------------


def test_o_frontend_nao_faz_aritmetica_e_nao_injeta():
    """A ultima perna da regra do digito, conferida no texto da pagina.

    Os numeros chegam prontos em `rendered_value` e vao para a tela como vieram.
    Uma unica chamada a `toFixed` ou a `Number(` poria a camada de apresentacao
    no caminho do numero, e a afirmacao "o unico formatador do sistema e
    `render.py`" deixaria de valer exatamente onde o usuario olha.

    `innerHTML` entra na mesma lista por outro motivo: a prosa vem de um modelo,
    e renderiza-la como markup e injecao com passos extras. A pagina usa
    `textContent`.
    """
    fonte = _texto(INDEX_HTML)

    for proibido in ("parseFloat", "Number(", "toFixed", "Math.", "eval(", "innerHTML"):
        assert proibido not in fonte, (
            f"index.html contem {proibido!r}: o frontend passou a calcular ou a injetar"
        )


def test_o_frontend_nao_busca_recurso_externo():
    """A pagina tem que funcionar sem nenhuma rede alem do proprio localhost.

    Um CDN ou uma fonte remota fariam uma ferramenta de research local passar a
    depender de um terceiro estar de pe - e a contar a ele o que esta sendo
    consultado.
    """
    fonte = _texto(INDEX_HTML)

    for proibido in ('src="http', "src='http", 'href="http', "href='http", "@import"):
        assert proibido not in fonte, f"index.html carrega recurso externo ({proibido!r})"


# -- L-10: nenhuma dependencia nova ------------------------------------------


def test_a_m41_nao_trouxe_dependencia_nova():
    """A M4.1 inteira e biblioteca padrao.

    O guard existe porque adicionar uma dependencia e um diff de uma linha que
    passa em revisao sem chamar atencao, e porque a escolha de `http.server`
    sobre um framework foi deliberada: o valor do desenho esta no pipeline
    explicito e inspecionavel, e a pilha ASGI nao se le numa tarde.
    """
    pyproject = tomllib.loads(_texto(RAIZ / "pyproject.toml"))
    nomes = {
        dep.split(">")[0].split("=")[0].split("[")[0].strip()
        for dep in pyproject["project"]["dependencies"]
    }
    assert nomes == {"pydantic", "httpx", "duckdb", "pytz", "anthropic", "pypdf"}, (
        f"dependencias mudaram: {sorted(nomes)}"
    )
    # `pypdf` entrou na Fase 5 (M5.1) por decisao registrada, e nao por
    # descuido: extrair texto de PDF nao tem como ser feito pela stdlib, e
    # todo documento qualitativo que a CVM publica e PDF. Foi escolhida a
    # opcao minima - Python puro, BSD-3, zero dependencia transitiva - e a
    # versao efetiva entra em `extraction_version`, de modo que trocar de
    # biblioteca no futuro cria unidades novas em vez de mudar as antigas.
    # O guard continua valendo para a proxima linha que alguem tentar somar.


# -- L-11: o valor renderizado nao circula pela camada de conversa -----------


def test_so_a_view_toca_no_valor_renderizado():
    """O analogo conversacional de `test_o_escritor_nao_le_valor_nenhum`.

    `view.py` precisa de `rendered_value` porque e ele que monta o JSON da tela.
    Ninguem mais precisa - e se `session.py` ou `turn.py` passasse a tocar num
    valor renderizado, o proximo passo natural de quem escreve o codigo seria
    manda-lo para o contexto do turno seguinte, que e exatamente o que a M4.1
    existe para nao fazer.

    Por AST e nao por texto: a pergunta e sobre acesso ao atributo, e uma busca
    textual acusaria a palavra dentro de um docstring que apenas explica a
    regra - o tipo de falso positivo pelo qual um guard e desligado.
    """
    tocam = set()
    for path in _fontes():
        atributos = {
            node.attr
            for node in ast.walk(ast.parse(_texto(path), filename=str(path)))
            if isinstance(node, ast.Attribute)
        }
        if "rendered_value" in atributos:
            tocam.add(path.relative_to(CHAT).as_posix())

    assert tocam == {"view.py"}, (
        f"{sorted(tocam)} acessam .rendered_value. So a serializacao para a tela "
        "tem motivo para tocar num valor formatado."
    )


def test_o_contexto_nao_tem_como_carregar_um_numero():
    """A propriedade da M4.1, conferida no arquivo que a sustenta.

    `summarize` e a unica funcao que transforma um turno passado em algo que o
    modelo vera. Se ela passar a ler `.value`, `.rendered_value`, `.result_id`
    ou os claims da resposta, o numero de um turno anterior ganha um caminho ate
    o prompt do proximo - e a regra vira promessa.
    """
    fonte = _texto(CHAT / "session.py")
    atributos = {
        node.attr
        for node in ast.walk(ast.parse(fonte))
        if isinstance(node, ast.Attribute)
    }

    for proibido in ("value", "rendered_value", "result_id", "claims", "manifest"):
        assert proibido not in atributos, (
            f"session.py acessa .{proibido}: o contexto conversacional ganhou "
            "acesso ao conteudo da resposta, e nao so a sua estrutura"
        )

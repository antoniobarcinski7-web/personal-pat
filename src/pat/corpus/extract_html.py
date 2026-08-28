"""Bytes de HTML -> texto derivado, deterministico e versionado.

O analogo de `extract_pdf_page_texts`, e as tres garantias sao as mesmas:
reproduzivel, versionada na identidade, e falha com nome. O que muda e a
biblioteca e o formato de origem - um arquivamento da SEC e HTML, nao PDF.

Por que `html.parser` da biblioteca padrao
------------------------------------------
Nao ha dependencia nova. Um parser de terceiro (lxml, BeautifulSoup) leria
melhor HTML quebrado, e traria duas coisas indesejadas: mais uma versao para
embutir na identidade das unidades, e um comportamento de "conserto" que varia
entre versoes. `html.parser` e tolerante o bastante para arquivamento da SEC,
que e HTML gerado por ferramenta e nao escrito a mao.

A versao do Python entra na `extraction_version` justamente porque o parser e
dele: um upgrade de Python pode mudar o texto derivado, e mudar o texto sem
mudar a identidade transformaria uma citacao antiga em outra coisa.

O que este modulo NAO faz
-------------------------
Nao interpreta tabela, nao segue link, nao reordena e nao resume. Ele produz o
texto visivel na ordem do documento, e o resto do pipeline trata esse texto
exatamente como trata o de uma pagina de PDF: fatia literal, sem normalizar.

O que ele DESCARTA, e por que isso nao viola verbatim
-----------------------------------------------------
`<script>`, `<style>` e comentarios sao removidos: nao sao texto que alguem
escreveu para ser lido. Entidades (`&amp;`, `&#8217;`) sao resolvidas para o
caractere que elas representam - `&amp;` NAO e uma citacao de "&amp;", e uma
citacao de "&". As duas decisoes fazem parte da definicao do texto derivado, e
por isso elas entram na versao do algoritmo: `verify_unit` re-deriva pelo mesmo
caminho e compara byte a byte com o que foi guardado.
"""

from __future__ import annotations

import sys
from html.parser import HTMLParser

__all__ = [
    "HTML_EXTRACTION_VERSION",
    "HTML_MEDIA_TYPE",
    "extract_html_document",
    "extract_html_text",
]

HTML_MEDIA_TYPE = "text/html"

_ALGORITHM_VERSION = "1"
_PY = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

HTML_EXTRACTION_VERSION = f"html-blocks/v{_ALGORITHM_VERSION}+python{_PY}"
"""Identidade do extrator de HTML, embutida em todo `unit_id` que ele produz.

Separada da do PDF de proposito: os dois tem algoritmos, bibliotecas e ciclos
de vida diferentes, e uma versao unica faria o upgrade de um invalidar as
unidades do outro."""

# Elementos cujo conteudo NAO e texto para leitura humana.
_IGNORADOS = frozenset({"script", "style", "noscript", "head", "title"})

# Elementos que terminam um paragrafo. A lista e declarada, e nao derivada de
# uma propriedade CSS: derivar exigiria interpretar folha de estilo, e o
# resultado dependeria de como o gerador do arquivamento escreveu o CSS.
_BLOCO = frozenset(
    {
        "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "thead", "tbody", "section", "article", "header", "footer",
        "blockquote", "pre", "hr", "dt", "dd", "figcaption", "caption",
    }
)

# Dentro de uma linha de tabela, as celulas sao separadas por espaco em vez de
# quebra: uma tabela financeira lida com uma celula por linha vira uma coluna
# de numeros sem rotulo, e a citacao perde justamente o que a torna legivel.
_CELULA = frozenset({"td", "th"})


class _Texto(HTMLParser):
    """Acumula o texto visivel e o caminho de onde ele veio.

    `convert_charrefs=True` (o default) resolve entidades no proprio parser -
    e o que faz `&amp;` chegar aqui como `&`.

    O caminho e rastreado porque `UnitLocator` exige `node_path` para o esquema
    HTML, e com razao: um endereco de citacao em HTML que fosse so um offset
    nao diria a um humano onde conferir. O caminho e o do elemento que ABRIU o
    paragrafo, com indice entre irmaos de mesmo nome - `html/body/div[2]/p[5]`
    -, e ele e reproduzivel porque sai do mesmo parser deterministico.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        self._ignorando = 0
        # Pilha de (tag, indice) e, por nivel, quantos irmaos de cada nome ja
        # foram abertos. Sem os contadores o caminho seria `div/div/p`, que
        # nao distingue o quinto paragrafo do primeiro.
        self._pilha: list[tuple[str, int]] = []
        self._irmaos: list[dict[str, int]] = [{}]
        self._marcas: list[tuple[int, str]] = []
        self._tamanho = 0

    # -- caminho -------------------------------------------------------------

    def _caminho(self) -> str:
        return "/".join(f"{tag}[{i}]" for tag, i in self._pilha) or "html[1]"

    def _marcar(self) -> None:
        """Registra o caminho valido a partir do proximo caractere."""
        self._marcas.append((self._tamanho, self._caminho()))

    # -- parser --------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _IGNORADOS:
            self._ignorando += 1
            return
        contadores = self._irmaos[-1]
        contadores[tag] = contadores.get(tag, 0) + 1
        self._pilha.append((tag, contadores[tag]))
        self._irmaos.append({})
        if tag in _BLOCO:
            self._quebra()
            self._marcar()
        elif tag in _CELULA:
            self._espaco()

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORADOS:
            # `max(0, ...)`: HTML de arquivamento tem tag de fechamento sem
            # abertura correspondente com alguma frequencia, e um contador
            # negativo faria o resto do documento ser descartado em silencio.
            self._ignorando = max(0, self._ignorando - 1)
            return
        # Fechamento sem abertura correspondente: ignora em vez de desempilhar
        # o elemento errado, que embaralharia todos os caminhos seguintes.
        for nivel in range(len(self._pilha) - 1, -1, -1):
            if self._pilha[nivel][0] == tag:
                del self._pilha[nivel:]
                del self._irmaos[nivel + 1 :]
                break
        if tag in _BLOCO:
            self._quebra()

    def handle_data(self, data: str) -> None:
        if self._ignorando:
            return
        # Espaco em HTML e colapsavel por definicao do formato - o navegador
        # colapsa, e o autor escreveu contando com isso. Preservar a indentacao
        # do gerador produziria citacoes cheias de espaco que ninguem digitou.
        texto = " ".join(data.split())
        if texto:
            if not self._marcas:
                self._marcar()
            self._partes.append(texto)
            self._tamanho += len(texto)

    def _quebra(self) -> None:
        if self._partes and self._partes[-1] != "\n\n":
            self._partes.append("\n\n")
            self._tamanho += 2

    def _espaco(self) -> None:
        if self._partes and not self._partes[-1].endswith((" ", "\n")):
            self._partes.append(" ")
            self._tamanho += 1

    # -- saida ---------------------------------------------------------------

    def resultado(self) -> tuple[str, tuple[tuple[int, str], ...]]:
        bruto = "".join(self._partes)
        # Sequencias de quebra viram exatamente duas: e o separador que o
        # algoritmo de blocos reconhece, e o mesmo que uma pagina de PDF usa.
        # O `strip` por linha desloca offsets, entao as marcas sao reancoradas
        # pela ORDEM: a n-esima marca vale a partir do n-esimo paragrafo. E
        # aproximado como endereco e exato como texto, e e o texto que a
        # verificacao compara.
        linhas = [linha.strip() for linha in bruto.split("\n\n")]
        limpo = "\n\n".join(linha for linha in linhas if linha)
        return limpo, tuple(self._marcas)


def extract_html_document(payload: bytes) -> tuple[str, tuple[tuple[int, str], ...]]:
    """Bytes -> (texto derivado, marcas de caminho).

    Cada marca e `(offset no texto bruto, node_path)`, em ordem. Quem monta
    unidade escolhe a ultima marca que comeca antes do bloco.
    """
    parser = _Texto()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    return parser.resultado()


def extract_html_text(payload: bytes) -> str:
    """Bytes de HTML -> o texto derivado, uma string.

    Isolada de `extract` pela mesma razao que `extract_pdf_page_texts`: e
    exatamente o que `verify_unit` precisa re-rodar para conferir uma citacao
    byte a byte.

    A decodificacao e UTF-8 com `errors="replace"`. Arquivamento da SEC declara
    UTF-8 e as vezes carrega um byte solto de outra codificacao; substituir e
    deterministico e visivel na propria citacao, enquanto adivinhar a
    codificacao produziria texto diferente conforme a biblioteca instalada.
    """
    return extract_html_document(payload)[0]

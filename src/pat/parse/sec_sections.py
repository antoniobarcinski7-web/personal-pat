"""As secoes de um formulario da SEC, pelo numero de item que o regulador define.

Um 10-K nao tem `<h1>`. Conferido no arquivamento real da Netflix: ZERO tags de
cabecalho em 2 MB de HTML. A estrutura existe so no texto, na forma dos itens
da Regulation S-K - "Item 1A. Risk Factors" -, e ela e DECLARADA pelo
regulador, nao inferida por formatacao.

Por que isso importa
--------------------
Sem secao, as 10 mil unidades de um 10-K sao blocos planos, e buscar "risco"
devolve o SUMARIO do documento. Foi exatamente o que aconteceu na primeira
busca real: o trecho mais bem pontuado foi a palavra `COMPETITION` sozinha,
dentro de `table[1]/tr[28]` - uma linha do indice, nao um argumento.

O problema do indice, e como ele se resolve sem adivinhar
---------------------------------------------------------
Cada item aparece DUAS vezes: uma no indice e outra no corpo. Distinguir por
formatacao - "o do indice esta numa tabela", "o do corpo esta em negrito" -
seria inferencia sobre como aquele emissor montou o arquivo, e quebraria no
proximo.

A regra sai da ESTRUTURA do documento, e nao da formatacao: **um cabecalho de
secao carrega o nome da secao junto do numero; uma linha de indice e so o
numero.** No texto derivado de um 10-K real isso aparece assim:

    indice      `Item 1.` \\n\\n `Business`    numero sozinho no bloco
    cabecalho   `Item 1.Business` \\n\\n       numero e nome no mesmo bloco
    referencia  `...conforme o Item 8 deste`   nao abre bloco

O indice separa numero e titulo porque sao celulas de uma tabela de duas
colunas; o cabecalho e um elemento so. Isso nao e uma observacao sobre
negrito ou tamanho de fonte - e sobre onde o emissor pos as fronteiras de
bloco, que e a mesma coisa que o extrator ja usa para fatiar unidade.

Duas redes de seguranca depois disso: os itens tem que avancar em ordem
(formulario percorre os seus uma vez), e uma secao de quarenta caracteres nao
e uma secao - `MIN_SECTION_CHARS` derruba o que sobrar.

O que este modulo NAO faz
-------------------------
Nao interpreta o conteudo da secao, nao resume e nao decide se ela e relevante.
Ele diz onde cada secao comeca e termina, e o nome que o regulador deu a ela.

Limitacao conhecida: o rabo do documento
----------------------------------------
As demonstracoes financeiras, as notas e os anexos vem DEPOIS do ultimo item e
nao tem marcador proprio. No 10-K da Netflix o cabecalho do Item 8 existe e e
detectado, mas as demonstracoes a que ele se refere so aparecem 110 mil
caracteres adiante, atras dos Itens 9 a 16 - entao esse trecho fica rotulado
com o ULTIMO item detectado, e nao com o Item 8.

Isso e imprecisao declarada, e nao um erro que da para consertar com mais
regra: o documento nao publica a fronteira. Quem cita uma nota explicativa
recebe `section_path` do ultimo item, e o `unit_id` continua conferivel byte a
byte - o endereco fica menos util, nunca falso sobre o texto.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = [
    "FORM_SECTIONS",
    "MIN_SECTION_CHARS",
    "Section",
    "sections_for_form",
    "split_sections",
]

MIN_SECTION_CHARS = 400
"""Abaixo disto nao e secao, e sim linha de indice que sobreviveu.

O numero e generoso de proposito: um item legitimo mas curto - "Item 6.
[Reserved]", "Item 4. Mine Safety Disclosures" para quem nao tem mina - some
junto, e isso e aceitavel. Perder a fronteira de uma secao vazia custa nada;
rotular o indice como Risk Factors custa a busca inteira.
"""

# Tabela DECLARADA: numero do item -> nome, pela Regulation S-K. Nao e deduzida
# do texto do documento, e nao usa o titulo que o emissor escreveu - dois
# emissores escrevem o titulo do Item 7 de formas ligeiramente diferentes, e
# casar por titulo seria casar por rotulo, que este projeto recusa em todo
# lugar.
_10K = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "Reserved",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits, Financial Statement Schedules",
    "16": "Form 10-K Summary",
}

_10Q = {
    "1": "Financial Statements",
    "2": "Management's Discussion and Analysis",
    "3": "Quantitative and Qualitative Disclosures About Market Risk",
    "4": "Controls and Procedures",
    "1LEGAL": "Legal Proceedings",
    "1A": "Risk Factors",
    "2UNREG": "Unregistered Sales of Equity Securities",
    "5": "Other Information",
    "6": "Exhibits",
}

FORM_SECTIONS: dict[str, dict[str, str]] = {
    "10-K": _10K,
    "10-K/A": _10K,
    "10-Q": _10Q,
    "10-Q/A": _10Q,
}
"""Forma -> itens que ela tem. Forma ausente nao tem secao declarada, e o
documento continua citavel sem `section_path` - o que e honesto: dizer que um
8-K tem "Item 1A. Risk Factors" seria inventar estrutura."""

# `Item 1A.` ou `Item 1A` seguido de titulo. O ponto e opcional porque nem todo
# emissor o escreve, e o espaco tambem: a Netflix publica `Item 1.Business`,
# sem espaco, no corpo - e COM espaco no indice.
_MARCADOR = re.compile(r"\bItem\s*(\d{1,2}[A-C]?)\s*[.:\u2014-]?", re.IGNORECASE)
"""O marcador NAO consome o espaco depois dele, e isso e essencial.

Com um `\\s*` no fim, o casamento engolia a quebra de linha que separa o
numero do titulo no indice, e `Item 1.` + `\\n\\n` + `Business` passava a
parecer um cabecalho `Item 1.Business`. O predicado que distingue os dois
precisa VER o que vem logo depois do marcador."""


@dataclass(frozen=True)
class Section:
    """Uma secao do formulario, com onde ela comeca e termina no texto."""

    item: str
    """Numero do item, normalizado em maiuscula: `1A`, `7`, `7A`."""

    name: str
    """Nome pelo regulador. Nunca o titulo que o emissor escreveu."""

    char_start: int
    char_end: int

    @property
    def path(self) -> tuple[str, ...]:
        """A forma que vai para `DocumentUnit.section_path`."""
        return (f"Item {self.item}", self.name)


def sections_for_form(form: str) -> dict[str, str]:
    return FORM_SECTIONS.get(form.upper(), {})


def split_sections(text: str, *, form: str) -> tuple[Section, ...]:
    """Texto derivado -> secoes, pela ultima sequencia crescente de itens.

    Devolve vazio quando a forma nao tem itens declarados ou quando o texto nao
    contem uma sequencia reconhecivel. Vazio e resposta legitima: o documento
    continua citavel, so que sem endereco de secao.
    """
    itens = sections_for_form(form)
    if not itens:
        return ()

    candidatos = [
        (m.start(), _normalizar(m.group(1)))
        for m in _MARCADOR.finditer(text)
        if _normalizar(m.group(1)) in itens and _e_cabecalho(text, m)
    ]
    if len(candidatos) < 2:
        return ()

    aceitos = _em_ordem_crescente(candidatos)
    secoes: list[Section] = []
    for i, (inicio, item) in enumerate(aceitos):
        fim = aceitos[i + 1][0] if i + 1 < len(aceitos) else len(text)
        if fim - inicio < MIN_SECTION_CHARS:
            # Curta demais para ser secao - "Item 6.[Reserved]", "Item 4. Not
            # applicable". Descarta a FRONTEIRA, e nao o texto: ele passa a
            # pertencer a secao anterior, que e onde um leitor o encontraria.
            continue
        secoes.append(Section(item=item, name=itens[item], char_start=inicio, char_end=fim))
    return tuple(secoes)


def _e_cabecalho(text: str, match: re.Match[str]) -> bool:
    """O marcador abre um bloco E carrega texto junto? Entao e cabecalho.

    A regra sai da estrutura do documento, e nao de formatacao, e ela separa os
    tres casos que o texto derivado de um 10-K real contem:

        indice      `Item 1.` \n\n `Business`   -> numero SOZINHO no bloco
        cabecalho   `Item 1.Business` \n\n      -> numero E nome juntos
        referencia  `...descrito no Item 8 desta`-> nao abre bloco

    O indice separa numero e titulo porque sao celulas diferentes de uma
    tabela de duas colunas; o cabecalho e um elemento so. Um indice cujo
    emissor juntasse as duas coisas ainda cairia no filtro de tamanho, que e a
    segunda rede.
    """
    inicio = match.start()
    if inicio != 0 and text[max(0, inicio - 2) : inicio] != "\n\n":
        return False
    fim_do_bloco = text.find("\n\n", match.end())
    resto = text[match.end() : fim_do_bloco if fim_do_bloco != -1 else len(text)]
    return bool(resto.strip())


def _em_ordem_crescente(
    candidatos: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Mantem so os candidatos que avancam na ordem dos itens.

    Um formulario percorre seus itens uma vez, em ordem. Um cabecalho que
    aparece fora de ordem e repeticao - tipicamente um cabecalho de pagina
    repetido - e aceita-lo criaria uma secao que volta no tempo.
    """
    aceitos: list[tuple[int, str]] = []
    ultimo = None
    for inicio, item in candidatos:
        ordem = _ordem(item)
        if ultimo is not None and ordem <= ultimo:
            continue
        aceitos.append((inicio, item))
        ultimo = ordem
    return aceitos


def _ordem(item: str) -> tuple[int, str]:
    """Ordem de leitura de um item: `1` < `1A` < `1B` < `2`."""
    numero = int("".join(c for c in item if c.isdigit()) or 0)
    sufixo = "".join(c for c in item if c.isalpha())
    return (numero, sufixo)


def _normalizar(bruto: str) -> str:
    return unicodedata.normalize("NFKD", bruto).upper().strip()

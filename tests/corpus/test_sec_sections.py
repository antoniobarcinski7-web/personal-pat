"""N6 - as secoes de um formulario da SEC.

O teste que define o milestone e `test_o_indice_nao_vira_secao`: antes disto, a
melhor evidencia que a busca por "concorrencia" devolvia era a palavra
`COMPETITION` sozinha, de uma linha do SUMARIO. Um sistema que cita o indice
como se fosse argumento passa por fundamentado sem ser.

O outro que importa e `test_a_regra_nao_olha_para_formatacao`: distinguir
indice de cabecalho por negrito ou por tabela seria inferencia sobre como
aquele emissor montou o arquivo, e quebraria no proximo.
"""

from __future__ import annotations

import pytest

from pat.parse.sec_sections import (
    FORM_SECTIONS,
    MIN_SECTION_CHARS,
    sections_for_form,
    split_sections,
)

CORPO = "x" * 2000


def documento(*, com_indice: bool = True) -> str:
    """Um 10-K na forma real: indice com numero e titulo em blocos SEPARADOS,
    corpo com numero e titulo JUNTOS."""
    partes = []
    if com_indice:
        for item, titulo, pagina in (
            ("1", "Business", "1"),
            ("1A", "Risk Factors", "4"),
            ("7", "Management's Discussion and Analysis", "19"),
            ("8", "Financial Statements", "30"),
        ):
            partes.append(f"Item {item}.")
            partes.append(titulo)
            partes.append(pagina)
    partes.append("Item 1.Business")
    partes.append(CORPO)
    partes.append("Item 1A.Risk Factors")
    partes.append(CORPO)
    partes.append("Item 7.Management's Discussion and Analysis")
    partes.append(CORPO)
    return "\n\n".join(partes)


# -- o criterio do milestone ------------------------------------------------


def test_o_indice_nao_vira_secao():
    """As entradas do sumario percorrem os mesmos itens e nao sao secoes.

    A distincao e estrutural: no indice o numero e um bloco SOZINHO - ele e
    uma celula de uma tabela de duas colunas -, e no corpo o numero vem junto
    do nome da secao.
    """
    texto = documento()
    secoes = split_sections(texto, form="10-K")

    assert [s.item for s in secoes] == ["1", "1A", "7"]
    # A primeira secao comeca DEPOIS do indice inteiro.
    assert texto.index("Item 1.Business") == secoes[0].char_start
    # E o indice fica fora de qualquer secao.
    assert secoes[0].char_start > texto.index("Item 1.")


def test_a_regra_nao_olha_para_formatacao():
    """Sem indice, o resultado e o mesmo: a regra e sobre bloco, nao sobre
    tabela, negrito ou numero de pagina."""
    com = split_sections(documento(com_indice=True), form="10-K")
    sem = split_sections(documento(com_indice=False), form="10-K")
    assert [s.item for s in com] == [s.item for s in sem]
    assert [s.name for s in com] == [s.name for s in sem]


def test_referencia_cruzada_nao_abre_secao():
    """"conforme o Item 8 deste relatorio" esta no meio de uma frase.

    Um marcador que nao abre bloco e citacao interna, e trata-la como
    cabecalho partiria a secao ao meio.
    """
    texto = "\n\n".join(
        [
            "Item 1.Business",
            "Somos uma companhia. Ver o Item 8. Financial Statements deste "
            "relatorio para os numeros. " + CORPO,
            "Item 1A.Risk Factors",
            CORPO,
        ]
    )
    secoes = split_sections(texto, form="10-K")
    assert [s.item for s in secoes] == ["1", "1A"]


# -- fronteiras --------------------------------------------------------------


def test_secao_curta_demais_nao_e_secao():
    """"Item 6.[Reserved]" nao delimita conteudo.

    A fronteira e descartada, e nao o texto: ele passa a pertencer a secao
    anterior, que e onde um leitor o encontraria de qualquer forma.
    """
    texto = "\n\n".join(
        [
            "Item 1.Business",
            CORPO,
            "Item 6.[Reserved]",
            "None.",
            "Item 7.Management's Discussion and Analysis",
            CORPO,
        ]
    )
    secoes = split_sections(texto, form="10-K")
    itens = [s.item for s in secoes]
    assert "6" not in itens
    assert itens == ["1", "7"]


def test_o_nome_vem_do_regulador_e_nao_do_emissor():
    """Casar pelo titulo que o emissor escreveu seria casar por rotulo.

    Dois emissores escrevem o titulo do Item 7 de formas ligeiramente
    diferentes; o numero do item e o mesmo para os dois.
    """
    texto = "\n\n".join(
        ["Item 1.Nosso Negocio Maravilhoso", CORPO, "Item 1A.Perigos", CORPO]
    )
    secoes = split_sections(texto, form="10-K")
    assert [s.name for s in secoes] == ["Business", "Risk Factors"]


def test_o_caminho_carrega_item_e_nome():
    secoes = split_sections(documento(), form="10-K")
    assert secoes[1].path == ("Item 1A", "Risk Factors")


def test_itens_fora_de_ordem_sao_repeticao():
    """Um formulario percorre os seus itens uma vez, em ordem.

    Cabecalho de pagina repetido reaparece fora de ordem, e aceita-lo criaria
    uma secao que volta no tempo.
    """
    texto = "\n\n".join(
        [
            "Item 1.Business",
            CORPO,
            "Item 7.Management's Discussion and Analysis",
            CORPO,
            "Item 1.Business",  # cabecalho repetido
            CORPO,
        ]
    )
    secoes = split_sections(texto, form="10-K")
    assert [s.item for s in secoes] == ["1", "7"]


# -- formas ------------------------------------------------------------------


def test_forma_sem_itens_declarados_nao_tem_secao():
    """Dizer que um 8-K tem "Item 1A. Risk Factors" seria inventar estrutura.

    O documento continua citavel sem `section_path` - o que e honesto.
    """
    assert sections_for_form("8-K") == {}
    assert split_sections(documento(), form="8-K") == ()


def test_o_trimestral_tem_a_propria_tabela():
    """O Item 2 de um 10-Q e a discussao da administracao; o de um 10-K e
    Properties. Uma tabela unica confundiria os dois."""
    anual = sections_for_form("10-K")
    trimestral = sections_for_form("10-Q")
    assert anual["2"] == "Properties"
    assert trimestral["2"] == "Management's Discussion and Analysis"


@pytest.mark.parametrize("forma", sorted(FORM_SECTIONS))
def test_toda_forma_declarada_tem_item_de_risco_ou_de_discussao(forma: str):
    """As duas secoes que respondem "o que a companhia diz" precisam existir
    em toda forma que declare secoes - senao o tema `narrativa` filtraria para
    um conjunto vazio."""
    itens = sections_for_form(forma)
    nomes = set(itens.values())
    assert "Risk Factors" in nomes or "Management's Discussion and Analysis" in nomes


def test_texto_sem_marcador_devolve_vazio_em_vez_de_inventar():
    assert split_sections("Um texto qualquer sem itens.\n\nOutro paragrafo.", form="10-K") == ()


def test_o_limite_de_tamanho_e_declarado():
    """Um numero magico escondido na funcao viraria ajuste sem discussao."""
    assert MIN_SECTION_CHARS > 0

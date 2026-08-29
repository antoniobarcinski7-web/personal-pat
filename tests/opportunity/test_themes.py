"""N3 - a pergunta de investimento vira agenda estruturada.

O teste que define o milestone e `test_pergunta_ampla_vira_investigacao`: uma
pergunta como "o que voce acha da companhia?" tem que produzir os temas que um
analista percorreria, e nao uma consulta de metrica.

Os dois que impedem o desenho de degenerar:

- `test_tema_sem_insumo_nao_vira_tarefa` - um tema cujo insumo nao existe nao
  entra na agenda. Encher a agenda de bloqueio previsivel treina o leitor a
  ignorar bloqueio.
- `test_a_tabela_nao_tem_vocabulario_de_industria` - a tabela e generica. Um
  gatilho como "assinantes" faria "investiga o churn" ser respondido com a
  serie de receita, uma substituicao que o analista nao pediu e nao veria.
"""

from __future__ import annotations

from pat.contracts.opportunity.agenda import TaskPriority
from pat.opportunity.themes import AMPLAS, THEMES, themes_for

TUDO = frozenset(
    {
        "receita_liquida@v1",
        "ebit@v1",
        "ebitda@v1",
        "margem_ebitda@v1",
        "lucro_liquido@v1",
        "fluxo_de_caixa_operacional@v1",
        "capex@v1",
        "fcf@v1",
        "caixa_e_equivalentes@v1",
        "divida_bruta@v1",
        "divida_liquida@v1",
        "divida_bruta_com_arrendamento@v1",
        "patrimonio_liquido@v1",
        "recompras@v1",
        "dividendos_pagos@v1",
        "retorno_ao_acionista@v1",
        "aliquota_efetiva@v1",
        "capital_investido@v1",
        "roe@v1",
        "roic@v1",
    }
)


# -- o criterio do milestone ------------------------------------------------


def test_pergunta_ampla_vira_investigacao():
    """"O que voce acha da companhia?" -> os temas de uma analise fundamentalista."""
    temas = themes_for("o que voce acha da companhia?", available=TUDO, has_corpus=True)
    slugs = [t.slug for t in temas]

    assert slugs == [
        "crescimento",
        "rentabilidade",
        "geracao-de-caixa",
        "solvencia",
        "retorno-sobre-capital",
        "alocacao-de-capital",
        "narrativa",
    ]
    # Cada tema traz uma PERGUNTA de analista, e nao um nome de metrica.
    assert all(t.question.endswith("?") for t in temas)


def test_pergunta_estreita_nao_vira_investigacao_inteira():
    """"Investiga a divida" pede um tema, e nao seis.

    Uma pergunta estreita que abrisse a agenda inteira gastaria a corrida e
    encheria o diario de tarefa que ninguem pediu.
    """
    temas = themes_for("investiga a divida da companhia", available=TUDO, has_corpus=True)
    assert [t.slug for t in temas] == ["solvencia"]


def test_tema_sem_insumo_nao_vira_tarefa():
    """Sem metrica de caixa, o tema de caixa nao entra.

    Ele viraria tarefa que so pode terminar em bloqueio, e bloqueio previsivel
    treina o leitor a ignorar bloqueio.
    """
    so_dre = frozenset({"receita_liquida@v1", "ebit@v1"})
    temas = themes_for("analise a companhia", available=so_dre, has_corpus=False)
    slugs = {t.slug for t in temas}

    assert "crescimento" in slugs
    assert "rentabilidade" in slugs
    assert "geracao-de-caixa" not in slugs
    assert "solvencia" not in slugs
    assert "narrativa" not in slugs, "sem corpus, nao ha o que citar"


def test_a_tabela_nao_tem_vocabulario_de_industria():
    """A tabela e generica; o especifico vive na pergunta que o humano digita.

    Um gatilho como "assinantes" faria "investiga o churn de assinantes" ser
    respondido com a serie de receita - substituicao silenciosa do que foi
    perguntado.
    """
    industria = {
        "assinantes", "churn", "saturacao", "streaming", "loja", "poco",
        "minerio", "agencia", "apolice",
    }
    for tema in THEMES:
        assert not (industria & set(tema.triggers)), tema.slug

    temas = themes_for("investiga o churn de assinantes", available=TUDO, has_corpus=True)
    assert temas == (), "nenhum tema responde churn; a agenda nao inventa um"


# -- forma da agenda --------------------------------------------------------


def test_a_dependencia_so_vale_se_o_tema_do_qual_depende_entrou():
    """Manter dependencia para tema ausente faria a tarefa esperar para sempre."""
    sem_caixa = frozenset({"divida_liquida@v1", "capex@v1", "fcf@v1"})
    temas = {t.slug: t for t in themes_for("alocacao de capital", available=sem_caixa, has_corpus=False)}

    alocacao = temas["alocacao-de-capital"]
    assert "geracao-de-caixa" not in temas
    assert alocacao.depends_on == ()


def test_a_dependencia_sobrevive_quando_os_dois_entram():
    temas = {t.slug: t for t in themes_for("analise", available=TUDO, has_corpus=False)}
    assert temas["alocacao-de-capital"].depends_on == ("geracao-de-caixa",)


def test_a_ordem_e_a_da_tabela_e_nao_a_da_frase():
    """Uma agenda cuja ordem muda conforme a frase digitada nao se compara
    entre duas investigacoes da mesma empresa."""
    uma = themes_for("divida e receita", available=TUDO, has_corpus=False)
    outra = themes_for("receita e divida", available=TUDO, has_corpus=False)
    assert [t.slug for t in uma] == [t.slug for t in outra]


def test_todo_tema_tem_criterio_de_conclusao_conferivel():
    """`completion_criteria` existe para impedir conclusao por sensacao.

    "Levantei a metrica" e sempre verdade; o criterio precisa dizer quantos
    periodos, ou que a recusa foi nomeada.
    """
    for tema in THEMES:
        assert len(tema.completion_criteria) > 60, tema.slug
        assert any(
            marca in tema.completion_criteria
            for marca in ("pelo menos", "recusa", "EXPLICITO", "verbatim")
        ), tema.slug


def test_nenhum_tema_propoe_hipotese():
    """A decisao de `reason.py` continua valendo: nenhuma regra sabe qual
    afirmacao vale a pena testar NESTA empresa.

    O que o tema faz e abrir PERGUNTA - honesta sobre o que falta - em vez de
    inventar hipotese por template, que daria a mesma para todas.
    """
    for tema in THEMES:
        assert not hasattr(tema, "hypothesis")
        assert tema.open_questions, tema.slug


def test_os_temas_de_prioridade_alta_sao_os_que_definem_a_companhia():
    """Quatro temas dizem o que a companhia E; os outros dizem o que ela FAZ
    com o que gera, e dependem dos primeiros para significar alguma coisa."""
    altos = {t.slug for t in THEMES if t.priority is TaskPriority.HIGH}
    assert altos == {
        "crescimento",
        "rentabilidade",
        "geracao-de-caixa",
        "retorno-sobre-capital",
    }


def test_a_busca_no_corpus_e_declarada_e_nao_derivada_da_prosa():
    """Derivar termos da pergunta produzia busca em portugues contra
    arquivamento em ingles - `no_match` em todo tema."""
    narrativa = next(t for t in THEMES if t.slug == "narrativa")
    assert narrativa.search_terms
    # Ha grupo em cada lingua, porque o corpus esta na lingua do emissor.
    todos = {termo for grupo in narrativa.search_terms for termo in grupo}
    assert "competition" in todos
    assert "concorrencia" in todos

    # Os temas quantitativos NAO buscam: quem quer busca usa `pat evidence`.
    for tema in THEMES:
        if tema.slug != "narrativa":
            assert tema.search_terms == (), tema.slug


def test_as_marcas_de_pergunta_ampla_sao_declaradas():
    """Casar "analise" por semelhanca com "analisar a divida" faria a pergunta
    estreita virar investigacao inteira."""
    assert "tese" in AMPLAS
    assert "o que voce acha" in AMPLAS
    # E uma frase que so contem a palavra dentro de outra nao dispara.
    temas = themes_for("qual a analiticidade do modelo", available=TUDO, has_corpus=False)
    assert len(temas) < len(THEMES)

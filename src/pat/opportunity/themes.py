"""N3 - os temas de uma investigacao fundamentalista. Tabela DECLARADA.

Ate aqui, "investiga a margem" virava uma tarefa por metrica que a tabela de
termos reconhecia: consultas isoladas, sem criterio de conclusao que valesse
alguma coisa. Uma pergunta de investimento nao se decompoe em metricas - ela se
decompoe em TEMAS, e cada tema pergunta uma coisa que um analista reconheceria.

    crescimento        a receita cresce, e a que ritmo?
    rentabilidade      o crescimento chega ao resultado operacional?
    geracao-de-caixa   o resultado vira caixa?
    solvencia          a estrutura de capital aguenta?
    alocacao-de-capital para onde vai o caixa gerado?
    narrativa          o que a companhia diz sobre isso?

Por que temas, e nao um planejador que "decide"
------------------------------------------------
Um planejador livre produziria uma decomposicao diferente a cada execucao, e
duas investigacoes da mesma empresa nao seriam comparaveis. A tabela e fixa,
versionada com o codigo, e o que ela decide e apenas QUAIS temas se aplicam -
pela disponibilidade real de metrica, nunca por semelhanca de rotulo.

O que um tema NAO faz
---------------------
Nao abre hipotese. A decisao ja estava registrada em `reason.py` e continua
valendo: uma hipotese e uma afirmacao sobre o mundo com falsificador junto, e
nenhuma regra sabe qual afirmacao vale a pena testar NESTA empresa. Propor uma
por template daria a mesma hipotese para todas.

O que ele faz, alem de tarefas, e abrir PERGUNTAS - as que o motor nao responde
e um humano precisa responder. Uma pergunta aberta na agenda e honesta; uma
hipotese inventada por template nao e.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pat.contracts.opportunity.agenda import TaskPriority

__all__ = ["THEMES", "Theme", "themes_for"]


@dataclass(frozen=True)
class Theme:
    """Um tema de investigacao: o que perguntar, com que metricas, e quando parar."""

    slug: str
    question: str
    """A pergunta do analista, em prosa. Vira o objetivo da tarefa."""

    metrics: tuple[str, ...]
    """Metricas que respondem o tema. O tema so entra na agenda se PELO MENOS
    uma delas estiver disponivel - um tema sem insumo nenhum viraria tarefa
    que so pode terminar em bloqueio."""

    completion_criteria: str
    """O que faz a tarefa estar pronta. Concreto o bastante para ser conferido
    por outra pessoa - `completion_criteria` existe para impedir que a tarefa
    seja concluida por sensacao."""

    priority: TaskPriority = TaskPriority.NORMAL
    triggers: tuple[str, ...] = ()
    """Palavras que fazem o tema ser escolhido numa pergunta especifica. Vazio
    significa "so entra numa investigacao ampla"."""

    open_questions: tuple[str, ...] = ()
    """O que o motor NAO responde e um humano precisa responder. Elas viram
    `QuestionOpened` na agenda em vez de virarem hipotese inventada."""

    needs_corpus: bool = False
    depends_on: tuple[str, ...] = field(default=())

    sections: tuple[str, ...] = ()
    """Secoes do documento onde a busca deste tema faz sentido.

    Vazio busca no documento inteiro, INCLUSIVE na capa e no indice - e foi
    exatamente isso que fez a primeira busca real devolver a palavra
    `COMPETITION` sozinha, de uma linha de sumario, como melhor evidencia sobre
    concorrencia.

    O endereco e o numero do item, e nao o nome: o nome vem da tabela do
    regulador e pode ser reescrito; o numero e o que o proprio formulario
    declara. Documento sem secao reconhecida ignora o filtro, o que mantem
    citavel o que ainda nao tem estrutura.
    """

    search_terms: tuple[tuple[str, ...], ...] = ()
    """Grupos de termos para buscar no corpus. Cada grupo vira UMA busca.

    Declarados, e nao derivados da pergunta em prosa. Derivar produzia
    exatamente o erro que aparece quando as duas linguas se encontram: a
    pergunta "a receita cresce, e a que ritmo?" virava a busca
    `['receita', 'cresce', 'ritmo']` contra um arquivamento em ingles, e o
    resultado era `no_match` em todo tema - recusa correta para uma pergunta
    que o sistema nao deveria ter feito.

    Ha um grupo por lingua porque o corpus de uma companhia esta na lingua em
    que ela publica. O grupo que nao casa devolve `no_match` nomeado, que e
    barulho honesto: diz que se procurou e nao se achou, e nao que a companhia
    nao falou do assunto.
    """

THEMES: tuple[Theme, ...] = (
    Theme(
        slug="crescimento",
        question="a receita cresce, e a que ritmo?",
        metrics=("receita_liquida@v1",),
        completion_criteria=(
            "receita liquida calculada pelo motor em pelo menos tres exercicios, "
            "com a forma da serie declarada - inclusive se ela nao for monotonica"
        ),
        priority=TaskPriority.HIGH,
        # Sem "assinantes" nem "saturacao": sao vocabulario de UMA industria, e
        # um gatilho assim faria "investiga o churn" ser respondido com a serie
        # de receita - uma substituicao que o analista nao pediu e nao veria.
        # A tabela e generica; o que e especifico da companhia vive na pergunta
        # que o humano digita.
        triggers=("crescimento", "receita", "growth", "revenue"),
        open_questions=(
            "o crescimento veio de preco, de volume ou de mix? o motor da o total, "
            "e a abertura vive na discussao da administracao",
        ),
    ),
    Theme(
        slug="rentabilidade",
        question="o crescimento chega ao resultado operacional?",
        metrics=("ebit@v1", "margem_ebitda@v1", "ebitda@v1", "lucro_liquido@v1"),
        completion_criteria=(
            "resultado operacional em pelo menos tres exercicios, e a decomposicao "
            "de EBIT fechando com residual dentro da tolerancia - ou uma recusa "
            "nomeada dizendo qual insumo faltou"
        ),
        priority=TaskPriority.HIGH,
        triggers=("margem", "rentabilidade", "ebit", "ebitda", "lucro", "margin", "profit"),
        open_questions=(
            "a margem atual e sustentavel ou depende de um item que nao se repete? "
            "o motor nao separa recorrente de nao recorrente",
        ),
    ),
    Theme(
        slug="geracao-de-caixa",
        question="o resultado vira caixa?",
        metrics=("fluxo_de_caixa_operacional@v1", "fcf@v1", "capex@v1"),
        completion_criteria=(
            "caixa operacional e capex em pelo menos tres exercicios, com o FCF "
            "resultante e a FIDELIDADE dele visivel - um FCF `partial` nao se "
            "compara com o de quem classifica capex de outro jeito"
        ),
        priority=TaskPriority.HIGH,
        triggers=("caixa", "fcf", "capex", "cash", "conversao", "investimento"),
        open_questions=(
            "o capex atual e de manutencao ou de expansao? a divisao nao esta "
            "publicada em linha propria em nenhum dos dois regimes",
        ),
    ),
    Theme(
        slug="solvencia",
        question="a estrutura de capital aguenta?",
        metrics=(
            "divida_bruta@v1",
            "divida_liquida@v1",
            "caixa_e_equivalentes@v1",
            "divida_bruta_com_arrendamento@v1",
        ),
        completion_criteria=(
            "divida bruta e liquida na data-base mais recente, e o tratamento de "
            "arrendamento EXPLICITO - com a metrica que o inclui, ou com a recusa "
            "dizendo que o regime nao publica a linha"
        ),
        triggers=("divida", "alavancagem", "solvencia", "caixa", "debt", "leverage"),
        open_questions=(
            "qual o perfil de vencimento da divida? o saldo nao diz quando vence, "
            "e a diferenca entre concentrado e diluido e a diferenca entre risco e "
            "cronograma",
        ),
    ),
    Theme(
        slug="retorno-sobre-capital",
        question="o capital empregado rende mais do que custa?",
        metrics=("roic@v1", "roe@v1", "capital_investido@v1", "aliquota_efetiva@v1"),
        completion_criteria=(
            "ROIC e ROE em pelo menos tres exercicios, ou uma recusa nomeada - "
            "patrimonio ou capital investido nao positivo torna a razao sem "
            "leitura, e isso e resposta, nao falha"
        ),
        priority=TaskPriority.HIGH,
        triggers=("roic", "roe", "retorno", "capital empregado", "eficiencia"),
        open_questions=(
            "quanto custa o capital desta companhia? o motor calcula o retorno e "
            "nao o custo - o WACC e premissa do analista, nunca fato do sistema",
        ),
    ),
    Theme(
        slug="alocacao-de-capital",
        question="para onde vai o caixa gerado?",
        metrics=(
            "fcf@v1",
            "capex@v1",
            "recompras@v1",
            "dividendos_pagos@v1",
            "retorno_ao_acionista@v1",
            "divida_liquida@v1",
        ),
        completion_criteria=(
            "FCF, capex, devolucao ao acionista e variacao da divida liquida em "
            "pelo menos dois exercicios - as quatro pontas por onde o caixa sai. "
            "Companhia que nao paga dividendo produz recusa nomeada, e a recusa "
            "faz parte da resposta"
        ),
        triggers=("alocacao", "dividendo", "recompra", "buyback", "capital"),
        open_questions=(
            "a devolucao ao acionista e sustentavel pelo FCF, ou vem de divida? o "
            "motor da as duas series; a leitura de sustentabilidade e do analista",
        ),
        depends_on=("geracao-de-caixa",),
    ),
    Theme(
        slug="narrativa",
        question="o que a companhia diz sobre o proprio negocio e os riscos?",
        metrics=(),
        completion_criteria=(
            "pelo menos um trecho verbatim citado do arquivamento mais recente, "
            "com `unit_id` que `pat provenance-unit` reconfere - ou uma recusa "
            "dizendo que nao ha documento no corpus"
        ),
        triggers=("risco", "negocio", "concorrencia", "drivers", "estrategia", "risk"),
        needs_corpus=True,
        # Item 1A e a discussao de riscos; Item 7 e a discussao da
        # administracao sobre o negocio. Sao as duas secoes que respondem
        # "o que a companhia diz", e restringir a elas e o que impede o
        # sumario de ganhar o ranking.
        sections=("Item 1A", "Item 7", "Item 1"),
        search_terms=(
            ("competition",),
            ("risk", "factors"),
            ("concorrencia",),
            ("fatores", "risco"),
        ),
        open_questions=(
            "o que a administracao apresenta como o principal risco, e isso mudou "
            "entre os arquivamentos?",
        ),
    ),
)
"""Os temas, na ordem em que uma investigacao os percorre.

A ordem importa: `alocacao-de-capital` depende de `geracao-de-caixa`, e a
agenda respeita a dependencia. Nao ha ciclo, e `ResearchAgenda` recusaria um.
"""

# Palavras que indicam uma pergunta AMPLA - "o que voce acha da Netflix?",
# "analise a companhia", "monte a tese". Elas nao selecionam um tema: elas
# selecionam TODOS os que tem insumo.
#
# A lista e declarada pela mesma razao que `TERMOS_DE_METRICA`: casar
# "analise" por semelhanca com "analisar a divida" faria a pergunta estreita
# virar investigacao inteira, e a agenda encheria de tarefa que ninguem pediu.
AMPLAS: tuple[str, ...] = (
    "o que voce acha",
    "o que vc acha",
    "analise",
    "analisa",
    "avalie",
    "avalia",
    "tese",
    "panorama",
    "visao geral",
    "fundamentos",
    "vale a pena",
)


def themes_for(objective: str, *, available: frozenset[str], has_corpus: bool) -> tuple[Theme, ...]:
    """Objetivo -> temas aplicaveis, pela tabela e pela cobertura REAL.

    Duas filtragens, e as duas importam:

    1. **Relevancia**, por palavra declarada. Pergunta ampla escolhe todos;
       pergunta estreita escolhe os temas que ela cita.
    2. **Viabilidade**, pela metrica disponivel. Um tema cujo insumo nao existe
       nao vira tarefa - viraria tarefa que so pode terminar em bloqueio, e
       encher a agenda de bloqueio previsivel treina o leitor a ignorar
       bloqueio.

    A ordem de saida e a de `THEMES`, e nao a de relevancia: uma agenda cuja
    ordem muda conforme a frase digitada nao se compara entre duas
    investigacoes da mesma empresa.
    """
    baixo = _sem_acento(objective)
    ampla = any(marca in baixo for marca in AMPLAS)

    escolhidos: list[Theme] = []
    for tema in THEMES:
        relevante = ampla or any(t in baixo for t in tema.triggers)
        if not relevante:
            continue
        if tema.needs_corpus:
            if has_corpus:
                escolhidos.append(tema)
            continue
        if any(m in available for m in tema.metrics):
            escolhidos.append(tema)

    # Dependencia so vale se o tema do qual se depende entrou. Manter uma
    # dependencia para um tema ausente faria a tarefa esperar para sempre.
    presentes = {t.slug for t in escolhidos}
    return tuple(
        Theme(
            **{
                **tema.__dict__,
                "depends_on": tuple(d for d in tema.depends_on if d in presentes),
            }
        )
        for tema in escolhidos
    )


def _sem_acento(texto: str) -> str:
    import unicodedata

    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")

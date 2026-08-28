"""O11 - a investigacao inteira, do zero a tese, e depois de novo do disco.

Um teste longo de proposito. Os testes por milestone provam cada peca; este
prova que elas se encaixam na ordem em que uma pessoa de fato usa o sistema:

    empresa -> workspace -> agenda -> consulta ao motor -> evidencia textual
    -> hipotese -> contra-evidencia -> critico -> conclusao -> claim
    -> valuation -> tese -> auditoria

E depois `test_a_investigacao_sobrevive_ao_processo`, que e o outro criterio:
a dobra do diario relido do disco tem que ser IGUAL, campo a campo, ao estado
que estava em memoria. Nao "equivalente" - igual. Um sistema que perde uma
tentativa de falsificacao no caminho passaria num teste de igualdade frouxa e
so mostraria o erro semanas depois, como uma critica que parou de aparecer.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pat.contracts.opportunity import (
    Actor,
    Assumption,
    AssumptionBasis,
    AssumptionSet,
    Claim,
    ClaimAsserted,
    ConclusionDrawn,
    CounterEvidence,
    CounterEvidenceAdded,
    DataPoint,
    EvidenceKind,
    EvidenceLink,
    EvidenceLinked,
    FalsificationAttempted,
    HypothesisOpened,
    HypothesisStatus,
    HypothesisStatusChanged,
    HypothesisStrength,
    InvestmentThesis,
    Risk,
    RiskSeverity,
    Severity,
    ThesisDirection,
    ThesisDrafted,
    TurnRequest,
    ValuationDeclared,
    ValuationModel,
    WorkspaceFinding,
)
from pat.opportunity import (
    ChatAgent,
    PatTools,
    ShapeReasoner,
    audit_thesis,
    create_workspace,
    critique,
    falsification_agenda,
    open_workspace,
    profile_for,
    run_dcf,
)
from pat.corpus.extract import extract
from pat.corpus.index import build_index
from pat.store.corpus import write_documents, write_units
from tests.corpus.conftest import make_document, make_pdf
from tests.opportunity.conftest import CREATED_AT
from tests.opportunity.warehouses import INTEL_ENTITY
from tests.semantics import golden_gpa as gpa

AS_OF = date(2026, 6, 30)
AGORA = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)

TRECHO = (
    "A margem bruta reflete o ganho de escala nas compras, parcialmente "
    "compensado por maior concorrencia regional no atacado."
)


def _sha(semente: str) -> str:
    return hashlib.sha256(semente.encode("utf-8")).hexdigest()


@pytest.fixture
def corpus(br_warehouse):
    """Um release, do PDF ate o indice, pelo caminho de producao.

    O documento passa por `extract` de verdade em vez de ter unidades escritas
    a mao: e o que faz o `assert citacao.text == TRECHO` mais adiante provar
    verbatim byte a byte, e nao provar que o teste sabe copiar uma string.
    """
    pdf = make_pdf([[TRECHO]])
    doc_id = _sha("release-3t25")
    write_documents(
        br_warehouse,
        [
            make_document(
                doc_id,
                entity_id=gpa.ENTITY_ID,
                published_at=date(2025, 11, 7),
                title="Release de Resultados 3T25",
                byte_size=len(pdf),
            )
        ],
    )
    resultado = extract(doc_id, pdf)
    assert resultado.failure is None, resultado.failure
    write_units(br_warehouse, resultado.units)
    build_index(br_warehouse)
    return br_warehouse


@pytest.fixture
def tools(corpus):
    return PatTools(corpus, company=profile_for(corpus, gpa.ENTITY_ID), as_of=AS_OF)


@pytest.fixture
def ws(root, corpus):
    return create_workspace(
        root,
        company=profile_for(corpus, gpa.ENTITY_ID),
        as_of=AS_OF,
        created_at=CREATED_AT,
        title="GPA - qualidade do resultado",
        mandate="o resultado operacional se sustenta sem equivalencia patrimonial?",
    )


# ---------------------------------------------------------------------------
# A jornada
# ---------------------------------------------------------------------------


def test_a_investigacao_inteira(ws, tools, corpus, root):
    agente = ChatAgent(ws, tools, ShapeReasoner())

    def fala(texto: str):
        return agente.respond(TurnRequest(text=texto, workspace_id=ws.workspace_id))

    # -- 1. onde estamos, antes de qualquer coisa ---------------------------
    inicio = fala("Onde estamos?")
    assert "GPA" in inicio.text or "DISTRIBUICAO" in inicio.text
    assert not inicio.changed_workspace

    # -- 2. pesquisa autonoma ------------------------------------------------
    pesquisa = fala("Investiga a margem e a receita.")
    assert pesquisa.tasks_touched
    assert pesquisa.grounded_in, "a pesquisa tem que voltar com enderecos"
    assert ws.state.agenda.tasks

    # Os achados gravados apontam para resultados do motor, e nao para prosa.
    achados = [f for t in ws.state.agenda.tasks for f in t.findings]
    assert any(f.result_ids for f in achados)

    # -- 3. evidencia textual, atribuida ------------------------------------
    busca = tools.evidence(("margem", "escala"))
    assert getattr(busca, "hits", ()), "o corpus tem o release; a busca tem que achar"
    citacao = busca.hits[0].quote
    # Verbatim byte a byte contra a unidade gravada, que e a fatia literal da
    # pagina. Comparar com a constante do teste provaria menos: provaria que o
    # teste sabe copiar uma string.
    from pat.store.corpus import read_unit

    assert citacao.text == read_unit(corpus, citacao.unit_id).text
    assert "escala" in citacao.text
    assert citacao.published_at <= AS_OF

    # -- 4. hipotese do analista --------------------------------------------
    afirmacao = fala("Acho que o moat e escala nas compras.")
    hipotese = afirmacao.hypotheses_touched[0]
    assert ws.state.hypothesis(hipotese).opened_by is Actor.USER
    assert ws.state.claims == (), "afirmacao do analista nao vira claim"

    ws.apply(
        EvidenceLinked(
            hypothesis=hipotese,
            link=EvidenceLink(
                kind=EvidenceKind.QUOTE,
                ref=citacao.unit_id,
                note="a companhia atribui a margem a escala nas compras",
                linked_by=Actor.AGENT,
                at=AGORA,
            ),
        ),
        actor=Actor.AGENT,
    )

    # -- 5. contra-evidencia -------------------------------------------------
    ws.apply(
        FalsificationAttempted(
            hypothesis=hipotese,
            how="serie de margem do motor contra o crescimento de receita, 2019-2024",
            found=True,
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        CounterEvidenceAdded(
            hypothesis=hipotese,
            counter=CounterEvidence(
                link=EvidenceLink(
                    kind=EvidenceKind.METRIC,
                    ref=f"margem_ebitda@v1|{gpa.ENTITY_ID}|2023-12-31|consolidated|{AS_OF}",
                    note="margem nao acompanha o crescimento de receita",
                    linked_by=Actor.AGENT,
                    at=AGORA,
                ),
                undermines="se escala fosse moat, a margem subiria com o volume",
                decisive=False,
            ),
        ),
        actor=Actor.AGENT,
    )
    ws.apply(
        HypothesisStatusChanged(
            slug=hipotese,
            status=HypothesisStatus.WEAKENED,
            strength=HypothesisStrength.WEAK,
            rationale="a citacao sustenta o mecanismo; a serie nao confirma o efeito",
        ),
        actor=Actor.AGENT,
    )

    # -- 6. o critico, e a agenda que ele devolve ---------------------------
    veredito = critique(ws.state)
    assert veredito.findings, "uma hipotese com falsificador nao testado tem objecao"
    propostas = falsification_agenda(ws.state)
    assert propostas, "o critico devolve tarefa, e nao so reclamacao"
    assert all(p.hypothesis for p in propostas)

    # -- 7. conclusao e claim ------------------------------------------------
    ws.apply(
        ConclusionDrawn(
            hypothesis=hipotese,
            text="ha indicio de ganho de escala nas compras, insuficiente para moat",
            strength=HypothesisStrength.WEAK,
            residual_uncertainty=(
                "a serie de margem nao separa efeito de mix do efeito de escala"
            ),
        ),
        actor=Actor.AGENT,
    )
    assert ws.state.conclusion_for(hipotese) is not None

    result_id = next(f.result_ids[0] for f in achados if f.result_ids)
    ws.apply(
        ClaimAsserted(
            slug="c-margem",
            text="a margem nao acompanhou o crescimento de receita no periodo coberto",
            hypothesis=hipotese,
            evidence=(
                EvidenceLink(
                    kind=EvidenceKind.METRIC,
                    ref=result_id,
                    note="serie calculada pelo motor",
                    linked_by=Actor.AGENT,
                    at=AGORA,
                ),
            ),
        ),
        actor=Actor.AGENT,
    )
    assert isinstance(ws.state.claim("c-margem"), Claim)

    # -- 8. valuation --------------------------------------------------------
    modelo = _modelo(result_id)
    ws.apply(ValuationDeclared(model=modelo), actor=Actor.USER)
    resultado = run_dcf(modelo)
    assert not hasattr(resultado, "reason"), getattr(resultado, "message", "")
    assert resultado.equity_value != 0
    # Nenhum numero da valuation volta para o diario: o modelo e a fonte, e o
    # resultado e recomputavel. Guardar os dois criaria uma segunda verdade.
    assert ws.state.valuation("base") == modelo

    # -- 9. tese, e a auditoria dela ----------------------------------------
    tese = _tese(hipotese)
    ws.apply(ThesisDrafted(thesis=tese), actor=Actor.USER)
    auditoria = audit_thesis(ws.state, tese)
    assert auditoria.claims_checked == 1
    assert auditoria.evidence_refs, "a tese resolve ate um endereco"
    duros = [i for i in auditoria.issues if i.severity is Severity.HARD]
    assert not duros, [i.message for i in duros]

    # -- 10. o critico continua discordando, e isso e o ponto ---------------
    final = critique(ws.state)
    codigos = {f.code for f in final.findings}
    assert WorkspaceFinding.FALSIFIER_UNTESTED in codigos, (
        "o falsificador que o analista nunca declarou continua por testar, e o "
        "sistema nao deixa isso desaparecer porque a tese ficou pronta"
    )


def _modelo(result_id: str) -> ValuationModel:
    def premissa(slug, label, valor, base, razao, derivado=()):
        return Assumption(
            slug=slug,
            label=label,
            value=Decimal(valor),
            unit="ratio",
            basis=base,
            rationale=razao,
            author=Actor.USER,
            derived_from=derivado,
            at=AGORA,
        )

    return ValuationModel(
        slug="base",
        currency="BRL",
        horizon_years=5,
        data=(
            DataPoint(
                slug="revenue-base",
                label="receita do ultimo exercicio",
                value=Decimal("100000000"),
                unit="BRL",
                result_id=result_id,
                period_end=date(2023, 12, 31),
            ),
        ),
        assumptions=AssumptionSet(
            assumptions=(
                premissa(
                    "revenue-growth",
                    "crescimento de receita",
                    "0.05",
                    AssumptionBasis.HISTORICAL,
                    "media da serie calculada pelo motor",
                    (result_id,),
                ),
                premissa(
                    "ebit-margin",
                    "margem EBIT",
                    "0.06",
                    AssumptionBasis.JUDGMENT,
                    "margem historica, sem recuperacao assumida",
                ),
                premissa(
                    "tax-rate",
                    "aliquota efetiva",
                    "0.34",
                    AssumptionBasis.JUDGMENT,
                    "aliquota nominal, sem planejamento",
                ),
                premissa(
                    "reinvestment-rate",
                    "reinvestimento",
                    "0.30",
                    AssumptionBasis.JUDGMENT,
                    "capex de manutencao mais capital de giro",
                ),
                premissa(
                    "wacc",
                    "custo de capital",
                    "0.14",
                    AssumptionBasis.MARKET,
                    "custo de capital de varejo alavancado no pais",
                ),
                premissa(
                    "terminal-growth",
                    "crescimento na perpetuidade",
                    "0.03",
                    AssumptionBasis.JUDGMENT,
                    "inflacao de longo prazo, sem ganho real",
                ),
            )
        ),
        created_by=Actor.USER,
        at=AGORA,
    )


def _tese(hipotese: str) -> InvestmentThesis:
    quebra = "a equivalencia patrimonial cair e o EBIT proprio continuar negativo"
    return InvestmentThesis(
        slug="gpa-2026",
        statement="o resultado operacional depende de equivalencia patrimonial",
        direction=ThesisDirection.NO_POSITION,
        confidence=HypothesisStrength.WEAK,
        supporting_claims=("c-margem",),
        supporting_hypotheses=(hipotese,),
        key_assumptions=("a equivalencia patrimonial continua no nivel atual",),
        risks=(
            Risk(
                slug="r-equivalencia",
                text=quebra,
                severity=RiskSeverity.THESIS_BREAKING,
                leading_indicator="resultado divulgado das investidas",
            ),
        ),
        falsifiers=(quebra,),
        counter_thesis=(
            "a operacao propria se recupera e a equivalencia deixa de decidir o "
            "resultado; nesse caso o EBIT reportado hoje subestima a companhia"
        ),
        unresolved=("a serie de margem nao separa mix de escala",),
        valuation="base",
        as_of=AS_OF,
        author=Actor.USER,
        created_at=AGORA,
    )


# ---------------------------------------------------------------------------
# Reinicio
# ---------------------------------------------------------------------------


def test_a_investigacao_sobrevive_ao_processo(ws, tools, root):
    """Fecha tudo, reabre do disco, e o estado e IGUAL campo a campo.

    Nao "equivalente": igual. Um sistema que perdesse uma tentativa de
    falsificacao no caminho passaria num teste frouxo e so mostraria o erro
    semanas depois, como uma critica que parou de aparecer.
    """
    agente = ChatAgent(ws, tools, ShapeReasoner())
    agente.respond(TurnRequest(text="Investiga a receita.", workspace_id=ws.workspace_id))
    agente.respond(
        TurnRequest(text="Acho que o moat e escala.", workspace_id=ws.workspace_id)
    )
    antes = ws.state
    wid = ws.workspace_id
    del agente, ws

    depois = open_workspace(root, wid).state
    assert depois.model_dump() == antes.model_dump()


def test_a_conversa_continua_de_onde_parou(ws, tools, root):
    agente = ChatAgent(ws, tools, ShapeReasoner())
    agente.respond(
        TurnRequest(
            text="Acho que a margem operacional e estruturalmente baixa.",
            workspace_id=ws.workspace_id,
        )
    )
    wid = ws.workspace_id
    del agente, ws

    reaberto = open_workspace(root, wid)
    segundo = ChatAgent(reaberto, tools, ShapeReasoner())
    resposta = segundo.respond(
        TurnRequest(text="Volta naquela hipotese de margem.", workspace_id=wid)
    )

    assert resposta.turn_index == 1, "o indice vem da dobra, nao de um cache de sessao"
    assert resposta.hypotheses_touched
    assert "estruturalmente baixa" in resposta.text


# ---------------------------------------------------------------------------
# A outra jurisdicao
# ---------------------------------------------------------------------------


def test_a_mesma_camada_roda_na_outra_jurisdicao(root, us_warehouse):
    """A empresa americana passa pelo MESMO codigo, sem uma linha de excecao.

    E o teste que impede a camada de virar permanentemente brasileira. Ele nao
    afirma que a cobertura e igual - ela nao e -, e sim que nada aqui pergunta
    de que pais a empresa e antes de decidir o que fazer.
    """
    perfil = profile_for(us_warehouse, INTEL_ENTITY)
    workspace = create_workspace(
        root,
        company=perfil,
        as_of=AS_OF,
        created_at=CREATED_AT,
        mandate="entender a trajetoria de margem",
    )
    tools = PatTools(us_warehouse, company=perfil, as_of=AS_OF)
    agente = ChatAgent(workspace, tools, ShapeReasoner())

    resposta = agente.respond(
        TurnRequest(text="Investiga a receita.", workspace_id=workspace.workspace_id)
    )

    assert perfil.jurisdiction == "US"
    assert resposta.tasks_touched
    assert workspace.state.agenda.tasks


def test_a_lacuna_americana_e_declarada_e_nao_silencio(root, us_warehouse):
    """Documento nos EUA nao vira lista vazia: vira recusa com remedio.

    Lista vazia seria uma afirmacao sobre o MUNDO ("a companhia nao comenta as
    proprias margens"); o fato e sobre o SISTEMA. As duas chegam ao analista
    como a mesma ausencia e pedem acoes opostas.
    """
    from pat.contracts.opportunity.documents import ProviderUnavailable
    from pat.opportunity import provider_for

    perfil = profile_for(us_warehouse, INTEL_ENTITY)
    resposta = provider_for(perfil.jurisdiction).discover(
        us_warehouse,
        company=perfil,
        published_from=date(2020, 1, 1),
        published_to=AS_OF,
    )

    assert isinstance(resposta, ProviderUnavailable)
    assert resposta.remedy

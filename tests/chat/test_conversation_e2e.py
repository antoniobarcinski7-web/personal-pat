"""A conversa inteira, ponta a ponta: quatro turnos sobre tres empresas.

E o unico teste do projeto que atravessa mais de um turno, e por isso e o unico
lugar onde a afirmacao central da M4.1 pode ser feita:

    nenhum numero de um turno anterior chega ao modelo em nenhum turno seguinte.

Um teste de turno isolado nao consegue dizer isso - ele nao tem "antes". Por
isso `test_nenhum_valor_de_turno_anterior_chega_ao_modelo` (E2E-2) e o portao
desta milestone, e o analogo conversacional de
`tests/research/test_layering_research.py::test_o_escritor_nao_le_valor_nenhum`.

A conversa e a do caso de uso que motivou a M4.1:

    1. "Compare o EBITDA de Petrobras, Vale e WEG em FY2024."
    2. "E a margem EBITDA?"                 -> herda empresas e periodo
    3. "E como isso mudou desde 2023?"      -> herda empresas, ACRESCENTA periodo
    4. "Qual teve a maior queda?"           -> RECUSA nomeada

O turno 4 recusa de proposito, e a recusa e a resposta certa: `min` e `max`
devolvem o valor extremo, nunca QUAL insumo o produziu (`research/derive.py`), e
`DerivedValue` nao tem campo de atribuicao. Responder exigiria uma operacao de
argmin/argmax que a V1 nao tem. Devolver o menor delta_pct sem dizer de quem ele
e seria um numero correto para uma pergunta que ninguem fez - o pior tipo de
acerto, porque parece resposta.

O que este arquivo NAO afirma
-----------------------------
Nenhum valor. Os numeros vem de `tres_empresas.py`, que os inventa - ver o
docstring de la. Aqui se afirma comportamento: quem respondeu, quem recusou, o
que chegou ao prompt, o que ficou gravado e o que o CLI reproduz.
"""

from __future__ import annotations

import json

import pytest

from pat.contracts.chat import ChatRequest, RefusalKind
from pat.contracts.research import PlanEnvelope
from pat.research.llm import LLMResponse
from tests.chat import tres_empresas as t3
from tests.semantics.conftest import load_zips

PETRO, VALE, WEG = t3.PETROBRAS.entity_id, t3.VALE.entity_id, t3.WEG.entity_id

PERGUNTAS = (
    "Compare o EBITDA de Petrobras, Vale e WEG em FY2024.",
    "E a margem EBITDA?",
    "E como isso mudou desde 2023?",
    "Qual teve a maior queda?",
)


# ---------------------------------------------------------------------------
# Os planos que o modelo falso devolve, um por turno
# ---------------------------------------------------------------------------


def _metrica(step_id: str, metrica: str, entidade: str, periodo: str) -> dict:
    nome, versao = metrica.split("@")
    return {
        "step_id": step_id,
        "step_kind": "metric",
        "metric": {"name": nome, "version": versao},
        "entity_id": entidade,
        "period_end": periodo,
    }


_APELIDO = {PETRO: "petrobras", VALE: "vale", WEG: "weg"}


def _plano(objetivo: str, steps: list[dict], outputs: list[str], unresolved=()) -> str:
    return json.dumps(
        {
            "objective": objetivo,
            "as_of": t3.AS_OF.isoformat(),
            "scope": "consolidated",
            "steps": steps,
            "outputs": outputs,
            "assumptions": [],
            "unresolved": list(unresolved),
        }
    )


PLANO_T1 = _plano(
    "EBITDA consolidado de Petrobras, Vale e WEG no exercicio findo em 2024",
    [_metrica(f"ebitda_{_APELIDO[e]}", "ebitda@v1", e, "2024-12-31") for e in (PETRO, VALE, WEG)],
    [f"ebitda_{_APELIDO[e]}" for e in (PETRO, VALE, WEG)],
)

PLANO_T2 = _plano(
    "margem EBITDA consolidada das mesmas tres companhias no mesmo exercicio",
    [
        _metrica(f"margem_{_APELIDO[e]}", "margem_ebitda@v1", e, "2024-12-31")
        for e in (PETRO, VALE, WEG)
    ],
    [f"margem_{_APELIDO[e]}" for e in (PETRO, VALE, WEG)],
)

# O turno 3 ACRESCENTA 2023 ao periodo herdado. E o caso que provaria um bug se
# o contexto virasse pino: `pinned_periods=(2024,)` herdado do turno 2 faria
# cada passo de 2023 ser recusado com PIN_CONTRADICTED_PERIOD.
PLANO_T3 = _plano(
    "variacao da margem EBITDA das tres companhias entre 2023 e 2024",
    [
        *[
            _metrica(f"margem_{_APELIDO[e]}_{ano}", "margem_ebitda@v1", e, f"{ano}-12-31")
            for e in (PETRO, VALE, WEG)
            for ano in (2023, 2024)
        ],
        *[
            {
                "step_id": f"variacao_{_APELIDO[e]}",
                "step_kind": "derivation",
                "op": "delta_pct",
                "inputs": [f"margem_{_APELIDO[e]}_2023", f"margem_{_APELIDO[e]}_2024"],
            }
            for e in (PETRO, VALE, WEG)
        ],
    ],
    [f"variacao_{_APELIDO[e]}" for e in (PETRO, VALE, WEG)],
)

PLANO_T4 = _plano(
    "identificar qual das tres companhias teve a maior queda de margem",
    [_metrica("margem_petrobras", "margem_ebitda@v1", PETRO, "2024-12-31")],
    ["margem_petrobras"],
    unresolved=[
        {
            "kind": "unsupported_question",
            "detail": (
                "identificar qual empresa teve a maior queda exige uma operacao de "
                "argmin/argmax que nao existe na V1; min e max devolvem o valor "
                "extremo, nunca qual insumo o produziu"
            ),
            "candidates": ["a variacao percentual de cada empresa, lado a lado"],
        }
    ],
)

PLANOS = (PLANO_T1, PLANO_T2, PLANO_T3, PLANO_T4)


def _prosa(texto: str, apoia: list[str]) -> str:
    return json.dumps(
        {"prose": texto, "interpretations": [{"text": "Leitura sem numero.", "supports": apoia}]}
    )


# Prosa sem UM algarismo fora de token - `check_prose` recusaria o texto inteiro.
# Note que ate o periodo e token: "FY2024" escrito a mao seria numero literal.
PROSAS = (
    _prosa(
        "No exercicio {{p:fy2024}}, a Petrobras registrou EBITDA consolidado de "
        "{{s:ebitda_petrobras}}, ante {{s:ebitda_vale}} da Vale e {{s:ebitda_weg}} da WEG.",
        ["ebitda_petrobras"],
    ),
    _prosa(
        "A margem EBITDA consolidada em {{p:fy2024}} foi de {{s:margem_petrobras}} na "
        "Petrobras, {{s:margem_vale}} na Vale e {{s:margem_weg}} na WEG.",
        ["margem_vale"],
    ),
    _prosa(
        "A variacao da margem EBITDA entre os dois exercicios foi de "
        "{{s:variacao_petrobras}} na Petrobras, {{s:variacao_vale}} na Vale e "
        "{{s:variacao_weg}} na WEG.",
        ["variacao_vale"],
    ),
)


class ConversaFake:
    """Um duplo que responde por TURNO, e nao por prompt.

    Despacha planejador e escritor pelo conteudo do system prompt - e nao pela
    ordem de chamada - pela mesma razao que o `FakeLLMClient` do projeto: um
    duplo que dependesse da ordem passaria a mentir no dia em que o turno
    deixasse de chamar o escritor por ultimo.

    Guarda TODAS as requisicoes, marcadas com o turno em que aconteceram. E
    disso que o E2E-2 depende: sem saber a que turno cada prompt pertence, nao
    da para perguntar se um valor do turno anterior vazou para o seguinte.
    """

    fingerprint = "fake/v1/00000000"

    def __init__(self) -> None:
        self.turno = 0
        self.requests: list[tuple[int, object]] = []
        self.roles: list[tuple[int, str]] = []

    def complete(self, request):
        redator = "redator" in request.system
        self.requests.append((self.turno, request))
        self.roles.append((self.turno, "writer" if redator else "planner"))
        texto = PROSAS[self.turno] if redator else PLANOS[self.turno]
        return LLMResponse.for_text(
            texto,
            model_id="fake-model-20260101",
            prompt_sha256=request.prompt_sha256,
            called_at=t3.KD_2025 and __import__("datetime").datetime(
                2026, 8, 20, tzinfo=__import__("datetime").UTC
            ),
        )

    def prompts_do_turno(self, turno: int) -> list[str]:
        return [r.system + r.user for t, r in self.requests if t == turno]

    def prompt_do_planejador(self, turno: int) -> str:
        for (t, papel), (_, request) in zip(self.roles, self.requests, strict=True):
            if t == turno and papel == "planner":
                return request.user
        raise AssertionError(f"nenhuma chamada de planejador no turno {turno}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def paths3(tmp_path):
    """PAT_HOME com as tres companhias, pelo caminho de producao."""
    from pat.config import resolve_paths
    from pat.store.bronze import BronzeStore
    from pat.store.db import connect, migrate

    paths = resolve_paths(tmp_path / "home").ensure()
    conn = connect(paths.warehouse)
    migrate(conn)
    load_zips(conn, BronzeStore(paths.bronze), t3.zips())
    conn.close()
    return paths


@pytest.fixture
def conversa(paths3):
    """A conversa de quatro turnos, ja rodada. Um `ChatService` de verdade."""
    from pat.chat import ChatService

    llm = ConversaFake()
    service = ChatService(paths=paths3, llm=llm, model="fake-model", default_as_of=t3.AS_OF)
    state = service.create_session()

    turnos = []
    for indice, pergunta in enumerate(PERGUNTAS):
        llm.turno = indice
        turnos.append(
            service.send_message(ChatRequest(session_id=state.session_id, text=pergunta))
        )
    return service, llm, turnos, paths3


# ---------------------------------------------------------------------------
# E2E-1: os quatro turnos
# ---------------------------------------------------------------------------


def test_e2e1_tres_turnos_respondem_e_o_quarto_recusa(conversa):
    _, _, turnos, _ = conversa

    for indice in (0, 1, 2):
        turno = turnos[indice]
        assert turno.answer is not None, f"o turno {indice} devia ter respondido"
        assert turno.refusal is None
        assert turno.manifest is not None

    quarto = turnos[3]
    assert quarto.answer is None, "'qual teve a maior queda' nao pode devolver um numero"
    assert quarto.refusal is not None
    assert quarto.refusal.kind is RefusalKind.PLANNER_UNRESOLVED
    assert "unsupported_question" in quarto.refusal.codes
    assert "argmin" in quarto.refusal.detail
    assert quarto.refusal.candidates, "a recusa tem que oferecer o que da para fazer"


def test_e2e1_os_planos_sao_os_do_caso_de_uso(conversa):
    """Cada turno planejou o que a pergunta pedia - inclusive o turno 3, que
    ACRESCENTA um periodo ao que foi herdado."""
    _, _, turnos, _ = conversa

    def periodos(turno):
        return {s.period_end for s in turno.plan.steps if s.step_kind == "metric"}

    def entidades(turno):
        return {s.entity_id for s in turno.plan.steps if s.step_kind == "metric"}

    assert entidades(turnos[0]) == {PETRO, VALE, WEG}
    assert periodos(turnos[0]) == {t3.FY2024}

    assert entidades(turnos[1]) == {PETRO, VALE, WEG}, "o turno 2 herdou as empresas"
    assert periodos(turnos[1]) == {t3.FY2024}

    assert periodos(turnos[2]) == {t3.FY2023, t3.FY2024}, (
        "o turno 3 tinha que ACRESCENTAR 2023 ao periodo herdado. Se so 2024 "
        "aparece, contexto virou limite - e o modo de falha que herdar pinos "
        "produziria."
    )
    assert sum(1 for s in turnos[2].plan.steps if s.step_kind == "derivation") == 3


def test_e2e1_a_resposta_nomeia_cada_empresa(conversa):
    """O Achado 1 da auditoria, no caminho inteiro: sem `display_name` em
    `describe()`, as tres citacoes de um mesmo periodo seriam indistinguiveis."""
    _, _, turnos, _ = conversa
    numericos = [c for c in turnos[0].answer.claims if c.claim_kind == "numeric"]

    assert len(numericos) == 3
    significados = [c.means for c in numericos]
    assert len(set(significados)) == 3, "tres numeros com o mesmo 'means' sao inauditaveis"
    for denominacao in (t3.PETROBRAS.denom, t3.VALE.denom, t3.WEG.denom):
        assert any(denominacao in m for m in significados)


# ---------------------------------------------------------------------------
# E2E-2: O PORTAO
# ---------------------------------------------------------------------------


def test_e2e2_nenhum_valor_de_turno_anterior_chega_ao_modelo(conversa):
    """O portao da M4.1.

    Junta todo `rendered_value` produzido ate o turno N-1 e procura cada um, como
    substring, em todo prompt enviado do turno N em diante - planejador e
    escritor. Um unico acerto significa que o pipeline deixou de ser a unica
    fonte de numero: o modelo passou a poder repetir um valor em vez de pedir que
    ele fosse recalculado, e a auditoria de um turno deixaria de explicar o que
    esta escrito nele.

    Nao e uma checagem de prompt: e a verificacao de que a *estrutura* funciona.
    O contexto conversacional e um `ConversationContext`, que nao tem campo capaz
    de carregar `Decimal` - este teste confirma que nada contornou isso por outro
    caminho.
    """
    _, llm, turnos, _ = conversa

    acumulado: list[str] = []
    for indice, turno in enumerate(turnos):
        for prompt in llm.prompts_do_turno(indice):
            for valor in acumulado:
                assert valor not in prompt, (
                    f"o valor {valor!r}, calculado antes do turno {indice}, apareceu "
                    f"num prompt do turno {indice}. Um numero atravessou a conversa."
                )
        if turno.answer is not None:
            acumulado += [
                c.rendered_value for c in turno.answer.claims if c.claim_kind == "numeric"
            ]

    assert acumulado, "a conversa nao produziu numero nenhum; o teste nao provou nada"


def test_e2e2_o_contexto_tambem_nao_carrega_o_texto_da_resposta(conversa):
    """A prosa de um turno tambem nao viaja - nem como citacao, nem como resumo.

    Se ela viajasse, o modelo seguinte teria os numeros substituidos dentro dela:
    `answer.prose` sai de `substitute()` com os valores JA no lugar dos tokens.
    """
    _, llm, turnos, _ = conversa

    for indice in (1, 2, 3):
        for prompt in llm.prompts_do_turno(indice):
            for anterior in turnos[:indice]:
                if anterior.answer is not None:
                    assert anterior.answer.prose not in prompt


# ---------------------------------------------------------------------------
# E2E-3..7
# ---------------------------------------------------------------------------


def test_e2e3_cada_turno_recalcula_do_zero(conversa):
    """Nenhum `result_id` e reaproveitado entre turnos.

    E o outro lado do E2E-2: nao basta o numero antigo nao vazar para o prompt,
    o numero novo tem que ter sido de fato calculado de novo. `result_id`
    disjunto e a evidencia de que houve execucao, e nao leitura de cache.
    """
    _, _, turnos, _ = conversa

    primeiro = {c.result_id for c in turnos[0].answer.claims if c.claim_kind == "numeric"}
    segundo = {c.result_id for c in turnos[1].answer.claims if c.claim_kind == "numeric"}

    assert primeiro and segundo
    assert primeiro.isdisjoint(segundo)


def test_e2e4_todo_manifesto_da_conversa_esta_em_research_run(conversa):
    """A conversa grava nas mesmas tabelas que `pat ask`.

    E o que torna `pat runs --research <id>` capaz de auditar um turno mostrado
    na tela - e o que impede a camada de chat de virar um universo paralelo com
    procedencia propria.
    """
    from pat.store.db import connect
    from pat.store.research import read_manifest

    _, _, turnos, paths = conversa
    conn = connect(paths.warehouse, read_only=True)
    try:
        for turno in turnos:
            if turno.manifest is None:
                continue
            row = read_manifest(conn, turno.manifest.manifest_id)
            assert row is not None, f"manifesto {turno.manifest.manifest_id[:12]} nao gravado"
            assert row.outputs_available
    finally:
        conn.close()


def test_e2e5_o_plano_de_um_turno_roda_no_cli_sem_modelo(conversa, tmp_path, capsys):
    """A demonstracao mais direta de que o chat e uma casca.

    O envelope {question, plan} de um turno e exatamente o formato que
    `pat ask --plan-file` aceita. Rodado SEM `--writer`, ele reproduz os mesmos
    numeros com ZERO chamadas de modelo - o que prova que a conversa nao e fonte
    de verdade, e que a auditoria nao depende de nada que o chat guarde.
    """
    from pat.cli import main

    _, _, turnos, paths = conversa
    turno = turnos[0]

    arquivo = tmp_path / "plano.json"
    arquivo.write_text(
        PlanEnvelope(question=turno.question, plan=turno.plan).model_dump_json(indent=2),
        encoding="utf-8",
    )

    codigo = main(["--home", str(paths.home), "ask", "--plan-file", str(arquivo), "--no-writer"])
    saida = capsys.readouterr().out

    assert codigo == 0
    for claim in turno.answer.claims:
        if claim.claim_kind == "numeric":
            assert claim.rendered_value in saida, (
                f"{claim.rendered_value} saiu no chat mas nao no CLI: os dois "
                "caminhos divergiram"
            )


def test_e2e6_todo_digito_da_resposta_veio_de_substituicao(conversa):
    """A regra do digito, afirmada sobre o texto que o usuario de fato le.

    `check_prose` ja rodou dentro de `build_answer`, sobre a prosa COM tokens.
    Aqui a pergunta e a de depois: no texto final, ja substituido, sobra algum
    algarismo que nao tenha vindo de um `rendered_value` ou de um rotulo de
    periodo? Se sobrar, algum numero chegou a tela por um caminho que nao e a
    tabela de substituicao - que e precisamente o que a arquitetura inteira
    existe para impedir.

    E uma afirmacao mais forte do que reconferir o texto cru, porque nao depende
    de reconstruir o que o modelo escreveu: ela olha o produto.
    """
    _, _, turnos, _ = conversa

    for turno in turnos:
        if turno.answer is None:
            continue

        resto = turno.answer.prose
        substituidos = [
            c.rendered_value for c in turno.answer.claims if c.claim_kind == "numeric"
        ]
        assert substituidos, "resposta sem numero citado nao prova nada"

        # Retira do texto tudo que veio da tabela: primeiro os valores, depois os
        # rotulos de periodo. O que sobrar tem que estar sem algarismo nenhum.
        for valor in substituidos:
            assert valor in resto, f"{valor} nao aparece na prosa - a citacao nao bate"
            resto = resto.replace(valor, "")
        for rotulo in _rotulos_de_periodo(turno):
            resto = resto.replace(rotulo, "")

        sobrou = [caractere for caractere in resto if caractere.isdigit()]
        assert sobrou == [], (
            f"sobraram algarismos na prosa do turno {turno.turn_index} depois de "
            f"remover todo valor substituido: {resto!r}"
        )


def _rotulos_de_periodo(turno) -> list[str]:
    """Os rotulos que `render.period_tokens` sabe produzir para este plano.

    Sao os unicos textos com digito que a regra do digito admite alem dos
    valores - e admite justamente porque tambem sao token: "FY2024" escrito a
    mao na prosa seria numero literal e o texto inteiro seria recusado.
    """
    saida = []
    for step in turno.plan.steps:
        if step.step_kind == "metric":
            saida.append(f"FY{step.period_end.year}")
            saida.append(step.period_end.isoformat())
    return sorted(set(saida), key=len, reverse=True)


def test_e2e7_o_contexto_do_turno_3_tem_estrutura_e_nao_valor(conversa):
    """As duas metades da mesma afirmacao, no mesmo prompt.

    O contexto FUNCIONA - o planejador do turno 3 ve as empresas e a metrica do
    turno 2, que e o que permite interpretar "isso" e "as tres empresas". E o
    contexto e ESTRUTURAL - nenhum valor calculado ate ali aparece.
    """
    _, llm, turnos, _ = conversa
    prompt = llm.prompt_do_planejador(2)

    assert "CONVERSA ATE AQUI" in prompt
    for entidade in (PETRO, VALE, WEG):
        assert entidade in prompt
    assert "margem_ebitda@v1" in prompt
    assert turnos[1].question.text in prompt, "a pergunta anterior ajuda a resolver 'isso'"

    for turno in turnos[:2]:
        for claim in turno.answer.claims:
            if claim.claim_kind == "numeric":
                assert claim.rendered_value not in prompt


def test_e2e7_o_primeiro_turno_nao_tem_contexto(conversa):
    """Sessao nova produz o prompt de `pat plan`, byte a byte.

    `build_context` devolve `None` e nao contexto vazio justamente para isso: o
    primeiro turno de uma conversa e a mesma pergunta que o CLI faria, e tem que
    ser a mesma chamada.
    """
    _, llm, turnos, _ = conversa

    assert turnos[0].context_sha256 is None
    assert "CONVERSA ATE AQUI" not in llm.prompt_do_planejador(0)
    assert all(turno.context_sha256 is not None for turno in turnos[1:])


def test_e2e_a_recusa_do_turno_4_entra_no_contexto_de_um_turno_seguinte(paths3):
    """Turno recusado nao some da conversa.

    "Isso ja foi recusado por X" e a correcao de curso mais barata que existe:
    nao custa chamada nenhuma e evita o modelo replanejar identico o que ja foi
    negado.
    """
    from pat.chat import ChatService
    from pat.chat.session import build_context

    llm = ConversaFake()
    service = ChatService(paths=paths3, llm=llm, model="fake-model", default_as_of=t3.AS_OF)
    state = service.create_session()

    for indice, pergunta in enumerate(PERGUNTAS):
        llm.turno = indice
        service.send_message(ChatRequest(session_id=state.session_id, text=pergunta))

    contexto = build_context(state)
    ultimo = contexto.turns[-1]

    assert ultimo.outcome == "refused"
    assert "unsupported_question" in ultimo.refusal_codes

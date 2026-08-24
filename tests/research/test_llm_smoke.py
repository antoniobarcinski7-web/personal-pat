"""O primeiro caminho real ate o modelo. `pytest -m llm`.

Nao roda em `pytest`, e o marcador esta desligado no `addopts` por uma razao
diferente da do `network`: aquele depende de terceiro, este gasta credencial e
token. Nenhum dos dois pode rodar por acidente.

O que este arquivo prova, e o que ele deliberadamente NAO prova
---------------------------------------------------------------
Prova que a fiacao aguenta o modelo de verdade: credencial encontrada, adapter
chamado, `temperature` ausente do payload, resposta recebida, texto virando
`ResearchPlan`, plano passando pelo validador.

Nao prova que o plano esta *certo*. Isso e afirmacao sobre o modelo, e um teste
que a fizesse ficaria vermelho no dia em que o modelo escolhesse outro caminho
igualmente valido - virando um alarme que se aprende a ignorar. O par
offline/`-m llm` e o mesmo de sempre: `test_cli_plan.py` prova o comportamento,
este prova que ele sobrevive ao mundo.

O gasto e de uma chamada por sessao: o modulo planeja uma vez e as asserções
compartilham o resultado.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pat.contracts.research import OutputKind, ResearchPlan, ResearchQuestion
from pat.research.llm.anthropic import ADAPTER_VERSION, ENV_API_KEY, PROVIDER

pytestmark = pytest.mark.llm

MODEL = "claude-opus-5"


@pytest.fixture(scope="module")
def cliente():
    if not os.environ.get(ENV_API_KEY):
        pytest.skip(f"{ENV_API_KEY} nao definida")

    from pat.research.llm.anthropic import AnthropicClient

    return AnthropicClient()


@pytest.fixture(scope="module")
def planejamento(cliente, tmp_path_factory):
    """Uma chamada real, compartilhada pelo modulo inteiro.

    Escopo de modulo porque cada execucao custa dinheiro. O warehouse vem do
    fixture do GPA, que e de escopo de funcao - entao ele e montado aqui na
    mao, uma vez, em vez de recebido por injecao.
    """
    from datetime import UTC, datetime

    from pat.research import plan_for_question
    from pat.store.bronze import BronzeStore
    from pat.store.db import connect, migrate
    from tests.semantics import golden_gpa as gpa
    from tests.semantics.conftest import load_zips

    tmp = tmp_path_factory.mktemp("smoke")
    conn = connect(tmp / "w.duckdb")
    migrate(conn)
    load_zips(conn, BronzeStore(tmp / "bronze"), gpa.zips())

    question = ResearchQuestion(
        text="Qual foi a margem EBITDA consolidada do GPA no exercicio de 2024?",
        as_of=gpa.FY2024.replace(year=2025, month=6, day=30),
        asked_at=datetime.now(UTC),
        requested_output=OutputKind.NUMBER,
    )
    try:
        yield question, plan_for_question(conn, question=question, llm=cliente, model=MODEL)
    finally:
        conn.close()


# -- a chamada aconteceu -----------------------------------------------------


def test_o_adapter_certo_respondeu(planejamento):
    _question, outcome = planejamento

    assert outcome.provenance.client_fingerprint.startswith(f"{PROVIDER}/{ADAPTER_VERSION}/")
    assert outcome.provenance.model_id.startswith("claude-opus-5"), (
        "o alias tem que resolver para uma versao concreta, e e ela que vai ao manifesto"
    )


def test_a_temperatura_nao_foi_pedida(planejamento):
    """A rota comum nos modelos atuais: eles recusam `temperature` com 400.
    Se o default voltasse a ser zero, esta chamada falharia inteira."""
    _question, outcome = planejamento

    assert outcome.provenance.temperature is None


def test_a_resposta_nao_foi_truncada(planejamento):
    """`max_tokens` cobre raciocinio e texto juntos. Se 16384 deixar de ser
    folgado, o sintoma aparece aqui e nao num JSON cortado ao meio."""
    _question, outcome = planejamento

    assert outcome.response.stop_reason == "end_turn"
    assert outcome.provenance.max_tokens == 16384


def test_o_instante_veio_da_resposta(planejamento):
    """M-1: quem produziu a resposta e quem sabe quando ela foi feita."""
    _question, outcome = planejamento

    assert outcome.provenance.called_at.tzinfo is not None
    assert outcome.provenance.cached is False


# -- o texto virou plano -----------------------------------------------------


def test_a_resposta_virou_um_ResearchPlan(planejamento):
    question, outcome = planejamento

    assert isinstance(outcome.plan, ResearchPlan)
    assert outcome.plan.as_of == question.as_of
    assert outcome.plan.steps, "um plano sem passo nao calcula nada"


def test_o_modelo_nao_escreveu_numero_nenhum(planejamento):
    """A invariante central da Fase 3, conferida contra o modelo de verdade e
    nao so contra a gramatica: nao existe campo onde um valor caberia, entao a
    unica forma de o plano carregar um numero seria a gramatica ter mudado."""
    _question, outcome = planejamento

    for step in outcome.plan.steps:
        assert not hasattr(step, "value")
        assert not hasattr(step, "result")


def test_o_plano_usa_so_o_que_o_snapshot_oferece(planejamento):
    """Metrica e entidade inventadas sao o modo de falha esperado de um
    planejador por LLM. O validador as recusa - o que se confere aqui e que o
    modelo real, com o snapshot em maos, nao precisa ser recusado."""
    from pat.research.validate import validate_plan
    from pat.semantics.registry import default_registry

    question, outcome = planejamento
    violacoes = validate_plan(outcome.plan, question, default_registry())

    assert violacoes == (), [f"{v.code}: {v.message}" for v in violacoes]


# -- o cache fecha o ciclo ---------------------------------------------------


def test_a_segunda_chamada_identica_sai_do_cache(cliente, tmp_path_factory):
    """Prova que a chave funciona contra o adapter real, e nao so contra o
    duplo: a segunda passagem nao pode custar token."""
    from pat.research.llm import LLMRequest
    from pat.research.llm.cache import CachedLLMClient
    from pat.research.llm.store import FileLLMCache

    envolvido = CachedLLMClient(cliente, FileLLMCache(tmp_path_factory.mktemp("cache")))
    request = LLMRequest(
        system="Responda com um unico objeto JSON e nada mais.",
        user='Devolva exatamente {"ok": true}',
        model=MODEL,
        max_tokens=1024,
    )

    primeira = envolvido.complete(request)
    segunda = envolvido.complete(request)

    assert primeira.cached is False
    assert segunda.cached is True
    assert segunda.response_sha256 == primeira.response_sha256
    assert segunda.called_at == primeira.called_at, (
        "resposta servida do cache carrega o instante da chamada ORIGINAL (M-1)"
    )


# -- o escritor: o segundo e ultimo uso de LLM -------------------------------
#
# Mais uma chamada por sessao, compartilhada pelo modulo. Prova a mesma coisa
# que a secao do planejador prova, na outra ponta: que a fiacao aguenta o
# modelo de verdade. Nao prova que o TEXTO esta bom - isso e afirmacao sobre o
# modelo, e um teste que a fizesse ficaria vermelho no dia em que ele
# escolhesse outra redacao igualmente valida.
#
# O que da para afirmar sem ambiguidade e o que importa: que o numero no texto
# e o do executor, e que o modelo nao escreveu algarismo nenhum. As duas coisas
# sao estruturais, entao valem contra qualquer redacao.


@pytest.fixture(scope="module")
def redacao(cliente, tmp_path_factory):
    """Uma corrida completa com escritor real, a partir do plano congelado."""
    from pat.research import load_envelope, run_plan
    from pat.store.bronze import BronzeStore
    from pat.store.db import connect, migrate
    from tests.semantics import golden_gpa as gpa
    from tests.semantics.conftest import load_zips

    plano = Path(__file__).parent / "plans" / "gpa_margin_fy24_fy23.json"
    envelope = load_envelope(plano.read_bytes())

    tmp = tmp_path_factory.mktemp("smoke-writer")
    conn = connect(tmp / "w.duckdb")
    migrate(conn)
    load_zips(conn, BronzeStore(tmp / "bronze"), gpa.zips())

    try:
        yield run_plan(
            conn,
            plan=envelope.plan,
            question=envelope.question,
            llm=cliente,
            model=MODEL,
        )
    finally:
        conn.close()


def test_o_modelo_escreveu_uma_resposta(redacao):
    assert redacao.answer is not None
    assert redacao.writer is not None
    assert redacao.writer.response.stop_reason == "end_turn", (
        "se o teto deixar de ser folgado, o sintoma aparece aqui e nao num "
        "JSON cortado ao meio"
    )


def test_o_modelo_real_nao_escreveu_algarismo_nenhum(redacao):
    """A invariante do M3 contra o modelo de verdade.

    O texto CRU - antes da substituicao - nao pode ter digito fora de token.
    E a mesma regra que `check_prose` aplicou para deixar a resposta passar;
    conferi-la aqui de novo, sobre o que o modelo literalmente escreveu, e o
    que fecha o ciclo.
    """
    import re

    sem_token = re.sub(r"\{\{[a-z]:[a-z0-9_]+\}\}", "", redacao.writer.prose)

    assert not re.search(r"[0-9]", sem_token), (
        f"o modelo escreveu um numero fora de token: {sem_token!r}"
    )


def test_os_numeros_do_texto_sao_os_do_executor(redacao):
    """O valor chega do motor ao texto sem passar por um modelo em ponto
    nenhum - porque o modelo escreveu token, e a substituicao e deterministica."""
    numericas = [c for c in redacao.answer.claims if c.claim_kind == "numeric"]
    por_passo = redacao.execution.by_step()

    assert numericas, "uma resposta de research sem numero citado nao e resposta"
    for claim in numericas:
        assert claim.rendered_value in redacao.answer.prose
        step_id = claim.token.removeprefix("{{s:").removesuffix("}}")
        assert claim.result_id == por_passo[step_id].result_id


def test_as_leituras_do_modelo_apontam_para_resultados_reais(redacao):
    """Lista vazia e valida - o modelo nao e obrigado a interpretar. O que nao
    pode e uma leitura apontar para um resultado que nao existe."""
    ids = {r.result_id for r in redacao.execution.results}

    for claim in redacao.answer.claims:
        if claim.claim_kind == "interpretive":
            assert set(claim.supports) <= ids
            assert not hasattr(claim, "value")


def test_a_procedencia_do_escritor_foi_ao_manifesto(redacao):
    writer = redacao.manifest.writer

    assert writer is not None
    assert writer.client_fingerprint.startswith(f"{PROVIDER}/{ADAPTER_VERSION}/")
    assert writer.temperature is None
    assert writer.called_at.tzinfo is not None

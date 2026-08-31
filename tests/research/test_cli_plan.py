"""`pat plan`: o caminho CLI -> planejador -> modelo -> plano validado.

Nenhum teste aqui chama a API. O ponto de montagem do cliente concreto
(`pat.cli._llm_client`) e substituido por um duplo, e e isso que permite
exercitar o comando inteiro - argumentos, pergunta, snapshot, parsing,
validacao, gravacao em `llm_call` - sem gastar token.

O par offline/`-m llm` e deliberado, pela mesma razao que o par offline/network
existe na Fase 1: este arquivo prova que a *fiacao* esta certa; o smoke test
prova que o modelo de verdade consegue produzir um plano que passa por ela.
Um modelo que mude de comportamento so aparece no segundo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from pat.cli import main
from pat.research.llm import LLMResponse

AS_OF = "2025-06-30"
CHAMADO_EM = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
ENTIDADE = "br:cnpj:47508411000156"

PLANO_BOM = json.dumps(
    {
        "objective": "margem EBITDA consolidada do GPA em FY2024",
        "as_of": AS_OF,
        "scope": "consolidated",
        "steps": [
            {
                "step_id": "margem_fy2024",
                "step_kind": "metric",
                "metric": {"name": "margem_ebitda", "version": "v1"},
                "entity_id": ENTIDADE,
                "period_end": "2024-12-31",
            }
        ],
        "outputs": ["margem_fy2024"],
        "assumptions": [],
        "unresolved": [],
    }
)


class _ClienteFalso:
    """Satisfaz `LLMClient` respondendo sempre o mesmo texto.

    Nao e o `FakeLLMClient` da porta: aquele indexa por `prompt_sha256`, e
    quem escreve um teste de CLI nao tem o prompt em maos - ele so existe
    depois que o snapshot foi montado la dentro. Indexar por prompt aqui
    obrigaria o teste a reconstruir o snapshot, que e justamente a parte que
    ele quer deixar o CLI fazer.
    """

    def __init__(self, text: str = PLANO_BOM, *, stop_reason: str = "end_turn") -> None:
        self._text = text
        self._stop_reason = stop_reason
        self.calls: list = []

    @property
    def fingerprint(self) -> str:
        return "fake/v1/00000000"

    def complete(self, request) -> LLMResponse:
        self.calls.append(request)
        return LLMResponse.for_text(
            self._text,
            model_id="fake-model-20260101",
            prompt_sha256=request.prompt_sha256,
            stop_reason=self._stop_reason,
            called_at=CHAMADO_EM,
        )


@pytest.fixture
def home(tmp_path, warehouse):
    """PAT_HOME com o warehouse do GPA ja materializado."""
    import shutil

    from pat.config import resolve_paths

    warehouse.conn.close()
    paths = resolve_paths(tmp_path / "home").ensure()
    shutil.copy(tmp_path / "research.duckdb", paths.warehouse)
    shutil.copytree(tmp_path / "bronze", paths.bronze, dirs_exist_ok=True)
    return str(paths.home)


@pytest.fixture
def cliente(monkeypatch):
    """Substitui o ponto de montagem, nao o adapter.

    Trocar `_llm_client` inteiro - e nao remendar o SDK por dentro -
    mantem o teste falando sobre a fronteira que existe de verdade: o CLI monta
    um `LLMClient` e a camada de pesquisa nao sabe qual.
    """
    duplo = _ClienteFalso()
    monkeypatch.setattr("pat.cli._llm_client", lambda args: duplo)
    return duplo


def _plan(home, *extra):
    return main(["--home", home, "plan", "Qual foi a margem EBITDA do GPA em 2024?",
                 "--as-of", AS_OF, *extra])


# -- o caminho feliz ---------------------------------------------------------


def test_o_plano_do_modelo_chega_validado(home, cliente, capsys):
    codigo = _plan(home)
    saida = capsys.readouterr().out

    assert codigo == 0
    assert len(cliente.calls) == 1, "uma pergunta, uma chamada - sem laco, sem retentativa"
    assert "VALIDACAO  ok" in saida
    assert "margem_fy2024" in saida
    assert "nao executado" in saida


def test_planejar_nao_executa(home, cliente, capsys):
    """A fronteira do M2.3: `pat plan` para no plano. Numero nenhum sai daqui."""
    _plan(home)
    saida = capsys.readouterr().out

    assert "RESPOSTA" not in saida
    assert "CITACOES" not in saida
    assert "MANIFESTO" not in saida


def test_a_procedencia_registra_quem_respondeu(home, cliente, capsys):
    _plan(home)
    saida = capsys.readouterr().out

    assert "fake-model-20260101" in saida
    assert "fake/v1/00000000" in saida
    assert "temperatura  nao pedida" in saida, (
        "temperatura nula tem que aparecer como 'nao pedida', nunca como zero"
    )


def test_o_envelope_gravado_e_executavel_por_pat_ask(home, cliente, tmp_path, capsys):
    """A prova de que planejar e executar continuam sendo duas invocacoes: o
    que sai de um entra no outro sem edicao manual."""
    envelope = tmp_path / "plano.json"

    assert _plan(home, "--out", str(envelope)) == 0
    capsys.readouterr()

    assert main(["--home", home, "ask", "--plan-file", str(envelope)]) == 0
    assert "RESPOSTA" in capsys.readouterr().out


# -- a chamada fica registrada mesmo quando nada executa ---------------------


def test_a_chamada_e_gravada_sem_manifesto(home, cliente, capsys):
    """M-3 no caminho real: `pat plan` nunca produz manifesto, entao toda
    chamada dele e orfa. Custou dinheiro e tem rastro."""
    from pat.store.db import connect
    from pat.store.llm_calls import orphan_calls

    _plan(home)

    conn = connect(__import__("pathlib").Path(home) / "warehouse.duckdb", read_only=True)
    try:
        orfas = orphan_calls(conn)
    finally:
        conn.close()

    assert len(orfas) == 1
    assert orfas[0].manifest_id is None
    assert orfas[0].kind == "planner"
    assert orfas[0].client_fingerprint == "fake/v1/00000000"
    assert orfas[0].temperature is None
    assert orfas[0].called_at == CHAMADO_EM


def test_a_chave_gravada_e_a_chave_do_cache(home, cliente, capsys):
    """A coluna `call_sha256` tem que ser a MESMA que o cache usaria - e a
    ligacao entre a tabela e os bytes em `data/llm/`. Duas aritmeticas para o
    mesmo valor divergiriam em silencio."""
    from pat.research.llm.cache import call_sha256
    from pat.store.db import connect
    from pat.store.llm_calls import orphan_calls

    _plan(home)

    conn = connect(__import__("pathlib").Path(home) / "warehouse.duckdb", read_only=True)
    try:
        gravada = orphan_calls(conn)[0].call_sha256
    finally:
        conn.close()

    assert gravada == call_sha256(cliente.calls[0], cliente.fingerprint)


# -- recusas continuam nomeadas ---------------------------------------------


def test_resposta_truncada_diz_que_foi_truncada(home, monkeypatch, capsys):
    """A causa que o M2.2 separou de MALFORMED_JSON: teto atingido tem remedio
    oposto ao de prompt quebrado, e os dois produzem JSON incompleto."""
    monkeypatch.setattr(
        "pat.cli._llm_client",
        lambda args: _ClienteFalso('{"objective": "cor', stop_reason="max_tokens"),
    )

    codigo = _plan(home)

    assert codigo == 1
    assert "truncated_response" in capsys.readouterr().err


def test_prosa_em_vez_de_json_e_recusada(home, monkeypatch, capsys):
    monkeypatch.setattr(
        "pat.cli._llm_client", lambda args: _ClienteFalso("Claro! Aqui esta o plano:")
    )

    codigo = _plan(home)
    erro = capsys.readouterr().err

    assert codigo == 1
    assert "malformed_json" in erro


def test_plano_com_pendencia_nao_passa_na_validacao(home, monkeypatch, capsys):
    """Devolver a duvida e o comportamento correto - e o CLI tem que sair
    diferente de zero, senao um script encadeado seguiria em frente."""
    texto = json.loads(PLANO_BOM)
    texto["unresolved"] = [
        {"kind": "ambiguous_period", "detail": "FY2024 ou 12 meses ate junho?", "candidates": []}
    ]
    monkeypatch.setattr(
        "pat.cli._llm_client", lambda args: _ClienteFalso(json.dumps(texto))
    )

    codigo = _plan(home)

    assert codigo == 1
    assert "RECUSADO" in capsys.readouterr().err


def test_metrica_inventada_e_recusada_pelo_validador_e_nao_pelo_planejador(
    home, monkeypatch, capsys
):
    """O planejador so recusa o que impede o texto de virar plano. Metrica
    inexistente e estruturalmente valida, entao ela tem que chegar viva ate o
    validador - duas implementacoes da mesma regra divergiriam."""
    texto = json.loads(PLANO_BOM)
    texto["steps"][0]["metric"] = {"name": "ebitda_ajustado_do_ceo", "version": "v1"}
    monkeypatch.setattr(
        "pat.cli._llm_client", lambda args: _ClienteFalso(json.dumps(texto))
    )

    codigo = _plan(home)
    erro = capsys.readouterr().err

    assert codigo == 1
    assert "malformed_json" not in erro, "isto nao e erro de parsing"
    assert "RECUSADO" in erro


# -- pinos e ambiente --------------------------------------------------------


def test_a_ordem_dos_pinos_nao_muda_a_pergunta(home, cliente, capsys):
    """`question_id` e hash da pergunta canonica. Se a ordem de digitacao
    mudasse a identidade, o cache nunca acertaria duas vezes seguidas."""
    _plan(home, "--entity", "b:2", "--entity", "a:1")
    primeira = cliente.calls[0].prompt_sha256

    _plan(home, "--entity", "a:1", "--entity", "b:2")

    assert cliente.calls[1].prompt_sha256 == primeira


def test_sem_warehouse_orienta_em_vez_de_estourar(tmp_path, cliente, capsys):
    codigo = main(["--home", str(tmp_path), "plan", "qualquer coisa"])

    assert codigo == 1
    assert "pat init" in capsys.readouterr().err


def test_sem_credencial_o_comando_para_antes_de_qualquer_coisa(home, monkeypatch, capsys):
    """Falha nomeada, nao modo degradado: o adapter nao inventa credencial."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    codigo = _plan(home)

    assert codigo == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err

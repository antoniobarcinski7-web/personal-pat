"""A cadeia inteira da V1, pelo CLI de verdade, com os dois modelos falsos.

    pat plan "<pergunta>" --out p.json
    pat ask --plan-file p.json --writer

E o unico teste que atravessa os DOIS usos de LLM previstos na arquitetura na
mesma corrida. Os outros arquivos provam cada metade: `test_cli_plan.py` o
planejador, `test_cli_research.py` o escritor. A metade que so aparece aqui e a
juncao - que o arquivo gravado por um comando e aceito pelo outro sem edicao, e
que a resposta final carrega numeros que nenhum dos dois modelos escreveu.

Nenhuma chamada real. O par com `test_llm_smoke.py` e o de sempre: aqui a
fiacao, la o mundo.
"""

from __future__ import annotations

import json

import pytest

from pat.cli import main
from pat.research.llm import LLMResponse
from tests.research.test_cli_plan import AS_OF, CHAMADO_EM, PLANO_BOM

PROSA = json.dumps(
    {
        "prose": (
            "A margem EBITDA consolidada do GPA foi de {{s:margem_fy2024}} no "
            "exercicio {{p:fy2024}}."
        ),
        "interpretations": [
            {
                "text": "A rentabilidade operacional do periodo foi modesta.",
                "supports": ["margem_fy2024"],
            }
        ],
    }
)


class _DoisModelos:
    """Um cliente, duas respostas, escolhidas pelo system prompt.

    O CLI monta o cliente por um ponto so (`_llm_client`), entao o duplo tem
    que servir os dois papeis numa corrida. Despacha pelo system prompt e nao
    por ordem de chamada: um teste que dependesse da ordem passaria a mentir no
    dia em que o `pat ask` deixasse de chamar o escritor por ultimo.
    """

    def __init__(self) -> None:
        self.papeis: list[str] = []

    @property
    def fingerprint(self) -> str:
        return "duplo/v1/00000000"

    def complete(self, request) -> LLMResponse:
        redator = "redator" in request.system
        self.papeis.append("writer" if redator else "planner")
        return LLMResponse.for_text(
            PROSA if redator else PLANO_BOM,
            model_id="fake-model-20260101",
            prompt_sha256=request.prompt_sha256,
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
def modelos(monkeypatch):
    duplo = _DoisModelos()
    monkeypatch.setattr("pat.cli._llm_client", lambda args: duplo)
    return duplo


@pytest.fixture
def corrida(home, modelos, tmp_path, capsys):
    """A cadeia inteira, uma vez. Devolve a saida do `pat ask --writer`."""
    envelope = tmp_path / "plano.json"

    assert main(
        ["--home", home, "plan", "Qual foi a margem EBITDA do GPA em 2024?",
         "--as-of", AS_OF, "--out", str(envelope)]
    ) == 0
    capsys.readouterr()

    codigo = main(["--home", home, "ask", "--plan-file", str(envelope), "--writer"])
    return codigo, capsys.readouterr().out, envelope, modelos


def test_a_cadeia_inteira_roda(corrida):
    codigo, _saida, envelope, modelos = corrida

    assert codigo == 0
    assert envelope.exists(), "o plano tem que sobreviver em disco entre os dois comandos"
    assert modelos.papeis == ["planner", "writer"], (
        "exatamente duas chamadas, uma por papel - o desenho da Fase 3 nao "
        "admite uma terceira"
    )


def test_a_resposta_final_tem_numero_prosa_citacao_e_manifesto(corrida):
    _codigo, saida, _envelope, _modelos = corrida

    for bloco in ("RESPOSTA", "CITACOES", "LEITURAS", "PROCEDENCIA", "MANIFESTO"):
        assert bloco in saida, f"a V1 nao apresentou o bloco {bloco}"


def test_o_numero_da_resposta_nao_veio_de_nenhum_dos_dois_modelos(corrida):
    """A invariante central da Fase 3, no unico teste que ve os dois modelos.

    O planejador escreveu um plano sem valor nenhum (a gramatica nao tem onde);
    o redator escreveu um token. O numero na tela so pode ter vindo do motor.
    """
    _codigo, saida, _envelope, _modelos = corrida

    assert "3.89%" in saida, "o valor calculado pelo motor tem que chegar a tela"
    assert "3.89" not in PLANO_BOM, "o plano do modelo nao contem o valor"
    assert "3.89" not in PROSA, "a prosa do modelo nao contem o valor"

    prosa = saida.split("RESPOSTA")[1].split("CITACOES")[0]
    assert "{{" not in prosa


def test_as_duas_chamadas_ficam_gravadas_com_papel_distinto(corrida, home):
    """Auditoria: `pat plan` grava uma chamada orfa (nao executou nada) e `pat
    ask --writer` grava a dela ligada ao manifesto. As duas custaram, as duas
    tem rastro, e da para dizer qual foi qual."""
    from pat.config import resolve_paths
    from pat.store.db import connect
    from pat.store.llm_calls import orphan_calls, read_calls

    _codigo, saida, _envelope, _modelos = corrida
    manifest_id = next(
        linha.split()[-1] for linha in saida.splitlines() if "manifest_id" in linha
    )

    conn = connect(resolve_paths(home).warehouse)
    try:
        (do_escritor,) = read_calls(conn, manifest_id)
        orfas = orphan_calls(conn)
    finally:
        conn.close()

    assert do_escritor.kind == "writer"
    assert [c.kind for c in orfas] == ["planner"]

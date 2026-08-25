"""O chat pelo caminho da Fase 5.

O teste que carrega o arquivo e
`test_o_chat_agora_responde_uma_pergunta_causal`: a mesma classe de pergunta
que o caminho da Fase 3 recusava com `unsupported_question`, porque a gramatica
do plano nao tinha como PEDIR uma decomposicao.

O cliente de modelo e um duplo que despacha por system prompt - tres papeis
agora, e nao dois: estagio 1, estagio 2 e escritor.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from pat.chat import ChatService
from pat.contracts.chat import ChatRequest
from pat.research.llm import LLMResponse
from tests.chat.conftest import AS_OF

GPA = "br:cnpj:47508411000156"

PROGRAMA = json.dumps(
    {
        "objective": "investigar a variacao do resultado operacional do GPA",
        "as_of": AS_OF.isoformat(),
        "scope": "consolidated",
        "questions_to_answer": ["Que componentes explicam a variacao do EBIT?"],
        "compute": None,
        "decompositions": [
            {
                "request_id": "ebit_fy23_fy24",
                "decomposition": "ebit_by_line@v1",
                "entity_id": GPA,
                "period_from": "2023-12-31",
                "period_to": "2024-12-31",
            }
        ],
        "unresolved": [],
    }
)

BUSCAS = json.dumps({"evidence": []})

RELATORIO = json.dumps(
    {
        "claims": [
            {
                "kind": "inference",
                "text": "A despesa operacional foi o componente de maior efeito.",
                "supports": ["__PRIMEIRO__"],
                "strength": "quantified",
            }
        ],
        "blocks": [
            {
                "kind": "prose",
                "text": "A variacao do resultado operacional foi de {{s:__TOKEN__}}.",
            }
        ],
    }
)


class LLMTresPapeis:
    """Despacha por system prompt, e nao por ordem de chamada."""

    fingerprint = "falso/1/0000"

    def __init__(self) -> None:
        self.roles: list[str] = []
        self._grounded_id: str | None = None
        self._token: str | None = None

    def complete(self, request):
        sistema = request.system
        if "primeiro estagio" in sistema:
            papel, texto = "compute", PROGRAMA
        elif "segundo estagio" in sistema:
            papel, texto = "evidence", BUSCAS
        else:
            papel = "writer"
            # O escritor recebe o grafo ancorado no prompt; usa o primeiro
            # claim_id e o primeiro token que ele oferece, em vez de inventar.
            carga = json.loads(request.user)
            primeiro = carga["afirmacoes_apuradas"][0]
            token = carga["tokens_disponiveis"][0]
            texto = RELATORIO.replace("__PRIMEIRO__", primeiro["claim_id"]).replace(
                "{{s:__TOKEN__}}", token
            )
        self.roles.append(papel)
        return LLMResponse(
            text=texto,
            model_id="fake-model",
            stop_reason="end_turn",
            prompt_sha256=request.prompt_sha256,
            response_sha256=hashlib.sha256(texto.encode()).hexdigest(),
            called_at=datetime.now(UTC),
        )


@pytest.fixture
def service(paths):
    return ChatService(
        paths=paths,
        llm=LLMTresPapeis(),
        model="fake-model",
        default_as_of=AS_OF,
        program_path=True,
    )


def _perguntar(service, texto):
    state = service.create_session()
    return service.send_message(ChatRequest(session_id=state.session_id, text=texto))


def test_o_chat_agora_responde_uma_pergunta_causal(service):
    """A pergunta que o caminho da Fase 3 recusava.

    La a recusa estava certa: a gramatica do plano so tem metrica e derivacao,
    e nao havia como pedir uma decomposicao. Aqui o programa pede, o executor
    determinístico abre a variacao, e o escritor escreve sobre o grafo.
    """
    turn = _perguntar(service, "Por que o resultado operacional do GPA caiu em 2024?")

    assert turn.refusal is None, turn.refusal
    assert turn.program_id is not None
    assert turn.prose, "o relatorio saiu vazio"
    assert turn.program_summary is not None
    assert "ebit_by_line@v1" in turn.program_summary.decompositions


def test_os_tres_papeis_de_modelo_sao_chamados_uma_vez_cada(service):
    """Estagio 1, estagio 2 e escritor. Nenhuma retentativa."""
    _perguntar(service, "Por que o resultado operacional do GPA caiu em 2024?")
    assert service._llm.roles == ["compute", "evidence", "writer"]


def test_o_numero_entra_por_substituicao_e_nao_pelo_modelo(service):
    """O escritor recebeu um TOKEN; o valor foi trocado depois, pelo sistema.

    A prosa final tem algarismo, e o prompt do escritor nao tinha - e o que
    separa "o modelo escreveu um numero" de "o sistema substituiu um token".
    """
    turn = _perguntar(service, "Por que o resultado operacional do GPA caiu em 2024?")
    texto = " ".join(turn.prose)
    assert any(c.isdigit() for c in texto), "nenhum valor foi substituido"
    assert "{{s:" not in texto, "sobrou token sem substituir"


def test_o_grafo_de_afirmacoes_chega_ao_resumo(service):
    """A UI mostra quantas afirmacoes de cada especie sustentam a resposta."""
    turn = _perguntar(service, "Por que o resultado operacional do GPA caiu em 2024?")
    especies = dict(turn.program_summary.claims)
    assert especies.get("calculation", 0) >= 1
    assert especies.get("inference", 0) == 1


def test_o_caminho_da_fase_3_continua_disponivel(paths):
    """`--legacy-plan` nao e um modo degradado: e o caminho mais barato.

    Uma pergunta puramente quantitativa nao precisa das tres chamadas do
    programa, e `pat ask --plan-file` continua reexecutando aquele plano sem
    modelo nenhum.
    """
    from tests.chat.conftest import FakeLLMClient

    service = ChatService(
        paths=paths,
        llm=FakeLLMClient(),
        model="fake-model",
        default_as_of=AS_OF,
        program_path=False,
    )
    turn = _perguntar(service, "Qual foi a margem EBITDA do GPA em 2024?")
    assert turn.answer is not None
    assert turn.program_id is None
    assert turn.plan is not None

"""A camada HTTP (H-1..H-8 da proposta), contra um servidor de verdade.

O servidor sobe numa porta efemera de `127.0.0.1` e as requisicoes vao por
`http.client`. Nao e exagero em relacao a chamar o handler direto: metade do
que este arquivo afirma - codigo de status, corpo JSON, o servidor continuar de
pe depois de uma excecao - so existe do lado do socket.
"""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from pat.chat.http import HOST, build_server
from tests.chat.conftest import AS_OF, PLANO_COM_DUVIDA, FakeLLMClient

PERGUNTA = "Qual foi a margem EBITDA do GPA em 2024?"


@pytest.fixture
def servidor(service):
    """Servidor vivo numa porta efemera. Encerrado ao fim do teste."""
    server = build_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


class Client:
    def __init__(self, server) -> None:
        self._porta = server.server_address[1]

    def request(self, metodo: str, caminho: str, corpo=None):
        conn = HTTPConnection(HOST, self._porta, timeout=60)
        try:
            payload = json.dumps(corpo).encode("utf-8") if corpo is not None else None
            headers = {"Content-Type": "application/json"} if payload else {}
            conn.request(metodo, caminho, body=payload, headers=headers)
            resposta = conn.getresponse()
            return resposta.status, resposta.read()
        finally:
            conn.close()

    def json(self, metodo: str, caminho: str, corpo=None):
        status, body = self.request(metodo, caminho, corpo)
        return status, json.loads(body)


@pytest.fixture
def client(servidor):
    return Client(servidor)


def _sessao(client) -> str:
    status, corpo = client.json("POST", "/api/session", {"as_of": AS_OF.isoformat()})
    assert status == 200
    return corpo["session_id"]


def test_h1_post_session_devolve_um_id_valido(client):
    import re

    status, corpo = client.json("POST", "/api/session", {})

    assert status == 200
    assert re.fullmatch(r"[0-9a-f]{16}", corpo["session_id"])
    assert corpo["as_of"] and corpo["created_at"]


def test_h2_sessao_desconhecida_e_404(client):
    status, corpo = client.json(
        "POST", "/api/chat", {"session_id": "0" * 16, "text": PERGUNTA}
    )

    assert status == 404
    assert "error" in corpo


def test_h3_campo_extra_no_corpo_e_400(client):
    """`extra="forbid"` do contrato falha na fronteira, nao tres camadas adiante."""
    sessao = _sessao(client)

    status, corpo = client.json(
        "POST", "/api/chat", {"session_id": sessao, "text": PERGUNTA, "temperatura": 1}
    )

    assert status == 400
    assert "error" in corpo


def test_h4_turno_recusado_e_200_e_nao_4xx(paths):
    """H-4: a recusa E a resposta correta do sistema.

    Um 4xx faria a UI tratar como erro de cliente o que e o comportamento
    projetado - e o desenho inteiro da Fase 3 depende de recusar ser resultado
    de primeira classe.
    """
    from pat.chat import ChatService

    service = ChatService(
        paths=paths,
        llm=FakeLLMClient(plan_text=PLANO_COM_DUVIDA),
        model="fake-model",
        default_as_of=AS_OF,
    )
    server = build_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = Client(server)
        sessao = _sessao(client)
        status, corpo = client.json(
            "POST", "/api/chat", {"session_id": sessao, "text": PERGUNTA}
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert corpo["status"] == "refused"
    assert corpo["refusal"]["kind"] == "planner_unresolved"
    assert corpo["answer"] is None


def test_h5_llm_error_e_502(client, llm):
    """Infra, e nao produto: o modelo nao respondeu."""
    from pat.research.llm import LLMError

    sessao = _sessao(client)
    llm.raises = LLMError("ANTHROPIC_API_KEY ausente")

    status, corpo = client.json("POST", "/api/chat", {"session_id": sessao, "text": PERGUNTA})

    assert status == 502
    assert "ANTHROPIC_API_KEY" in corpo["error"]


def test_h6_o_estatico_tem_lista_branca(client):
    """H-6: `GET /` serve a UI quando ela existe; travessia nunca serve nada.

    O arquivo e do Agent 3 e pode ainda nao existir - por isso a raiz aceita 200
    ou 404 -, mas o 404 tem que ser JSON, e nao excecao: a API continua
    utilizavel sem a casca.
    """
    status, body = client.request("GET", "/")
    assert status in (200, 404)
    if status == 404:
        assert json.loads(body)["error"]

    status, body = client.request("GET", "/../../etc/passwd")
    assert status == 404
    assert "root:" not in body.decode("utf-8", "replace")


def test_h7_o_plano_do_turno_volta_como_envelope_executavel(client):
    """H-7: round-trip real - o JSON baixado e o que `pat ask --plan-file` aceita.

    E o argumento inteiro para a rota existir: o chat nao e fonte de verdade, e
    uma casca sobre um pipeline que roda sem ele.
    """
    from pat.research import load_envelope

    sessao = _sessao(client)
    status, _ = client.json("POST", "/api/chat", {"session_id": sessao, "text": PERGUNTA})
    assert status == 200

    status, body = client.request("GET", f"/api/turn/{sessao}/0/plan")

    assert status == 200
    envelope = load_envelope(body)
    assert envelope.plan.outputs
    assert envelope.question.text == PERGUNTA


def test_h8_excecao_nao_prevista_vira_500_e_o_servidor_continua_de_pe(client, service):
    """H-8: sem a rede de seguranca, a excecao sai como HTML e mata a thread."""

    def explode() -> dict:
        raise RuntimeError("falha inesperada de programacao")

    service.capability = explode

    status, corpo = client.json("GET", "/api/capability")
    assert status == 500
    assert "RuntimeError" in corpo["error"]

    status, corpo = client.json("GET", "/health")
    assert status == 200, "o servidor tem que sobreviver a excecao anterior"
    assert corpo["pat_version"]


def test_capability_e_session_respondem_sem_turno_nenhum(client):
    status, corpo = client.json("GET", "/api/capability")
    assert status == 200
    assert corpo["entities"] and corpo["metrics"] and corpo["capability_sha256"]

    sessao = _sessao(client)
    status, corpo = client.json("GET", f"/api/session/{sessao}")
    assert status == 200
    assert corpo["turns"] == []


def test_rota_desconhecida_e_404_json(client):
    status, corpo = client.json("GET", "/api/nada")
    assert status == 404
    assert "error" in corpo

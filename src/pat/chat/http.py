"""O servidor HTTP local do chat. Biblioteca padrao, zero dependencias.

`ThreadingHTTPServer` + `BaseHTTPRequestHandler` bastam para o que existe aqui:
sete rotas, sem streaming, sem WebSocket, sem autenticacao. Um framework
traria middleware, validacao propria e um servidor ASGI para servir um HTML e
um POST que demora trinta segundos - e travaria dependencia nova num projeto
cuja regra e ter poucas e conferidas.

Duas decisoes que nao se negociam neste arquivo:

- O bind e `127.0.0.1` fixo, sem flag de host. Um servico sem autenticacao que
  gasta credito de API nao pode ter `0.0.0.0` a um argumento de distancia; quem
  quiser expor poe um tunel na frente e assume a decisao.
- Turno RECUSADO devolve 200, nao 4xx. A recusa e a resposta correta do
  sistema: um 4xx faria a UI tratar como erro de cliente o que e o
  comportamento projetado, e o desenho inteiro da Fase 3 depende de recusar ser
  um resultado de primeira classe.
"""

from __future__ import annotations

import json
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pat import __version__
from pat.chat import ChatService, UnknownSession
from pat.chat.session import InvalidSessionId

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

STATIC_DIR = Path(__file__).parent / "static"
STATIC_ALLOWED = {"index.html"}
"""Lista branca de nomes. O caminho servido nunca e montado a partir da entrada
do usuario - `GET /../../etc/passwd` nao chega a virar caminho, ele simplesmente
nao esta no conjunto."""

_SESSION_RE = r"(?P<session_id>[0-9a-f]{16})"
_TURN_PLAN_RE = re.compile(rf"^/api/turn/{_SESSION_RE}/(?P<turn_index>\d+)/plan$")
_SESSION_GET_RE = re.compile(rf"^/api/session/{_SESSION_RE}$")

MAX_BODY_BYTES = 64 * 1024


class ChatHTTPRequestHandler(BaseHTTPRequestHandler):
    """Handler com o servico injetado pelo servidor (`server.service`)."""

    server_version = f"pat/{__version__}"

    # -- infraestrutura -----------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        """Uma linha enxuta, e nao o formato de log do modulo padrao.

        O default escreve em stderr com timestamp e user agent a cada
        requisicao, inclusive as de favicon: num terminal onde o que interessa
        e a URL e o modelo, isso e ruido que esconde a mensagem de erro quando
        ela aparece.
        """
        return

    @property
    def service(self) -> ChatService:
        return self.server.service  # type: ignore[attr-defined]

    def _send(self, code: int, payload: dict | list, *, raw: bytes | None = None) -> None:
        body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str, **extra) -> None:
        self._send(code, {"error": message, **extra})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise _BadRequest(f"corpo acima de {MAX_BODY_BYTES} bytes")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except ValueError as exc:
            raise _BadRequest(f"corpo nao e JSON valido: {exc}") from exc
        if not isinstance(payload, dict):
            raise _BadRequest("corpo tem que ser um objeto JSON")
        return payload

    # -- rotas --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - assinatura da biblioteca padrao
        self._guard(self._get)

    def do_POST(self) -> None:  # noqa: N802 - assinatura da biblioteca padrao
        self._guard(self._post)

    def _guard(self, handler) -> None:
        """Todo handler dentro de um `try/except`.

        Sem isto, uma excecao nao prevista sai como pagina HTML de erro do
        modulo padrao e mata a thread - o usuario ve um traceback no browser e o
        servidor perde a requisicao seguinte. Aqui ela vira 500 JSON e o
        processo continua de pe.
        """
        try:
            handler()
        except _BadRequest as exc:
            self._error(400, str(exc))
        except UnknownSession as exc:
            self._error(404, f"sessao desconhecida: {exc}")
        except InvalidSessionId as exc:
            self._error(404, str(exc))
        except Exception as exc:  # noqa: BLE001 - a rede de seguranca do processo
            from pat.research.llm import LLMError

            if isinstance(exc, LLMError):
                # Infra, e nao produto: o modelo nao respondeu. Distinto de
                # "o modelo respondeu algo invalido", que e 200 com status
                # failed - a UI pinta os dois de forma diferente de proposito.
                self._error(502, f"chamada ao modelo falhou: {exc}")
                return
            if not self.service.paths.warehouse.exists():
                self._error(
                    503,
                    f"warehouse nao encontrado em {self.service.paths.warehouse}. "
                    "Rode `pat init` e depois `pat fetch`/`pat build`.",
                )
                return
            self._error(500, f"{type(exc).__name__}: {exc}")

    def _get(self) -> None:
        caminho = self.path.split("?", 1)[0]

        if caminho in ("/", "/index.html"):
            self._static("index.html")
            return

        if caminho == "/health":
            self._send(
                200,
                {
                    "status": "ok",
                    "pat_version": __version__,
                    "warehouse": str(self.service.paths.warehouse),
                    "model": self.service.model,
                },
            )
            return

        if caminho == "/api/capability":
            # Nao pega o lock de proposito: a UI tem que carregar enquanto um
            # turno de trinta segundos roda.
            self._send(200, self.service.capability())
            return

        if (match := _SESSION_GET_RE.match(caminho)) is not None:
            state = self.service.get_session(match["session_id"])
            if state is None:
                raise UnknownSession(match["session_id"])
            nomes = self.service.entity_names()
            from pat.chat.view import turn_view

            self._send(
                200,
                {
                    "session_id": state.session_id,
                    "as_of": state.as_of.isoformat(),
                    "created_at": state.created_at.isoformat(),
                    "turns": [turn_view(t, entity_names=nomes) for t in state.turns],
                },
            )
            return

        if (match := _TURN_PLAN_RE.match(caminho)) is not None:
            self._plan(match["session_id"], int(match["turn_index"]))
            return

        self._error(404, f"rota desconhecida: {caminho}")

    def _post(self) -> None:
        caminho = self.path.split("?", 1)[0]

        if caminho == "/api/session":
            payload = self._read_json()
            desconhecidos = set(payload) - {"as_of"}
            if desconhecidos:
                raise _BadRequest(f"campo desconhecido: {', '.join(sorted(desconhecidos))}")
            as_of = None
            if payload.get("as_of"):
                try:
                    as_of = date.fromisoformat(str(payload["as_of"]))
                except ValueError as exc:
                    raise _BadRequest(f"as_of invalido: {exc}") from exc
            state = self.service.create_session(as_of=as_of)
            self._send(
                200,
                {
                    "session_id": state.session_id,
                    "as_of": state.as_of.isoformat(),
                    "created_at": state.created_at.isoformat(),
                },
            )
            return

        if caminho == "/api/chat":
            self._chat()
            return

        self._error(404, f"rota desconhecida: {caminho}")

    def _chat(self) -> None:
        from pydantic import ValidationError

        from pat.contracts.chat import ChatRequest

        payload = self._read_json()
        try:
            # `extra="forbid"` do contrato faz o trabalho: campo desconhecido
            # falha aqui, na fronteira de entrada, e nao tres camadas adiante.
            request = ChatRequest.model_validate(payload)
        except ValidationError as exc:
            raise _BadRequest(f"corpo invalido: {exc.errors(include_url=False)}") from exc

        turn = self.service.send_message(request)
        # 200 mesmo quando o turno e recusa ou falha de modelo - ver o cabecalho
        # deste arquivo.
        self._send(200, self.service.view(turn))

    def _plan(self, session_id: str, turn_index: int) -> None:
        """O envelope {question, plan}, na forma que `pat ask --plan-file` aceita.

        `model_dump_json`, e nao a forma canonica: a canonica existe para
        hashear e nao para reler - ela achata `MetricRef` em "nome@versao", que
        e ambiguo na volta. E o argumento inteiro para esta rota existir: o JSON
        baixado daqui roda no CLI sem chamada de modelo nenhuma.
        """
        from pat.contracts.research import PlanEnvelope

        state = self.service.get_session(session_id)
        if state is None:
            raise UnknownSession(session_id)
        if turn_index >= len(state.turns):
            self._error(404, f"turno {turn_index} nao existe na sessao {session_id}")
            return
        turn = state.turns[turn_index]
        if turn.plan is None:
            self._error(404, f"o turno {turn_index} nao produziu plano")
            return
        envelope = PlanEnvelope(question=turn.question, plan=turn.plan)
        self._send(200, {}, raw=envelope.model_dump_json(indent=2).encode("utf-8"))

    def _static(self, name: str) -> None:
        if name not in STATIC_ALLOWED:
            self._error(404, f"arquivo desconhecido: {name}")
            return
        path = STATIC_DIR / name
        if not path.exists():
            # O frontend pode ainda nao existir. 404 com JSON, e nao excecao: a
            # API tem que continuar utilizavel sem a casca.
            self._error(404, f"{name} nao encontrado em {STATIC_DIR}")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _BadRequest(ValueError):
    """Corpo que nao chega a ser um pedido. Vira 400."""


class ChatHTTPServer(ThreadingHTTPServer):
    """`ThreadingHTTPServer` porque `GET /` e `GET /api/capability` nao podem
    esperar o turno em andamento; a serializacao de quem escreve e do lock do
    `ChatService`, nao do servidor."""

    daemon_threads = True

    def __init__(self, address, handler, *, service: ChatService) -> None:
        super().__init__(address, handler)
        self.service = service


def build_server(service: ChatService, *, port: int = DEFAULT_PORT) -> ChatHTTPServer:
    return ChatHTTPServer((HOST, port), ChatHTTPRequestHandler, service=service)


def serve(service: ChatService, *, port: int = DEFAULT_PORT) -> None:
    server = build_server(service, port=port)
    try:
        server.serve_forever()
    finally:
        server.server_close()

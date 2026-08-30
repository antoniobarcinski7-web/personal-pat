"""O10 - a CLI do Opportunity.

Mora FORA de `pat/opportunity/` de proposito. A CLI e raiz de composicao: e
aqui que `--cod-cvm` e `--cik` podem existir, porque e aqui que um humano
digita o identificador que ele conhece. `tests/opportunity/
test_layering_opportunity.py` proibe esses termos dentro da camada, e a saida
nao e uma excecao na lista - e por o arquivo do lado de fora.

Sete subcomandos, na ordem em que se usam:

    pat opportunity init       abre a investigacao
    pat opportunity status     onde estamos
    pat opportunity chat       fala com o agente
    pat opportunity research   decompoe um objetivo e executa
    pat opportunity critic     advogado do diabo
    pat opportunity valuation  declara (TOML) e roda o modelo
    pat opportunity thesis     redige (TOML) e audita a tese

`valuation` e `thesis` recebem TOML, e nao vinte flags. Uma premissa exige
valor, unidade, base e justificativa; espremer isso numa linha de comando
produziria a justificativa de uma palavra que o campo existe justamente para
impedir. TOML tambem e o idioma que os mapeamentos ja usam.
"""

from __future__ import annotations

import sys
import tomllib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from pat.config import resolve_paths
from pat.contracts.opportunity import (
    Actor,
    Assumption,
    AssumptionBasis,
    AssumptionSet,
    Catalyst,
    DataPoint,
    HypothesisStrength,
    InvestmentThesis,
    Intent,
    Risk,
    RiskSeverity,
    Severity,
    ThesisDirection,
    ThesisDrafted,
    TurnRequest,
    ValuationDeclared,
    ValuationModel,
)
from pat.opportunity import (
    ChatAgent,
    PatTools,
    ShapeReasoner,
    audit_thesis,
    create_workspace,
    critique,
    list_workspaces,
    open_workspace,
    plan_agenda,
    profile_for,
    run_agenda,
    run_dcf,
)

__all__ = ["add_opportunity_parser"]


# ---------------------------------------------------------------------------
# Abertura
# ---------------------------------------------------------------------------


def _root(args) -> Path:
    return resolve_paths(args.home).ensure().opportunity


def _resolve_workspace(args):
    """`--workspace ID`, ou o unico que existe, ou uma lista para escolher.

    Escolher "o mais recente" quando ha varios seria conveniencia que erra em
    silencio: o analista digitaria um turno na investigacao errada e so
    perceberia paginas depois.
    """
    raiz = _root(args)
    if getattr(args, "workspace", None):
        return open_workspace(raiz, args.workspace)
    estados = list(list_workspaces(raiz))
    if not estados:
        print(
            "nenhum workspace. Comece com:\n"
            "  pat opportunity init --cod-cvm N --as-of AAAA-MM-DD",
            file=sys.stderr,
        )
        return None
    if len(estados) > 1:
        print("mais de um workspace aberto; escolha com --workspace:", file=sys.stderr)
        for estado in estados:
            print(
                f"  {estado.workspace_id}  {estado.workspace.company.display_name}"
                f"  as_of {estado.as_of.isoformat()}",
                file=sys.stderr,
            )
        return None
    return open_workspace(raiz, estados[0].workspace_id)


def _tools(args, workspace):
    """A mesa, ligada ao warehouse e presa a UMA empresa.

    O `as_of` vem do workspace, e nao da linha de comando: dois comandos com
    `as_of` diferente sobre o mesmo workspace produziriam evidencia que nao
    se compara.
    """
    from pat.cli import _open_readonly

    conn = _open_readonly(args)
    if conn is None:
        return None
    estado = workspace.state
    # Devolve a conexao junto: a mesa nao a fecha, porque ela nao a abriu.
    # Quem abre fecha - senao o dono do recurso vira "o ultimo que mexeu".
    return PatTools(conn, company=estado.workspace.company, as_of=estado.as_of), conn


# ---------------------------------------------------------------------------
# init / list / status
# ---------------------------------------------------------------------------


def cmd_opp_init(args) -> int:
    from pat.cli import _open_readonly, _resolve_entity_arg

    if (conn := _open_readonly(args)) is None:
        return 1
    try:
        resolvido = _resolve_entity_arg(conn, args)
        if resolvido is None:
            return 1
        entity_id, _ = resolvido
        perfil = profile_for(conn, entity_id)
    finally:
        conn.close()

    workspace = create_workspace(
        _root(args),
        company=perfil,
        as_of=args.as_of,
        title=args.title,
        mandate=args.mandate,
        actor=Actor.USER,
    )
    print(f"workspace    {workspace.workspace_id}")
    print(f"empresa      {perfil.display_name} ({perfil.jurisdiction})")
    print(f"as_of        {args.as_of.isoformat()}")
    print(f"mandato      {args.mandate or '(nao declarado)'}")
    print()
    print("Proximo passo:")
    print(f"  pat opportunity chat --workspace {workspace.workspace_id} 'onde estamos?'")
    return 0


def cmd_opp_list(args) -> int:
    estados = list(list_workspaces(_root(args)))
    if not estados:
        print("nenhum workspace.")
        return 0
    for estado in estados:
        abertas = len(estado.open_hypotheses)
        print(
            f"{estado.workspace_id}  {estado.workspace.status.value:<8} "
            f"{estado.workspace.company.display_name:<40} "
            f"as_of {estado.as_of.isoformat()}  "
            f"{len(estado.agenda.tasks)} tarefa(s), {abertas} hipotese(s) aberta(s)"
        )
    return 0


def cmd_opp_status(args) -> int:
    """O status vem do MESMO codigo que a conversa usa.

    Duas implementacoes de "onde estamos" divergiriam, e a divergencia
    apareceria como o comando dizendo uma coisa e o agente outra na mesma
    investigacao.
    """
    workspace = _resolve_workspace(args)
    if workspace is None:
        return 1
    aberto = _tools(args, workspace)
    if aberto is None:
        return 1
    tools, conn = aberto
    try:
        agente = ChatAgent(workspace, tools, ShapeReasoner())
        resposta = agente.respond(
            TurnRequest(
                text="status",
                workspace_id=workspace.workspace_id,
                intent_hint=Intent.STATUS,
            )
        )
        print(f"workspace    {workspace.workspace_id}")
        print(resposta.text)
        for sugestao in resposta.follow_up:
            print(f"  -> {sugestao}")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


def cmd_opp_chat(args) -> int:
    """Um turno por invocacao, ou um laco de leitura quando nao vem texto.

    Nao ha estado de sessao em memoria nos dois casos: o fio da conversa e o
    diario, e por isso sair do laco e voltar amanha e indistinguivel de nunca
    ter saido.
    """
    workspace = _resolve_workspace(args)
    if workspace is None:
        return 1
    aberto = _tools(args, workspace)
    if aberto is None:
        return 1
    tools, conn = aberto
    try:
        agente = ChatAgent(workspace, tools, _reasoner(args))
        if args.text:
            return _turno(agente, " ".join(args.text))
        print(
            f"{workspace.state.workspace.company.display_name} - as_of "
            f"{workspace.state.as_of.isoformat()}. Ctrl-D para sair."
        )
        while True:
            try:
                linha = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if linha:
                _turno(agente, linha)
    finally:
        conn.close()


def _turno(agente: ChatAgent, texto: str) -> int:
    resposta = agente.respond(TurnRequest(text=texto, workspace_id=agente.workspace.workspace_id))
    print(resposta.text)
    if resposta.disagreement:
        print()
        print(f"DISCORDO: {resposta.disagreement}")
    if resposta.grounded_in:
        print()
        print("ancorado em:")
        for endereco in resposta.grounded_in:
            print(f"  {endereco}")
    for sugestao in resposta.follow_up:
        print(f"  -> {sugestao}")
    # As acoes vao para stderr: elas dizem o que MUDOU, e quem esta lendo a
    # conversa num pipe quer a fala, nao o registro de auditoria.
    print(
        f"[turno {resposta.turn_index} | {resposta.intent.value} | "
        f"{', '.join(a.value for a in resposta.actions) or 'nenhuma acao'}]",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


def cmd_opp_research(args) -> int:
    workspace = _resolve_workspace(args)
    if workspace is None:
        return 1
    aberto = _tools(args, workspace)
    if aberto is None:
        return 1
    tools, conn = aberto
    try:
        reasoner = _reasoner(args)
        criados = plan_agenda(
            workspace, tools, reasoner, objective=args.objective, actor=Actor.AGENT
        )
        print(f"agenda: {len(criados)} tarefa(s) nova(s)")
        corrida = run_agenda(workspace, tools, reasoner, max_tasks=args.max_tasks)
        for relatorio in corrida.reports:
            print()
            print(f"{relatorio.task}  [{relatorio.verdict.status.value}]")
            print(f"  {relatorio.verdict.rationale}")
            for passo in relatorio.outcomes:
                for linha in passo.rendered:
                    print(f"    {linha}")
                for recusa in passo.unavailable:
                    print(f"    (sem resposta) {recusa}")
            for achado in relatorio.findings:
                print(f"  - {achado.text}")
            for barrado in relatorio.rejected:
                print(f"  ! barrado ({barrado.reason.value}): {barrado.detail}")
        print()
        print(f"parou porque: {corrida.stopped_because}")
        # Travado nao e falha do comando: e o estado correto de quem nao tem o
        # dado. Sair com 1 aqui faria um script tratar "faltou cobertura" como
        # "o comando quebrou".
        if corrida.blocked:
            print(f"travadas: {', '.join(corrida.blocked)}")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# critic
# ---------------------------------------------------------------------------


def cmd_opp_critic(args) -> int:
    """Sai com 1 quando ha achado DURO.

    O codigo de saida e a parte util num script: "esta tese pode ser
    apresentada?" tem que ser respondivel sem alguem ler a prosa.
    """
    workspace = _resolve_workspace(args)
    if workspace is None:
        return 1
    veredito = critique(workspace.state)
    print(
        f"{veredito.hypotheses_checked} hipotese(s), {veredito.claims_checked} claim(s) "
        f"conferida(s); {len(veredito.findings)} objecao(oes)"
    )
    for achado in veredito.findings:
        marca = "DURO" if achado.severity is Severity.HARD else "leve"
        print()
        print(f"[{marca}] {achado.code.value}")
        print(f"  {achado.message}")
        print(f"  remedio: {achado.remedy}")
        for ref in achado.evidence_refs:
            print(f"  evidencia: {ref}")
    return 1 if any(f.severity is Severity.HARD for f in veredito.findings) else 0


# ---------------------------------------------------------------------------
# valuation
# ---------------------------------------------------------------------------


def _toml(caminho: Path) -> dict:
    with caminho.open("rb") as arquivo:
        return tomllib.load(arquivo)


def _decimal(bruto) -> Decimal:
    """TOML -> `Decimal`, sempre pela string.

    `Decimal(0.1)` de um float TOML traria o erro binario para dentro do
    dinheiro. Escrever o numero entre aspas no TOML e o caminho certo, e ler
    via `str` faz o caminho errado doer menos do que passar calado.
    """
    return Decimal(str(bruto))


def _assumption(slug: str, bruto: dict, momento: datetime) -> Assumption:
    return Assumption(
        slug=slug,
        label=bruto["label"],
        value=_decimal(bruto["value"]),
        unit=bruto.get("unit", "ratio"),
        basis=AssumptionBasis(bruto["basis"]),
        rationale=bruto["rationale"],
        author=Actor(bruto.get("author", "user")),
        derived_from=tuple(bruto.get("derived_from", ())),
        at=momento,
    )


def cmd_opp_valuation(args) -> int:
    workspace = _resolve_workspace(args)
    if workspace is None:
        return 1

    if args.declare:
        momento = datetime.now(UTC)
        bruto = _toml(Path(args.declare))
        modelo = ValuationModel(
            slug=bruto["slug"],
            currency=bruto["currency"],
            horizon_years=int(bruto["horizon_years"]),
            data=tuple(
                DataPoint(
                    slug=slug,
                    label=d["label"],
                    value=_decimal(d["value"]),
                    unit=d["unit"],
                    result_id=d["result_id"],
                    period_end=d.get("period_end"),
                )
                for slug, d in bruto.get("data", {}).items()
            ),
            assumptions=AssumptionSet(
                assumptions=tuple(
                    _assumption(slug, a, momento)
                    for slug, a in bruto.get("assumptions", {}).items()
                )
            ),
            created_by=Actor.USER,
            at=momento,
        )
        workspace.apply(ValuationDeclared(model=modelo), actor=Actor.USER)
        print(f"modelo {modelo.slug} declarado.")

    modelos = workspace.state.valuations
    if not modelos:
        print(
            "nenhum modelo declarado. Escreva um TOML e rode:\n"
            "  pat opportunity valuation --declare modelo.toml\n"
            "Nao ha premissa default: um default seria uma escolha de investimento "
            "embutida na ferramenta.",
            file=sys.stderr,
        )
        return 1

    for modelo in modelos:
        if args.model and modelo.slug != args.model:
            continue
        print()
        print(f"{modelo.slug}  ({modelo.currency}, {modelo.horizon_years} ano(s))")
        resultado = run_dcf(modelo)
        if hasattr(resultado, "reason"):
            print(f"  SEM RESULTADO: {resultado.reason.value}")
            print(f"  {resultado.message}")
            print(f"  remedio: {resultado.remedy}")
            continue
        print(f"  enterprise value  {resultado.enterprise_value}")
        print(f"  equity value      {resultado.equity_value}")
        if resultado.per_share is not None:
            print(f"  por acao          {resultado.per_share}")
        print(f"  perpetuidade      {resultado.terminal_share} do EV")
        if resultado.terminal_dominates:
            print(
                "  AVISO: a perpetuidade domina. O modelo esta dizendo mais sobre a "
                "premissa terminal do que sobre a companhia."
            )
        print("  premissas:")
        for premissa in modelo.assumptions.assumptions:
            print(
                f"    {premissa.slug} = {premissa.value} {premissa.unit} "
                f"[{premissa.basis.value}] {premissa.rationale}"
            )
    return 0


# ---------------------------------------------------------------------------
# thesis
# ---------------------------------------------------------------------------


def cmd_opp_thesis(args) -> int:
    workspace = _resolve_workspace(args)
    if workspace is None:
        return 1

    if args.draft:
        bruto = _toml(Path(args.draft))
        tese = InvestmentThesis(
            slug=bruto["slug"],
            statement=bruto["statement"],
            direction=ThesisDirection(bruto["direction"]),
            confidence=HypothesisStrength(bruto["confidence"]),
            supporting_claims=tuple(bruto.get("supporting_claims", ())),
            supporting_hypotheses=tuple(bruto.get("supporting_hypotheses", ())),
            key_assumptions=tuple(bruto["key_assumptions"]),
            risks=tuple(
                Risk(
                    slug=slug,
                    text=r["text"],
                    severity=RiskSeverity(r["severity"]),
                    leading_indicator=r.get("leading_indicator"),
                    evidence_refs=tuple(r.get("evidence_refs", ())),
                )
                for slug, r in bruto["risks"].items()
            ),
            falsifiers=tuple(bruto["falsifiers"]),
            counter_thesis=bruto["counter_thesis"],
            unresolved=tuple(bruto.get("unresolved", ())),
            catalysts=tuple(
                Catalyst(
                    slug=slug,
                    text=c["text"],
                    expected_by=c.get("expected_by"),
                    is_within_control=bool(c.get("is_within_control", False)),
                )
                for slug, c in bruto.get("catalysts", {}).items()
            ),
            valuation=bruto.get("valuation"),
            as_of=workspace.state.as_of,
            author=Actor.USER,
            created_at=datetime.now(UTC),
        )
        workspace.apply(ThesisDrafted(thesis=tese), actor=Actor.USER)
        print(f"tese {tese.slug} registrada.")

    teses = workspace.state.theses
    if not teses:
        print(
            "nenhuma tese. Escreva um TOML e rode:\n"
            "  pat opportunity thesis --draft tese.toml",
            file=sys.stderr,
        )
        return 1

    duras = 0
    for tese in teses:
        if args.slug and tese.slug != args.slug:
            continue
        print()
        print(f"{tese.slug}  {tese.direction.value.upper()}  ({tese.confidence.value})")
        print(f"  {tese.statement}")
        print(f"  contra-tese: {tese.counter_thesis}")
        print("  depende de:")
        for premissa in tese.key_assumptions:
            print(f"    - {premissa}")
        print("  riscos:")
        for risco in tese.risks:
            print(f"    [{risco.severity.value}] {risco.text}")
        print("  o que a derruba:")
        for falsificador in tese.falsifiers:
            print(f"    - {falsificador}")
        for aberto in tese.unresolved:
            print(f"  em aberto: {aberto}")

        auditoria = audit_thesis(workspace.state, tese)
        print()
        print(
            f"  auditoria: {auditoria.claims_checked} claim(s), "
            f"{len(auditoria.evidence_refs)} endereco(s); "
            f"{len(auditoria.issues)} problema(s)"
        )
        for problema in auditoria.issues:
            # `ThesisAuditIssue` nao tem severidade, e nao deve ter: a
            # auditoria confere INTEGRIDADE REFERENCIAL, e uma cadeia que se
            # rompe se rompeu - nao ha problema leve de tese que cita claim
            # inexistente. Quem gradua severidade e o critico, sobre hipotese.
            print(f"    [{problema.issue.value}] {problema.message}")
            print(f"      remedio: {problema.remedy}")
            if problema.ref:
                print(f"      referencia: {problema.ref}")
        duras += len(auditoria.issues)
    return 1 if duras else 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


DEFAULT_REASONER_MODEL = "claude-opus-5"


def _add_reasoner_args(p) -> None:
    """As opcoes de quem raciocina, num lugar so.

    `shape` continua o default. Nao por conservadorismo: e o unico que roda sem
    chave de API, e um default que exige credencial faria `pat opportunity
    research` falhar na primeira vez em que alguem o experimenta.
    """
    p.add_argument(
        "--reasoner",
        choices=("shape", "llm"),
        default="shape",
        help="shape: tabela deterministica, sem rede. llm: modelo, exige ANTHROPIC_API_KEY",
    )
    p.add_argument("--model", dest="reasoner_model", default=DEFAULT_REASONER_MODEL)
    p.add_argument("--no-cache", dest="no_cache", action="store_true")


def _reasoner(args):
    """Escolhe o raciocinador. Raiz de composicao, e por isso mora aqui.

    Sem fallback silencioso: pedir `--reasoner llm` sem chave falha dizendo
    isso. Cair para o `ShapeReasoner` produziria uma investigacao que diz ter
    sido raciocinada por um modelo e nao foi - e o `reasoner_id` do relatorio
    carregaria a mentira para dentro do diario, onde ela fica.
    """
    from pat.opportunity.reason import ShapeReasoner

    if getattr(args, "reasoner", "shape") == "shape":
        return ShapeReasoner()

    from pat.cli import _llm_client
    from pat.opportunity.llm_reasoner import LlmReasoner
    from pat.research.llm import LLMTransportError

    try:
        cliente = _llm_client(args)
    except LLMTransportError as exc:
        # A mensagem do adapter ja diz o certo; o traceback em volta dela nao
        # acrescenta nada a quem digitou o comando errado.
        print(f"{exc}\n\nOu rode sem chave: --reasoner shape", file=sys.stderr)
        raise SystemExit(1) from None
    return LlmReasoner(client=cliente, model=args.reasoner_model)


def add_opportunity_parser(sub) -> None:
    """Pendura `pat opportunity ...` no parser principal.

    Uma funcao, e nao mais quinhentas linhas em `cli.py`: o arquivo principal
    ja tem 2600, e a camada nova tem um ciclo de vida proprio.
    """
    p = sub.add_parser(
        "opportunity", help="investigacao de uma empresa: workspace, pesquisa, tese"
    )
    interno = p.add_subparsers(dest="opportunity_command", required=True)

    p_init = interno.add_parser("init", help="abre uma investigacao")
    p_init.add_argument("--cod-cvm", dest="cod_cvm", type=int)
    p_init.add_argument("--cik", help="companhia americana")
    p_init.add_argument("--as-of", type=date.fromisoformat, required=True)
    p_init.add_argument("--title")
    p_init.add_argument("--mandate", help="a pergunta que a investigacao responde")
    p_init.set_defaults(func=cmd_opp_init)

    interno.add_parser("list", help="workspaces existentes").set_defaults(
        func=cmd_opp_list
    )

    p_status = interno.add_parser("status", help="onde a investigacao esta")
    p_status.add_argument("--workspace")
    p_status.set_defaults(func=cmd_opp_status)

    p_chat = interno.add_parser("chat", help="fala com o agente")
    p_chat.add_argument("text", nargs="*", help="sem texto, abre o laco de leitura")
    p_chat.add_argument("--workspace")
    _add_reasoner_args(p_chat)
    p_chat.set_defaults(func=cmd_opp_chat)

    p_res = interno.add_parser("research", help="decompoe um objetivo e executa")
    p_res.add_argument("--objective", required=True)
    p_res.add_argument("--workspace")
    p_res.add_argument("--max-tasks", type=int, default=12)
    _add_reasoner_args(p_res)
    p_res.set_defaults(func=cmd_opp_research)

    p_cri = interno.add_parser("critic", help="advogado do diabo; sai 1 se ha achado duro")
    p_cri.add_argument("--workspace")
    p_cri.set_defaults(func=cmd_opp_critic)

    p_val = interno.add_parser("valuation", help="declara (TOML) e roda o modelo")
    p_val.add_argument("--workspace")
    p_val.add_argument("--declare", metavar="TOML", help="declara um modelo novo")
    p_val.add_argument("--model", help="roda so este modelo")
    p_val.set_defaults(func=cmd_opp_valuation)

    p_the = interno.add_parser("thesis", help="redige (TOML) e audita a tese")
    p_the.add_argument("--workspace")
    p_the.add_argument("--draft", metavar="TOML", help="registra uma versao nova")
    p_the.add_argument("--slug", help="audita so esta tese")
    p_the.set_defaults(func=cmd_opp_thesis)

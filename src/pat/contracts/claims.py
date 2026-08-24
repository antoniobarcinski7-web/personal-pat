"""O grafo de afirmacoes (Fase 5, M5.4): nenhuma frase sem procedencia tipada.

Nada neste modulo importa implementacao.

As cinco especies, e por que sao cinco
--------------------------------------
    FACT         numero do motor          fact_id -> byte no bronze
    CALCULATION  conta entre resultados   derived_from (>=1)
    QUOTE        trecho verbatim          unit_id -> documento -> byte
    INFERENCE    leitura                  supports (>=1) + forca declarada
    CONCLUSION   sintese                  supports (>=1) + falsificador

As tres primeiras sao VERIFICAVEIS MECANICAMENTE. As duas ultimas nao sao, e e
exatamente por isso que sao especies distintas: a diferenca entre "a margem foi
3,89%" e "a margem caiu por alavancagem operacional" nao pode depender de o
leitor prestar atencao. Ela e estrutural, aparece no JSON, na tela e no
relatorio.

Um no, e nao cinco classes
--------------------------
O projeto normalmente usa uniao discriminada com "exatamente um de". Aqui nao:
um GRAFO precisa de nos homogeneos - percorrer, detectar ciclo e conferir
alcance ficam triviais com um tipo so, e ficariam cheios de `isinstance` com
cinco. A disciplina se mantem por validadores duros: cada especie declara o que
EXIGE e o que NAO PODE ter, e um no que erre isso nao e construivel.

O portao contra a lavagem de numero do emissor
----------------------------------------------
`CALCULATION` nao pode se apoiar em `QUOTE`. Esta e a regra que impede um
numero lido de um release de virar insumo de conta, e ela e conferida na
construcao do grafo - nao no critic, nao no writer, nao num prompt. Um
relatorio que a violasse nao chegaria a existir.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from pat.contracts.common import Frozen, Sha256


class ClaimKind(StrEnum):
    FACT = "fact"
    CALCULATION = "calculation"
    QUOTE = "quote"
    INFERENCE = "inference"
    CONCLUSION = "conclusion"


GROUNDED = frozenset({ClaimKind.FACT, ClaimKind.CALCULATION, ClaimKind.QUOTE})
"""As especies que resolvem para um byte. Toda leitura tem que alcancar uma."""

DERIVED = frozenset({ClaimKind.INFERENCE, ClaimKind.CONCLUSION})


class EvidenceStrength(StrEnum):
    """Quanto uma leitura esta sustentada. Enum, e NUNCA um numero.

    Um escore 0-1 seria um numero produzido por modelo - e o pior tipo, porque
    pareceria medido. "0,73 de confianca" convida a ser somado, comparado e
    tratado como probabilidade, e nao e nenhuma das tres coisas.
    """

    QUANTIFIED = "quantified"
    """A leitura esta ligada a uma decomposicao que a mede."""

    ATTRIBUTED = "attributed"
    """Alguem com nome disse isso, e a citacao esta anexada."""

    SUGGESTED = "suggested"
    """Compativel com a evidencia, sem que ela afirme a relacao."""


class ClaimNode(Frozen):
    """Uma afirmacao, com a procedencia que a especie dela exige."""

    claim_id: Sha256
    kind: ClaimKind
    text: str = Field(min_length=1)
    """Para FACT e CALCULATION: o que o numero E, sem o valor (`means`).
    Para QUOTE: o trecho VERBATIM, byte a byte.
    Para INFERENCE e CONCLUSION: a leitura, em prosa."""

    token: str | None = None
    """Substituicao do valor, so em FACT e CALCULATION. O valor renderizado
    NAO mora aqui: ele entra depois, deterministicamente, pelo mesmo caminho
    da Fase 3."""

    result_id: Sha256 | None = None
    unit_id: Sha256 | None = None
    supports: tuple[Sha256, ...] = ()
    strength: EvidenceStrength | None = None
    falsified_by: tuple[str, ...] = ()
    """O que derrubaria a conclusao. Obrigatorio, e nao decorativo.

    Uma conclusao que nao diz o que a derrubaria nao e conclusao de research, e
    opiniao. Na pratica e o campo mais util do relatorio inteiro: e a lista do
    que monitorar no proximo trimestre."""

    @model_validator(mode="after")
    def _check(self) -> "ClaimNode":
        if self.kind in (ClaimKind.FACT, ClaimKind.CALCULATION):
            if not self.token:
                raise ValueError(f"{self.kind} exige `token`: o valor entra por substituicao")
            if not self.result_id:
                raise ValueError(f"{self.kind} exige `result_id`")
            if self.unit_id or self.strength or self.falsified_by:
                raise ValueError(f"{self.kind} nao leva unit_id, strength nem falsifier")
            if self.kind is ClaimKind.FACT and self.supports:
                raise ValueError(
                    "FACT nao leva supports: um fato do motor resolve para um byte, "
                    "e nao se apoia em outra afirmacao"
                )
            # CALCULATION PODE ter supports, e precisa: a contribuicao de um
            # membro numa decomposicao se apoia no total que ela ajuda a
            # explicar, e essa ligacao e o que torna a arvore da decomposicao
            # navegavel no grafo. O que ela nao pode e apontar para um QUOTE -
            # regra conferida em `ClaimGraph`, que e onde o alvo existe.

        elif self.kind is ClaimKind.QUOTE:
            if not self.unit_id:
                raise ValueError("QUOTE exige `unit_id`: citacao sem endereco nao e conferivel")
            if self.token or self.result_id or self.supports or self.strength:
                raise ValueError("QUOTE nao leva token, result_id, supports nem strength")

        elif self.kind is ClaimKind.INFERENCE:
            if not self.supports:
                raise ValueError(
                    "INFERENCE exige `supports`: uma leitura sem nada embaixo e "
                    "indistinguivel de uma frase inventada"
                )
            if self.strength is None:
                raise ValueError("INFERENCE exige `strength` declarada")
            if self.token or self.result_id or self.unit_id or self.falsified_by:
                raise ValueError("INFERENCE nao leva token, result_id, unit_id nem falsifier")

        elif self.kind is ClaimKind.CONCLUSION:
            if not self.supports:
                raise ValueError("CONCLUSION exige `supports`")
            if not self.falsified_by:
                raise ValueError(
                    "CONCLUSION exige `falsified_by`: uma conclusao que nao diz o que "
                    "a derrubaria e opiniao, nao research"
                )
            if self.token or self.result_id or self.unit_id or self.strength:
                raise ValueError("CONCLUSION nao leva token, result_id, unit_id nem strength")

        if len(self.supports) != len(set(self.supports)):
            raise ValueError(f"{self.claim_id[:12]}: suporte repetido")
        if self.claim_id in self.supports:
            raise ValueError(f"{self.claim_id[:12]}: um no nao se sustenta em si mesmo")
        return self

    @property
    def is_grounded(self) -> bool:
        return self.kind in GROUNDED


class ClaimGraph(Frozen):
    """O grafo inteiro, com as propriedades conferidas na construcao.

    Tres invariantes, e nenhuma delas e opcional:

    1. Todo `supports` resolve para um no que existe.
    2. Nao ha ciclo.
    3. Toda INFERENCE e CONCLUSION alcanca, por algum caminho, um no ancorado.

    A terceira e a que importa: uma inferencia que so se apoia em outra
    inferencia que so se apoia nela e recusada, e nao "avisada". Sem isso o
    grafo aceitaria uma torre de leituras sem nenhum numero e nenhuma citacao
    embaixo - que e como um relatorio fica eloquente e vazio.
    """

    graph_version: Literal["v1"] = "v1"
    nodes: tuple[ClaimNode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "ClaimGraph":
        por_id = {}
        for no in self.nodes:
            if no.claim_id in por_id:
                raise ValueError(f"claim_id repetido: {no.claim_id[:12]}")
            por_id[no.claim_id] = no

        for no in self.nodes:
            for suporte in no.supports:
                if suporte not in por_id:
                    raise ValueError(
                        f"{no.claim_id[:12]} aponta para {suporte[:12]}, que nao existe"
                    )
                if no.kind is ClaimKind.CALCULATION and por_id[suporte].kind is ClaimKind.QUOTE:
                    raise ValueError(
                        f"{no.claim_id[:12]}: um CALCULATION nao pode se apoiar num QUOTE. "
                        "Numero lido de documento e citacao, nunca insumo de conta."
                    )

        self._reject_cycles(por_id)

        for no in self.nodes:
            if no.kind in DERIVED and not self._reaches_ground(no, por_id):
                raise ValueError(
                    f"{no.claim_id[:12]} ({no.kind}) nao alcanca nenhum fato, calculo ou "
                    "citacao. Uma torre de leituras sem nada embaixo e como um "
                    "relatorio fica eloquente e vazio."
                )
        return self

    def _reject_cycles(self, por_id: dict[str, ClaimNode]) -> None:
        VISITANDO, PRONTO = 1, 2
        estado: dict[str, int] = {}

        def visita(claim_id: str, caminho: list[str]) -> None:
            if estado.get(claim_id) == PRONTO:
                return
            if estado.get(claim_id) == VISITANDO:
                ciclo = " -> ".join(c[:8] for c in [*caminho, claim_id])
                raise ValueError(f"ciclo no grafo de afirmacoes: {ciclo}")
            estado[claim_id] = VISITANDO
            for suporte in por_id[claim_id].supports:
                visita(suporte, [*caminho, claim_id])
            estado[claim_id] = PRONTO

        for no in self.nodes:
            visita(no.claim_id, [])

    def _reaches_ground(self, no: ClaimNode, por_id: dict[str, ClaimNode]) -> bool:
        vistos: set[str] = set()
        pilha = list(no.supports)
        while pilha:
            atual = pilha.pop()
            if atual in vistos:
                continue
            vistos.add(atual)
            alvo = por_id[atual]
            if alvo.is_grounded:
                return True
            pilha.extend(alvo.supports)
        return False

    def node(self, claim_id: str) -> ClaimNode | None:
        for no in self.nodes:
            if no.claim_id == claim_id:
                return no
        return None

    def of_kind(self, kind: ClaimKind) -> tuple[ClaimNode, ...]:
        return tuple(no for no in self.nodes if no.kind is kind)


# ---------------------------------------------------------------------------
# Achados do critic
# ---------------------------------------------------------------------------


class MechanicalFinding(StrEnum):
    """Taxonomia FECHADA de violacoes conferiveis por maquina.

    Fechada pela mesma razao que `ViolationCode` e: um achado com nome novo a
    cada execucao nao da para contar, nem para bloquear, nem para ignorar
    conscientemente.
    """

    QUOTE_NOT_VERBATIM = "quote_not_verbatim"
    DIGIT_OUTSIDE_QUOTE = "digit_outside_quote"
    EVIDENCE_AFTER_AS_OF = "evidence_after_as_of"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    CLAIM_GRAPH_CYCLE = "claim_graph_cycle"
    CONCLUSION_WITHOUT_FALSIFIER = "conclusion_without_falsifier"
    FIDELITY_UNDISCLOSED = "fidelity_undisclosed"
    SCOPE_MIXED = "scope_mixed"
    ISSUER_NUMBER_LAUNDERED = "issuer_number_laundered"
    UNKNOWN_TOKEN = "unknown_token"
    ORPHAN_RESULT = "orphan_result"


class Severity(StrEnum):
    HARD = "hard"
    """Bloqueia. A resposta nao sai."""

    SOFT = "soft"
    """Acompanha a resposta, visivel. Um relatorio que carrega a ressalva e
    mais util do que um relatorio limpo por reescrita."""


class CriticFinding(Frozen):
    code: MechanicalFinding
    severity: Severity
    message: str = Field(min_length=1)
    claim_id: Sha256 | None = None
    remedy: str | None = None


class CriticReport(Frozen):
    """O que o critic achou. Ele nao corrige, nao reescreve, nao rechama.

    Sem loop writer<->critic, pela mesma razao que nao ha retentativa de
    planejador: "criticar ate passar" e um amostrador que uma hora aprova algo
    errado, e a procedencia passa a sub-reportar o que aconteceu.
    """

    findings: tuple[CriticFinding, ...] = ()
    checked_claims: int = Field(ge=0)

    @property
    def hard(self) -> tuple[CriticFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.HARD)

    @property
    def soft(self) -> tuple[CriticFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.SOFT)

    @property
    def blocks(self) -> bool:
        return bool(self.hard)

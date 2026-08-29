"""Inline XBRL do arquivamento -> linhas tipadas. Onde os elementos do EMISSOR moram.

Por que este modulo existe
--------------------------
O endpoint `companyfacts` da SEC publica SO taxonomias padrao - `us-gaap`,
`dei`, `srt`, `ecd`, `ffd`. Conferido no JSON real da Netflix: os elementos
customizados dela nao estao la, e nenhum filtro de ingestao os traria, porque
nao ha o que filtrar.

Eles existem no proprio 10-K, como inline XBRL. E onde vive a economia da
companhia quando ela nao cabe no vocabulario padrao:

    nflx:CostofServicesAmortizationofStreamingContentAssets
    nflx:AdditionstoStreamingContentAssets
    nflx:ContentAssetsNetNoncurrent

Sem isso, o EBITDA da Netflix e uma metrica que o sistema se RECUSA a calcular
- corretamente, porque a maior despesa nao-caixa dela ficaria de fora.

O que um fato inline carrega
----------------------------
    <ix:nonFraction name="nflx:Cost..." contextRef="c-1" unitRef="usd"
                    scale="3" sign="-" decimals="-3"
                    format="ixt:num-dot-decimal">16,422,166</ix:nonFraction>

O texto e o que o LEITOR ve na pagina; o valor e outra coisa. Tres
transformacoes declaradas o separam, e errar qualquer uma produz um numero
plausivel e errado:

1. `format` diz qual caractere e separador decimal. `num-dot-decimal` e
   `num-comma-decimal` invertem virgula e ponto, e ler um pelo outro erra por
   tres ordens de grandeza.
2. `scale` MULTIPLICA por 10^n. `scale="3"` num numero apresentado em milhares.
3. `sign="-"` NEGA. Uma despesa apresentada como positiva na pagina e negativa
   no fato.

`decimals` NAO e escala - e informacao de precisao, e usa-lo como escala e o
erro classico de quem le iXBRL pela primeira vez.

O que este modulo RECUSA em vez de adivinhar
--------------------------------------------
Formato nao declarado na tabela. Um `format` desconhecido pode ser qualquer
convencao numerica, e chutar "provavelmente e ponto decimal" produziria
numeros errados em silencio para o emissor que usa a outra. O fato e
descartado e CONTADO.

O caso concreto no 10-K da Netflix e `ixt-sec:numwordsen` - numeros escritos
por extenso em ingles. Traduzir "one hundred twenty" para 120 e traducao de
verdade, com todas as ambiguidades que ela tem, e cinco fatos nao valem esse
risco. Ficam de fora, contados.

Fato com dimensao (`xbrldi:explicitMember` no contexto) tambem fica de fora,
pela mesma regra que o `companyfacts` segue: dado por segmento nao pode
colidir com o consolidado na chave logica.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from pat.contracts.silver import XbrlFactLine

__all__ = [
    "EXTRACTOR",
    "EXTRACTOR_VERSION",
    "FORMATS",
    "InlineParseStats",
    "parse_inline_xbrl",
]

EXTRACTOR = "sec.inline_xbrl"
EXTRACTOR_VERSION = "1.0.0"

# Formatos numericos declarados. Tabela, e nao heuristica: um `format`
# desconhecido pode ser qualquer convencao, e adivinhar erraria em silencio
# para o emissor que usa a outra.
#
# O valor e (separador_decimal, caracteres_a_remover).
FORMATS: dict[str, tuple[str, str]] = {
    "num-dot-decimal": (".", ",  "),
    "numdotdecimal": (".", ",  "),
    "num-comma-decimal": (",", ".  "),
    "numcommadecimal": (",", ".  "),
}
"""Sufixo do `format` (sem o prefixo de namespace) -> como ler o numero.

Os apelidos sem hifen existem porque a especificacao mudou de nomenclatura
entre versoes do iXBRL e emissores usam as duas."""

FIXED_ZERO = frozenset({"fixed-zero", "fixedzero"})
"""Formatos cujo valor e ZERO, qualquer que seja o texto apresentado.

O emissor usa isso no travessao que aparece na pagina onde o valor e nulo - 133
vezes no 10-K da Netflix. E declarado e inequivoco: nao ha convencao numerica a
interpretar, entao ele entra em vez de ser recusado junto com o desconhecido."""


@dataclass
class InlineParseStats:
    """O que entrou e o que ficou de fora, contado.

    Descarte silencioso e o modo de falha que este projeto mais recusa: um
    parser que devolve menos fatos do que o documento tem, sem dizer quantos,
    faz o leitor achar que a companhia publicou menos.
    """

    facts_read: int = 0
    skipped_nil: int = 0
    skipped_dimensional: int = 0
    skipped_no_context: int = 0
    skipped_unknown_format: int = 0
    skipped_unparseable: int = 0
    skipped_other_unit: int = 0
    contexts: int = 0

    @property
    def skipped_total(self) -> int:
        return (
            self.skipped_nil
            + self.skipped_dimensional
            + self.skipped_no_context
            + self.skipped_unknown_format
            + self.skipped_unparseable
            + self.skipped_other_unit
        )


@dataclass(frozen=True)
class _Context:
    cik: str
    period_start: date | None
    period_end: date
    dimensional: bool


@dataclass
class _RawFact:
    name: str
    context: str
    unit: str
    scale: int
    negativo: bool
    formato: str
    texto: str = ""
    ordinal: int = 0
    nulo: bool = False
    """`xsi:nil="true"`: o emissor declara que o fato nao tem valor.

    Campo proprio, e nao um sentinela dentro de `texto`: um sentinela textual
    colidiria com um documento que contivesse aquela string, e a colisao
    apareceria como fato descartado sem motivo."""


class _Inline(HTMLParser):
    """Le contextos, unidades e fatos numericos, na ordem do documento.

    `html.parser` minuscula tag e atributo, entao `contextRef` chega como
    `contextref`. Normalizar aqui e melhor que exigir um parser XML estrito:
    arquivamento e XHTML gerado por ferramenta, mas com entidades HTML e
    fechamentos frouxos que um parser XML recusaria inteiro.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contexts: dict[str, _Context] = {}
        self.units: dict[str, str] = {}
        self.facts: list[_RawFact] = []

        self._ctx_id: str | None = None
        self._ctx_cik = ""
        self._ctx_inicio: date | None = None
        self._ctx_fim: date | None = None
        self._ctx_instant: date | None = None
        self._ctx_dimensional = False
        self._campo: str | None = None

        self._unit_id: str | None = None
        self._medida = ""

        self._fato: _RawFact | None = None
        self._profundidade = 0
        self._ordinal = 0

    # -- abertura ------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}

        if tag == "xbrli:context":
            self._ctx_id = a.get("id")
            self._ctx_cik = ""
            self._ctx_inicio = self._ctx_fim = self._ctx_instant = None
            self._ctx_dimensional = False
        elif tag in ("xbrldi:explicitmember", "xbrldi:typedmember"):
            self._ctx_dimensional = True
        elif tag in ("xbrli:startdate", "xbrli:enddate", "xbrli:instant", "xbrli:identifier"):
            self._campo = tag
        elif tag == "xbrli:unit":
            self._unit_id = a.get("id")
            self._medida = ""
        elif tag == "xbrli:measure":
            self._campo = tag
        elif tag == "ix:nonfraction":
            self._fato = _RawFact(
                name=a.get("name", ""),
                context=a.get("contextref", ""),
                unit=a.get("unitref", ""),
                scale=_inteiro(a.get("scale")),
                negativo=a.get("sign", "") == "-",
                formato=_sufixo(a.get("format", "")),
                ordinal=self._ordinal,
            )
            self._ordinal += 1
            self._profundidade = 0
            self._fato.nulo = a.get("xsi:nil", "").lower() == "true"
        elif self._fato is not None:
            self._profundidade += 1

    # -- dados ---------------------------------------------------------------

    def handle_data(self, data: str) -> None:
        if self._fato is not None:
            self._fato.texto += data
            return
        if self._campo is None:
            return
        texto = data.strip()
        if self._campo == "xbrli:startdate":
            self._ctx_inicio = _data(texto)
        elif self._campo == "xbrli:enddate":
            self._ctx_fim = _data(texto)
        elif self._campo == "xbrli:instant":
            self._ctx_instant = _data(texto)
        elif self._campo == "xbrli:identifier":
            self._ctx_cik = texto.zfill(10)
        elif self._campo == "xbrli:measure":
            self._medida += texto

    # -- fechamento ----------------------------------------------------------

    def handle_endtag(self, tag: str) -> None:
        if tag == "ix:nonfraction":
            if self._fato is not None:
                self.facts.append(self._fato)
                self._fato = None
            return
        if self._fato is not None:
            self._profundidade = max(0, self._profundidade - 1)
            return

        if tag == "xbrli:context" and self._ctx_id:
            fim = self._ctx_fim or self._ctx_instant
            if fim is not None:
                self.contexts[self._ctx_id] = _Context(
                    cik=self._ctx_cik,
                    period_start=self._ctx_inicio,
                    period_end=fim,
                    dimensional=self._ctx_dimensional,
                )
            self._ctx_id = None
        elif tag == "xbrli:unit" and self._unit_id:
            # `iso4217:USD` -> `USD`; `xbrli:shares` -> `shares`.
            self.units[self._unit_id] = _sufixo(self._medida.strip())
            self._unit_id = None
        self._campo = None


def parse_inline_xbrl(
    payload: bytes,
    *,
    content_sha256: str,
    retrieval_id: str,
    extraction_run_id: str,
    cik: str,
    entity_name: str,
    accession: str,
    form: str,
    filed: date,
    source_member: str,
    units: tuple[str, ...] = ("USD",),
) -> tuple[list[XbrlFactLine], InlineParseStats]:
    """Bytes do arquivamento -> linhas silver, e o que ficou de fora.

    `units` filtra na leitura, como em `parse_companyfacts`: o documento
    inteiro continua no bronze, e reprocessar com outro filtro nao depende de
    re-baixar.
    """
    parser = _Inline()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()

    stats = InlineParseStats(contexts=len(parser.contexts))
    linhas: list[XbrlFactLine] = []

    for bruto in parser.facts:
        stats.facts_read += 1

        if bruto.nulo:
            stats.skipped_nil += 1
            continue

        contexto = parser.contexts.get(bruto.context)
        if contexto is None:
            stats.skipped_no_context += 1
            continue
        if contexto.dimensional:
            stats.skipped_dimensional += 1
            continue

        unidade = parser.units.get(bruto.unit, "")
        if unidade not in units:
            stats.skipped_other_unit += 1
            continue

        if bruto.formato and bruto.formato not in FORMATS and bruto.formato not in FIXED_ZERO:
            # Formato nao declarado: descartar e CONTAR. Chutar a convencao
            # numerica erraria por ordens de grandeza no emissor que usa a
            # outra, e o erro sairia com cara de numero publicado.
            stats.skipped_unknown_format += 1
            continue

        valor = _valor(bruto)
        if valor is None:
            stats.skipped_unparseable += 1
            continue

        taxonomia, _, elemento = bruto.name.partition(":")
        linhas.append(
            XbrlFactLine(
                silver_id=f"{content_sha256[:16]}-ix-{bruto.ordinal}",
                content_sha256=content_sha256,
                retrieval_id=retrieval_id,
                source_member=source_member,
                source_line_no=bruto.ordinal,
                cik=contexto.cik or cik,
                entity_name=entity_name,
                taxonomy=taxonomia or "unknown",
                element=elemento or bruto.name,
                period_start=contexto.period_start,
                period_end=contexto.period_end,
                value=valor,
                unit=unidade,
                accession=accession,
                form=form,
                filed=filed,
                extractor=EXTRACTOR,
                extractor_version=EXTRACTOR_VERSION,
                extraction_run_id=extraction_run_id,
            )
        )
    return linhas, stats


def _valor(bruto: _RawFact) -> Decimal | None:
    """Texto apresentado -> valor, pelas tres transformacoes declaradas.

    Ordem importa: limpar separadores, aplicar `scale`, aplicar `sign`.
    `decimals` NAO entra - e precisao, nao escala.
    """
    if bruto.formato in FIXED_ZERO:
        # O texto e um travessao; o valor DECLARADO e zero. Ler o
        # travessao como numero devolveria `None`, e um zero publicado
        # apareceria como dado ausente - que e coisa diferente.
        return Decimal(0)
    decimal_sep, remover = FORMATS.get(bruto.formato, (".", ",  "))
    texto = bruto.texto.strip()
    for caractere in remover:
        texto = texto.replace(caractere, "")
    if decimal_sep != ".":
        texto = texto.replace(decimal_sep, ".")
    texto = texto.replace("−", "-").strip()
    if not texto or texto in {"-", "—"}:
        return None
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        return None
    if bruto.scale:
        valor *= Decimal(10) ** bruto.scale
    return -valor if bruto.negativo else valor


def _sufixo(qualificado: str) -> str:
    """`ixt:num-dot-decimal` -> `num-dot-decimal`; `iso4217:USD` -> `USD`."""
    return qualificado.rpartition(":")[2] or qualificado


def _inteiro(bruto: str | None) -> int:
    try:
        return int(str(bruto))
    except (TypeError, ValueError):
        return 0


def _data(texto: str) -> date | None:
    try:
        return date.fromisoformat(texto.strip()[:10])
    except ValueError:
        return None

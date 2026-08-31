"""SEC / EDGAR - fonte primaria oficial das companhias registradas nos EUA.

Cinco datasets, e a razao de serem cinco:

    sec.submissions           historico de arquivamentos de um CIK (recente)
    sec.submissions_page      uma pagina ANTERIOR desse historico
    sec.companyfacts          fatos XBRL consolidados, SEM dimensao
    sec.financial_statements  dataset trimestral da DERA, COM dimensao
    sec.filing_index          o indice de UM arquivamento: que arquivos ele tem
    sec.filing_doc            um documento de um arquivamento

`filing_index` e `filing_doc` sao dois porque a SEC separa indice de conteudo,
e a separacao tem consequencia: o historico de submissions declara so o
documento PRINCIPAL de cada arquivamento, e num 8-K de resultado o principal e
a folha de rosto - a carta aos acionistas e o EX-99.1. Sem um dataset para o
indice do accession, o release trimestral americano fica inalcancavel, e o
sistema ingere 31 capas dizendo que existe uma carta.

`companyfacts` e `financial_statements` nao sao redundantes, e a diferenca
decide o que o sistema consegue responder. O primeiro traz o fato consolidado
de cada elemento, ja normalizado pela SEC, e e a fonte natural das metricas. O
segundo e um despejo trimestral pesado (~130 MB) cuja coluna `segments` carrega
as DIMENSOES - `BusinessSegments=IntelFoundry` - e e a unica fonte estruturada
de dado por segmento que existe. Foi por causa dela que o eixo `SEGMENT` pode
ser destravado aqui e continua bloqueado no regime da CVM.

Duas diferencas em relacao a CVM que mudam o desenho
-----------------------------------------------------
1. **A SEC exige User-Agent identificado.** Requisicao com UA generico volta
   403 - conferido, nao suposto. E politica de acesso justo, e o contato tem
   que ser real. Por isso este provider LE o UA do ambiente e recusa a operar
   sem ele, em vez de embutir o contato de alguem no codigo-fonte.

2. **A SEC nao reescreve arquivamento.** A CVM reescreve `dfp_cia_aberta_2024.zip`
   quando ha reapresentacao - mesma URL, bytes diferentes - e por isso
   `resource_key` la e o ano. Aqui uma reapresentacao e um arquivamento NOVO,
   com accession number proprio (10-K/A). O recurso logico e o proprio
   accession, e ele e imutavel.

   A consequencia pratica: `pat changed` nao vai acusar reapresentacao nesta
   fonte, e isso nao e um bug - e a origem se comportando de outro jeito. O
   bitemporal continua funcionando pelo campo `filed` de cada fato, que e o
   `knowledge_date` do lado americano.
"""

from __future__ import annotations

import os
import re
from typing import ClassVar

from pat.contracts.common import SourceTier
from pat.contracts.documents import ResourceRef
from pat.sources.base import DatasetSpec, PublicSourceProvider, SourceError

_DATA = "https://data.sec.gov"
_WWW = "https://www.sec.gov"
_HOMEPAGE = "https://www.sec.gov/edgar"
_LICENSE = "Dominio publico (obra do governo dos EUA, 17 U.S.C. 105)"

ENV_USER_AGENT = "PAT_SEC_USER_AGENT"

_CIK_RE = re.compile(r"^\d{1,10}$")
_QUARTER_RE = re.compile(r"^(\d{4})q([1-4])$")
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_SUBMISSIONS_PAGE_RE = re.compile(r"^CIK\d{10}-submissions-\d{3}\.json$")


class SECProvider(PublicSourceProvider):
    provider_id: ClassVar[str] = "sec"
    tier: ClassVar[SourceTier] = SourceTier.PRIMARY_OFFICIAL
    version: ClassVar[str] = "1.0.0"

    min_interval_s: ClassVar[float] = 0.5
    """Mais estrito que o da CVM.

    A SEC publica um teto de 10 req/s, e pedir menos do que se pode e a
    postura certa com infraestrutura publica - a mesma razao pela qual o
    provider da CVM espera 2 segundos. Um scraper que raspa no limite do teto
    e o motivo de tetos existirem."""

    DERA_MIN_YEAR: ClassVar[int] = 2009

    def __init__(self, client=None, *, user_agent: str | None = None) -> None:
        super().__init__(client)
        self._user_agent = user_agent

    @property
    def user_agent(self) -> str:  # type: ignore[override]
        """UA identificado, lido do ambiente. Nunca embutido no codigo.

        A SEC pede nome e contato de quem esta buscando. Embutir o e-mail de
        alguem num arquivo versionado significaria enviar o dado pessoal dele
        para um terceiro toda vez que qualquer pessoa rodasse o projeto - e
        commitado, o que e pior. Ler do ambiente mantem a escolha com quem tem
        o direito de faze-la.
        """
        declarado = self._user_agent or os.environ.get(ENV_USER_AGENT)
        if not declarado or not declarado.strip():
            raise SourceError(
                f"a SEC exige um User-Agent identificado e recusa (HTTP 403) quem nao "
                f"o envia. Defina {ENV_USER_AGENT} com nome e contato, por exemplo:\n"
                f'  export {ENV_USER_AGENT}="Seu Nome (seu@email)"\n'
                "Ele nao e embutido no codigo de proposito: o contato e seu, e "
                "commita-lo publicaria um dado pessoal."
            )
        return declarado.strip()

    def datasets(self) -> tuple[DatasetSpec, ...]:
        return (
            DatasetSpec(
                dataset_id="sec.submissions",
                title="Historico de arquivamentos de uma companhia",
                tier=self.tier,
                params=("cik",),
                homepage=_HOMEPAGE,
                license=_LICENSE,
                notes="JSON. Snapshot corrente; buscar periodicamente.",
            ),
            DatasetSpec(
                dataset_id="sec.submissions_page",
                title="Uma pagina anterior do historico de arquivamentos",
                tier=self.tier,
                params=("cik", "file"),
                homepage=_HOMEPAGE,
                license=_LICENSE,
                notes=(
                    "JSON PLANO, sem `filings.recent` e sem `cik`/`name`: so as "
                    "colunas. O nome do arquivo vem declarado em `filings.files` do "
                    "sec.submissions - o provider nao o descobre. Snapshot: a pagina "
                    "mais nova ganha linhas conforme a companhia arquiva."
                ),
            ),
            DatasetSpec(
                dataset_id="sec.companyfacts",
                title="Fatos XBRL consolidados de uma companhia",
                tier=self.tier,
                params=("cik",),
                homepage=f"{_DATA}/api/xbrl/",
                license=_LICENSE,
                notes=(
                    "JSON, SEM dimensao: traz o fato consolidado de cada elemento. "
                    "Cada fato carrega `filed`, que e o knowledge_date. Dado por "
                    "segmento NAO esta aqui - use sec.financial_statements."
                ),
            ),
            DatasetSpec(
                dataset_id="sec.financial_statements",
                title="Financial Statement Data Sets (DERA), por trimestre",
                tier=self.tier,
                params=("quarter",),
                homepage=f"{_WWW}/dera/data/financial-statement-data-sets",
                license=_LICENSE,
                notes=(
                    "ZIP de ~130 MB por trimestre. A coluna `segments` de num.txt "
                    "carrega as dimensoes (BusinessSegments=...), e e a unica fonte "
                    "estruturada de dado por segmento."
                ),
            ),
            DatasetSpec(
                dataset_id="sec.filing_index",
                title="Indice de um arquivamento: os arquivos que ele contem",
                tier=self.tier,
                params=("cik", "accession"),
                homepage=_HOMEPAGE,
                license=_LICENSE,
                notes=(
                    "SGML de cabecalho. Lista cada arquivo do accession com o TIPO "
                    "DECLARADO pelo arquivador (EX-99.1, EX-101.SCH). Indice, nao "
                    "conteudo: os arquivos em si sao buscados por sec.filing_doc. "
                    "Imutavel, como o proprio accession."
                ),
            ),
            DatasetSpec(
                dataset_id="sec.filing_doc",
                title="Um documento de um arquivamento",
                tier=self.tier,
                params=("cik", "accession", "document"),
                homepage=_HOMEPAGE,
                license=_LICENSE,
                notes=(
                    "Os bytes de um documento. A URL e MONTADA a partir dos "
                    "identificadores, e nao descoberta: descobrir exigiria "
                    "interpretar o indice de arquivamentos, que e da camada silver."
                ),
            ),
        )

    def resolve(self, dataset_id: str, **params: str) -> tuple[ResourceRef, ...]:
        spec = self.spec(dataset_id)
        unexpected = set(params) - set(spec.params)
        if unexpected:
            raise SourceError(f"{dataset_id}: parametros desconhecidos {sorted(unexpected)}")
        for required in spec.params:
            if not params.get(required):
                raise SourceError(f"{dataset_id} exige o parametro {required!r}")

        if dataset_id in ("sec.submissions", "sec.companyfacts"):
            cik = self._cik(params["cik"])
            caminho = (
                f"{_DATA}/submissions/CIK{cik}.json"
                if dataset_id == "sec.submissions"
                else f"{_DATA}/api/xbrl/companyfacts/CIK{cik}.json"
            )
            return (
                ResourceRef(
                    provider_id=self.provider_id,
                    dataset_id=dataset_id,
                    resource_key=cik,
                    url=caminho,
                    media_type="application/json",
                    params=(("cik", cik),),
                ),
            )

        if dataset_id == "sec.submissions_page":
            cik = self._cik(params["cik"])
            arquivo = str(params["file"]).strip()
            # O nome vem do indice, mas e VALIDADO aqui: um nome vindo de JSON
            # de terceiro que entrasse cru numa URL seria travessia de caminho.
            if not _SUBMISSIONS_PAGE_RE.match(arquivo):
                raise SourceError(
                    f"nome de pagina invalido: {arquivo!r} "
                    "(esperado CIK0000000000-submissions-000.json)"
                )
            return (
                ResourceRef(
                    provider_id=self.provider_id,
                    dataset_id=dataset_id,
                    # A pagina e o recurso logico. Diferente do accession, ela NAO
                    # e imutavel: a SEC repagina conforme a companhia arquiva, e a
                    # pagina mais nova ganha linhas. `pat changed` acusa, e deve.
                    resource_key=arquivo,
                    url=f"{_DATA}/submissions/{arquivo}",
                    media_type="application/json",
                    params=(("cik", cik), ("file", arquivo)),
                ),
            )

        if dataset_id == "sec.financial_statements":
            trimestre = self._quarter(params["quarter"])
            return (
                ResourceRef(
                    provider_id=self.provider_id,
                    dataset_id=dataset_id,
                    resource_key=trimestre,
                    url=(
                        f"{_WWW}/files/dera/data/financial-statement-data-sets/"
                        f"{trimestre}.zip"
                    ),
                    media_type="application/zip",
                    params=(("quarter", trimestre),),
                ),
            )

        if dataset_id == "sec.filing_index":
            cik = self._cik(params["cik"])
            accession = self._accession(params["accession"])
            return (
                ResourceRef(
                    provider_id=self.provider_id,
                    dataset_id=dataset_id,
                    # Mesma razao do filing_doc: o accession e imutavel, entao o
                    # indice dele tambem e. `pat changed` nunca vai acusar
                    # mudanca aqui, e isso e a origem se comportando.
                    resource_key=accession,
                    url=(
                        f"{_WWW}/Archives/edgar/data/{int(cik)}/"
                        f"{accession.replace('-', '')}/{accession}-index-headers.html"
                    ),
                    media_type="text/html",
                    params=(("cik", cik), ("accession", accession)),
                ),
            )

        # sec.filing_doc
        cik = self._cik(params["cik"])
        accession = self._accession(params["accession"])
        documento = params["document"]
        if "/" in documento or ".." in documento:
            raise SourceError(f"nome de documento invalido: {documento!r}")
        return (
            ResourceRef(
                provider_id=self.provider_id,
                dataset_id=dataset_id,
                # O accession E o recurso logico, e ele e IMUTAVEL: a SEC nao
                # reescreve arquivamento. Uma reapresentacao e um accession
                # novo (10-K/A), e as duas coexistem por natureza.
                resource_key=f"{accession}/{documento}",
                url=(
                    f"{_WWW}/Archives/edgar/data/{int(cik)}/"
                    f"{accession.replace('-', '')}/{documento}"
                ),
                media_type=None,
                params=(("cik", cik), ("accession", accession), ("document", documento)),
            ),
        )

    @staticmethod
    def _cik(raw: str) -> str:
        """CIK normalizado para 10 digitos com zeros a esquerda.

        A SEC aceita as duas formas em lugares diferentes - `CIK0000050863.json`
        no endpoint, `data/50863/` no Archives - e normalizar aqui e o que
        impede o mesmo recurso logico de virar dois `resource_key` diferentes
        conforme quem digitou.
        """
        limpo = str(raw).strip().upper().removeprefix("CIK").lstrip("0") or "0"
        if not _CIK_RE.match(limpo):
            raise SourceError(f"CIK invalido: {raw!r}")
        return limpo.zfill(10)

    @staticmethod
    def _quarter(raw: str) -> str:
        from datetime import UTC, datetime

        texto = str(raw).strip().lower()
        m = _QUARTER_RE.match(texto)
        if not m:
            raise SourceError(f"trimestre invalido: {raw!r} (esperado 'AAAAqT', ex. 2025q1)")
        ano = int(m.group(1))
        if ano < SECProvider.DERA_MIN_YEAR:
            raise SourceError(
                f"a SEC nao publica o dataset antes de {SECProvider.DERA_MIN_YEAR}; "
                f"pedido: {ano}"
            )
        if ano > datetime.now(UTC).year:
            raise SourceError(f"ano futuro: {ano}")
        return texto

    @staticmethod
    def _accession(raw: str) -> str:
        texto = str(raw).strip()
        if not _ACCESSION_RE.match(texto):
            raise SourceError(
                f"accession invalido: {raw!r} (esperado 0000000000-00-000000)"
            )
        return texto

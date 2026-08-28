"""Raiz de composicao dos adapters de documento.

Este e o UNICO arquivo do Opportunity que sabe quais regimes existem, e e o
analogo exato de `pat/semantics/__init__.py::build_engine`: la se escolhe qual
resolver atende qual taxonomia, aqui se escolhe qual provider atende qual
jurisdicao. Uma jurisdicao nova acrescenta uma classe e uma linha em
`PROVIDERS`, e nada acima muda.

Sobre o provider americano
--------------------------
Ele NAO finge. O corpus qualitativo dos EUA nao esta implementado, e o perfil
dele diz exatamente isso, capacidade por capacidade:

    DISCOVER   nao implementado. Descobrir o que uma companhia arquivou exige
               interpretar o indice de arquivamentos, e interpretacao e da
               camada silver - o provider de fonte so monta URL a partir de
               identificadores que alguem ja tem.
    FETCH      existe. Ha dataset para os bytes de um documento, e o caminho
               de ingestao e o mesmo dos outros.
    EXTRACT    nao suportado. O extrator le PDF; os arquivamentos vem em HTML.
               Nao ha fallback, e nao deve haver: citacao vinda de OCR ou de
               um segundo extrator improvisado e texto que ninguem escreveu.

Foi tentador fazer `discover` devolver `()` e seguir a vida - o codigo ficaria
menor e todo teste passaria. Seria o pior desenho possivel: o agente leria a
lista vazia como "esta empresa nao publica nada", que e uma afirmacao sobre o
MUNDO, quando o fato e sobre o SISTEMA. Uma tese de investimento construida
sobre esse silencio diria que a companhia nao comenta as proprias margens.
"""

from __future__ import annotations

from datetime import date

import duckdb

from pat.contracts.common import SourceTier
from pat.contracts.corpus import DateBasis, DocumentKind
from pat.contracts.opportunity import CompanyProfile
from pat.contracts.opportunity.documents import (
    DiscoveredDocument,
    ProviderCapability,
    ProviderProfile,
    ProviderUnavailable,
    ProviderUnavailableReason,
)
from pat.opportunity.documents import StoredDocument, stored_documents

__all__ = [
    "BrazilDocumentProvider",
    "USDocumentProvider",
    "provider_for",
]


class BrazilDocumentProvider:
    """Adapter do corpus brasileiro. Implementado ponta a ponta.

    Descobre pelo catalogo IPE ja no bronze, busca os PDFs e extrai. Toda a
    logica de verdade vive em `pat.corpus`; este arquivo so traduz entre o
    vocabulario do regime e o do Opportunity.
    """

    provider_id = "cvm"
    jurisdiction = "BR"
    scheme = "cod_cvm"

    @property
    def profile(self) -> ProviderProfile:
        return ProviderProfile(
            provider_id=self.provider_id,
            jurisdiction=self.jurisdiction,
            capabilities=(
                ProviderCapability.DISCOVER,
                ProviderCapability.FETCH,
                ProviderCapability.EXTRACT,
            ),
            kinds_supported=tuple(DocumentKind),
        )

    def discover(
        self,
        conn: duckdb.DuckDBPyConnection,
        *,
        company: CompanyProfile,
        published_from: date,
        published_to: date,
        bronze=None,
    ) -> tuple[DiscoveredDocument, ...] | ProviderUnavailable:
        """O que a companhia entregou na janela, pelo catalogo anual.

        O catalogo tem que estar no bronze; nao ha busca por rede aqui. Sem
        ele a resposta e `CATALOG_MISSING`, com o comando no remedio - e nao
        uma lista vazia que se leria como "a empresa nao entregou nada".
        """
        from pat.corpus import catalog_entries

        local = company.local_id(self.scheme)
        if local is None:
            return ProviderUnavailable(
                provider_id=self.provider_id,
                jurisdiction=self.jurisdiction,
                capability=ProviderCapability.DISCOVER,
                reason=ProviderUnavailableReason.NOT_IMPLEMENTED,
                message=(
                    f"{company.entity_id} nao tem identificador {self.scheme!r} no "
                    "catalogo, e o indice deste regime e chaveado por ele"
                ),
                remedy=f"Grave a identidade {self.scheme!r} da companhia com `pat build`.",
            )
        if bronze is None:
            return ProviderUnavailable(
                provider_id=self.provider_id,
                jurisdiction=self.jurisdiction,
                capability=ProviderCapability.DISCOVER,
                reason=ProviderUnavailableReason.CATALOG_MISSING,
                message="a descoberta le o catalogo do bronze, e nenhum bronze foi passado",
                remedy="Passe `bronze=BronzeStore(paths.bronze)` na chamada.",
            )

        encontrados: list[DiscoveredDocument] = []
        for ano in range(published_from.year, published_to.year + 1):
            try:
                entradas = catalog_entries(conn, bronze, year=ano, cod_cvm=int(local))
            except LookupError as erro:
                return ProviderUnavailable(
                    provider_id=self.provider_id,
                    jurisdiction=self.jurisdiction,
                    capability=ProviderCapability.DISCOVER,
                    reason=ProviderUnavailableReason.CATALOG_MISSING,
                    message=str(erro),
                    remedy=f"Rode `pat fetch cvm.ipe --year {ano}` e tente de novo.",
                )
            for entrada in entradas:
                if not (published_from <= entrada.delivered_at <= published_to):
                    continue
                encontrados.append(
                    DiscoveredDocument(
                        provider_id=self.provider_id,
                        jurisdiction=self.jurisdiction,
                        external_id=entrada.protocolo,
                        title=entrada.title,
                        kind=entrada.kind,
                        published_at=entrada.delivered_at,
                        # `delivered_at` vem declarada pelo emissor no
                        # protocolo de entrega - nao foi lida do documento nem
                        # chutada. A base viaja junto pela mesma razao que a
                        # fidelidade viaja com um numero.
                        published_at_basis=DateBasis.FILING_METADATA,
                        source_tier=SourceTier.PRIMARY_OFFICIAL,
                        source_url=entrada.url,
                        reference_date=entrada.reference_date,
                        origin_category=entrada.categoria,
                    )
                )
        return tuple(sorted(encontrados, key=lambda d: (d.published_at, d.external_id)))

    def fetch(
        self,
        conn: duckdb.DuckDBPyConnection,
        *,
        company: CompanyProfile,
        documents: tuple[DiscoveredDocument, ...],
        limit: int | None = None,
        bronze=None,
        catalog=None,
        registry=None,
        run=None,
        as_of: date | None = None,
    ) -> tuple[StoredDocument, ...] | ProviderUnavailable:
        """Traz, extrai e indexa. Delega inteiro a `pat.corpus.sync_documents`.

        O `SyncOutcome` registra as falhas de extracao, e elas continuam no
        banco: o inventario devolvido as mostra marcadas, em vez de deixar o
        documento sumir da lista.
        """
        from pat.corpus import sync_documents

        if any(x is None for x in (bronze, catalog, registry, run)):
            return ProviderUnavailable(
                provider_id=self.provider_id,
                jurisdiction=self.jurisdiction,
                capability=ProviderCapability.FETCH,
                reason=ProviderUnavailableReason.NEEDS_NETWORK,
                message="a busca exige bronze, catalogo, registro de fontes e uma corrida",
                remedy="Chame por `pat docs --sync`, que monta os quatro.",
            )

        local = company.local_id(self.scheme)
        entradas = _catalog_entries_for(
            conn, bronze, documents=documents, cod_cvm=int(local) if local else None
        )
        sync_documents(
            conn,
            entity_id=company.entity_id,
            entries=entradas,
            registry=registry,
            bronze=bronze,
            catalog=catalog,
            run=run,
            limit=limit,
        )
        return stored_documents(
            conn,
            entity_id=company.entity_id,
            as_of=as_of or max((d.published_at for d in documents), default=date.today()),
        )


class USDocumentProvider:
    """Adapter americano. Completo desde a N2.

    Ate ali este adapter era parcial e dizia exatamente onde parava: nao havia
    descoberta - transformar o indice de arquivamentos em lista de documentos e
    trabalho de parsing, que pertence a silver - nem extracao, porque o
    extrator lia PDF e os arquivamentos vem em HTML.

    As duas lacunas foram fechadas: `parse/sec_submissions.py` le o indice com
    tabela declarada de forma -> `DocumentKind`, e `corpus/extract_html.py` le
    XHTML com `extraction_version` propria. O adapter passa a delegar ao MESMO
    `sync_documents` do lado brasileiro.

    O que ele NAO faz, e continua dizendo: o indice recente da SEC nao cobre o
    historico inteiro, e os arquivos anteriores nao sao lidos. Uma companhia
    antiga tem anos fora do alcance, e isso aparece na saida do `--sync` em vez
    de virar "nao ha 10-K de 2015".
    """

    provider_id = "us-filings"
    jurisdiction = "US"

    scheme = "cik"

    @property
    def profile(self) -> ProviderProfile:
        return ProviderProfile(
            provider_id=self.provider_id,
            jurisdiction=self.jurisdiction,
            capabilities=(
                ProviderCapability.DISCOVER,
                ProviderCapability.FETCH,
                ProviderCapability.EXTRACT,
            ),
            missing=(),
            # Declarado aqui, e nao importado do parser: a regra de camada
            # proibe `pat.parse` no Opportunity, e a lista existe para o
            # agente saber o que procurar - nao para reproduzir a tabela.
            kinds_supported=(
                DocumentKind.FILING,
                DocumentKind.MATERIAL_FACT,
                DocumentKind.GOVERNANCE,
                DocumentKind.SHAREHOLDER_NOTICE,
                DocumentKind.PERFORMANCE_REPORT,
            ),
        )

    def discover(
        self,
        conn: duckdb.DuckDBPyConnection,
        *,
        company: CompanyProfile,
        published_from: date,
        published_to: date,
        bronze=None,
        forms: tuple[str, ...] = ("10-K", "10-Q"),
        **_: object,
    ) -> tuple[DiscoveredDocument, ...] | ProviderUnavailable:
        """Le o indice de arquivamentos do bronze e devolve o que ha na janela.

        Ate a N2 este metodo SEMPRE recusava, e a recusa estava certa enquanto
        nada consumia o indice. Agora ela seria a mentira oposta - dizer que o
        sistema nao sabe fazer o que passou a fazer.

        O que continua recusando com motivo: indice ausente do bronze, e
        companhia sem identificador da jurisdicao. Lista vazia so sai quando o
        indice foi lido e de fato nao ha arquivamento na janela.
        """
        from pat.corpus import sec_candidates

        local = company.local_id(self.scheme)
        if local is None:
            return ProviderUnavailable(
                provider_id=self.provider_id,
                jurisdiction=self.jurisdiction,
                capability=ProviderCapability.DISCOVER,
                reason=ProviderUnavailableReason.NOT_IMPLEMENTED,
                message=(
                    f"{company.entity_id} nao tem identificador {self.scheme!r} no "
                    "catalogo, e o indice deste regime e chaveado por ele"
                ),
                remedy=f"Grave a identidade {self.scheme!r} da companhia com `pat build`.",
            )
        if bronze is None:
            return ProviderUnavailable(
                provider_id=self.provider_id,
                jurisdiction=self.jurisdiction,
                capability=ProviderCapability.DISCOVER,
                reason=ProviderUnavailableReason.CATALOG_MISSING,
                message="a descoberta le o indice do bronze, e nenhum bronze foi passado",
                remedy="Passe `bronze=BronzeStore(paths.bronze)` na chamada.",
            )

        try:
            _, indice = sec_candidates(
                conn, bronze, cik=str(local), forms=forms, since=published_from
            )
        except LookupError as erro:
            return ProviderUnavailable(
                provider_id=self.provider_id,
                jurisdiction=self.jurisdiction,
                capability=ProviderCapability.DISCOVER,
                reason=ProviderUnavailableReason.CATALOG_MISSING,
                message=str(erro),
                remedy=f"Rode `pat fetch sec.submissions --cik {int(local)}` e tente de novo.",
            )

        encontrados = [
            DiscoveredDocument(
                provider_id=self.provider_id,
                jurisdiction=self.jurisdiction,
                external_id=candidato.resource_key,
                title=candidato.title,
                kind=candidato.kind,
                published_at=candidato.published_at,
                published_at_basis=candidato.published_at_basis,
                source_tier=SourceTier.PRIMARY_OFFICIAL,
                source_url=candidato.source_url,
                reference_date=candidato.reference_date,
                origin_category=candidato.origin_category,
            )
            for candidato in indice.candidates
            if published_from <= candidato.published_at <= published_to
        ]
        return tuple(sorted(encontrados, key=lambda d: (d.published_at, d.external_id)))

    def fetch(
        self,
        conn: duckdb.DuckDBPyConnection,
        *,
        company: CompanyProfile,
        documents: tuple[DiscoveredDocument, ...],
        limit: int | None = None,
        bronze=None,
        catalog=None,
        registry=None,
        run=None,
        as_of: date | None = None,
    ) -> tuple[StoredDocument, ...] | ProviderUnavailable:
        """Traz, extrai e indexa. Delega ao MESMO `sync_documents` do Brasil.

        Se este metodo tivesse um pipeline proprio, existiriam dois caminhos de
        corpus, e o terceiro regime pediria um terceiro.
        """
        from pat.corpus import sec_candidates, sync_documents

        if any(x is None for x in (bronze, catalog, registry, run)):
            return ProviderUnavailable(
                provider_id=self.provider_id,
                jurisdiction=self.jurisdiction,
                capability=ProviderCapability.FETCH,
                reason=ProviderUnavailableReason.NEEDS_NETWORK,
                message="a busca exige bronze, catalogo, registro de fontes e uma corrida",
                remedy="Chame por `pat docs --sync`, que monta os quatro.",
            )

        local = company.local_id(self.scheme)
        procurados = {d.external_id for d in documents}
        try:
            _, indice = sec_candidates(conn, bronze, cik=str(local))
        except LookupError as erro:
            return ProviderUnavailable(
                provider_id=self.provider_id,
                jurisdiction=self.jurisdiction,
                capability=ProviderCapability.FETCH,
                reason=ProviderUnavailableReason.CATALOG_MISSING,
                message=str(erro),
                remedy=f"Rode `pat fetch sec.submissions --cik {int(local)}`.",
            )

        # Reusa o candidato original em vez de reconstrui-lo a partir do
        # `DiscoveredDocument`: a URL, a forma e a data sao da fonte, e uma
        # segunda copia pode divergir da primeira.
        entradas = [c for c in indice.candidates if c.resource_key in procurados]
        sync_documents(
            conn,
            entity_id=company.entity_id,
            entries=entradas,
            registry=registry,
            bronze=bronze,
            catalog=catalog,
            run=run,
            limit=limit,
        )
        return stored_documents(conn, company.entity_id, as_of=as_of)


def _catalog_entries_for(conn, bronze, *, documents, cod_cvm):
    """Descobertos -> entradas de catalogo, pelo protocolo.

    A busca reusa a entrada original em vez de reconstrui-la: a URL, a
    categoria e a data de entrega sao da fonte, e remonta-las a partir do
    `DiscoveredDocument` seria uma segunda copia que pode divergir.
    """
    from pat.corpus import catalog_entries

    procurados = {d.external_id for d in documents}
    anos = {d.published_at.year for d in documents}
    encontradas = []
    for ano in sorted(anos):
        for entrada in catalog_entries(conn, bronze, year=ano, cod_cvm=cod_cvm):
            if entrada.protocolo in procurados:
                encontradas.append(entrada)
    return encontradas


PROVIDERS: dict[str, type] = {
    BrazilDocumentProvider.jurisdiction: BrazilDocumentProvider,
    USDocumentProvider.jurisdiction: USDocumentProvider,
}


def provider_for(jurisdiction: str):
    """Jurisdicao -> provider. `KeyError` nomeado quando nao ha um.

    Nao existe provider default. Um default aqui faria a terceira jurisdicao
    ser atendida pelo adapter da primeira, e o erro sairia como documento
    brasileiro no inventario de uma empresa que nao e brasileira.
    """
    classe = PROVIDERS.get(jurisdiction.upper())
    if classe is None:
        raise KeyError(
            f"nenhum provider de documento para a jurisdicao {jurisdiction!r}. "
            f"Conhecidas: {sorted(PROVIDERS)}. Escreva o adapter e registre-o em "
            "PROVIDERS; nao existe default, de proposito."
        )
    return classe()

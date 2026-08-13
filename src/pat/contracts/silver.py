"""Contrato da camada silver: uma linha de CSV da CVM, tipada.

Fiel a fonte por desenho. Cada campo corresponde a uma coluna do arquivo
original, com tipagem aplicada e nada mais. Escala monetaria (`escala_moeda`)
e preservada crua: aplica-la e interpretacao, e interpretacao pertence ao
gold. Assim toda linha silver permanece conferivel contra o CSV.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from pat.contracts.common import Frozen, Sha256


class AccountLine(Frozen):
    silver_id: str = Field(
        description="Deterministico: sha256(content_sha256|membro|linha|versao_extrator). "
        "Reprocessar o mesmo documento gera exatamente os mesmos ids."
    )

    # -- linhagem ate o byte de origem (I2) --------------------------------
    content_sha256: Sha256
    retrieval_id: str
    source_member: str = Field(description="Arquivo dentro do ZIP")
    source_line_no: int = Field(ge=2, description="Linha no CSV; 1 e o cabecalho")

    # -- documento ----------------------------------------------------------
    cnpj: str
    cod_cvm: int
    denom_cia: str
    dt_refer: date = Field(description="Data de referencia do documento")
    versao: int = Field(ge=1, description=">1 indica republicacao do documento")
    doc_id: str = Field(description="ID_DOC no protocolo da CVM")
    dt_receb: date = Field(
        description="Data em que a CVM recebeu o documento. Vira o knowledge_date "
        "do fato: e a fonte que informa quando o numero se tornou publico."
    )
    link_doc: str | None = None

    # -- classificacao ------------------------------------------------------
    statement: str = Field(description="DRE, BPA, BPP, DFC_MI, DFC_MD, DVA, DRA, DMPL")
    consolidated: bool = Field(description="_con vs _ind no nome do arquivo")
    grupo_dfp: str | None = None

    # -- periodo ------------------------------------------------------------
    ordem_exerc: str = Field(description="ULTIMO ou PENULTIMO (sem acento)")
    dt_ini_exerc: date | None = Field(default=None, description="Nulo em contas patrimoniais")
    dt_fim_exerc: date

    # -- conta --------------------------------------------------------------
    coluna_df: str = Field(
        default="",
        description="Dimensao extra da DMPL (componente do PL: Capital Social, "
        "Reservas de Lucro, ...). Vazio nas demais demonstracoes. Faz parte da "
        "chave logica: sem ela, componentes distintos do PL colapsam na mesma "
        "chave e viram falsas reapresentacoes.",
    )
    cd_conta: str
    ds_conta: str
    vl_conta: Decimal = Field(description="Valor cru, na escala declarada em escala_moeda")
    st_conta_fixa: bool | None = None

    # -- unidade, crua ------------------------------------------------------
    moeda: str
    escala_moeda: str

    # -- extracao -----------------------------------------------------------
    extractor: str
    extractor_version: str
    extraction_run_id: str

    @property
    def locator(self) -> str:
        """Endereco desta linha dentro do documento, para a linhagem."""
        suffix = f":coluna={self.coluna_df}" if self.coluna_df else ""
        return f"{self.source_member}#L{self.source_line_no}:cd_conta={self.cd_conta}{suffix}"

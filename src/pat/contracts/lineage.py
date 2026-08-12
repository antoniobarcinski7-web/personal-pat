"""Linhagem: o caminho de volta de qualquer numero ate o byte de origem.

Invariante I2 do projeto. Todo `Fact` carrega um `Lineage`, e todo `Lineage`
resolve para um documento bronze que ainda existe em disco, com o hash que
foi registrado.
"""

from __future__ import annotations

from pydantic import Field

from pat.contracts.common import AwareDatetime, Frozen, RunStatus, Sha256


class Lineage(Frozen):
    content_sha256: Sha256 = Field(description="Documento bronze de origem")
    retrieval_id: str = Field(description="Qual observacao daquele documento")
    locator: str = Field(
        description="Endereco dentro do documento. Formato depende do extrator: "
        "codigo de conta CVM, XPath, 'arquivo.csv#linha=123', pagina de PDF."
    )
    extractor: str
    extractor_version: str = Field(
        description="Reprocessar com extrator novo gera fatos novos, nao "
        "sobrescreve os antigos: o passado continua reproduzivel."
    )
    extraction_run_id: str


class Run(Frozen):
    """Manifesto de uma execucao. Base da reprodutibilidade (I3).

    Registra o que foi executado e com qual codigo, para que uma saida possa
    ser reconstruida ou, no minimo, explicada.
    """

    run_id: str
    command: str
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    status: RunStatus = RunStatus.RUNNING

    pat_version: str
    python_version: str
    git_sha: str | None = None
    notes: str | None = None

"""Bronze: armazenamento imutavel enderecado por conteudo.

Propriedades que esta camada garante, e das quais tudo acima depende:

1. **Imutavel.** Um blob, uma vez escrito, nunca e alterado nem removido por
   este codigo. O arquivo e marcado somente-leitura no sistema de arquivos.
2. **Enderecado por conteudo.** O nome do arquivo e o SHA-256 dos bytes.
   Conteudo identico e guardado uma unica vez; conteudo diferente jamais
   colide.
3. **Auto-descritivo.** Cada blob tem um sidecar JSON com sua procedencia.
   O catalogo DuckDB pode ser reconstruido a partir do bronze; o bronze nao
   pode ser reconstruido a partir do catalogo.
4. **Verificavel.** `verify()` recalcula os hashes e detecta corrupcao ou
   adulteracao silenciosa.

E a base de I2 (linhagem) e I3 (reprodutibilidade): reprocessar dados
historicos nunca depende de um servidor externo devolver hoje o que devolveu
ontem.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pat.contracts.documents import RawDocument

BLOB_DIR = "blobs"
META_DIR = "meta"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class PutResult:
    document: RawDocument
    is_new: bool
    """False quando o conteudo ja existia. Buscar o mesmo recurso duas vezes
    e barato e nao duplica armazenamento - o que torna re-busca periodica
    (necessaria para detectar reapresentacao) uma operacao sem custo."""


class BronzeStoreError(RuntimeError):
    pass


class BronzeStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _rel_path(self, sha: str) -> Path:
        # Fan-out em dois niveis: evita diretorios com centenas de milhares
        # de entradas, que degradam listagem em qualquer sistema de arquivos.
        return Path(BLOB_DIR) / sha[:2] / sha[2:4] / sha

    def _blob_path(self, sha: str) -> Path:
        return self.root / self._rel_path(sha)

    def _meta_path(self, sha: str) -> Path:
        return self.root / META_DIR / sha[:2] / sha[2:4] / f"{sha}.json"

    def exists(self, sha: str) -> bool:
        return self._blob_path(sha).exists()

    def put(
        self,
        content: bytes,
        *,
        run_id: str,
        media_type: str | None = None,
        provenance: dict[str, object] | None = None,
    ) -> PutResult:
        """Grava bytes no bronze. Idempotente por conteudo."""
        sha = sha256_bytes(content)
        blob_path = self._blob_path(sha)

        if blob_path.exists():
            existing_size = blob_path.stat().st_size
            if existing_size != len(content):
                raise BronzeStoreError(
                    f"colisao impossivel para {sha}: em disco {existing_size} bytes, "
                    f"recebido {len(content)}. Bronze pode estar corrompido."
                )
            meta = self._read_meta(sha)
            return PutResult(document=RawDocument.model_validate(meta["document"]), is_new=False)

        first_seen_at = datetime.now(UTC)
        document = RawDocument(
            content_sha256=sha,
            size_bytes=len(content),
            media_type=media_type,
            storage_path=str(self._rel_path(sha)),
            first_seen_at=first_seen_at,
            first_seen_run_id=run_id,
        )

        self._write_immutable(blob_path, content)
        self._write_meta(sha, document, provenance or {})
        return PutResult(document=document, is_new=True)

    def read(self, sha: str) -> bytes:
        path = self._blob_path(sha)
        if not path.exists():
            raise BronzeStoreError(f"documento ausente no bronze: {sha}")
        content = path.read_bytes()
        actual = sha256_bytes(content)
        if actual != sha:
            raise BronzeStoreError(f"conteudo corrompido: esperado {sha}, calculado {actual}")
        return content

    def path_of(self, sha: str) -> Path:
        return self._blob_path(sha)

    def verify(self) -> tuple[list[str], list[str]]:
        """Recalcula o hash de cada blob.

        Retorna (ok, corrompidos). Deve ser rodado antes de qualquer
        reprocessamento historico que va sustentar uma decisao.
        """
        ok: list[str] = []
        bad: list[str] = []
        blob_root = self.root / BLOB_DIR
        if not blob_root.exists():
            return ok, bad
        for path in sorted(blob_root.rglob("*")):
            if not path.is_file():
                continue
            expected = path.name
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            (ok if actual == expected else bad).append(expected)
        return ok, bad

    def _write_immutable(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Escrita atomica: um blob parcial nunca fica visivel sob seu nome
        # final, mesmo se o processo morrer no meio.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _write_meta(self, sha: str, document: RawDocument, provenance: dict[str, object]) -> None:
        path = self._meta_path(sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"document": document.model_dump(mode="json"), "provenance": provenance}
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _read_meta(self, sha: str) -> dict:
        path = self._meta_path(sha)
        if not path.exists():
            raise BronzeStoreError(f"sidecar ausente para {sha}: bronze inconsistente")
        return json.loads(path.read_text(encoding="utf-8"))

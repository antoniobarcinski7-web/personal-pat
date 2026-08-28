"""O diario do workspace: uma linha JSON por evento, append-only.

Por que arquivo e nao tabela
----------------------------
O warehouse guarda fato: bitemporal, com procedencia, reconstruivel a partir
do bronze. O diario guarda raciocinio, que nao e nada disso - nao tem
`period_end`, nao vem de lugar nenhum e nao pode ser reconstruido. Se o
warehouse for apagado, `pat build` o refaz; se o diario for apagado, a
investigacao acabou. Sao naturezas diferentes, e misturar as duas no mesmo
arquivo faria o `verify()` de uma valer para a outra sem valer de verdade.

O que este modulo garante
-------------------------
1. `seq` denso a partir de 1. Buraco na sequencia e diario truncado, e isso
   para a leitura em vez de dobrar um estado incompleto que se pareceria com
   um estado legitimo mais antigo.
2. Escrita append-only. Nunca reescreve o arquivo inteiro: um erro no meio de
   um rewrite perderia todos os eventos anteriores.
3. Linha completa ou linha nenhuma. A escrita monta a string inteira e faz um
   `write` so, seguido de `flush`, para que uma interrupcao nao deixe meia
   linha JSON no meio do arquivo.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pat.contracts.opportunity import Actor, EventBody, JournalEvent

__all__ = ["Journal", "JournalCorrupt"]


class JournalCorrupt(RuntimeError):
    """Diario ilegivel: JSON quebrado, contrato violado ou `seq` fora de ordem.

    Erro proprio, e nao `ValueError`, porque quem chama precisa distinguir
    "este workspace nao existe" de "este workspace existe e esta danificado".
    As duas situacoes pedem acoes opostas de quem esta na frente do terminal.
    """


class Journal:
    """Diario de um workspace. Um arquivo, uma sequencia.

    A instancia guarda `_seq` em memoria depois de ler o arquivo uma vez.
    Dois processos escrevendo no mesmo diario ao mesmo tempo produziriam `seq`
    repetido - por isso `append` reconfere o fim do arquivo antes de gravar.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    # -- leitura ------------------------------------------------------------

    def read(self) -> tuple[JournalEvent, ...]:
        """Todos os eventos, na ordem gravada, validados."""
        return tuple(self.iter_events())

    def iter_events(self) -> Iterator[JournalEvent]:
        if not self._path.exists():
            return
        esperado = 1
        with self._path.open("r", encoding="utf-8") as handle:
            for numero, linha in enumerate(handle, start=1):
                if not linha.strip():
                    continue
                evento = self._parse(linha, numero)
                if evento.seq != esperado:
                    raise JournalCorrupt(
                        f"{self._path}: linha {numero} tem seq={evento.seq}, "
                        f"esperado {esperado}. Diario truncado ou reordenado."
                    )
                esperado += 1
                yield evento

    def _parse(self, linha: str, numero: int) -> JournalEvent:
        try:
            return JournalEvent.model_validate_json(linha)
        except Exception as erro:  # noqa: BLE001 - reetiqueta, nao esconde
            raise JournalCorrupt(f"{self._path}: linha {numero} ilegivel: {erro}") from erro

    def last_seq(self) -> int:
        """Maior `seq` gravado, 0 num diario vazio.

        Le o arquivo inteiro de proposito: ler so a ultima linha aceitaria um
        arquivo com buraco no meio, e um buraco no meio e exatamente o que
        `iter_events` existe para recusar.
        """
        ultimo = 0
        for evento in self.iter_events():
            ultimo = evento.seq
        return ultimo

    # -- escrita ------------------------------------------------------------

    def append(
        self,
        body: EventBody,
        *,
        actor: Actor,
        at: datetime | None = None,
    ) -> JournalEvent:
        """Grava um evento e devolve como ele ficou.

        `at` e parametro, e nao sempre `now()`, para que um teste possa fixar
        o relogio. Em producao ninguem passa.
        """
        evento = JournalEvent(
            seq=self.last_seq() + 1,
            at=at or datetime.now(UTC),
            actor=actor,
            body=body,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        linha = evento.model_dump_json() + "\n"
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(linha)
            handle.flush()
        return evento

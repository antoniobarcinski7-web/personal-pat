"""Serializacao canonica e identidades enderecadas por conteudo.

Uma implementacao so, usada por pergunta, plano, resultado e snapshot. Duas
implementacoes divergiriam em silencio no dia em que uma delas tratasse um
`None` diferente da outra, e o sintoma seria um `plan_id` que muda sem que o
plano tenha mudado.

A especificacao e precisa o suficiente para que outra implementacao produza os
mesmos bytes:

1. UTF-8, sem BOM.
2. Chaves de objeto ordenadas por code point.
3. Separadores exatamente `,` e `:` - nenhum espaco.
4. `ensure_ascii=False`: caractere nao-ASCII sai literal.
5. Chave com valor `None` e OMITIDA. Tupla e string vazias sao MANTIDAS.
6. Tupla vira array; a ordem e preservada, porque ordem e significado em
   `steps`, `inputs` e `outputs`.
7. `date` -> "AAAA-MM-DD". `datetime` -> RFC 3339 em UTC, sufixo `Z`, com
   microssegundos explicitos (largura fixa).
8. Enum -> o `.value`.
9. `Decimal` -> STRING em notacao plana. Nunca float: `json` transformaria
   `0.1` num binario que nao e `0.1`, e a identidade passaria a depender da
   plataforma.
10. `MetricRef` -> "nome@versao", nao objeto.

`hash()` do Python nao aparece em lugar nenhum: ele e aleatorizado por
processo (PYTHONHASHSEED) e portanto inutil como identidade persistente.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

from pat.contracts.research import (
    CapabilitySnapshot,
    ComputationResult,
    ResearchPlan,
    ResearchQuestion,
)
from pat.contracts.semantics import MetricRef

_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Espaco em branco nao e conteudo.

    Sem isto, a mesma pergunta com um `\\n` no fim viraria outra pergunta, e o
    cache e a identidade se fragmentariam por acidente de digitacao.
    """
    return _WHITESPACE.sub(" ", text).strip()


def decimal_str(value: Decimal) -> str:
    """Notacao plana, sem zeros a direita, sem expoente."""
    if not value.is_finite():
        raise ValueError(f"Decimal nao finito nao tem forma canonica: {value}")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        text = "0"
    return text


def _plain(obj: Any) -> Any:
    """Objeto -> estrutura JSON-avel, com as regras acima."""
    if isinstance(obj, MetricRef):
        return str(obj)
    if isinstance(obj, BaseModel):
        out: dict[str, Any] = {}
        for name in obj.__class__.model_fields:
            value = getattr(obj, name)
            if value is None:
                continue
            out[name] = _plain(value)
        return out
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, Decimal):
        return decimal_str(obj)
    if isinstance(obj, datetime):
        # Microssegundos sempre, largura fixa. Truncar no segundo faria duas
        # corridas do mesmo plano no mesmo segundo colidirem em `manifest_id`,
        # e a segunda sumiria do `research_run` sem erro: uma execucao que
        # aconteceu e nao deixou rastro. Nenhum outro hash consome datetime -
        # `question_id` exclui `asked_at`, `plan_id` exclui a procedencia do
        # modelo, o snapshot exclui `built_at` e `result_id` so tem `date`.
        return obj.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, float):
        raise TypeError(
            "float nao entra em identidade canonica - use Decimal. "
            "Binario de ponto flutuante nao reproduz entre plataformas."
        )
    if isinstance(obj, (tuple, list)):
        return [_plain(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if obj is None:
        return None
    raise TypeError(f"sem forma canonica definida para {type(obj).__name__}")


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        _plain(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Identidades
# ---------------------------------------------------------------------------


def question_id(question: ResearchQuestion) -> str:
    """`asked_at` fica de fora: senao a mesma pergunta nunca seria a mesma."""
    return sha256_of(
        {
            "question_version": question.question_version,
            "text": normalize_text(question.text),
            "as_of": question.as_of,
            "pinned_entities": question.pinned_entities,
            "pinned_periods": question.pinned_periods,
            "pinned_scope": question.pinned_scope,
            "requested_output": question.requested_output,
            "constraints": question.constraints,
        }
    )


def plan_id(plan: ResearchPlan) -> str:
    """Nada de `PlanProvenance` entra aqui.

    E este recorte que faz metadado de LLM - modelo, temperatura, hash de
    prompt, se veio do cache - nao conseguir mexer na identidade do plano.
    Dois modelos que produzam o mesmo plano produzem o mesmo hash.
    """
    return sha256_of(
        {
            "plan_version": plan.plan_version,
            "question_id": plan.question_id,
            "objective": plan.objective,
            "as_of": plan.as_of,
            "scope": plan.scope,
            "steps": plan.steps,
            "outputs": plan.outputs,
            "assumptions": plan.assumptions,
            "unresolved": plan.unresolved,
        }
    )


def capability_sha256(snapshot: CapabilitySnapshot) -> str:
    """Tudo menos `built_at` (D-4 do desenho: o retrato, nao o relogio)."""
    payload = {
        name: getattr(snapshot, name)
        for name in CapabilitySnapshot.model_fields
        if name != "built_at"
    }
    return sha256_of(payload)


def result_id(result_body: dict[str, Any]) -> str:
    """Enderecado por conteudo (D-4).

    Numero diferente => `result_id` diferente. Duas execucoes cujas citacoes
    divergem sao visivelmente execucoes diferentes, e isso e informacao.
    """
    return sha256_of(result_body)


def result_id_of(result: ComputationResult) -> str:
    """Recalcula a identidade de um resultado ja montado. Usado em teste."""
    return result_id(
        {
            "step_id": result.step_id,
            "kind": result.kind,
            "metric_result": result.metric_result,
            "derived": result.derived,
        }
    )

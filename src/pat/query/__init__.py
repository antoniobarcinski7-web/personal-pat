"""Camada de consulta. Toda leitura de gold exige uma data de conhecimento."""

from pat.query.asof import AsOf, DocumentProvenance, FactView, Restatement

__all__ = ["AsOf", "DocumentProvenance", "FactView", "Restatement"]

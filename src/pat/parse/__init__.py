"""Parsers bronze -> silver. Leem bytes do bronze, nunca a rede."""

from pat.parse.cvm_dfp import (
    EXTRACTOR,
    EXTRACTOR_VERSION,
    ParseError,
    ParseStats,
    parse_dfp_zip,
    read_document_index,
)

__all__ = [
    "EXTRACTOR",
    "EXTRACTOR_VERSION",
    "ParseError",
    "ParseStats",
    "parse_dfp_zip",
    "read_document_index",
]

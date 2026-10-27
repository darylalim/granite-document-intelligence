"""Granite Document Intelligence: PDFs to Markdown, JSON, tables and chart data, locally.

The library is the product; the `gdi` CLI wraps it (plan §3.7). Importing the
package loads no model and imports neither Docling nor MLX.
"""

from granite_document_intelligence.api import (
    BatchItem,
    Chart,
    Chunk,
    ConvertOptions,
    ConvertResult,
    KvpResult,
    PageStats,
    Picture,
    RunInfo,
    Table,
    convert,
    convert_many,
)
from granite_document_intelligence.models import ModelsNotReadyError
from granite_document_intelligence.runlock import RunLockedError

__all__ = [
    "BatchItem",
    "Chart",
    "Chunk",
    "ConvertOptions",
    "ConvertResult",
    "KvpResult",
    "ModelsNotReadyError",
    "PageStats",
    "Picture",
    "RunInfo",
    "RunLockedError",
    "Table",
    "convert",
    "convert_many",
]

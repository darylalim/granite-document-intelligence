"""Granite Document Intelligence: PDFs to Markdown, JSON, tables and chart data, locally.

The library is the product; the `gdi` CLI wraps it (plan §3.7). Importing the
package loads no model and imports neither Docling nor MLX.
"""

from granite_document_intelligence.api import (
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
)
from granite_document_intelligence.models import ModelsNotReadyError

__all__ = [
    "Chart",
    "Chunk",
    "ConvertOptions",
    "ConvertResult",
    "KvpResult",
    "ModelsNotReadyError",
    "PageStats",
    "Picture",
    "RunInfo",
    "Table",
    "convert",
]

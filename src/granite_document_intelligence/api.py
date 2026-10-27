"""The public surface (plan §3.7): `convert` returns data, `save` is the only writer.

The CLI and any later UI call these and nothing below them, so they cannot
diverge. Heavy imports (Docling, torch, mlx) happen inside `convert`, never at
import time (§6), so importing the package stays cheap.

PR 4 ships Phase A — the document, tables, charts and pictures. The KVP and
chunk types are declared now so every module targets the public types, and are
filled by PR 5; `convert_many` and resume arrive with PR 6.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import pandas as pd
    from docling_core.types.doc import DoclingDocument
    from PIL.Image import Image

TextSource = Literal["vlm", "pdf"]
TEXT_SOURCES: tuple[TextSource, ...] = ("vlm", "pdf")


@dataclass(frozen=True)
class ConvertOptions:
    """What a conversion does. Recorded in `run.json`, where resume compares it (§3.8).

    `text_source="pdf"` replaces each text item's text with the PDF's own text
    layer; it is an opt-in for born-digital PDFs whose text layer you trust, and
    empties every text item on a scan (§3.2).
    """

    text_source: TextSource = "vlm"
    charts: bool = True
    chart_code: bool = False
    save_page_images: bool = False

    def __post_init__(self) -> None:
        if self.text_source not in TEXT_SOURCES:
            raise ValueError(
                f"text_source must be one of {', '.join(TEXT_SOURCES)}, "
                f"not {self.text_source!r}"
            )
        if self.chart_code and not self.charts:
            raise ValueError("chart_code needs charts")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Chart:
    """A picture classified as a chart, with what Granite Vision read from it.

    `image` is the crop Granite Vision saw (our 1.5× render, §3.14 rule 2).
    `csv` is None when the model's table could not be parsed at all; `raw_csv`
    always keeps what the model wrote, since a dropped CSV is otherwise silent
    (§17). `code` is untrusted model output and is never executed (§3.10).
    """

    index: int
    page_no: int
    kind: str
    confidence: float
    image: Image
    csv: str | None
    raw_csv: str | None
    summary: str | None
    code: str | None
    ref: str


@dataclass
class Picture:
    """A picture that is not a chart; `image` is Docling's crop of it."""

    index: int
    page_no: int
    kind: str | None
    image: Image | None
    ref: str


@dataclass
class Table:
    index: int
    page_no: int
    dataframe: pd.DataFrame
    html: str
    ref: str


@dataclass
class KvpResult:
    """Key-value extraction against a user schema (§3.3). Filled by PR 5."""

    pages: dict[int, dict[str, Any] | None]
    merged: dict[str, Any]
    conflicts: list[dict[str, Any]]
    valid: bool
    raw: dict[int, str]


@dataclass
class Chunk:
    """One `chunks.jsonl` line (§3.9). Filled by PR 5."""

    id: str
    text: str
    contextualized: str
    headings: list[str]
    page_nos: list[int]
    refs: list[str]
    kind: str


@dataclass
class PageStats:
    """Per-page cost and health of granite-docling's generation (§3.14 rule 3).

    `capped` and `looped` are our own checks on the raw DocTags (§3.10): Docling
    reports neither on MLX.
    """

    page_no: int
    tokens: int
    generation_ms: float
    render_ms: float | None
    capped: bool
    looped: bool


@dataclass
class RunInfo:
    """Everything `run.json` records about how the output was produced (§3.5)."""

    input_sha256: str
    page_count: int
    options: dict[str, Any]
    models: dict[str, Any]
    versions: dict[str, str]
    timings_ms: dict[str, float]
    pages: list[PageStats]
    chart_ms: list[float]
    peak_memory_bytes: dict[str, int]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ConvertResult:
    source: Path
    document: DoclingDocument
    charts: list[Chart]
    pictures: list[Picture]
    tables: list[Table]
    kvp: KvpResult | None
    chunks: list[Chunk]
    run: RunInfo

    def save(self, out_dir: Path) -> None:
        """Write the §3.5 tree to `out_dir`, replacing whatever was there atomically."""
        from granite_document_intelligence.export import save

        save(self, out_dir)


def convert(pdf: Path, options: ConvertOptions | None = None) -> ConvertResult:
    """Convert one PDF and return the results; writes nothing to disk.

    Refuses to start, before loading any model, when the models directory is
    missing, unstripped or disagrees with the pinned revisions — the message names
    `gdi convert-model`, never a fallback to the Hub (§3.13 rules 1–2).
    """
    from granite_document_intelligence.pipeline import run

    return run(Path(pdf), options or ConvertOptions())

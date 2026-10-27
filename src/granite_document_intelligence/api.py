"""The public surface (plan §3.7): `convert` returns data, `save` is the only writer.

The CLI and any later UI call these and nothing below them, so they cannot
diverge. Heavy imports (Docling, torch, mlx) happen inside `convert`, never at
import time (§6), so importing the package stays cheap.

Both entry points hold the process-level run lock (§3.14 rule 4) for as long as
they run, so two runs on one Mac never load the models side by side.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import pandas as pd
    from docling_core.types.doc import DoclingDocument
    from PIL.Image import Image

from granite_document_intelligence.config import (
    CHUNK_TOKENS_DEFAULT,
    CHUNK_TOKENS_MAX,
    CHUNK_TOKENS_MIN,
    MAX_FILE_MB_DEFAULT,
    MAX_PAGES_DEFAULT,
)

TextSource = Literal["vlm", "pdf"]
TEXT_SOURCES: tuple[TextSource, ...] = ("vlm", "pdf")


@dataclass(frozen=True)
class ConvertOptions:
    """What a conversion does. Recorded in `run.json`, where resume compares it (§3.8).

    `text_source="pdf"` replaces each text item's text with the PDF's own text
    layer; it is an opt-in for born-digital PDFs whose text layer you trust, and
    empties every text item on a scan (§3.2).

    `schema` turns on key-value extraction (§3.3) on `pages` — page 1 by
    default, `"1,3-4"` or `"all"`; the schema file is checked before any model
    loads (§3.13 rule 8). `chunk_tokens` sizes the RAG chunks (§3.9).
    """

    text_source: TextSource = "vlm"
    charts: bool = True
    chart_code: bool = False
    save_page_images: bool = False
    schema: Path | None = None
    pages: str = "1"
    chunks: bool = True
    chunk_tokens: int = CHUNK_TOKENS_DEFAULT
    max_pages: int = MAX_PAGES_DEFAULT
    max_file_mb: int = MAX_FILE_MB_DEFAULT

    def __post_init__(self) -> None:
        for name in ("max_pages", "max_file_mb"):
            if getattr(self, name) < 1:
                raise ValueError(
                    f"{name} must be at least 1, not {getattr(self, name)}"
                )
        if self.text_source not in TEXT_SOURCES:
            raise ValueError(
                f"text_source must be one of {', '.join(TEXT_SOURCES)}, "
                f"not {self.text_source!r}"
            )
        if self.chart_code and not self.charts:
            raise ValueError("chart_code needs charts")
        from granite_document_intelligence.kvp import parse_pages

        parse_pages(self.pages)
        if self.schema is None and self.pages != "1":
            raise ValueError("pages needs a schema")
        if not CHUNK_TOKENS_MIN <= self.chunk_tokens <= CHUNK_TOKENS_MAX:
            raise ValueError(
                f"chunk_tokens must be {CHUNK_TOKENS_MIN}–{CHUNK_TOKENS_MAX}, "
                f"not {self.chunk_tokens}"
            )

    def as_dict(self) -> dict[str, Any]:
        """The options that shape the output, as JSON values: what resume compares.

        The limits are left out: they decide whether a PDF is converted, not
        what its output is, so raising `max_pages` re-runs no finished PDF. The
        schema's content hash is added by `recorded_options`.
        """
        options = asdict(self)
        del options["max_pages"], options["max_file_mb"]
        options["schema"] = str(self.schema) if self.schema is not None else None
        return options


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
    """Key-value extraction against a user schema (§3.3).

    `pages` holds each page's parsed answer (None when its output held no JSON
    object) and `raw` what the model wrote. `merged` takes each field's first
    non-null value in page order; `conflicts` lists fields the pages disagree
    on. `valid` is whether `merged` matches the schema, and `errors` says where
    it does not — an off-schema answer is recorded, never raised (§7).
    """

    pages: dict[int, dict[str, Any] | None]
    merged: dict[str, Any]
    conflicts: list[dict[str, Any]]
    valid: bool
    errors: list[str]
    raw: dict[int, str]


@dataclass
class Chunk:
    """One `chunks.jsonl` line (§3.9).

    `contextualized` is the heading-prefixed text meant for embedding; `refs`
    are the document items the chunk covers (`doc_item_refs` in the file).
    Chart summaries and CSVs arrive inside the chunk that cites the chart (S7).
    """

    id: str
    text: str
    contextualized: str
    headings: list[str]
    page_nos: list[int]
    refs: list[str]


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
    kvp_ms: list[float] = field(default_factory=list)


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


BatchStatus = Literal["done", "skipped", "failed"]


@dataclass
class BatchItem:
    """One PDF's outcome in `convert_many`.

    `done` was converted and saved, `skipped` already had a matching successful
    `run.json` (§3.8), and `failed` has an error `run.json` in `out_dir` — unless
    `out_dir` itself was refused (§3.10), when nothing was written. `result` is
    set only for `done`; `output_bytes` is the folder's size as `run.json`
    records it.
    """

    pdf: Path
    out_dir: Path
    status: BatchStatus
    result: ConvertResult | None = None
    error: str | None = None
    output_bytes: int = 0


def convert(
    pdf: Path, options: ConvertOptions | None = None, *, wait: bool = True
) -> ConvertResult:
    """Convert one PDF and return the results; writes nothing to disk.

    Refuses to start, before loading any model, when the models directory is
    missing, unstripped or disagrees with the pinned revisions — the message names
    `gdi convert-model`, never a fallback to the Hub (§3.13 rules 1–2) — and when
    the file is not a PDF or is over a limit (§3.10). Waits for another run to
    finish first, or raises `RunLockedError` with `wait=False` (§3.14 rule 4).
    """
    from granite_document_intelligence.pipeline import run
    from granite_document_intelligence.runlock import run_lock

    with run_lock(wait=wait):
        return run(Path(pdf), options or ConvertOptions())


def convert_many(
    inputs: Iterable[Path | str],
    out_dir: Path,
    options: ConvertOptions | None = None,
    *,
    force: bool = False,
    recursive: bool = False,
    wait: bool = True,
) -> Iterator[BatchItem]:
    """Convert PDFs and directories of them into `out_dir`, yielding one item per PDF.

    Owns the §3.8 rules so a CLI and a UI cannot diverge on them: directory
    expansion, one output folder per PDF by its path relative to its input,
    resume (a PDF is skipped only when its `run.json` records success for the
    same file and options; `force` re-runs everything), and continuing past a
    failed PDF. Nothing happens until the first item is requested; a problem
    with the batch as a whole — a missing input, two PDFs sharing an output
    folder, an unready models directory, a bad schema — raises then, before any
    model loads.
    """
    from granite_document_intelligence.batch import run

    yield from run(
        [Path(p) for p in inputs],
        Path(out_dir),
        options or ConvertOptions(),
        force=force,
        recursive=recursive,
        wait=wait,
    )

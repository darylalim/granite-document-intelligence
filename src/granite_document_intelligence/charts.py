"""Our chart stage: Granite Vision on a 1.5× render of each chart, raw output kept.

It replaces Docling's chart-extraction enrichment for two reasons, both only
reachable this way through public API (decided with the user, 2026-10-27):

- **The crop.** Docling cuts charts at 72 dpi, and no option reaches it; our own
  1.5× render of the picture's box matched 43 of 69 corpus cells against 37
  (S11). The classifier still reads Docling's crop, so which pictures count as
  charts does not change.
- **The raw CSV.** Docling's pandas parse drops a chart's whole CSV when a label
  holds an unquoted comma, keeping nothing (S2, §17). Here the model's text is
  kept, and parsed leniently.

The prompts, chat template and greedy settings are Docling's
`granite_vision_v4_mlx` preset's, so the model sees what it saw in S6/S7/S11. The
results also go onto each `PictureItem.meta`, as Docling's stage would put them,
so Markdown, JSON and chunks carry them (§3.9).
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from granite_document_intelligence import config, memory
from granite_document_intelligence.api import Chart

if TYPE_CHECKING:
    from docling_core.types.doc import DoclingDocument, PictureItem, TableData
    from PIL.Image import Image

_log = logging.getLogger(__name__)

# The picture classes Docling's own chart stage sends to Granite Vision.
CHART_KINDS = frozenset({"bar_chart", "pie_chart", "line_chart"})
CSV_PROMPT = "<chart2csv>"
SUMMARY_PROMPT = "<chart2summary>"
CODE_PROMPT = "<chart2code>"
_CSV_FENCE = re.compile(r"```csv\s*\n(.*?)\n```", re.DOTALL)
_PYTHON_FENCE = re.compile(r"```python\s*\n(.*?)\n```", re.DOTALL)
_CREATED_BY = "granite-document-intelligence"


def chart_kind(picture: PictureItem) -> tuple[str, float] | None:
    """The picture's main classification, if it is a chart class; else None."""
    meta = picture.meta
    if meta is None or meta.classification is None:
        return None
    main = meta.classification.get_main_prediction()
    if main.class_name not in CHART_KINDS:
        return None
    return main.class_name, main.confidence or 0.0


# --- Parsing ------------------------------------------------------------------


def csv_body(raw: str) -> str:
    """The CSV inside the model's ```csv fence, or the bare text without fences."""
    match = _CSV_FENCE.search(raw)
    if match:
        return match.group(1).strip()
    body = re.sub(r"^```+(?:csv)?\s*", "", raw.strip())
    return re.sub(r"```+\s*$", "", body).strip()


def parse_chart_csv(raw: str) -> tuple[list[list[str]] | None, int]:
    """Rows of the model's table, and how many rows had to be repaired.

    The table's width is the commonest field count among the body rows. A longer
    row was split by an unquoted comma, and where the comma sat differs by row,
    on the evidence of one case each:

    - a **body** row's comma is in its label, the first column (`Full-Time,
      Year-Round Workers,Male,61440`, S2), so its leading fields are rejoined;
    - the **header**'s was in the last series name (GAO-07-631 Figure 6, S6 and
      PR 4's hand run), so its trailing fields are rejoined.

    A shorter row is padded. Returns None for no rows.
    """
    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(csv_body(raw)))
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        return None, 0
    body = rows[1:] or rows
    width = Counter(len(row) for row in body).most_common(1)[0][0]
    repaired = 0
    out: list[list[str]] = []
    for i, row in enumerate(rows):
        if len(row) > width:
            extra = len(row) - width + 1
            if i == 0 and len(rows) > 1:
                row = [*row[: width - 1], ", ".join(row[width - 1 :])]
            else:
                row = [", ".join(row[:extra]), *row[extra:]]
            repaired += 1
        elif len(row) < width:
            row = row + [""] * (width - len(row))
            repaired += 1
        out.append(row)
    return out, repaired


def rows_to_csv(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", ""))
    except ValueError:
        return False
    return True


def rows_to_table_data(rows: list[list[str]]) -> TableData:
    """`TableData` for `PictureMeta.tabular_chart`; a non-numeric first row is the header."""
    from docling_core.types.doc import TableCell, TableData

    header = len(rows) > 1 and any(cell and not _is_number(cell) for cell in rows[0])
    cells = [
        TableCell(
            text=text,
            start_row_offset_idx=r,
            end_row_offset_idx=r + 1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
            column_header=header and r == 0,
            row_header=c == 0
            and bool(text)
            and not _is_number(text)
            and not (header and r == 0),
        )
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    return TableData(table_cells=cells, num_rows=len(rows), num_cols=len(rows[0]))


def python_code(raw: str) -> str | None:
    match = _PYTHON_FENCE.search(raw)
    return match.group(1).strip() if match else None


_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# A clause ends at a newline or after ., ; ! or ?, also when a closing quote or
# bracket follows it (`…savings." The y-axis…`).
_CLAUSE = re.compile(r"\n|(?<=[.;!?])\s+|(?<=[.;!?][\"'”’)\]])\s+")
_MIN_LABEL = 3  # shorter labels ("A", "US") match inside ordinary words


def _value(text: str) -> float | None:
    cleaned = text.strip().replace(",", "").replace("$", "").rstrip("%").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


# Every warning about a written chart starts with its name and page, so the UI can
# put it on that chart's card by matching this prefix alone (plan §18.3). A chart
# skipped for having no image has no card and no name, so it is named by its
# Docling reference instead and appears on the Run view only.
WARNING_PREFIX = re.compile(r"^(chart-\d{3}) \(page (\d+)\): ")


def summary_disagreements(rows: list[list[str]], summary: str) -> list[str]:
    """Rows the summary pairs with a number the row does not hold.

    Both Granite Vision variants attached every percentage of the GAO-08-14 pie
    to the wrong slice while the CSV was right (S6), so the summary is checked
    against the CSV: in each clause naming a row's label, the first number after
    the label must be one of that row's values (within 1%). The summary is still
    written as generated; a disagreement is a `run.json` warning. It reads only
    label–number pairs, so it misses a wrong number stated without its label, and
    it skips labels holding a digit (`1995 to 2000`), whose "next number" is often
    part of another label — a false alarm PR 4's hand run hit.

    Each entry names the row by its position (`row 4: summary 12, CSV 9`; row 1 is
    the first after the header), never by its label: the label is document
    content as the model read it, and `run.json` holds none (§3.13 rule 5, §18.4
    rule 3; decided with the user, 2026-10-30).
    """
    by_label: dict[str, tuple[int, list[float]]] = {}
    for number, row in enumerate(rows[1:], start=1):
        label = row[0].strip() if row else ""
        values = [v for v in (_value(cell) for cell in row[1:]) if v is not None]
        has_digit = any(ch.isdigit() for ch in label)
        if len(label) >= _MIN_LABEL and not has_digit and values:
            by_label[label] = (number, values)
    found: list[str] = []
    for clause in _CLAUSE.split(summary):
        lowered = clause.lower()
        for label, (number, values) in by_label.items():
            at = lowered.find(label.lower())
            if at < 0:
                continue
            match = _NUMBER.search(clause, at + len(label))
            claimed = _value(match.group()) if match else None
            if claimed is None:
                continue
            if not any(abs(claimed - v) <= 0.01 * max(abs(v), 1e-9) for v in values):
                found.append(
                    f"row {number}: summary {claimed:g}, "
                    f"CSV {', '.join(f'{v:g}' for v in values)}"
                )
    return found


# --- The crop -----------------------------------------------------------------


def render_crop(pdf: Any, picture: PictureItem, page_height: float) -> Image | None:
    """The picture's box rendered from the PDF at `CHART_CROP_SCALE` (S11).

    `pdf` is an open `pypdfium2.PdfDocument`. Returns None — the caller then uses
    Docling's crop — for a picture with no box or on a rotated page, where the
    crop's coordinates were never verified.
    """
    if not picture.prov:
        return None
    prov = picture.prov[0]
    page = pdf[prov.page_no - 1]
    if page.get_rotation() != 0:
        return None
    width, height = page.get_size()
    box = prov.bbox.to_top_left_origin(page_height=page_height)
    # pypdfium2 crops by how much to cut from each edge: left, bottom, right, top.
    crop = (box.l, height - box.b, width - box.r, box.t)
    if min(crop) < 0 or box.r <= box.l or box.b <= box.t:
        return None
    bitmap = page.render(scale=config.CHART_CROP_SCALE, crop=crop)
    return bitmap.to_pil().convert("RGB")


# --- Granite Vision -----------------------------------------------------------


class GraniteVision:
    """The 8-bit Granite Vision, loaded from the models directory.

    `trust_remote_code=False` is passed explicitly: the folder holds no repo code
    to run (S10), and this is the second guard, not the fix (§3.13 rule 2).
    """

    def __init__(self, models_dir: Path) -> None:
        from mlx_vlm import load
        from mlx_vlm.utils import load_config

        folder = models_dir / config.GRANITE_VISION_FOLDER
        # mlx-vlm's annotations are narrower than what it accepts (a PIL image,
        # a rendered prompt), so these are kept untyped.
        self.model: Any
        self.processor: Any
        self.model, self.processor = load(str(folder), trust_remote_code=False)
        self.config: Any = load_config(folder)

    def generate(self, image: Image, prompt: str) -> str:
        """Greedy generation, as Docling's preset runs it (temperature 0, 4096 tokens)."""
        text, _ = self.generate_counted(image, prompt, config.CHART_MAX_NEW_TOKENS)
        return text

    def generate_counted(
        self, image: Image, prompt: str, max_tokens: int
    ) -> tuple[str, int]:
        """Greedy generation on one image; the text and how many tokens it took.

        The count is what tells a capped generation from a finished one (§3.10).
        """
        from mlx_vlm import stream_generate
        from mlx_vlm.prompt_utils import apply_chat_template

        formatted: Any = apply_chat_template(
            self.processor, self.config, prompt, num_images=1
        )
        text = ""
        tokens = 0
        for chunk in stream_generate(
            self.model,
            self.processor,
            formatted,
            [image],  # ty: ignore[invalid-argument-type]
            max_tokens=max_tokens,
            temperature=0.0,
            verbose=False,
        ):
            text += chunk.text
            tokens = chunk.generation_tokens
        return text, tokens

    def close(self) -> None:
        del self.model, self.processor


@dataclass
class ChartsResult:
    charts: list[Chart]
    chart_ms: list[float]
    warnings: list[str]


def run(
    pdf_path: Path,
    document: DoclingDocument,
    models_dir: Path,
    *,
    code: bool = False,
    vision: GraniteVision | None = None,
) -> ChartsResult:
    """Extract every chart in `document`, writing the results onto its pictures too.

    Loads Granite Vision only if there is a chart, and releases it before
    returning unless the caller passed its own `vision`.
    """
    import pypdfium2
    from docling_core.types.doc import (
        DescriptionMetaField,
        PictureMeta,
        TabularChartMetaField,
    )
    from docling_core.types.doc.document import CodeLanguageLabel, CodeMetaField

    targets = [
        (p, kind) for p in document.pictures if (kind := chart_kind(p)) is not None
    ]
    if not targets:
        return ChartsResult([], [], [])

    owned = vision is None
    model = vision or GraniteVision(models_dir)
    charts: list[Chart] = []
    chart_ms: list[float] = []
    warnings: list[str] = []
    pdf = pypdfium2.PdfDocument(pdf_path)
    try:
        for position, (picture, (kind, confidence)) in enumerate(targets, start=1):
            index = len(charts) + 1
            name = f"chart-{index:03d}"
            page_no = picture.prov[0].page_no if picture.prov else 0
            _log.info("  chart %d/%d (page %d)", position, len(targets), page_no)
            page = document.pages.get(page_no)
            image = render_crop(pdf, picture, page.size.height) if page else None
            if image is None:
                image = picture.image.pil_image if picture.image else None
                if image is None:
                    warnings.append(
                        f"{picture.self_ref} (page {page_no}): a chart with no image; skipped"
                    )
                    continue
                warnings.append(
                    f"{name} (page {page_no}): used Docling's crop, not our render"
                )

            start = time.perf_counter()
            raw_csv = model.generate(image, CSV_PROMPT)
            summary = model.generate(image, SUMMARY_PROMPT).strip() or None
            raw_code = model.generate(image, CODE_PROMPT) if code else None
            chart_ms.append((time.perf_counter() - start) * 1000)

            rows, repaired = parse_chart_csv(raw_csv)
            if rows is None:
                warnings.append(
                    f"{name} (page {page_no}): no table in the model's output"
                )
            elif repaired:
                warnings.append(
                    f"{name} (page {page_no}): {repaired} row(s) had a stray field count; "
                    "joined a comma-split label or padded"
                )
            if rows is not None and summary is not None:
                disagreements = summary_disagreements(rows, summary)
                if disagreements:
                    warnings.append(
                        f"{name} (page {page_no}): the summary disagrees with its CSV: "
                        + "; ".join(disagreements)
                    )
            chart_code = python_code(raw_code) if raw_code is not None else None

            if picture.meta is None:
                picture.meta = PictureMeta()
            if rows is not None:
                picture.meta.tabular_chart = TabularChartMetaField(
                    chart_data=rows_to_table_data(rows), created_by=_CREATED_BY
                )
            if summary is not None:
                picture.meta.description = DescriptionMetaField(
                    text=summary, created_by=_CREATED_BY
                )
            if chart_code is not None:
                picture.meta.code = CodeMetaField(
                    text=chart_code,
                    language=CodeLanguageLabel.PYTHON,
                    created_by=_CREATED_BY,
                )
            charts.append(
                Chart(
                    index=index,
                    page_no=page_no,
                    kind=kind,
                    confidence=confidence,
                    image=image,
                    csv=rows_to_csv(rows) if rows is not None else None,
                    raw_csv=raw_csv,
                    summary=summary,
                    code=chart_code,
                    ref=picture.self_ref,
                )
            )
    finally:
        pdf.close()
        if owned:
            model.close()
            del model
            memory.release()
    return ChartsResult(charts, chart_ms, warnings)

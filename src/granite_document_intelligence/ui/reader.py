"""The §3.5 output tree, read for the viewer (plan §18.3).

The viewer treats the layout `export.save` writes as a contract, and
`tests/test_ui_reader.py` round-trips a result through `save()` into `read()`, so
a layout change that would empty a view fails the gate instead.

Every string read here is untrusted: it came from a PDF, from a model, or from a
file name. This module only finds and parses files. How the strings are shown is
`app.py`'s job, under §18.4 rule 3.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from granite_document_intelligence.charts import WARNING_PREFIX

RUN_JSON = "run.json"
# Crash residue can hold a run.json (export writes it into the staging folder
# before the swap), so these are never listed (§18.3).
SKIPPED_SUFFIXES = (".partial", ".old")
_CHART_PNG = re.compile(r"chart-(\d{3,})\.png")
_TABLE_CSV = re.compile(r"table-(\d{3,})\.csv")


@dataclass(frozen=True)
class RunEntry:
    """One PDF's output folder under the output root."""

    folder: Path
    name: str  # the folder relative to the root, as the selector shows it
    mtime: float  # of run.json, since run.json records no timestamp (§18.3)


def list_runs(root: Path) -> list[RunEntry]:
    """Every folder under `root` holding a `run.json`, newest first.

    Descent stops at a folder holding one, skips `.partial` and `.old`, and never
    follows a symlink, so a link cannot make the listing leave the root.
    """
    found: list[RunEntry] = []
    if not root.is_dir():
        return found
    for current, dirs, files in os.walk(root, onerror=lambda _: None):
        here = Path(current)
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.endswith(SKIPPED_SUFFIXES) and not (here / d).is_symlink()
        )
        if RUN_JSON in files and not (here / RUN_JSON).is_symlink():
            try:
                mtime = (here / RUN_JSON).stat().st_mtime
            except OSError:
                continue
            name = here.relative_to(root).as_posix() if here != root else here.name
            found.append(RunEntry(here, name, mtime))
            dirs[:] = []
    return sorted(found, key=lambda e: (-e.mtime, e.name))


@dataclass
class ChartCard:
    name: str  # chart-NNN
    page_no: int | None  # None when the tree does not say (see `_chart_pages`)
    png: Path
    csv: Path | None
    raw: Path | None
    summary: str | None
    code: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class TableFile:
    name: str  # table-NNN
    page_no: int | None
    csv: Path


@dataclass
class Result:
    """What one PDF's folder holds, for the six views."""

    folder: Path
    run: dict[str, Any]
    document_md: Path | None
    tables: list[TableFile]
    charts: list[ChartCard]
    kvp: dict[str, Any] | None
    chunks_jsonl: Path | None
    run_warnings: list[str]  # the ones that belong to no card
    problems: list[str]  # files that were present but could not be read

    @property
    def ok(self) -> bool:
        return self.run.get("status") == "ok"

    def counts(self) -> dict[str, int | None]:
        """Pages, tables, charts and chunks, from run.json, else the tree."""
        counts = self.run.get("counts") or {}
        page_count = self.run.get("page_count")
        return {
            "pages": page_count if isinstance(page_count, int) else None,
            "tables": _int(counts.get("tables"), len(self.tables)),
            "charts": _int(counts.get("charts"), len(self.charts)),
            "chunks": _int(counts.get("chunks"), None),
        }


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, fallback: int | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _json(path: Path, problems: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        problems.append(path.name)
        return None


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read(folder: Path) -> Result:
    """One PDF's folder. Missing files are empty views; unreadable ones are named
    in `problems`."""
    problems: list[str] = []
    run = _json(folder / RUN_JSON, problems)
    run = run if isinstance(run, dict) else {}
    document = _json(folder / "document.json", problems)
    document = document if isinstance(document, dict) else {}

    warnings = [w for w in _list(run.get("warnings")) if isinstance(w, str)]
    by_chart: dict[str, list[str]] = {}
    run_warnings: list[str] = []
    for warning in warnings:
        match = WARNING_PREFIX.match(warning)
        if match:
            by_chart.setdefault(match.group(1), []).append(warning)
        else:
            run_warnings.append(warning)

    charts = _charts(folder / "charts", document, by_chart)
    known = {card.name for card in charts}
    # A prefixed warning whose chart has no files still belongs on the Run view.
    run_warnings += [
        w for name, ws in by_chart.items() if name not in known for w in ws
    ]

    kvp = _json(folder / "kvp.json", problems)
    md = folder / "document.md"
    chunks = folder / "chunks.jsonl"
    return Result(
        folder=folder,
        run=run,
        document_md=md if md.is_file() else None,
        tables=_tables(folder / "tables", document),
        charts=charts,
        kvp=kvp if isinstance(kvp, dict) else None,
        chunks_jsonl=chunks if chunks.is_file() else None,
        run_warnings=run_warnings,
        problems=problems,
    )


def _numbered(directory: Path, pattern: re.Pattern[str]) -> list[tuple[int, Path]]:
    if not directory.is_dir():
        return []
    found = []
    for path in directory.iterdir():
        match = pattern.fullmatch(path.name)
        if match and path.is_file() and not path.is_symlink():
            found.append((int(match.group(1)), path))
    return sorted(found)


def _tables(directory: Path, document: dict[str, Any]) -> list[TableFile]:
    # table-NNN is the Nth of document.json's tables (pipeline.py), so its page
    # comes from there: the CSV carries none (§18.3).
    tables = _list(document.get("tables"))
    return [
        TableFile(
            path.stem,
            _page(tables[number - 1]) if number <= len(tables) else None,
            path,
        )
        for number, path in _numbered(directory, _TABLE_CSV)
    ]


def _page(item: Any) -> int | None:
    try:
        page = item["prov"][0]["page_no"]
    except (KeyError, IndexError, TypeError):
        return None
    return page if isinstance(page, int) else None


def _chart_pages(document: dict[str, Any], count: int) -> list[int | None]:
    """Each chart's page, from the pictures that carry chart results.

    The chart stage writes a description or a chart table onto each picture it
    read (charts.py), in document order, as it numbers the charts. A chart that
    produced neither leaves no mark, so the mapping is trusted only when the
    counts agree. Otherwise every page is unknown rather than possibly wrong.
    """
    pictures = _list(document.get("pictures"))
    marked = [
        _page(p)
        for p in pictures
        if isinstance(p, dict)
        and isinstance(p.get("meta"), dict)
        and ("description" in p["meta"] or "tabular_chart" in p["meta"])
    ]
    return marked if len(marked) == count else [None] * count


def _charts(
    directory: Path, document: dict[str, Any], by_chart: dict[str, list[str]]
) -> list[ChartCard]:
    pngs = _numbered(directory, _CHART_PNG)
    pages = _chart_pages(document, len(pngs))
    cards = []
    for (_, png), page in zip(pngs, pages, strict=True):
        stem = png.with_suffix("")
        csv, raw = stem.with_suffix(".csv"), stem.with_suffix(".raw.txt")
        summary = _text(stem.with_suffix(".summary.txt"))
        cards.append(
            ChartCard(
                name=stem.name,
                page_no=page,
                png=png,
                csv=csv if csv.is_file() else None,
                raw=raw if raw.is_file() else None,
                summary=summary.strip() if summary is not None else None,
                code=_text(stem.with_suffix(".py")),
                warnings=by_chart.get(stem.name, []),
            )
        )
    return cards


def read_chunks(path: Path, start: int, count: int) -> tuple[list[dict[str, Any]], int]:
    """Chunks `start` to `start + count`, and how many lines the file holds.

    Paged, not all at once (§18.3): a page's rows are parsed and the rest only
    counted. A line that is not a JSON object becomes an empty row.
    """
    rows: list[dict[str, Any]] = []
    total = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for total, line in enumerate(handle, start=1):
            if start < total <= start + count:
                try:
                    row = json.loads(line)
                except ValueError:
                    row = None
                rows.append(row if isinstance(row, dict) else {})
    return rows, total

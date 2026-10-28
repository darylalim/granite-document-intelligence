"""The quality eval: convert the corpus, score it, check budgets and the baseline.

Run by hand on the development Mac, never in CI (plan §8.1) — it loads the real
models. From the repository root:

    uv run python eval/score.py                 # the corpus, a cold run + 3 timed
    uv run python eval/score.py --repeats 1 --no-cold --label try-x

It converts every public PDF (and every private one, when `eval/private/` has
any) through `convert_many()` into `eval/runs/<id>/` (private ones into
`eval/private/runs/<id>/`), scores the first timed run's output against
`eval/expected/`, takes timings as the median of the timed runs and memory as
their maximum (§8.2 rule 8), and checks both against `eval/budgets.toml` and
its baseline results file (§3.11, §8.2 rule 1). It prints a Markdown report and
writes `eval/results/<date>-<sha>[-<label>].json`. Exit status: 0 when every
budget, tolerance and adversarial check holds, 1 when any fails.

The scoring rules are `matching.py`'s; this file only finds the files and adds
up. The run is offline (`HF_HUB_OFFLINE=1` unless already set): every model is
local, and the eval must not depend on the network.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import matching
from matching import Item

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval"
RESULTS_FORMAT = 1
TIMED_PREFIX = "r"
COLD = "cold"

# Every score compared with the baseline: name → (higher is better, the
# `[tolerance]` key in budgets.toml that bounds a drop). Rule 1's proposal:
# 0 for counts and KVP fields, 1 percentage point for rates, 0.01 for text.
METRICS: dict[str, tuple[bool, str]] = {
    "chart_cells_pct": (True, "rate_pct"),
    "chart_headers_pct": (True, "rate_pct"),
    "charts_found": (True, "count"),
    "chart_false_positives": (False, "count"),
    "summary_unsupported_pct": (False, "rate_pct"),
    "summary_pair_errors": (False, "count"),
    "table_cells_pct": (True, "rate_pct"),
    "table_cells_found_pct": (True, "rate_pct"),
    "tables_found": (True, "count"),
    "kvp_fields_matched": (True, "kvp"),
    "kvp_schema_valid": (True, "kvp"),
    "headings_found": (True, "count"),
    "headings_in_text": (True, "count"),
    "page_counts_ok": (True, "count"),
    "text_similarity": (True, "text"),
    "word_f1": (True, "text"),
    "reading_order": (True, "text"),
    "chunks_over_limit": (False, "count"),
    "chunk_pages_unsupported_pct": (False, "rate_pct"),
}


class PrivatePathError(ValueError):
    """A results file bound for git names something from the private tier (rule 7)."""


# --- The corpus -----------------------------------------------------------------


@dataclass
class Entry:
    """One corpus PDF and its expected outputs."""

    name: str
    tier: str  # "public" | "private"
    pdf: Path
    expected: Path
    manifest: dict[str, Any]

    @property
    def adversarial(self) -> dict[str, Any] | None:
        return self.manifest.get("adversarial")

    @property
    def scanned(self) -> bool:
        return not self.manifest.get("text_layer", True)


def load_corpus(eval_dir: Path, tier: str) -> list[Entry]:
    """Every `<tier root>/expected/*/manifest.json` with its PDF under `corpus/`."""
    root = eval_dir if tier == "public" else eval_dir / "private"
    entries = []
    for manifest_path in sorted((root / "expected").glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries.append(
            Entry(
                name=manifest_path.parent.name,
                tier=tier,
                pdf=root / "corpus" / manifest["pdf"],
                expected=manifest_path.parent,
                manifest=manifest,
            )
        )
    return entries


def check_corpus(entries: list[Entry]) -> None:
    """A PDF changed under its expectations makes every score meaningless."""
    from granite_document_intelligence.pipeline import sha256_of

    for entry in entries:
        if not entry.pdf.is_file():
            raise SystemExit(
                f"{entry.pdf}: missing (named by {entry.name}/manifest.json)"
            )
        if sha256_of(entry.pdf) != entry.manifest["sha256"]:
            raise SystemExit(f"{entry.pdf}: SHA-256 differs from its manifest")


def read_rows(path: Path) -> matching.Rows:
    return [row for row in csv.reader(io.StringIO(path.read_text(encoding="utf-8")))]


# --- The text layer (rule 2) ----------------------------------------------------


def _visible(textpage: Any, index: int) -> bool:
    """False for white-filled or invisible (render mode 3) characters: text a
    reader cannot see must not be expected of a prediction (rule 2)."""
    import ctypes

    import pypdfium2.raw as pdfium_c

    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if pdfium_c.FPDFText_GetFillColor(
        textpage,
        index,
        ctypes.byref(r),
        ctypes.byref(g),
        ctypes.byref(b),
        ctypes.byref(a),
    ) and (r.value, g.value, b.value) == (255, 255, 255):
        return False
    obj = pdfium_c.FPDFText_GetTextObject(textpage, index)
    return (
        not obj
        or pdfium_c.FPDFTextObj_GetTextRenderMode(obj)
        != pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE
    )


def text_layer(pdf: Path, hidden: list[str]) -> dict[int, str]:
    """Each page's visible text, with the manifest's `hidden_text` strings removed.

    Two kinds of hidden text need two mechanisms (rule 2): a white-on-white
    injection is visible per character, but the Census tables' "—Con." is an
    ordinary text object hidden by what is drawn over it, so its manifest
    lists it under `hidden_text` and it is removed by string.
    """
    import pypdfium2
    import pypdfium2.raw as pdfium_c

    pages: dict[int, str] = {}
    doc = pypdfium2.PdfDocument(pdf)
    try:
        for i in range(len(doc)):
            textpage = doc[i].get_textpage()
            raw = textpage.raw
            chars = []
            for index in range(textpage.count_chars()):
                code = pdfium_c.FPDFText_GetUnicode(raw, index)
                if code and _visible(raw, index):
                    chars.append(chr(code))
            text = "".join(chars)
            for string in hidden:
                text = text.replace(string, "")
            pages[i + 1] = text
    finally:
        doc.close()
    return pages


# --- Reading a converted tree ---------------------------------------------------


@dataclass
class Tree:
    """What `score.py` reads from one PDF's §3.5 output folder."""

    root: Path
    run: dict[str, Any]
    doc: dict[str, Any]
    markdown: str
    charts: list[Item]
    summaries: dict[str, str]
    tables: list[Item]
    kvp: dict[str, Any] | None
    chunks: list[dict[str, Any]]


def _resolve(doc: dict[str, Any], ref: str) -> dict[str, Any]:
    _, kind, index = ref.split("/")
    return doc[kind][int(index)]


def _walk(doc: dict[str, Any], node: dict[str, Any]) -> list[dict[str, Any]]:
    """Items under `node` in document order (the body tree is reading order)."""
    out = []
    for child in node.get("children", []):
        item = _resolve(doc, child["$ref"])
        out.append(item)
        out.extend(_walk(doc, item))
    return out


def _page(item: dict[str, Any]) -> int:
    prov = item.get("prov") or []
    return prov[0]["page_no"] if prov else 0


def _table_text(item: dict[str, Any]) -> str:
    grid = (item.get("data") or {}).get("grid") or []
    return "\n".join(" ".join(cell.get("text", "") for cell in row) for row in grid)


def read_tree(root: Path) -> Tree:
    run = json.loads((root / "run.json").read_text(encoding="utf-8"))
    doc = json.loads((root / "document.json").read_text(encoding="utf-8"))
    charts: list[Item] = []
    summaries: dict[str, str] = {}
    for picture in doc.get("pictures", []):
        uri = str((picture.get("image") or {}).get("uri") or "")
        if not uri.startswith("charts/"):
            continue
        stem = Path(uri).stem  # chart-NNN
        csv_path = root / "charts" / f"{stem}.csv"
        rows = read_rows(csv_path) if csv_path.is_file() else []
        charts.append(Item(stem, _page(picture), int(stem.split("-")[1]), rows))
        summary = root / "charts" / f"{stem}.summary.txt"
        if summary.is_file():
            summaries[stem] = summary.read_text(encoding="utf-8")
    tables = []
    for index, table in enumerate(doc.get("tables", []), start=1):
        stem = f"table-{index:03d}"
        path = root / "tables" / f"{stem}.csv"
        tables.append(
            Item(stem, _page(table), index, read_rows(path) if path.is_file() else [])
        )
    kvp_path = root / "kvp.json"
    chunks_path = root / "chunks.jsonl"
    return Tree(
        root=root,
        run=run,
        doc=doc,
        markdown=(root / "document.md").read_text(encoding="utf-8"),
        charts=charts,
        summaries=summaries,
        tables=tables,
        kvp=json.loads(kvp_path.read_text(encoding="utf-8"))
        if kvp_path.is_file()
        else None,
        chunks=[
            json.loads(line)
            for line in chunks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if chunks_path.is_file()
        else [],
    )


def page_texts(tree: Tree) -> tuple[dict[int, str], dict[int, list[str]]]:
    """Each page's predicted text, and its body paragraphs in reading order.

    Read from `document.json`, which `document.md` is exported from, because
    only it says which page an item came from. Tables contribute their cells,
    since the text layer holds them; furniture (running heads, folios) counts
    toward the page's text, since the text layer holds that too, but not
    toward reading order. Model-written chart summaries are not page text.
    """
    texts: dict[int, list[str]] = {}
    paragraphs: dict[int, list[str]] = {}
    for layer in ("body", "furniture"):
        for item in _walk(tree.doc, tree.doc.get(layer, {})):
            ref = item.get("self_ref", "")
            if ref.startswith("#/texts/"):
                text = item.get("text", "")
            elif ref.startswith("#/tables/"):
                text = _table_text(item)
            else:
                continue
            page = _page(item)
            texts.setdefault(page, []).append(text)
            if layer == "body" and ref.startswith("#/texts/"):
                paragraphs.setdefault(page, []).append(text)
    return {p: "\n".join(t) for p, t in texts.items()}, paragraphs


# --- Scoring one PDF ------------------------------------------------------------


def _chart_score(expected: matching.Rows, predicted: matching.Rows) -> int:
    return matching.score_chart(expected, predicted).matched


def _table_score(expected: matching.Rows, predicted: matching.Rows) -> int:
    return matching.score_table(expected, predicted).found


def _chunk_tokenizer(chunk_tokens: int) -> Any:
    from granite_document_intelligence.chunk import load_tokenizer

    return load_tokenizer(chunk_tokens)


def score_pdf(entry: Entry, tree: Tree, tokenizer: Any | None) -> dict[str, Any]:
    """Every §8.1 quality score for one PDF's output, as JSON values."""
    manifest = entry.manifest
    expected_charts = [
        Item(
            c["id"],
            c["page"],
            int(c["id"].split("-")[1]),
            read_rows(entry.expected / c["csv"]),
        )
        for c in manifest.get("charts", [])
    ]
    expected_tables = [
        Item(
            t["id"],
            t["page"],
            int(t["id"].split("-")[1]),
            read_rows(entry.expected / t["csv"]),
        )
        for t in manifest.get("tables", [])
    ]

    # Charts and their summaries (rules 3, 4, 5).
    pairing = matching.pair_items(
        expected_charts, tree.charts, _chart_score, shared=True
    )
    charts: dict[str, Any] = {}
    for exp, pred in pairing.pairs:
        s = matching.score_chart(exp.rows, pred.rows)
        charts[exp.key] = {"predicted": pred.key, **asdict(s)}
    for exp in pairing.missed:
        s = matching.score_chart(exp.rows, [])
        charts[exp.key] = {"predicted": None, **asdict(s)}
    summaries: dict[str, Any] = {}
    by_pred: dict[str, matching.Rows] = {}
    for exp, pred in pairing.pairs:
        rows = by_pred.setdefault(pred.key, [exp.rows[0]] if exp.rows else [])
        rows.extend(exp.rows[1:])
    for pred_key, rows in by_pred.items():
        if pred_key in tree.summaries:
            summaries[pred_key] = asdict(
                matching.score_summary(tree.summaries[pred_key], rows)
            )
    false_positives = [p.key for p in pairing.false_positives]

    # Tables (rule 3).
    table_pairing = matching.pair_items(expected_tables, tree.tables, _table_score)
    tables: dict[str, Any] = {}
    for exp, pred in table_pairing.pairs:
        tables[exp.key] = {
            "predicted": pred.key,
            **asdict(matching.score_table(exp.rows, pred.rows)),
        }
    for exp in table_pairing.missed:
        tables[exp.key] = {
            "predicted": None,
            **asdict(matching.score_table(exp.rows, [])),
        }

    # KVP, including the adversarial checks (rule 6).
    kvp: dict[str, Any] | None = None
    if "kvp" in manifest:
        want = json.loads(
            (entry.expected / manifest["kvp"]["expected"]).read_text(encoding="utf-8")
        )
        merged = (tree.kvp or {}).get("merged") or {}
        fields = matching.kvp_fields(want, merged)
        kvp = {
            "fields": len(fields),
            "matched": sum(fields.values()),
            "missed": sorted(k for k, ok in fields.items() if not ok),
            "valid": bool((tree.kvp or {}).get("valid")),
        }

    # Document text and headings (rule 2).
    reference = text_layer(entry.pdf, manifest.get("hidden_text", []))
    predicted, paragraphs = page_texts(tree)
    pages = {}
    if manifest.get("text_layer", True):
        for page, ref in reference.items():
            pred = predicted.get(page, "")
            pages[page] = {
                "similarity": round(matching.text_similarity(pred, ref), 4),
                "word_f1": round(matching.word_f1(pred, ref), 4),
                "reading_order": matching.reading_order(paragraphs.get(page, []), ref),
            }
    headings = [
        line.strip()
        for line in (entry.expected / manifest["headings"])
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    found = matching.headings_found(headings, tree.markdown)
    in_text = matching.headings_in_text(headings, tree.markdown)

    # Chunks (rule 4).
    limit = int(tree.run.get("options", {}).get("chunk_tokens", 512))
    over = None
    if tokenizer is not None:
        over = matching.chunks_over_limit(
            [c["contextualized"] for c in tree.chunks], tokenizer.count_tokens, limit
        )
    # A chunk's pages are checked against the text layer, so not on a scan.
    page_claims = page_refs = 0
    if manifest.get("text_layer", True):
        for c in tree.chunks:
            for page in c["page_nos"]:
                claim = matching.page_claim(c["text"], reference.get(page, ""))
                if claim is not None:
                    page_claims += 1
                    page_refs += not claim
    breadcrumbs = {
        matching.normalize_label(matching.normalize_text(h))
        for c in tree.chunks
        for h in c["headings"]
    }

    run = tree.run
    return {
        "page_count": run.get("page_count"),
        "page_count_ok": run.get("page_count") == manifest["pages"],
        "charts": charts,
        "chart_false_positives": false_positives,
        "summaries": summaries,
        "tables": tables,
        "kvp": kvp,
        "pages": pages,
        "headings": {
            "expected": len(headings),
            "found": sum(found),
            "in_text": sum(in_text),
            "missing": [h for h, ok in zip(headings, found, strict=True) if not ok],
        },
        "chunks": {
            "count": len(tree.chunks),
            "over_limit": over,
            "page_claims": page_claims,
            "page_claims_unsupported": page_refs,
            "headings_in_breadcrumbs": sum(
                1
                for h in headings
                if matching.normalize_label(matching.normalize_text(h)) in breadcrumbs
            ),
        },
        "capped_pages": [p["page_no"] for p in run.get("pages", []) if p.get("capped")],
        "looped_pages": [p["page_no"] for p in run.get("pages", []) if p.get("looped")],
        "warnings": len(run.get("warnings", [])),
    }


def adversarial_check(
    entry: Entry, tree: Tree, scores: dict[str, Any]
) -> dict[str, Any]:
    """Rule 6, with one amendment decided with the user (PR 7): a listed loop
    page that did not loop is reported, not failed. The page was built to loop
    and at the shipped settings does not (1,203 tokens, a clean table), so the
    loop check went untested rather than wrong; a page that does loop or cap
    must still be in the warnings."""
    adv = entry.adversarial or {}
    merged = (tree.kvp or {}).get("merged") or {}
    injected = matching.injected_found(adv.get("injected_values", []), merged)
    flagged = set(scores["capped_pages"]) | set(scores["looped_pages"])
    loop_pages = adv.get("loop_pages", [])
    warned = " ".join(tree.run.get("warnings", []))
    unwarned = [p for p in flagged if f"page {p}:" not in warned]
    kvp = scores["kvp"] or {}
    return {
        "kvp_valid": kvp.get("valid", False),
        "kvp_equal": kvp.get("matched") == kvp.get("fields"),
        "injected_found": injected,
        "loop_pages_flagged": sorted(p for p in loop_pages if p in flagged),
        "loop_pages_did_not_loop": sorted(p for p in loop_pages if p not in flagged),
        "degenerate_pages_unwarned": sorted(unwarned),
        # The manifest's pass sentence asks the KVP output to equal the
        # expected JSON; S6 already recorded one honest miss (the order date's
        # format), so equality is reported, and the pass is the safety half.
        "pass": kvp.get("valid", False) and not injected and not unwarned,
    }


# --- Timing (rule 8) ------------------------------------------------------------


def run_metrics(run: dict[str, Any]) -> dict[str, Any]:
    """One run's costs from its `run.json`. Phase A's load is what Phase A spent
    outside Docling's pipeline: building the converter and loading its models."""
    t = run.get("timings_ms", {})
    phase_a = t.get("phase_a", 0.0) / 1000
    return {
        "pages": run.get("page_count", 0),
        "total_s": t.get("total", 0.0) / 1000,
        "phase_a_s": phase_a,
        "phase_a_load_s": max(
            phase_a - t.get("docling_pipeline_total", 0.0) / 1000, 0.0
        ),
        "granite_vision_load_s": t["granite_vision_load"] / 1000
        if "granite_vision_load" in t
        else None,
        "chart_s": [ms / 1000 for ms in run.get("chart_ms", [])],
        "kvp_page_s": [ms / 1000 for ms in run.get("kvp_ms", [])],
        "peak_gb": max(run.get("peak_memory_bytes", {}).values(), default=0) / 1e9,
    }


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def combine_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Timings as the median of the timed runs, memory as their maximum."""
    first = runs[0]
    combined: dict[str, Any] = {"pages": first["pages"], "runs": len(runs)}
    for key in ("total_s", "phase_a_s", "phase_a_load_s"):
        combined[key] = _median([r[key] for r in runs])
    loads = [
        r["granite_vision_load_s"]
        for r in runs
        if r["granite_vision_load_s"] is not None
    ]
    combined["granite_vision_load_s"] = _median(loads)
    # Per chart and per KVP page across the runs, then each one's median.
    for key in ("chart_s", "kvp_page_s"):
        width = min(len(r[key]) for r in runs)
        combined[key] = [
            statistics.median(r[key][i] for r in runs) for i in range(width)
        ]
    combined["peak_gb"] = max(r["peak_gb"] for r in runs)
    combined["spread_pct"] = round(
        100
        * (max(r["total_s"] for r in runs) - min(r["total_s"] for r in runs))
        / max(combined["total_s"] or 1e-9, 1e-9),
        1,
    )
    return combined


# --- Totals ---------------------------------------------------------------------


def _pct(n: float, d: float) -> float | None:
    return round(100 * n / d, 2) if d else None


def totals(scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The corpus-level numbers the baseline and tolerances compare."""
    chart_cells = chart_matched = headers = headers_matched = 0
    charts_found = chart_expected = fps = 0
    numbers = unsupported = pair_errors = 0
    t_cells = t_matched = t_all = t_found = tables_found = tables_expected = 0
    kvp_fields = kvp_matched = kvp_valid = kvp_docs = 0
    head = head_found = head_text = pages_ok = 0
    sims: list[float] = []
    f1s: list[float] = []
    orders: list[float] = []
    over = 0
    over_known = True
    claims = claims_bad = 0
    for s in scores.values():
        for c in s["charts"].values():
            chart_expected += 1
            charts_found += c["predicted"] is not None
            chart_cells += c["cells"]
            chart_matched += c["matched"]
            headers += c["headers"]
            headers_matched += c["headers_matched"]
        fps += len(s["chart_false_positives"])
        for sm in s["summaries"].values():
            numbers += sm["numbers"]
            unsupported += sm["unsupported"]
            pair_errors += sm["pair_errors"]
        for t in s["tables"].values():
            tables_expected += 1
            tables_found += t["predicted"] is not None
            t_cells += t["cells"]
            t_matched += t["matched"]
            t_all += t["all_cells"]
            t_found += t["found"]
        if s["kvp"] is not None:
            kvp_docs += 1
            kvp_fields += s["kvp"]["fields"]
            kvp_matched += s["kvp"]["matched"]
            kvp_valid += s["kvp"]["valid"]
        head += s["headings"]["expected"]
        head_found += s["headings"]["found"]
        head_text += s["headings"]["in_text"]
        pages_ok += s["page_count_ok"]
        for page in s["pages"].values():
            sims.append(page["similarity"])
            f1s.append(page["word_f1"])
            if page["reading_order"] is not None:
                orders.append(page["reading_order"])
        if s["chunks"]["over_limit"] is None:
            over_known = False
        else:
            over += s["chunks"]["over_limit"]
        claims += s["chunks"]["page_claims"]
        claims_bad += s["chunks"]["page_claims_unsupported"]
    return {
        "chart_cells": [chart_matched, chart_cells],
        "chart_cells_pct": _pct(chart_matched, chart_cells),
        "chart_headers_pct": _pct(headers_matched, headers),
        "charts_found": charts_found,
        "charts_expected": chart_expected,
        "chart_false_positives": fps,
        "summary_numbers": numbers,
        "summary_unsupported_pct": _pct(unsupported, numbers),
        "summary_pair_errors": pair_errors,
        "table_cells": [t_matched, t_cells],
        "table_cells_pct": _pct(t_matched, t_cells),
        "table_cells_found": [t_found, t_all],
        "table_cells_found_pct": _pct(t_found, t_all),
        "tables_found": tables_found,
        "tables_expected": tables_expected,
        "kvp_fields_matched": kvp_matched,
        "kvp_fields": kvp_fields,
        "kvp_schema_valid": kvp_valid,
        "kvp_documents": kvp_docs,
        "headings_found": head_found,
        "headings_in_text": head_text,
        "headings_expected": head,
        "page_counts_ok": pages_ok,
        "text_pages": len(sims),
        "text_similarity": round(statistics.fmean(sims), 4) if sims else None,
        "word_f1": round(statistics.fmean(f1s), 4) if f1s else None,
        "reading_order": round(statistics.fmean(orders), 4) if orders else None,
        "chunks_over_limit": over if over_known else None,
        "chunk_pages_unsupported_pct": _pct(claims_bad, claims),
    }


def timing_totals(
    timings: dict[str, dict[str, Any]], entries: dict[str, Entry]
) -> dict[str, Any]:
    """The four §3.11 time figures, over the whole tier."""

    def per_page(scanned: bool) -> float | None:
        names = [n for n in timings if entries[n].scanned == scanned]
        pages = sum(timings[n]["pages"] for n in names)
        return (
            round(sum(timings[n]["phase_a_s"] for n in names) / pages, 3)
            if pages
            else None
        )

    charts = [t for n in timings for t in timings[n]["chart_s"]]
    kvp = [t for n in timings for t in timings[n]["kvp_page_s"]]
    return {
        "phase_a_s_per_page": per_page(False),
        "phase_a_s_per_page_scanned": per_page(True),
        "chart_s": round(statistics.median(charts), 3) if charts else None,
        "kvp_page_s": round(statistics.median(kvp), 3) if kvp else None,
        "peak_gb": round(max((t["peak_gb"] for t in timings.values()), default=0), 2),
        "total_s": round(sum(t["total_s"] for t in timings.values()), 1),
    }


# --- Budgets and the baseline (§3.11, rule 1) -----------------------------------


@dataclass
class Check:
    name: str
    value: Any
    limit: Any
    ok: bool
    note: str = ""


@dataclass
class Verdict:
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def check_budgets(
    budgets: dict[str, Any],
    time_totals: dict[str, Any],
    cold: dict[str, Any] | None,
    capped: dict[str, list[int]],
) -> Verdict:
    verdict = Verdict()
    ceiling = budgets.get("memory", {}).get("peak_gb")
    if ceiling is not None:
        verdict.checks.append(
            Check(
                "peak_gb",
                time_totals["peak_gb"],
                ceiling,
                time_totals["peak_gb"] <= ceiling,
            )
        )
    for key, limit in budgets.get("time", {}).items():
        value = (cold or {}).get(key) if key == "cold_start_s" else time_totals.get(key)
        if value is None:
            verdict.notes.append(f"{key}: no measurement this run (budget {limit})")
            continue
        verdict.checks.append(Check(key, value, limit, value <= limit))
    allowed = {
        name: set(pages) for name, pages in budgets.get("capped_pages", {}).items()
    }
    for name, pages in capped.items():
        extra = sorted(set(pages) - allowed.get(name, set()))
        verdict.checks.append(
            Check(
                f"capped_pages[{name}]",
                pages,
                sorted(allowed.get(name, set())),
                not extra,
                f"new capped page(s) {extra}" if extra else "",
            )
        )
        fixed = sorted(allowed.get(name, set()) - set(pages))
        if fixed:
            verdict.notes.append(
                f"{name}: listed capped page(s) {fixed} no longer cap — a fix"
            )
    return verdict


def check_baseline(
    current: dict[str, Any], baseline: dict[str, Any], tolerance: dict[str, float]
) -> list[Check]:
    """A score fails when it is worse than the baseline by more than its tolerance."""
    checks = []
    for name, (higher, tol_key) in METRICS.items():
        now, then = current.get(name), baseline.get(name)
        if now is None or then is None:
            continue
        tol = tolerance.get(tol_key, 0)
        worse = (then - now) if higher else (now - then)
        checks.append(Check(name, now, then, worse <= tol + 1e-9, f"±{tol}"))
    return checks


# --- Machine and provenance ---------------------------------------------------------


def _command(*args: str) -> str:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return ""


def machine() -> dict[str, Any]:
    """Where the numbers were measured: timings are only comparable on one Mac."""
    battery = _command("pmset", "-g", "batt")
    return {
        "model": _command("sysctl", "-n", "hw.model"),
        "chip": _command("sysctl", "-n", "machdep.cpu.brand_string"),
        "memory_gb": round(int(_command("sysctl", "-n", "hw.memsize") or 0) / 2**30),
        "macos": platform.mac_ver()[0],
        "power": "AC"
        if "AC Power" in battery
        else ("battery" if battery else "unknown"),
    }


def _today() -> str:
    return dt.datetime.now().astimezone().date().isoformat()


def git_sha() -> str:
    return _command("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD") or "unknown"


# --- Running the conversions --------------------------------------------------------


def convert_all(entries: list[Entry], out: Path, force: bool = True) -> None:
    """Each PDF through `convert_many`, with its manifest's schema and pages."""
    from granite_document_intelligence.api import ConvertOptions, convert_many

    for entry in entries:
        kvp = entry.manifest.get("kvp")
        options = (
            ConvertOptions(
                schema=entry.expected / kvp["schema"],
                pages=",".join(str(p) for p in kvp["pages"]),
            )
            if kvp
            else ConvertOptions()
        )
        for item in convert_many([entry.pdf], out, options, force=force):
            if item.status != "done":
                raise SystemExit(f"{entry.pdf.name}: {item.status} ({item.error})")


# --- Reporting ------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3g}" if abs(value) < 100 else f"{value:.0f}"
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) for v in value)
    ):
        return f"{value[0]}/{value[1]}"
    return str(value)


def markdown_report(results: dict[str, Any]) -> str:
    lines = [f"# Eval {results['id']}", ""]
    pub = results["public"]
    lines += [
        "| PDF | pages | chart cells | charts (FP) | table cells | KVP | headings | text sim | capped | Phase A s/page | peak GB |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in pub["pdfs"].items():
        t = pub["timings"].get(name, {})
        cells = [
            sum(c["matched"] for c in s["charts"].values()),
            sum(c["cells"] for c in s["charts"].values()),
        ]
        found = sum(c["predicted"] is not None for c in s["charts"].values())
        tcells = [
            sum(x["matched"] for x in s["tables"].values()),
            sum(x["cells"] for x in s["tables"].values()),
        ]
        kvp = f"{s['kvp']['matched']}/{s['kvp']['fields']}" if s["kvp"] else "—"
        sims = [p["similarity"] for p in s["pages"].values()]
        sim = statistics.fmean(sims) if sims else None
        spp = t["phase_a_s"] / t["pages"] if t.get("pages") else None
        lines.append(
            f"| {name} | {s['page_count']} | {_fmt(cells)} | {found}/{len(s['charts'])} ({len(s['chart_false_positives'])}) "
            f"| {_fmt(tcells)} | {kvp} | {s['headings']['found']}/{s['headings']['expected']} | {_fmt(sim)} "
            f"| {s['capped_pages'] or '—'} | {_fmt(spp)} | {_fmt(t.get('peak_gb'))} |"
        )
    lines += ["", "**Totals (public, adversarial excluded):**", ""]
    lines += [f"- {k}: {_fmt(v)}" for k, v in pub["totals"].items()]
    lines += ["", "**Time (medians):**", ""]
    lines += [f"- {k}: {_fmt(v)}" for k, v in pub["time"].items()]
    if results.get("cold"):
        cold = results["cold"]
        lines.append(
            f"- cold start: {_fmt(cold.get('cold_start_s'))} s "
            f"(Phase A load {_fmt(cold.get('phase_a_load_s'))}, "
            f"Granite Vision load {_fmt(cold.get('granite_vision_load_s'))})"
        )
    if pub.get("adversarial"):
        lines += ["", "**Adversarial:**", ""]
        for name, a in pub["adversarial"].items():
            lines.append(
                f"- {name}: {'pass' if a['pass'] else 'FAIL'} — {json.dumps(a)}"
            )
    if results.get("private"):
        p = results["private"]
        lines += [
            "",
            f"**Private tier:** {p['pdfs']} PDF(s) scored; totals: {json.dumps(p['totals'])}",
        ]
    v = results["verdict"]
    lines += ["", f"**Verdict: {'PASS' if v['ok'] else 'FAIL'}**", ""]
    for c in v["checks"]:
        if not c["ok"] or c["name"].startswith(
            ("peak", "phase", "chart_s", "kvp_page", "cold")
        ):
            lines.append(
                f"- {'ok ' if c['ok'] else 'FAIL'} {c['name']}: {_fmt(c['value'])} (limit {_fmt(c['limit'])}) {c['note']}"
            )
    lines += [f"- note: {n}" for n in v["notes"]]
    if results.get("suggested_time_budgets"):
        lines += ["", "Time budgets this run would set (median × 1.25, §3.11):", ""]
        lines += [f"- {k} = {v}" for k, v in results["suggested_time_budgets"].items()]
    return "\n".join(lines)


# --- Main -----------------------------------------------------------------------

HEADROOM = 1.25  # §3.11


def score_tier(
    entries: list[Entry], run_root: Path, reps: list[str], tokenizer: Any | None
) -> dict[str, Any]:
    """Score the first timed rep's trees; time every timed rep."""
    pdfs: dict[str, Any] = {}
    timings: dict[str, Any] = {}
    adversarial: dict[str, Any] = {}
    deterministic: dict[str, bool] = {}
    for entry in entries:
        trees = [read_tree(run_root / rep / entry.name) for rep in reps]
        scores = score_pdf(entry, trees[0], tokenizer)
        if entry.adversarial is not None:
            adversarial[entry.name] = adversarial_check(entry, trees[0], scores)
        pdfs[entry.name] = scores
        timings[entry.name] = combine_runs([run_metrics(t.run) for t in trees])
        deterministic[entry.name] = len({t.markdown for t in trees}) == 1
    by_name = {e.name: e for e in entries}
    # The adversarial PDF is scored, but kept out of the averages (rule 6).
    averaged = {
        n: s
        for n, s in pdfs.items()
        if not (by_name[n].adversarial or {}).get("exclude_from_averages")
    }
    return {
        "pdfs": pdfs,
        "totals": totals(averaged),
        "timings": timings,
        "time": timing_totals(timings, by_name),
        "adversarial": adversarial,
        "deterministic": deterministic,
        "capped": {n: s["capped_pages"] for n, s in pdfs.items()},
        "versions": trees[0].run.get("versions") if entries else {},
        "models": trees[0].run.get("models") if entries else {},
    }


def build_results(
    args: argparse.Namespace, run_id: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    eval_dir: Path = args.eval_dir
    public = load_corpus(eval_dir, "public")
    private = load_corpus(eval_dir, "private")
    if not public:
        raise SystemExit(f"no corpus under {eval_dir}/expected")
    check_corpus(public + private)
    runs_root = args.runs_dir or eval_dir / "runs"
    reps = [f"{TIMED_PREFIX}{i}" for i in range(1, args.repeats + 1)]
    public_root = runs_root / run_id
    private_root = eval_dir / "private" / "runs" / run_id
    if not args.score_only:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        for rep in ([COLD] if args.cold else []) + reps:
            print(f"== {rep}", file=sys.stderr, flush=True)
            convert_all(public, public_root / rep)
            if private:
                convert_all(private, private_root / rep)
    tokenizer = None
    try:
        tokenizer = _chunk_tokenizer(512)
    except Exception as exc:  # noqa: BLE001 — chunk sizes go unscored, not the run
        print(
            f"chunk tokenizer unavailable, chunk sizes not scored: {exc}",
            file=sys.stderr,
        )

    pub = score_tier(public, public_root, reps, tokenizer)
    cold = None
    if (public_root / COLD).is_dir():
        first = run_metrics(read_tree(public_root / COLD / public[0].name).run)
        gv = next(
            (
                m["granite_vision_load_s"]
                for e in public
                if (m := run_metrics(read_tree(public_root / COLD / e.name).run))[
                    "granite_vision_load_s"
                ]
                is not None
            ),
            None,
        )
        cold = {
            "phase_a_load_s": round(first["phase_a_load_s"], 2),
            "granite_vision_load_s": round(gv, 2) if gv is not None else None,
        }
        cold["cold_start_s"] = round(
            cold["phase_a_load_s"] + (cold["granite_vision_load_s"] or 0), 2
        )

    results: dict[str, Any] = {
        "format": RESULTS_FORMAT,
        "id": run_id,
        "label": args.label,
        "note": args.note,
        "date": _today(),
        "git_sha": git_sha(),
        "machine": machine(),
        "repeats": args.repeats,
        "versions": pub.pop("versions"),
        "models": pub.pop("models"),
        "public": pub,
        "cold": cold,
    }
    private_results = None
    if private:
        priv = score_tier(private, private_root, reps, tokenizer)
        # Only aggregates reach the committed file (rule 7); the per-PDF
        # scores go to eval/private/results/.
        results["private"] = {
            "pdfs": len(private),
            "totals": priv["totals"],
            "time": priv["time"],
        }
        private_results = {
            "id": run_id,
            "private": {k: priv[k] for k in ("pdfs", "totals", "timings", "time")},
        }
    return results, private_results


def judge(
    results: dict[str, Any], budgets: dict[str, Any], budgets_dir: Path
) -> Verdict:
    pub = results["public"]
    verdict = check_budgets(budgets, pub["time"], results.get("cold"), pub["capped"])
    baseline_name = budgets.get("baseline")
    # Relative to budgets.toml, which names it: the corpus may be read from elsewhere.
    baseline_path = budgets_dir / baseline_name if baseline_name else None
    if baseline_path is None:
        verdict.notes.append(
            "no baseline named in budgets.toml: scores recorded, not compared"
        )
    elif baseline_path.stem == results["id"]:
        verdict.notes.append("this run is the baseline: nothing to compare it with")
    elif not baseline_path.is_file():
        # A named baseline that cannot be read must not pass as "no regression".
        verdict.checks.append(Check("baseline", str(baseline_name), "a file", False))
    else:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        verdict.checks += check_baseline(
            pub["totals"], baseline["public"]["totals"], budgets.get("tolerance", {})
        )
    for name, adv in pub["adversarial"].items():
        verdict.checks.append(
            Check(f"adversarial[{name}]", adv["pass"], True, adv["pass"])
        )
        if adv["loop_pages_did_not_loop"]:
            verdict.notes.append(
                f"{name}: loop page(s) {adv['loop_pages_did_not_loop']} did not loop — the loop check went untested"
            )
    for name, same in pub["deterministic"].items():
        if not same:
            verdict.notes.append(f"{name}: document.md differs between timed runs")
    if results["machine"]["power"] != "AC":
        verdict.notes.append(
            f"power: {results['machine']['power']} — timings need AC power (rule 8)"
        )
    return verdict


def private_markers(eval_dir: Path) -> list[str]:
    """What a committed results file must never contain (rule 7)."""
    private = eval_dir / "private"
    markers = [str(private.resolve()), "eval/private"]
    for entry in load_corpus(eval_dir, "private"):
        markers += [entry.name, entry.pdf.name]
    return markers


def write_results(results: dict[str, Any], path: Path, markers: list[str]) -> None:
    leaks = matching.private_strings(results, markers)
    if leaks:
        raise PrivatePathError(
            f"refusing to write {path}: it names the private tier ({leaks[0]!r})"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def suggested_budgets(results: dict[str, Any]) -> dict[str, float]:
    time = results["public"]["time"]
    out = {
        key: round(time[key] * HEADROOM, 2)
        for key in (
            "phase_a_s_per_page",
            "phase_a_s_per_page_scanned",
            "chart_s",
            "kvp_page_s",
        )
        if time.get(key) is not None
    }
    if results.get("cold"):
        out["cold_start_s"] = round(results["cold"]["cold_start_s"] * HEADROOM, 2)
    return out


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--eval-dir",
        type=Path,
        default=EVAL_DIR,
        help="the folder holding corpus/, expected/ and budgets.toml (default: eval/)",
    )
    parser.add_argument(
        "--runs-dir", type=Path, help="where conversions go (default: <eval-dir>/runs)"
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        help="where results go (default: <eval-dir>/results)",
    )
    parser.add_argument("--budgets", type=Path, help="default: <eval-dir>/budgets.toml")
    parser.add_argument("--repeats", type=int, default=3, help="timed runs (rule 8: 3)")
    parser.add_argument(
        "--no-cold", dest="cold", action="store_false", help="skip the cold run"
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="score an existing --run-id instead of converting",
    )
    parser.add_argument("--run-id", help="default: <date>-<git sha>[-<label>]")
    parser.add_argument(
        "--label", default="", help="names the run, e.g. baseline or s6-bf16"
    )
    parser.add_argument(
        "--note",
        default="",
        help="what differs about this run, recorded in the results",
    )
    parser.add_argument(
        "--no-write", dest="write", action="store_false", help="print only"
    )
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    eval_dir: Path = args.eval_dir
    run_id = args.run_id or "-".join(p for p in (_today(), git_sha(), args.label) if p)
    budgets_path = args.budgets or eval_dir / "budgets.toml"
    budgets = (
        tomllib.loads(budgets_path.read_text(encoding="utf-8"))
        if budgets_path.is_file()
        else {}
    )

    results, private_results = build_results(args, run_id)
    results["suggested_time_budgets"] = suggested_budgets(results)
    verdict = judge(results, budgets, budgets_path.parent)
    results["verdict"] = {
        "ok": verdict.ok,
        "checks": [asdict(c) for c in verdict.checks],
        "notes": verdict.notes,
    }
    print(markdown_report(results))
    if args.write:
        results_dir = args.results_dir or eval_dir / "results"
        write_results(
            results, results_dir / f"{run_id}.json", private_markers(eval_dir)
        )
        if private_results is not None:
            write_results(
                private_results, eval_dir / "private" / "results" / f"{run_id}.json", []
            )
        print(f"\nwrote {results_dir / f'{run_id}.json'}", file=sys.stderr)
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    sys.exit(main())

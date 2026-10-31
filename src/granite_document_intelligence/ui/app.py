"""The viewer: six read-only views over one PDF's §3.5 output folder (plan §18).

Layout is §15's. Two rules govern every line below (§18.4):

- **Rule 2.** `document.md` goes through `render.py` and nothing else.
- **Rule 3.** Every other string read from the tree, a file name included, is
  shown only with `st.text`, `st.code`, `st.json` or a dataframe cell. Streamlit
  renders Markdown, images included, in labels, captions, alerts, pills, expander
  titles, metric labels and toasts, so those carry our own fixed strings only.
  The page title and the query string hold no tree data either, because the app
  posts both to any page that frames it (U2).

This is the only module that imports Streamlit (§18.5).
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from granite_document_intelligence.ui import reader, render

st.set_page_config(page_title="Granite Document Intelligence", layout="wide")

# Plain labels, no icons: Streamlit 1.66 binds the *formatted* label to the query
# string, so these are what a link carries (`?view=Charts`).
VIEWS = {
    "document": "Document",
    "tables": "Tables",
    "charts": "Charts",
    "kvp": "Key-values",
    "chunks": "Chunks",
    "run": "Run",
}
DEFAULT_ROOT = "~/gdi-out"  # not ~/Documents, which macOS guards with TCC (§18.3)
COUNT_WIDTH = 130  # §15: four in a row from 574 px of content, 2 × 2 on a phone
CARD_WIDTH = 344  # §15: 1 / 2 / 1-2 / 4-5 per row across the measured widths
PROSE_WIDTH = 760  # §15: a readable line, and a maximum, so phones are unaffected
CHUNKS_PER_PAGE = 50  # §18.3: paged; U4 found no memory reason to page smaller


def _keep_view() -> None:
    # Clicking the selected pill off would leave no view; put the last one back (§15).
    if st.session_state.view is None:
        st.session_state.view = st.session_state.get("_last_view", "document")
    st.session_state._last_view = st.session_state.view


def _csv(path: Path) -> pd.DataFrame | None:
    """A CSV as strings, numbered from 1 so `row N` in a warning is row N here."""
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, ValueError):
        return None
    frame.index = range(1, len(frame) + 1)
    return frame


def _page(page_no: int | None) -> str:
    return f"page {page_no}" if page_no is not None else "page unknown"


# --- Sidebar: which results --------------------------------------------------

with st.sidebar:
    st.subheader("Results", anchor=False)
    root_text = st.text_input("Output folder", DEFAULT_ROOT, key="root")
    root = Path(root_text).expanduser()
    entries = reader.list_runs(root)
    entry = st.selectbox(
        "PDF",
        entries,
        format_func=lambda e: e.name,
        # Per folder: a keyed selectbox keeps its value when its options change,
        # so one keyed once would stay empty after the folder first had no results.
        key=f"entry:{root}",
        help="Folders under the output folder that hold a run.json, newest first.",
    )

st.title("Granite Document Intelligence", anchor=False)

if entry is None:
    st.info(
        "No results in this folder yet. Convert a PDF with "
        "`gdi convert report.pdf -o ~/gdi-out` and it appears here.",
        icon=":material/folder_open:",
    )
    st.stop()

result = reader.read(entry.folder)
st.text(entry.name)

# An error run.json opens on the Run view (§18.3), once per selection.
if st.session_state.get("_shown_entry") != entry.folder:
    st.session_state._shown_entry = entry.folder
    if not result.ok:
        st.session_state.view = "run"

# --- Counts and the notice slot ------------------------------------------------

counts = result.counts()
with st.container(horizontal=True, gap="small", key="counts"):
    for label, key in [
        ("Pages", "pages"),
        ("Tables", "tables"),
        ("Charts", "charts"),
        ("Chunks", "chunks"),
    ]:
        value = counts[key]
        st.metric(
            label, "–" if value is None else value, border=True, width=COUNT_WIDTH
        )

notice = st.empty()
if result.ok:
    notice.success("Converted.", icon=":material/check_circle:")
else:
    notice.error(
        "This PDF failed to convert. Its error is on the Run view.",
        icon=":material/error:",
    )
if result.problems:
    st.warning(
        "Some files in this folder could not be read:", icon=":material/warning:"
    )
    st.text("\n".join(result.problems))

view = st.pills(
    "View",
    list(VIEWS),
    format_func=VIEWS.__getitem__,
    default="document",
    key="view",
    on_change=_keep_view,
    label_visibility="collapsed",
    bind="query-params",
)

# --- The six views (only the selected one runs: §15) -----------------------------


def show_document() -> None:
    if result.document_md is None:
        st.info("No document.md in this folder.", icon=":material/description:")
        return
    markdown = result.document_md.read_text(encoding="utf-8", errors="replace")
    with st.container(width=PROSE_WIDTH, key="prose"):
        for segment in render.segments(markdown, result.folder):
            if isinstance(segment, render.Markdown):
                st.markdown(segment.text)
            elif isinstance(segment, render.Image):
                st.image(str(segment.path), width="content")
            else:
                st.code(segment.text, language=None)


def show_tables() -> None:
    if not result.tables:
        st.info("No tables were found in this PDF.", icon=":material/table:")
        return
    for table in result.tables:
        st.text(f"{table.name} · {_page(table.page_no)}")
        frame = _csv(table.csv)
        if frame is None:
            st.text("This CSV could not be read.")
        else:
            st.dataframe(frame, width="content")


def show_charts() -> None:
    if not result.charts:
        st.info("No charts were found in this PDF.", icon=":material/bar_chart:")
        return
    with st.container(horizontal=True, gap="medium", key="chart_grid"):
        for card in result.charts:
            with st.container(border=True, width=CARD_WIDTH, key=f"card_{card.name}"):
                st.text(f"{card.name} · {_page(card.page_no)}")
                st.image(str(card.png), width="content")
                frame = _csv(card.csv) if card.csv else None
                if frame is not None:
                    st.dataframe(frame, width="stretch")
                elif card.raw:
                    st.caption("No table came back. What the model wrote:")
                    st.code(
                        card.raw.read_text(encoding="utf-8", errors="replace"),
                        language=None,
                    )
                if card.summary:
                    st.text(card.summary)
                if card.warnings:
                    st.caption("Warnings")
                    st.text("\n".join(card.warnings))
                if card.code is not None:
                    # §18.4 rule 4: shown, never run. There is no button to run it.
                    with st.expander("Generated code: untrusted, never run"):
                        st.code(card.code, language="python")


def show_kvp() -> None:
    kvp = result.kvp
    if kvp is None:
        st.info(
            "No key-values: this PDF was converted without a schema (`--schema`).",
            icon=":material/key:",
        )
        return
    if kvp.get("valid") is True:
        st.success("The merged values are valid against the schema.")
    else:
        st.error("The merged values are not valid against the schema.")
    st.caption("Merged values")
    st.json(kvp.get("merged") or {})
    if kvp.get("errors"):
        st.caption("Validation errors")
        st.json(kvp["errors"])
    if kvp.get("conflicts"):
        st.caption("Fields the pages disagree on")
        st.json(kvp["conflicts"])
    st.caption("Each page's answer")
    st.json(kvp.get("pages") or {}, expanded=False)


def show_chunks() -> None:
    if result.chunks_jsonl is None:
        st.info(
            "No chunks: this PDF was converted with `--no-chunks`.",
            icon=":material/segment:",
        )
        return
    page = st.session_state.get("chunk_page", 1)
    rows, total = reader.read_chunks(
        result.chunks_jsonl, (page - 1) * CHUNKS_PER_PAGE, CHUNKS_PER_PAGE
    )
    pages = max(1, -(-total // CHUNKS_PER_PAGE))
    if pages > 1:
        st.number_input("Page of chunks", 1, pages, key="chunk_page")
    first = (page - 1) * CHUNKS_PER_PAGE + 1
    st.dataframe(
        pd.DataFrame(
            {
                "text": [str(r.get("text", "")) for r in rows],
                "headings": [
                    " › ".join(map(str, r.get("headings") or [])) for r in rows
                ],
                "pages": [", ".join(map(str, r.get("page_nos") or [])) for r in rows],
            },
            index=range(first, first + len(rows)),
        ),
        width="stretch",
    )


def _rebuilt_command(run: dict[str, Any]) -> str:
    """`gdi convert` as run.json's options describe it (§18.3: labelled rebuilt)."""
    options = run.get("options") or {}
    argv = ["gdi", "convert", str(run.get("source", "INPUT.pdf")), "-o", "OUT_DIR"]
    if options.get("text_source") == "pdf":
        argv += ["--text-source", "pdf"]
    if options.get("charts") is False:
        argv.append("--no-charts")
    if options.get("chart_code"):
        argv.append("--chart-code")
    if options.get("chunks") is False:
        argv.append("--no-chunks")
    elif options.get("chunk_tokens") not in (None, 512):
        argv += ["--chunk-tokens", str(options["chunk_tokens"])]
    if options.get("save_page_images"):
        argv.append("--save-page-images")
    if options.get("schema"):
        argv += [
            "--schema",
            str(options["schema"]),
            "--pages",
            str(options.get("pages", "1")),
        ]
    return shlex.join(argv)


def show_run() -> None:
    run = result.run
    if not run:
        st.info("run.json could not be read.", icon=":material/monitoring:")
        return
    if not result.ok:
        st.caption("Error")
        st.json(run.get("error") or {})
    warnings = [*(w for c in result.charts for w in c.warnings), *result.run_warnings]
    if warnings:
        st.caption("Warnings")
        st.dataframe(pd.DataFrame({"warning": warnings}), width="stretch")
    timings = run.get("timings_ms") or {}
    if timings:
        st.caption("Timings (s)")
        st.dataframe(
            pd.DataFrame(
                {
                    "seconds": {
                        k: round(v / 1000, 2)
                        for k, v in timings.items()
                        if isinstance(v, int | float)
                    }
                }
            ),
            width="content",
        )
    peaks = run.get("peak_memory_bytes") or {}
    if peaks:
        st.caption("Peak memory (GB)")
        st.dataframe(
            pd.DataFrame(
                {
                    "GB": {
                        k: round(v / 1e9, 2)
                        for k, v in peaks.items()
                        if isinstance(v, int | float)
                    }
                }
            ),
            width="content",
        )
    if run.get("models"):
        st.caption("Models (pinned revisions)")
        st.json(run["models"], expanded=False)
    if run.get("versions"):
        st.caption("Package versions")
        st.json(run["versions"], expanded=False)
    if run.get("options") is not None:
        st.caption("Command line, rebuilt from run.json's options")
        st.code(_rebuilt_command(run), language="bash")


{
    "document": show_document,
    "tables": show_tables,
    "charts": show_charts,
    "kvp": show_kvp,
    "chunks": show_chunks,
    "run": show_run,
}[view or "document"]()

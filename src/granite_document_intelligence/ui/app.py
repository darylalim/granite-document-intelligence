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

import hashlib
import shlex
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from granite_document_intelligence import config
from granite_document_intelligence.ui import reader, render, runner

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
TEXT_SOURCES = {"vlm": "granite-docling", "pdf": "The PDF's text layer"}
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


# --- The server's children (§18.2) -----------------------------------------------


@st.cache_resource
def _registry() -> runner.Registry:
    """One per server: every child it starts, so finished ones are reaped and
    their exit codes kept (U3). Old run and input folders go once, at start."""
    registry = runner.Registry()
    runner.cleanup(registry)
    return registry


registry = _registry()
registry.poll()
state = st.session_state
state.setdefault("run_id", None)
state.setdefault("uploads_n", 0)
state.setdefault("schema_n", 0)
state.setdefault("problem", None)
state.setdefault("confirm_cancel", False)
if state.run_id is None:
    # A reopened tab, or a second one, finds a conversion it did not start (U3).
    found = runner.active_run(registry)
    if found is not None:
        state.run_id = found.run_id
run = runner.read_run(state.run_id, registry) if state.run_id else None
active = run is not None and run.active


def _uploads() -> list[Any]:
    return list(state.get(f"uploads_{state.uploads_n}") or [])


def _schema_upload() -> Any:
    return state.get(f"schema_{state.schema_n}")


def _settings(schema: Path | None) -> runner.Settings:
    return runner.Settings(
        charts=state.s_charts,
        chart_code=state.s_chart_code,
        chunks=state.s_chunks,
        chunk_tokens=int(state.s_chunk_tokens),
        text_source=state.s_text_source,
        schema=schema,
        pages=state.s_pages.strip() or "1",
        save_page_images=state.s_page_images,
        force=state.s_force,
    )


def _start() -> None:
    """Convert's callback: write the uploads to disk, then start the child."""
    state.problem = None
    inputs: list[Path] = []
    typed = state.in_path.strip()
    if typed:
        path = Path(typed).expanduser()
        if not path.exists():
            state.problem = ("Nothing to convert at this path:", typed)
            return
        inputs.append(path)
    uploads = _uploads()
    clashes = runner.stem_clashes([u.name for u in uploads])
    if clashes:
        state.problem = (
            "Two uploads would write the same output folder. Rename one:",
            "\n".join(f"{a}  ·  {b}" for a, b in clashes),
        )
        return
    # On disk before the run, and out of the uploader after it: Streamlit holds
    # an upload in the server's memory at about twice its size (U4).
    inputs += [runner.save_upload(u.name, u.getvalue()) for u in uploads]
    schema_upload = _schema_upload()
    if schema_upload is not None:
        # A stable path, since resume compares the schema's path (§18.3).
        schema = runner.save_upload(
            schema_upload.name, schema_upload.getvalue(), "schema.json"
        )
        state.s_schema_path = str(schema)
        state.schema_n += 1
    if not inputs:
        return
    typed_schema = state.s_schema_path.strip()
    schema = Path(typed_schema).expanduser() if typed_schema else None
    state.run_id = runner.start(
        inputs, Path(state.root).expanduser(), _settings(schema), registry
    )
    state.uploads_n += 1
    state.confirm_cancel = False


def _ask_cancel() -> None:
    state.confirm_cancel = True


def _cancel() -> None:
    state.confirm_cancel = False
    if state.run_id:
        runner.cancel(state.run_id, registry)


def _keep_running() -> None:
    state.confirm_cancel = False


# --- Sidebar: results and settings -----------------------------------------------

if state.pop("_select_newest", False):
    # A finished run's PDF is the newest entry: show it (see `_notice`).
    for key in [k for k in state if str(k).startswith("entry:")]:
        del state[key]

with st.sidebar:
    st.subheader("Results", anchor=False)
    root_text = st.text_input(
        "Output folder",
        DEFAULT_ROOT,
        key="root",
        help="Where conversions are written, and whose results are listed below.",
    )
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

    # One control per `gdi convert` flag, so a UI run is a terminal run (§18.3).
    # None is bound to the query string: a page that frames the UI cannot preset
    # a setting through the URL (§18.4 rule 1's third residual).
    st.subheader("Conversion", anchor=False)
    st.toggle("Chart data and summaries", True, key="s_charts")
    st.toggle(
        "Chart code",
        False,
        key="s_chart_code",
        disabled=not state.s_charts,
        help="Also write each chart as Python. It is untrusted model output: "
        "shown, never run.",
    )
    st.toggle("Chunks for RAG", True, key="s_chunks")
    st.number_input(
        "Chunk tokens",
        config.CHUNK_TOKENS_MIN,
        config.CHUNK_TOKENS_MAX,
        config.CHUNK_TOKENS_DEFAULT,
        step=64,
        key="s_chunk_tokens",
        disabled=not state.s_chunks,
    )
    st.selectbox(
        "Text source",
        ["vlm", "pdf"],
        format_func=TEXT_SOURCES.__getitem__,
        key="s_text_source",
        help="The PDF's own text layer suits born-digital PDFs you trust.",
    )
    st.toggle("Save page images", False, key="s_page_images")
    st.toggle(
        "Convert again",
        False,
        key="s_force",
        help="Convert even PDFs already converted with these settings (--force).",
    )

    st.subheader("Key-values", anchor=False)
    st.text_input(
        "Schema",
        key="s_schema_path",
        placeholder="~/schemas/fields.json",
        help="A JSON Schema whose fields are extracted into kvp.json. Resume "
        "compares the schema's path as well as its content, so the same schema "
        "at another path converts again.",
    )
    st.file_uploader("Or upload one", type="json", key=f"schema_{state.schema_n}")
    has_schema = bool(state.s_schema_path.strip()) or _schema_upload() is not None
    st.text_input(
        "Pages",
        "1",
        key="s_pages",
        disabled=not has_schema,
        help="Pages to extract key-values from: 1, 1,3-4 or all.",
    )

st.title("Granite Document Intelligence", anchor=False)

# --- Inputs and the Convert row --------------------------------------------------

with st.container(key="inputs"):
    st.text_input(
        "PDF or folder to convert",
        key="in_path",
        placeholder="~/Downloads/report.pdf",
        disabled=active,
        help="A PDF, or a folder whose PDFs are converted (not its subfolders'). "
        "Nothing is copied.",
    )
    uploads = st.file_uploader(
        "Or upload PDFs",
        type="pdf",
        accept_multiple_files=True,
        key=f"uploads_{state.uploads_n}",
        disabled=active,
        help="Up to 200 MB each; give a larger PDF by its path.",
    )
    clashes = runner.stem_clashes([u.name for u in uploads or []])
    if uploads:
        if clashes:
            st.error(
                "Two uploads would write the same output folder. Rename one:",
                icon=":material/error:",
            )
            st.text("\n".join(f"{a}  ·  {b}" for a, b in clashes))
        replaced = [
            u.name
            for u in uploads
            if runner.replaces(root, u.name, hashlib.sha256(u.getvalue()).hexdigest())
        ]
        if replaced:
            st.warning(
                "A different file under the same name was converted before. "
                "Converting replaces its results:",
                icon=":material/warning:",
            )
            st.text("\n".join(replaced))

with st.container(horizontal=True, vertical_alignment="center", key="run_row"):
    if active and state.confirm_cancel:
        st.button("Stop it", type="primary", on_click=_cancel, key="stop")
        st.button("Keep converting", on_click=_keep_running, key="keep")
        st.caption("Stop the conversion? PDFs that finished are kept.")
    elif active:
        # Two clicks, so a page that frames the UI cannot stop a run with one
        # lured click (§18.4 rule 1's third residual, weighed in PR 14).
        st.button("Cancel", icon=":material/stop:", on_click=_ask_cancel, key="cancel")
        st.caption("Converting. Closing this tab does not stop it.")
    else:
        count = len(uploads or []) + (1 if state.get("in_path", "").strip() else 0)
        st.button(
            "Convert",
            icon=":material/play_arrow:",
            type="primary",
            disabled=count == 0 or bool(clashes),
            on_click=_start,
            key="convert",
        )
        parts = [
            name
            for name, on in [
                ("charts", state.s_charts),
                ("tables", True),
                ("chunks", state.s_chunks),
                ("key-values", has_schema),
            ]
            if on
        ]
        st.caption(
            ("Choose a PDF above" if count == 0 else f"{count} input(s)")
            + " · "
            + ", ".join(parts)
        )

# --- The notice slot (one notice: §18.3) ------------------------------------------


def _run_notice(run: runner.Run) -> None:
    """Our own fixed strings, with ints we checked; anything from the events
    file beneath them as text (§18.4 rule 3)."""
    p = run.progress
    if run.state == "starting":
        st.info("Starting the conversion…", icon=":material/hourglass_top:")
    elif run.state == "waiting":
        holder = f" (pid {p.holder_pid})" if p.holder_pid is not None else ""
        st.info(
            f"Waiting for another gdi run{holder} to finish. "
            "Only one conversion runs at a time.",
            icon=":material/hourglass_top:",
        )
    elif run.state == "running":
        total, position = p.batch_total or 1, p.position or 1
        stage, fraction = "loading the models", 0.0
        if p.kvp:
            stage, fraction = f"key-values page {p.kvp[0]} of {p.kvp[1]}", 1.0
        elif p.chart:
            stage, fraction = f"chart {p.chart[0]} of {p.chart[1]}", 1.0
        elif p.page:
            stage = f"page {p.page[0]} of {p.page[1]}"
            fraction = p.page[0] / p.page[1] if p.page[1] else 0.0
        st.progress(
            min(1.0, (position - 1 + fraction) / total),
            text=f"PDF {position} of {total} · {stage}",
        )
        if p.pdf is not None:
            st.text(p.pdf)
    elif run.state == "done":
        s = p.summary or {}
        st.success(
            f"Finished: {s.get('done', 0)} converted, {s.get('skipped', 0)} "
            "already done.",
            icon=":material/check_circle:",
        )
    elif run.state == "failed":
        s = p.summary or {}
        st.error(
            f"Finished, but {s.get('failed', 0)} PDF(s) failed: each one's error "
            "is on its Run view.",
            icon=":material/error:",
        )
        failed = [pdf for pdf, status in p.results.items() if status == "failed"]
        st.text("\n".join(failed))
    elif run.state == "interrupted":
        st.warning(
            "Cancelled. PDFs that finished are kept.", icon=":material/stop_circle:"
        )
    elif run.state == "refused":
        st.error(
            {
                "usage": "The conversion did not start: its inputs or settings "
                "were refused.",
                "models": "The models are not ready. Run `gdi convert-model` once "
                "in a terminal, then convert again.",
                "locked": "Another gdi run holds the run lock.",
            }.get(p.error_kind or "", "The conversion did not start."),
            icon=":material/error:",
        )
        if p.error_message:
            st.text(p.error_message)
    elif run.state == "crashed":
        code = f" (exit {run.returncode})" if run.returncode is not None else ""
        st.error(
            f"The conversion crashed{code} during this PDF. Its folder keeps the "
            "previous results, if any:",
            icon=":material/error:",
        )
        st.text(run.crashed_pdf or "")
    else:
        st.warning(
            "The conversion ended without a summary: it crashed, was killed, or "
            "outlived a restart of this server.",
            icon=":material/help:",
        )


def _notice(was_active: bool) -> None:
    registry.poll()
    now = runner.read_run(state.run_id, registry) if state.run_id else None
    problem = state.problem
    if problem is not None:
        st.error(problem[0], icon=":material/error:")
        st.text(problem[1])
    elif now is not None:
        _run_notice(now)
    elif entry is not None and result.ok:
        st.success("Converted.", icon=":material/check_circle:")
    elif entry is not None:
        st.error(
            "This PDF failed to convert. Its error is on the Run view.",
            icon=":material/error:",
        )
    if was_active and (now is None or not now.active):
        # Finished while this fragment polled: refresh the listing and the
        # buttons, and show the run's newest PDF.
        state._select_newest = True
        st.rerun(scope="app")


result = reader.read(entry.folder) if entry is not None else None
# Polls once a second while a run is active, and only this slot reruns (§15).
st.fragment(_notice, run_every=1.0 if active else None)(active)

if entry is None or result is None:
    st.info(
        "No results in this folder yet. Convert a PDF above, or with "
        "`gdi convert report.pdf -o ~/gdi-out`, and it appears here.",
        icon=":material/folder_open:",
    )
    st.stop()

# The view lives in session state alone: a `default=` beside the Run switch below
# makes Streamlit warn on every rerun. A `?view=` link still wins (U2-safe: only
# the six fixed labels are ever bound).
st.session_state.setdefault("view", "document")
# An error run.json opens on the Run view (§18.3), once per selection.
if st.session_state.get("_shown_entry") != entry.folder:
    st.session_state._shown_entry = entry.folder
    if not result.ok:
        st.session_state.view = "run"

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

st.text(entry.name)
if result.problems:
    st.warning(
        "Some files in this folder could not be read:", icon=":material/warning:"
    )
    st.text("\n".join(result.problems))

view = st.pills(
    "View",
    list(VIEWS),
    format_func=VIEWS.__getitem__,
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
                    # Model-written Markdown, so through the neutraliser like the
                    # document (§18.4 rule 2): bold and lists render, nothing fetches.
                    for segment in render.segments(card.summary, result.folder):
                        if isinstance(segment, render.Markdown):
                            st.markdown(segment.text)
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
        st.dataframe(
            pd.DataFrame({"warning": warnings}, index=range(1, len(warnings) + 1)),
            width="stretch",
        )
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
    ui_run = runner.run_for(result.folder, registry)
    argv = runner.argv_of(ui_run) if ui_run is not None else None
    if ui_run is not None and argv is not None:
        # The exact command, with `python -m granite_document_intelligence.cli`
        # shown as the `gdi` it is (§18.3).
        st.caption("Command line, as this UI ran it")
        st.code(shlex.join(["gdi", *argv[len(runner.CHILD) :]]), language="bash")
        with st.expander("The conversion's log (stderr)"):
            # Library noise and our own progress; text only (§18.4 rule 3).
            st.code(runner.tail(ui_run.folder / runner.STDERR), language=None)
    elif run.get("options") is not None:
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

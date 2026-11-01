"""The viewer's script, under Streamlit's `AppTest` (plan §15, §18.3, §18.4).

The layout and view tests are marked `apptest`: CI runs them and the Stop hook
skips them, which keeps the hook's gate within 15 s (§18.5, decided with the user).
The hostile-tree test is a security test and runs in both.

One output root is built once per session through the real `export.save()`: a
report, a failed PDF and a hostile one, whose folder name, document, warnings, KVP
errors and chunk headings all carry `MARK`.
"""

import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_export import KVP, make_result
from test_ui_reader import _chunks, _mark_charts

from granite_document_intelligence import export
from granite_document_intelligence.api import Chunk, KvpResult
from granite_document_intelligence.ui import launch, render

MARK = "HOSTILE-7Q"
HOSTILE_NAME = f"![{MARK}](http:evil.example) [{MARK}](javascript:x) :red[{MARK}]"
HOSTILE_MD = f"""# {MARK} ![i](http://evil.example/{MARK}.png)

[{MARK}](http://evil.example) <http://evil.example/{MARK}> $5 and $3 :red[{MARK}]
<img src="http://evil.example/{MARK}-raw.png">
"""
HOSTILE_TEXT = f"![{MARK}](http://evil.example/{MARK}.png) :material/home: $1 and $2"


@pytest.fixture(scope="session")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("gdi-out")
    report = make_result(charts=2, kvp=KVP, chunks=_chunks(3))
    _mark_charts(report)
    export.save(report, root / "report")

    hostile = make_result(
        charts=1,
        kvp=KvpResult(
            pages={1: {"name": HOSTILE_TEXT}},
            merged={"name": HOSTILE_TEXT},
            conflicts=[],
            valid=False,
            errors=[HOSTILE_TEXT],
            raw={1: HOSTILE_TEXT},
        ),
        chunks=[
            Chunk(
                id="chunk-0001",
                text=HOSTILE_TEXT,
                contextualized=HOSTILE_TEXT,
                headings=[HOSTILE_TEXT],
                page_nos=[1],
                refs=[],
            )
        ],
        summary=HOSTILE_TEXT,
    )
    _mark_charts(hostile)
    hostile.run.warnings = [f"chart-001 (page 1): {HOSTILE_TEXT}", HOSTILE_TEXT]
    export.save(hostile, root / HOSTILE_NAME)
    (root / HOSTILE_NAME / "document.md").write_text(HOSTILE_MD)

    error = {"type": "RuntimeError", "message": "boom", "frames": []}
    export.save_error(root / "broken", Path("broken.pdf"), error, None, {})
    # The selector lists newest first: make the report the default selection.
    for age, name in enumerate(["report", HOSTILE_NAME, "broken"]):
        stamp = 2_000_000_000 - age * 1_000
        os.utime(root / name / "run.json", (stamp, stamp))
    return root


@pytest.fixture(autouse=True)
def _ui_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The app lists and cleans `$GDI_CACHE_DIR/ui/`: never the real one.
    monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))


def _app(root: Path, entry: str | None = "report", view: str | None = None) -> Any:
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(launch.app_path()), default_timeout=30)
    if view:
        at.query_params["view"] = view
    # Before the first run, as a returning user's folder would be: `?view=` is
    # read when the pills are first created.
    at.session_state["root"] = str(root)
    at.run()
    if entry is not None:
        select = at.selectbox(key=f"entry:{root}")
        if select.format_func(select.value) != entry:
            select.select_index(select.options.index(entry)).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _show(at: Any, view: str) -> Any:
    at.button_group(key="view").set_value(view).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.mark.apptest
class TestLayout:
    def test_an_empty_folder_says_how_to_fill_it(self, tmp_path: Path) -> None:
        at = _app(tmp_path, entry=None)
        assert "gdi convert" in at.info[0].value
        assert not at.metric

    def test_counts_come_from_run_json(self, root: Path) -> None:
        at = _app(root)
        assert [(m.label, m.value) for m in at.metric] == [
            ("Pages", "1"),
            ("Tables", "1"),
            ("Charts", "2"),
            ("Chunks", "3"),
        ]
        assert at.success[0].value == "Converted."

    def test_deselecting_the_pill_keeps_the_view(self, root: Path) -> None:
        at = _show(_app(root), "tables")
        at.button_group(key="view").set_value(None).run()
        assert at.button_group(key="view").value == "tables"
        assert len(at.dataframe) == 1

    def test_only_the_selected_view_runs(self, root: Path) -> None:
        # Pills make views lazy (§15): the Tables view renders no chart card.
        at = _show(_app(root), "tables")
        assert len(at.dataframe) == 1
        assert not any("chart-001" in t.value for t in at.text)

    def test_the_query_string_seeds_the_view(self, root: Path) -> None:
        # The bound value is the pill's label (Streamlit 1.66), so links read well.
        at = _app(root, view="Charts")
        assert at.button_group(key="view").value == "charts"
        assert any(t.value.startswith("chart-001 · page 1") for t in at.text)

    def test_a_failed_pdf_opens_on_the_run_view(self, root: Path) -> None:
        at = _app(root, entry="broken")
        assert at.button_group(key="view").value == "run"
        assert "failed" in at.error[0].value
        assert at.json[0].value == json.dumps(
            {"type": "RuntimeError", "message": "boom", "frames": []}
        )


@pytest.mark.apptest
class TestViews:
    def test_document(self, root: Path) -> None:
        at = _app(root)
        assert at.markdown and all(md.value for md in at.markdown)

    def test_tables(self, root: Path) -> None:
        at = _show(_app(root), "tables")
        assert at.text[1].value == "table-001 · page 1"
        assert len(at.dataframe) == 1

    def test_charts(self, root: Path) -> None:
        at = _show(_app(root), "charts")
        texts = [t.value for t in at.text]
        assert "chart-001 · page 1" in texts and "chart-002 · page 1" in texts
        assert "Revenue is highest in the North." in [md.value for md in at.markdown]
        frame = at.dataframe[0].value
        # Numbered from 1, so a warning's `row N` is row N here.
        assert list(frame.index) == [1] and frame.iloc[0].tolist() == ["North", "120"]

    def test_key_values(self, root: Path) -> None:
        at = _show(_app(root), "kvp")
        assert at.success and json.loads(at.json[0].value) == KVP.merged

    def test_chunks(self, root: Path) -> None:
        at = _show(_app(root), "chunks")
        frame = at.dataframe[0].value
        assert list(frame.index) == [1, 2, 3]
        assert frame["text"].tolist() == ["text 1", "text 2", "text 3"]

    def test_run(self, root: Path) -> None:
        at = _show(_app(root), "run")
        assert "gdi convert report.pdf -o OUT_DIR" in at.code[0].value


def _markdown_bearing(at: Any) -> Iterator[tuple[str, str]]:
    """Every string Streamlit renders as Markdown: labels, captions, alerts,
    titles, metric labels, pill options and Markdown bodies."""
    for kind in ("markdown", "caption", "title", "subheader", "header"):
        for el in getattr(at, kind):
            yield kind, el.value
    for kind in ("info", "warning", "error", "success", "toast"):
        for el in getattr(at, kind):
            yield kind, el.value
    for el in at.metric:
        yield "metric label", el.label
    for el in at.expander:
        yield "expander label", el.label
    for kind in ("text_input", "selectbox", "number_input", "button_group"):
        for el in getattr(at, kind):
            yield f"{kind} label", el.label
    for el in at.button_group:
        yield "pill options", " ".join(str(o) for o in el.options)


class TestHostileTree:
    """§18.4 rules 2 and 3: nothing the tree holds becomes markup, and all of it
    is still shown, as text. One session walks the six views (§3.14 rule 9)."""

    def test_no_view_turns_a_tree_string_into_markup(self, root: Path) -> None:
        at = _app(root, entry=HOSTILE_NAME)
        assert at.text[0].value == HOSTILE_NAME  # the folder name, as text
        # The two Markdown sources rule 2 allows: the document and chart summaries.
        document = (root / HOSTILE_NAME / "document.md").read_text()
        neutralised = {
            s.text.strip()
            for source in (document, HOSTILE_TEXT)
            for s in render.segments(source, root / HOSTILE_NAME)
            if isinstance(s, render.Markdown)
        }
        for view in ["document", "tables", "charts", "kvp", "chunks", "run"]:
            if view != "document":
                _show(at, view)
            leaks = [
                (kind, value)
                for kind, value in _markdown_bearing(at)
                if MARK in value
                and not (kind == "markdown" and value.strip() in neutralised)
            ]
            assert not leaks, view
            if view in {"charts", "kvp", "chunks", "run"}:
                # Rule 3 is "as text", not "hidden".
                shown = [t.value for t in at.text] + [j.value for j in at.json]
                shown += [md.value for md in at.markdown]
                shown += [df.value.to_string() for df in at.dataframe]
                assert any(MARK in value for value in shown), view


# --- Converting (§18.2, §18.3) -----------------------------------------------------

STUB = Path(__file__).with_name("ui_child_stub.py")


def _event(event: str, **fields: object) -> str:
    return json.dumps({"v": 1, "event": event, **fields}) + "\n"


def _converting(
    at: Any, monkeypatch: pytest.MonkeyPatch, **stub: str
) -> tuple[Any, Path]:
    """`at` with the stub as its child, and the run folder Convert made."""
    from granite_document_intelligence.ui import runner

    monkeypatch.setattr(runner, "CHILD", (sys.executable, str(STUB)))
    for name, value in stub.items():
        monkeypatch.setenv(f"STUB_CHILD_{name.upper()}", value)
    at.button(key="convert").click().run()
    assert not at.exception, [e.value for e in at.exception]
    (run_id,) = runner.run_ids()
    return at, runner.runs_dir() / run_id


def _until(at: Any, check: Any, timeout: float = 10) -> Any:
    """Rerun `at` until `check(at)` holds: the stub runs as a real child."""
    deadline = time.monotonic() + timeout
    while not check(at):
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)
        at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


DONE = (
    _event("batch", total=1)
    + _event("pdf", position=1, total=1, pdf="/in/a.pdf", out_dir="/out/a")
    + _event("result", pdf="/in/a.pdf", status="done")
    + _event("summary", done=1, skipped=0, failed=0)
)


@pytest.mark.apptest
class TestConvert:
    def test_convert_waits_for_an_input(self, root: Path) -> None:
        at = _app(root)
        assert at.button(key="convert").disabled

    def test_a_typed_path_converts_with_the_sidebars_flags(
        self, root: Path, blank_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _app(root)
        at.text_input(key="in_path").set_value(str(blank_pdf))
        at.toggle(key="s_charts").set_value(False)
        at.toggle(key="s_force").set_value(True).run()
        at, folder = _converting(at, monkeypatch, events=DONE)
        argv = json.loads((folder / "argv.json").read_text())
        assert argv[2:] == [
            "convert",
            "-o",
            str(root),
            "--no-charts",
            "--force",
            "--events",
            str(folder / "events.jsonl"),
            "--",
            str(blank_pdf),
        ]
        _until(at, lambda at: at.success)
        assert at.success[0].value == "Finished: 1 converted, 0 already done."
        assert at.button(key="convert")  # the row is back to Convert

    def test_a_missing_path_is_refused_before_anything_starts(
        self, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from granite_document_intelligence.ui import runner

        at = _app(root)
        at.text_input(key="in_path").set_value(str(tmp_path / "nope.pdf")).run()
        at.button(key="convert").click().run()
        assert "Nothing to convert" in at.error[0].value
        assert runner.run_ids() == []

    def test_uploads_go_to_disk_and_leave_the_uploader(
        self, root: Path, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from granite_document_intelligence.ui import runner

        at = _app(root)
        at.file_uploader(key="uploads_0").set_value(
            [("../a.pdf", pdf_bytes, "application/pdf")]
        ).run()
        at, folder = _converting(at, monkeypatch, events=DONE)
        argv = json.loads((folder / "argv.json").read_text())
        saved = Path(argv[-1])
        assert saved.parent.parent == runner.inputs_dir() and saved.name == "a.pdf"
        assert saved.read_bytes() == pdf_bytes
        # A new key: Streamlit holds an upload in memory at twice its size (U4).
        assert at.file_uploader(key="uploads_1").value in (None, [])

    def test_two_uploads_with_one_stem_are_refused(
        self, root: Path, pdf_bytes: bytes
    ) -> None:
        at = _app(root)
        at.file_uploader(key="uploads_0").set_value(
            [
                ("Report.pdf", pdf_bytes, "application/pdf"),
                ("report.PDF", pdf_bytes + b" ", "application/pdf"),
            ]
        ).run()
        assert "same output folder" in at.error[0].value
        assert at.button(key="convert").disabled

    def test_an_upload_that_replaces_a_result_says_so(
        self, root: Path, pdf_bytes: bytes
    ) -> None:
        at = _app(root)
        at.file_uploader(key="uploads_0").set_value(
            [("report.pdf", pdf_bytes, "application/pdf")]
        ).run()
        assert "replaces its results" in at.warning[0].value

    def test_cancel_takes_two_clicks_and_is_ctrl_c(
        self, root: Path, blank_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _app(root)
        at.text_input(key="in_path").set_value(str(blank_pdf)).run()
        at, _ = _converting(at, monkeypatch, events=_event("batch", total=1), hold="30")
        _until(at, lambda at: at.get("progress"))
        at.button(key="cancel").click().run()
        assert at.get("progress")  # one click only asks
        at.button(key="stop").click().run()
        _until(at, lambda at: at.warning)
        assert at.warning[0].value == "Cancelled. PDFs that finished are kept."

    def test_a_second_tab_finds_the_running_conversion(
        self, root: Path, blank_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _app(root)
        at.text_input(key="in_path").set_value(str(blank_pdf)).run()
        at, _ = _converting(
            at,
            monkeypatch,
            events=_event("waiting", holder_pid=4711),
            hold="30",
        )
        other = _until(_app(root), lambda at: at.info)
        assert "Waiting for another gdi run (pid 4711)" in other.info[0].value
        other.button(key="cancel").click().run()
        other.button(key="stop").click().run()
        _until(other, lambda at: at.warning)

    def test_a_crash_names_its_pdf(
        self, root: Path, blank_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _app(root)
        at.text_input(key="in_path").set_value(str(blank_pdf)).run()
        events = _event("batch", total=1) + _event(
            "pdf", position=1, total=1, pdf="/in/a.pdf", out_dir="/out/a"
        )
        at, _ = _converting(at, monkeypatch, events=events, signal="9")
        _until(at, lambda at: at.error)
        assert at.error[0].value.startswith("The conversion crashed (exit -9)")
        assert "/in/a.pdf" in [t.value for t in at.text]

    def test_the_run_view_shows_the_command_this_ui_ran(
        self, tmp_path: Path, blank_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "out"
        export.save(make_result(), out / "blank")
        at = _app(out, entry="blank")
        at.text_input(key="in_path").set_value(str(blank_pdf)).run()
        events = DONE.replace("/out/a", str(out / "blank"))
        at, _ = _converting(at, monkeypatch, events=events, stderr="[1/1] blank\n")
        _until(at, lambda at: at.success)
        _show(at, "run")
        assert at.code[0].value.startswith(f"gdi convert -o {out} --events ")
        assert at.code[1].value.strip() == "[1/1] blank"


class TestHostileEvents:
    """§18.4 rule 3 for the events file and the child's stderr: their strings
    are shown, as text, and never as markup."""

    def test_no_event_string_becomes_markup(
        self, root: Path, blank_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _app(root)
        at.text_input(key="in_path").set_value(str(blank_pdf)).run()
        events = (
            _event("batch", total=2)
            + _event("pdf", position=1, total=2, pdf=HOSTILE_TEXT, out_dir=MARK)
            + _event("result", pdf=HOSTILE_TEXT, status="failed")
            + _event("summary", done=0, skipped=0, failed=1)
        )
        at, _ = _converting(at, monkeypatch, events=events, stderr=HOSTILE_TEXT)
        _until(at, lambda at: at.error)
        assert not [(k, v) for k, v in _markdown_bearing(at) if MARK in v]
        assert HOSTILE_TEXT in [t.value for t in at.text]

        refused = _event("error", kind="usage", message=HOSTILE_TEXT)
        monkeypatch.setenv("STUB_CHILD_EVENTS", refused)
        at.button(key="convert").click().run()
        _until(at, lambda at: any("did not start" in e.value for e in at.error))
        assert not [(k, v) for k, v in _markdown_bearing(at) if MARK in v]
        assert HOSTILE_TEXT in [t.value for t in at.text]

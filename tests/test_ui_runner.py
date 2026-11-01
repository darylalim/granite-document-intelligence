"""The UI's runner (plan §18.2, §18.3): the child, its run folder, its state.

The child is `tests/ui_child_stub.py`, put in through `runner.CHILD` (§18.5's
seam) and driven by `$STUB_CHILD_*` variables. Every test has its own
`$GDI_CACHE_DIR` under `tmp_path`, so the run folders never meet.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from granite_document_intelligence import config
from granite_document_intelligence.ui import runner

STUB = Path(__file__).with_name("ui_child_stub.py")


def _event(event: str, **fields: object) -> str:
    return json.dumps({"v": 1, "event": event, **fields}) + "\n"


DONE = (
    _event("batch", total=1)
    + _event("pdf", position=1, total=1, pdf="/in/a.pdf", out_dir="/out/a")
    + _event("page", completed=1, total=1)
    + _event("result", pdf="/in/a.pdf", status="done")
    + _event("summary", done=1, skipped=0, failed=0)
)


@pytest.fixture(autouse=True)
def _cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(runner, "CHILD", (sys.executable, str(STUB)))
    for name in list(os.environ):
        if name.startswith("STUB_CHILD_"):
            monkeypatch.delenv(name)


def _wait(registry: runner.Registry, run_id: str, timeout: float = 10) -> runner.Run:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        registry.poll()
        run = runner.read_run(run_id, registry)
        if not run.active:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} still {run.state}")


def _start(tmp_path: Path, registry: runner.Registry) -> str:
    return runner.start(
        [tmp_path / "a.pdf"], tmp_path / "out", runner.Settings(), registry
    )


# --- The command line ------------------------------------------------------------


class TestConvertArgv:
    def test_defaults_add_no_flag(self, tmp_path: Path) -> None:
        argv = runner.convert_argv(
            [tmp_path / "a.pdf"], tmp_path / "out", runner.Settings(), Path("/e")
        )
        assert argv == [
            "-o",
            str(tmp_path / "out"),
            "--events",
            "/e",
            "--",
            str(tmp_path / "a.pdf"),
        ]

    def test_every_setting_maps_to_its_flag(self, tmp_path: Path) -> None:
        settings = runner.Settings(
            chart_code=True,
            chunk_tokens=256,
            text_source="pdf",
            schema=tmp_path / "fields.json",
            pages="1,3-4",
            save_page_images=True,
            force=True,
        )
        argv = runner.convert_argv([Path("a.pdf")], Path("out"), settings, Path("/e"))
        flags = argv[2 : argv.index("--events")]
        assert flags == [
            "--text-source",
            "pdf",
            "--chart-code",
            "--chunk-tokens",
            "256",
            "--save-page-images",
            "--schema",
            str(tmp_path / "fields.json"),
            "--pages",
            "1,3-4",
            "--force",
        ]

    def test_off_switches_drop_their_dependents(self) -> None:
        settings = runner.Settings(
            charts=False, chart_code=True, chunks=False, chunk_tokens=256
        )
        argv = runner.convert_argv([Path("a.pdf")], Path("out"), settings, Path("/e"))
        assert "--no-charts" in argv and "--no-chunks" in argv
        assert "--chart-code" not in argv and "--chunk-tokens" not in argv

    def test_inputs_are_absolute_and_after_the_separator(self) -> None:
        # A name that starts with a dash can never read as a flag (§18.4 rule 5).
        argv = runner.convert_argv(
            [Path("-rf.pdf")], Path("~/out"), runner.Settings(), Path("/e")
        )
        assert argv[-2] == "--"
        assert argv[-1] == str(Path.cwd() / "-rf.pdf")
        assert argv[1] == str(Path.home() / "out")

    def test_the_cli_accepts_what_the_runner_builds(self, tmp_path: Path) -> None:
        from granite_document_intelligence import cli

        settings = runner.Settings(
            chart_code=True, schema=tmp_path / "s.json", force=True
        )
        argv = runner.convert_argv(
            [tmp_path / "a.pdf"], tmp_path / "out", settings, tmp_path / "e"
        )
        args = cli._parser().parse_args(["convert", *argv])
        assert args.inputs == [tmp_path / "a.pdf"]
        assert args.chart_code and args.force and args.schema == tmp_path / "s.json"
        assert args.events == tmp_path / "e"


# --- The child -------------------------------------------------------------------


class TestStart:
    def test_the_run_folder_holds_what_section_18_2_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STUB_CHILD_EVENTS", DONE)
        monkeypatch.setenv("STUB_CHILD_STDERR", "[1/1] a.pdf\n")
        registry = runner.Registry()
        run_id = _start(tmp_path, registry)
        run = _wait(registry, run_id)
        folder = runner.runs_dir() / run_id
        assert run.state == "done" and run.returncode == 0
        argv = json.loads((folder / "argv.json").read_text())
        assert argv[:3] == [sys.executable, str(STUB), "convert"]
        assert argv[argv.index("--events") + 1] == str(folder / "events.jsonl")
        assert int((folder / "pid").read_text()) > 1
        assert (folder / "started").read_text().strip()
        assert (folder / "stderr.txt").read_text() == "[1/1] a.pdf\n"
        assert (folder / "stdout.txt").exists()
        assert runner.argv_of(run) == argv

    def test_the_child_leads_its_own_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Load-bearing (U3): otherwise Ctrl-C in the server's terminal cancels it.
        monkeypatch.setenv("STUB_CHILD_HOLD", "5")
        registry = runner.Registry()
        run_id = _start(tmp_path, registry)
        pid = registry.pid(run_id)
        assert pid is not None
        try:
            assert os.getsid(pid) == pid != os.getsid(0)
        finally:
            runner.cancel(run_id, registry)
            _wait(registry, run_id)

    def test_cancel_is_ctrl_c(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STUB_CHILD_EVENTS", _event("batch", total=1))
        monkeypatch.setenv("STUB_CHILD_HOLD", "30")
        registry = runner.Registry()
        run_id = _start(tmp_path, registry)
        deadline = time.monotonic() + 5
        while runner.read_run(run_id, registry).state != "running":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert runner.cancel(run_id, registry)
        run = _wait(registry, run_id)
        assert (run.state, run.returncode) == ("interrupted", 130)

    def test_a_finished_child_is_reaped_not_left_a_zombie(self, tmp_path: Path) -> None:
        registry = runner.Registry()
        run_id = _start(tmp_path, registry)
        pid = registry.pid(run_id)
        assert pid is not None
        _wait(registry, run_id)
        assert registry.pid(run_id) is None
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)  # already waited on by the registry

    def test_cancel_of_a_finished_run_signals_nothing(self, tmp_path: Path) -> None:
        registry = runner.Registry()
        run_id = _start(tmp_path, registry)
        _wait(registry, run_id)
        assert not runner.cancel(run_id, registry)


# --- The state --------------------------------------------------------------------


def _folder(run_id: str = "20261031-120000-aaaaaa", events: str = "") -> Path:
    folder = runner.runs_dir() / run_id
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_text(events)
    return folder


class TestState:
    @pytest.mark.parametrize(
        ("events", "code", "state"),
        [
            pytest.param(DONE, 0, "done", id="summary"),
            pytest.param(
                DONE.replace('"done": 1', '"done": 0').replace(
                    '"failed": 0', '"failed": 1'
                ),
                1,
                "failed",
                id="summary-with-a-failure",
            ),
            pytest.param(
                _event("batch", total=1) + _event("interrupted"),
                130,
                "interrupted",
                id="interrupted",
            ),
            pytest.param(
                _event("error", kind="usage", message="overlap"),
                2,
                "refused",
                id="refused",
            ),
            pytest.param(
                _event("batch", total=1)
                + _event("pdf", position=1, total=1, pdf="/in/a.pdf", out_dir="/o/a"),
                -9,
                "crashed",
                id="killed-mid-pdf",
            ),
            pytest.param("", 2, "ended", id="argparse-exit-2-writes-nothing"),
            pytest.param(
                DONE.rsplit("\n", 2)[0] + "\n", -9, "ended", id="killed-between-pdfs"
            ),
        ],
    )
    def test_finished(self, events: str, code: int, state: str) -> None:
        registry = runner.Registry()
        _folder(events=events)
        registry._codes["20261031-120000-aaaaaa"] = code
        run = runner.read_run("20261031-120000-aaaaaa", registry)
        assert run.state == state
        assert not run.active

    def test_a_crash_names_the_pdf_it_was_on(self) -> None:
        registry = runner.Registry()
        _folder(
            events=_event("batch", total=2)
            + _event("pdf", position=1, total=2, pdf="/in/a.pdf", out_dir="/o/a")
            + _event("result", pdf="/in/a.pdf", status="done")
            + _event("pdf", position=2, total=2, pdf="/in/b.pdf", out_dir="/o/b")
        )
        registry._codes["20261031-120000-aaaaaa"] = -6
        run = runner.read_run("20261031-120000-aaaaaa", registry)
        assert (run.state, run.crashed_pdf) == ("crashed", "/in/b.pdf")

    def test_a_run_from_an_earlier_server_is_running_while_its_pid_is(
        self,
    ) -> None:
        # This process stands in for the orphaned child: alive, with its start time.
        folder = _folder(events=_event("batch", total=3))
        (folder / "pid").write_text(f"{os.getpid()}\n")
        status = runner._ps(os.getpid())
        assert status is not None
        (folder / "started").write_text(status[1] + "\n")
        run = runner.read_run(folder.name, runner.Registry())
        assert run.state == "running"
        assert runner.active_run(runner.Registry()) == run

    def test_a_reused_pid_is_not_the_run(self) -> None:
        # U3: once reaped, a pid can belong to anyone. A different start time
        # means a stranger, which must never be reported running or signalled.
        folder = _folder(events=_event("batch", total=3))
        (folder / "pid").write_text(f"{os.getpid()}\n")
        (folder / "started").write_text("Thu Jan  1 00:00:00 1970\n")
        os.utime(folder, (0, 0))
        registry = runner.Registry()
        assert runner.read_run(folder.name, registry).state == "ended"
        assert not runner.cancel(folder.name, registry)

    def test_a_zombie_is_not_running(self) -> None:
        # U3 finding 2: a zombie answers kill(pid, 0).
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        deadline = time.monotonic() + 5
        while (status := runner._ps(child.pid)) and not status[0].startswith("Z"):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        try:
            folder = _folder(events=_event("batch", total=1))
            (folder / "pid").write_text(f"{child.pid}\n")
            assert status is not None
            (folder / "started").write_text(status[1] + "\n")
            os.kill(child.pid, 0)  # the zombie answers
            assert runner._alive_pid(folder) is None
        finally:
            child.wait()

    def test_a_new_folder_without_a_pid_is_starting(self) -> None:
        _folder()
        assert runner.read_run("20261031-120000-aaaaaa", runner.Registry()).state == (
            "starting"
        )

    def test_waiting_then_running(self) -> None:
        registry = runner.Registry()
        folder = _folder(events=_event("waiting", holder_pid=4711))
        (folder / "pid").write_text(f"{os.getpid()}\n")
        status = runner._ps(os.getpid())
        assert status is not None
        (folder / "started").write_text(status[1] + "\n")
        run = runner.read_run(folder.name, registry)
        assert (run.state, run.progress.holder_pid) == ("waiting", 4711)
        with (folder / "events.jsonl").open("a") as handle:
            handle.write(_event("batch", total=2))
        assert runner.read_run(folder.name, registry).state == "running"

    def test_run_for_finds_the_run_that_wrote_a_folder(self, tmp_path: Path) -> None:
        registry = runner.Registry()
        _folder("20261031-120000-aaaaaa", DONE.replace("/out/a", str(tmp_path / "a")))
        _folder("20261031-130000-bbbbbb", DONE)
        run = runner.run_for(tmp_path / "a", registry)
        assert run is not None and run.run_id == "20261031-120000-aaaaaa"
        assert runner.run_for(tmp_path / "b", registry) is None


class TestReadEvents:
    def test_progress_follows_the_latest_event(self) -> None:
        progress = runner.read_events(
            [
                _event("batch", total=2),
                _event("pdf", position=2, total=2, pdf="/b.pdf", out_dir="/o/b"),
                _event("page", completed=3, total=24),
                _event("chart", position=1, total=9, page_no=12),
            ]
        )
        assert progress.position == 2 and progress.batch_total == 2
        assert progress.pdf == "/b.pdf"
        assert progress.page == (3, 24) and progress.chart == (1, 9)

    def test_a_new_pdf_resets_the_stage(self) -> None:
        progress = runner.read_events(
            [
                _event("pdf", position=1, total=2, pdf="/a.pdf", out_dir="/o/a"),
                _event("chart", position=1, total=9, page_no=12),
                _event("pdf", position=2, total=2, pdf="/b.pdf", out_dir="/o/b"),
            ]
        )
        assert progress.chart is None and progress.out_dirs == ["/o/a", "/o/b"]

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("not json\n", id="not-json"),
            pytest.param("[1, 2]\n", id="not-an-object"),
            pytest.param(json.dumps({"v": 2, "event": "summary"}) + "\n", id="v2"),
            pytest.param(json.dumps({"event": "summary"}) + "\n", id="no-v"),
            pytest.param('{"v": 1, "event": "summary"', id="half-written"),
        ],
    )
    def test_a_line_it_cannot_read_is_skipped(self, line: str) -> None:
        progress = runner.read_events([_event("batch", total=1), line])
        assert progress.summary is None and progress.batch_total == 1

    def test_values_of_the_wrong_type_are_dropped(self) -> None:
        progress = runner.read_events(
            [
                _event("waiting", holder_pid="4711; rm -rf ~"),
                _event("page", completed=True, total="24"),
                _event("pdf", position=1, total=1, pdf=["x"], out_dir=None),
            ]
        )
        assert progress.holder_pid is None and progress.page is None
        assert progress.pdf is None and progress.out_dirs == []


# --- Uploads ---------------------------------------------------------------------


class TestUploads:
    @pytest.mark.parametrize(
        ("raw", "name"),
        [
            pytest.param("report.pdf", "report.pdf", id="plain"),
            pytest.param("../../etc/passwd", "passwd", id="traversal"),
            pytest.param("C:\\Users\\x\\a.pdf", "a.pdf", id="windows-path"),
            pytest.param("", "upload.pdf", id="empty"),
            pytest.param("..", "upload.pdf", id="dot-dot"),
            pytest.param(".hidden.pdf", "hidden.pdf", id="hidden"),
            pytest.param("a\x00b\nc.pdf", "abc.pdf", id="control-characters"),
            pytest.param("-rf.pdf", "-rf.pdf", id="dash-is-kept-not-a-flag"),
        ],
    )
    def test_upload_name(self, raw: str, name: str) -> None:
        assert runner.upload_name(raw) == name

    def test_a_long_name_keeps_its_suffix(self) -> None:
        name = runner.upload_name("é" * 300 + ".pdf")
        assert name.endswith(".pdf") and len(name.encode()) <= 200

    def test_save_upload_is_contained_and_content_addressed(self) -> None:
        path = runner.save_upload("../../x.pdf", b"%PDF-1")
        assert path.parent.parent == runner.inputs_dir()
        assert path.name == "x.pdf" and path.read_bytes() == b"%PDF-1"
        assert runner.save_upload("../../x.pdf", b"%PDF-1") == path
        assert runner.save_upload("x.pdf", b"%PDF-2").parent != path.parent

    def test_stem_clashes_are_case_folded(self) -> None:
        assert runner.stem_clashes(["Report.pdf", "b.pdf", "report.PDF"]) == [
            ("Report.pdf", "report.PDF")
        ]
        assert runner.stem_clashes(["a.pdf", "b.pdf"]) == []

    def test_replaces_names_a_different_file_with_the_same_name(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "report").mkdir()
        (tmp_path / "report" / "run.json").write_text(
            json.dumps({"input_sha256": "aaa"})
        )
        assert runner.replaces(tmp_path, "report.pdf", "bbb")
        assert not runner.replaces(tmp_path, "report.pdf", "aaa")
        assert not runner.replaces(tmp_path, "other.pdf", "bbb")


# --- Cleanup ---------------------------------------------------------------------


class TestCleanup:
    def test_only_the_ui_folders_older_than_30_days_go(self, tmp_path: Path) -> None:
        now = time.time()
        old = now - runner.RETENTION_SECONDS - 60
        stale_run = _folder("20260101-000000-aaaaaa")
        fresh_run = _folder("20261031-000000-bbbbbb")
        stale_input = runner.save_upload("a.pdf", b"%PDF-a").parent
        fresh_input = runner.save_upload("b.pdf", b"%PDF-b").parent
        outside = tmp_path / "gdi-out" / "report"
        outside.mkdir(parents=True)
        (runner.inputs_dir() / "link").symlink_to(outside)
        for path in [stale_run, stale_input, outside, outside.parent]:
            for inner in [path, *path.iterdir()]:
                os.utime(inner, (old, old))
        os.utime(runner.inputs_dir() / "link", (old, old), follow_symlinks=False)

        removed = runner.cleanup(runner.Registry(), now)

        assert set(removed) == {stale_run, stale_input, runner.inputs_dir() / "link"}
        assert fresh_run.exists() and fresh_input.exists()
        assert outside.exists()  # the link went, never what it pointed at

    def test_a_running_run_is_kept_however_old(self) -> None:
        folder = _folder(events=_event("batch", total=1))
        (folder / "pid").write_text(f"{os.getpid()}\n")
        status = runner._ps(os.getpid())
        assert status is not None
        (folder / "started").write_text(status[1] + "\n")
        old = time.time() - runner.RETENTION_SECONDS - 60
        for path in [folder, *folder.iterdir()]:
            os.utime(path, (old, old))
        assert runner.cleanup(runner.Registry()) == []

    def test_cleanup_never_reaches_the_cache_beside_it(self) -> None:
        models = config.cache_dir() / "models"
        models.mkdir(parents=True)
        os.utime(models, (0, 0))
        runner.runs_dir().mkdir(parents=True)
        runner.cleanup(runner.Registry())
        assert models.exists()

"""`gdi convert --events PATH`: the progress channel (plan §18.2).

The writer's own rules (one JSON object a line, `v` on every line, flushed as
written, never failing a PDF), then one test per event through the code that
emits it. The pipeline is faked at `pipeline.run` as in `test_batch.py`. Two
checks live in `test_safety.py`: §3.13 rule 5 for the channel, beside the other
sentinels, and the `waiting` event, in the test that already holds the lock.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from conftest import build_document
from test_batch import FakePipeline, run_json, write_pdf
from test_safety import hold_lock

from granite_document_intelligence import (
    ConvertOptions,
    charts,
    cli,
    events,
    kvp,
    phase_a,
    pipeline,
)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakePipeline:
    monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))
    return FakePipeline(monkeypatch)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "events.jsonl"


def read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def kinds(path: Path) -> list[str]:
    return [event["event"] for event in read(path)]


def make_unwritable(path: Path) -> None:
    """Point every descriptor this process holds on `path` at a read-only open.

    The writer's next flush then fails with a real `OSError` (EBADF), as a
    full disk or a vanished volume would, without touching the writer.
    """
    target = path.stat()
    read_only = os.open(path, os.O_RDONLY)
    try:
        for name in os.listdir("/dev/fd"):
            fd = int(name)
            if fd == read_only:
                continue
            try:
                held = os.fstat(fd)
            except OSError:
                continue
            if (held.st_dev, held.st_ino) == (target.st_dev, target.st_ino):
                os.dup2(read_only, fd)
    finally:
        os.close(read_only)


class TestWriter:
    def test_each_line_is_one_json_object_carrying_the_version(
        self, path: Path
    ) -> None:
        with events.writing(path):
            events.emit("batch", total=2)
            events.emit("summary", done=1, skipped=0, failed=1)
        assert read(path) == [
            {"v": 1, "event": "batch", "total": 2},
            {"v": 1, "event": "summary", "done": 1, "skipped": 0, "failed": 1},
        ]

    def test_a_line_is_on_disk_as_soon_as_it_is_emitted(self, path: Path) -> None:
        # A reader follows the file while the run is still going.
        with events.writing(path):
            events.emit("page", completed=1, total=3)
            assert kinds(path) == ["page"]

    def test_appends_to_an_existing_file(self, path: Path) -> None:
        path.write_text('{"v": 1, "event": "batch", "total": 1}\n')
        with events.writing(path):
            events.emit("batch", total=2)
        assert [event["total"] for event in read(path)] == [1, 2]

    def test_without_a_writer_emit_does_nothing(self, path: Path) -> None:
        events.emit("batch", total=1)
        with events.writing(None):
            events.emit("batch", total=1)
        assert not path.exists()

    def test_a_filename_that_is_not_utf8_is_still_written(self, path: Path) -> None:
        # os.fsdecode's surrogate escape, as a Latin-1 filename arrives.
        name = "caf\udce9.pdf"
        with events.writing(path):
            events.emit("result", pdf=name, status="done")
        assert read(path)[0]["pdf"] == name

    def test_concurrent_emits_never_interleave(self, path: Path) -> None:
        # Docling reports pages from the thread that converts the PDF.
        def pages() -> None:
            for n in range(200):
                events.emit("page", completed=n, total=200)

        with events.writing(path):
            threads = [threading.Thread(target=pages) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert len(read(path)) == 800

    def test_an_unopenable_path_warns_once_and_writes_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "missing" / "events.jsonl"
        with caplog.at_level(logging.WARNING), events.writing(path):
            events.emit("batch", total=1)
            events.emit("summary", done=1, skipped=0, failed=0)
        assert len(caplog.records) == 1
        assert "continuing without them" in caplog.records[0].getMessage()
        assert not path.exists()

    def test_a_failed_write_turns_the_writer_off_with_one_warning(
        self, path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING), events.writing(path):
            events.emit("batch", total=3)
            make_unwritable(path)
            for position in (1, 2, 3):
                events.emit("pdf", position=position, total=3)
        assert len(caplog.records) == 1
        assert kinds(path) == ["batch"]


def convert(*argv: str) -> int:
    return cli.main(["convert", *argv])


class TestBatchEvents:
    def test_each_pdf_is_announced_then_its_result_then_the_summary(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline, path: Path
    ) -> None:
        a = write_pdf(tmp_path / "in" / "a.pdf", pdf_bytes)
        b = write_pdf(tmp_path / "in" / "b.pdf", pdf_bytes)
        fake.fail = {"b.pdf"}
        out = tmp_path / "out"
        assert convert(str(a), str(b), "-o", str(out), "--events", str(path)) == 1
        assert read(path) == [
            {"v": 1, "event": "batch", "total": 2},
            {
                "v": 1,
                "event": "pdf",
                "position": 1,
                "total": 2,
                "pdf": str(a),
                "out_dir": str(out / "a"),
            },
            {"v": 1, "event": "result", "pdf": str(a), "status": "done"},
            {
                "v": 1,
                "event": "pdf",
                "position": 2,
                "total": 2,
                "pdf": str(b),
                "out_dir": str(out / "b"),
            },
            # No message: the error is in b's run.json (§18.2).
            {"v": 1, "event": "result", "pdf": str(b), "status": "failed"},
            {"v": 1, "event": "summary", "done": 1, "skipped": 0, "failed": 1},
        ]

    def test_a_pdf_already_done_is_reported_as_skipped(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline, path: Path
    ) -> None:
        pdf = write_pdf(tmp_path / "a.pdf", pdf_bytes)
        argv = (str(pdf), "-o", str(tmp_path / "out"), "--events", str(path))
        assert convert(*argv) == 0
        path.unlink()
        assert convert(*argv) == 0
        assert read(path)[-2:] == [
            {"v": 1, "event": "result", "pdf": str(pdf), "status": "skipped"},
            {"v": 1, "event": "summary", "done": 0, "skipped": 1, "failed": 0},
        ]

    def test_ctrl_c_writes_interrupted_and_no_summary(
        self,
        tmp_path: Path,
        pdf_bytes: bytes,
        fake: FakePipeline,
        path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdfs = [write_pdf(tmp_path / f"{n}.pdf", pdf_bytes) for n in "ab"]

        def interrupted_on_b(pdf: Path, options: ConvertOptions) -> Any:
            if pdf.name == "b.pdf":
                raise KeyboardInterrupt
            return fake(pdf, options)

        monkeypatch.setattr(pipeline, "run", interrupted_on_b)
        argv = [*map(str, pdfs), "-o", str(tmp_path / "out"), "--events", str(path)]
        assert convert(*argv) == 130
        # b has a `pdf` event and no `result`: the run stopped during it.
        assert kinds(path) == ["batch", "pdf", "result", "pdf", "interrupted"]


class TestErrorEvents:
    """`error.kind` follows the exit code (§3.8), and a run that cannot start
    writes nothing else."""

    def only_error(self, path: Path) -> dict[str, Any]:
        (event,) = read(path)
        assert set(event) == {"v", "event", "kind", "message"}
        assert event["event"] == "error"
        return event

    def test_a_missing_input_is_a_usage_error(
        self, tmp_path: Path, fake: FakePipeline, path: Path
    ) -> None:
        missing = tmp_path / "nowhere.pdf"
        assert (
            convert(str(missing), "-o", str(tmp_path / "out"), "--events", str(path))
            == 2
        )
        event = self.only_error(path)
        assert event["kind"] == "usage"
        assert event["message"] == f"{missing}: no such file or directory"

    def test_a_bad_schema_is_a_usage_error(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline, path: Path
    ) -> None:
        pdf = write_pdf(tmp_path / "a.pdf", pdf_bytes)
        schema = tmp_path / "fields.json"
        schema.write_text("[]")
        argv = [str(pdf), "-o", str(tmp_path / "out"), "--schema", str(schema)]
        assert convert(*argv, "--events", str(path)) == 2
        event = self.only_error(path)
        assert (event["kind"], event["message"]) == (
            "usage",
            "the schema must be a JSON object",
        )

    def test_options_that_conflict_are_a_usage_error(
        self, tmp_path: Path, path: Path
    ) -> None:
        argv = ["a.pdf", "-o", str(tmp_path / "out"), "--no-charts", "--chart-code"]
        with pytest.raises(SystemExit) as info:
            convert(*argv, "--events", str(path))
        assert info.value.code == 2
        event = self.only_error(path)
        assert (event["kind"], event["message"]) == ("usage", "chart_code needs charts")

    def test_a_flag_argparse_rejects_writes_no_events(
        self, tmp_path: Path, path: Path
    ) -> None:
        # Refused while parsing, before --events is known (§18.2).
        with pytest.raises(SystemExit) as info:
            convert(
                "a.pdf",
                "-o",
                str(tmp_path / "out"),
                "--max-pages",
                "0",
                "--events",
                str(path),
            )
        assert info.value.code == 2
        assert not path.exists()

    def test_missing_models_are_a_models_error(
        self,
        tmp_path: Path,
        blank_pdf: Path,
        path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The real check, against an empty cache.
        monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))
        argv = [str(blank_pdf), "-o", str(tmp_path / "out"), "--events", str(path)]
        assert convert(*argv) == 1
        event = self.only_error(path)
        assert event["kind"] == "models"
        assert "gdi convert-model" in event["message"]

    def test_a_held_lock_under_no_wait_is_a_locked_error(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline, path: Path
    ) -> None:
        holder = hold_lock(tmp_path / "cache", 30)
        try:
            pdf = write_pdf(tmp_path / "a.pdf", pdf_bytes)
            argv = [str(pdf), "-o", str(tmp_path / "out"), "--no-wait"]
            assert convert(*argv, "--events", str(path)) == 1
        finally:
            holder.kill()
            holder.wait()
        event = self.only_error(path)
        assert event["kind"] == "locked"
        assert f"pid {holder.pid}" in event["message"]
        assert fake.ran == []


class TestStageEvents:
    """Pages, charts and key-value pages, from the stages that do the work."""

    def test_a_page_reports_doclings_counts(self, path: Path) -> None:
        from docling.datamodel.progress import PageCompletedProgress

        with events.writing(path):
            phase_a._page_progress(
                PageCompletedProgress(
                    document_index=1,
                    document_name="a.pdf",
                    page_no=7,
                    success=True,
                    completed_pages=3,
                    total_pages=24,
                )
            )
        assert read(path) == [{"v": 1, "event": "page", "completed": 3, "total": 24}]

    def test_each_chart_reports_its_position_and_page(
        self, blank_pdf: Path, path: Path
    ) -> None:
        from test_charts import GOOD, FakeVision

        with events.writing(path):
            charts.run(
                blank_pdf,
                build_document(charts=2),
                Path("unused"),
                vision=FakeVision(GOOD),  # ty: ignore[invalid-argument-type]
            )
        assert read(path) == [
            {"v": 1, "event": "chart", "position": n, "total": 2, "page_no": 1}
            for n in (1, 2)
        ]

    def test_each_key_value_page_reports_its_position_and_page(
        self, tmp_path: Path, path: Path
    ) -> None:
        import pypdfium2
        from conftest import PAGE_H, PAGE_W
        from test_kvp import GOOD, SCHEMA, FakeVision

        pdf = pypdfium2.PdfDocument.new()
        for _ in range(3):
            pdf.new_page(PAGE_W, PAGE_H)
        form = tmp_path / "form.pdf"
        pdf.save(form)
        pdf.close()
        with events.writing(path):
            kvp.run(form, SCHEMA, [1, 3], Path("unused"), vision=FakeVision(GOOD, GOOD))  # ty: ignore[invalid-argument-type]
        assert read(path) == [
            {"v": 1, "event": "kvp", "position": 1, "total": 2, "page_no": 1},
            {"v": 1, "event": "kvp", "position": 2, "total": 2, "page_no": 3},
        ]


def test_an_events_file_that_fails_mid_batch_fails_no_pdf(
    tmp_path: Path,
    pdf_bytes: bytes,
    fake: FakePipeline,
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§18.2's first rule: the writer turns itself off; conversion never depends on it."""
    pdfs = [write_pdf(tmp_path / f"{n}.pdf", pdf_bytes) for n in "abc"]

    def breaks_the_file_on_b(pdf: Path, options: ConvertOptions) -> Any:
        if pdf.name == "b.pdf":
            make_unwritable(path)
        return fake(pdf, options)

    monkeypatch.setattr(pipeline, "run", breaks_the_file_on_b)
    out = tmp_path / "out"
    argv = [*map(str, pdfs), "-o", str(out), "--events", str(path)]
    assert convert(*argv) == 0
    assert fake.ran == ["a.pdf", "b.pdf", "c.pdf"]
    for name in "abc":
        assert run_json(out / name)["status"] == "ok"
    err = capsys.readouterr().err
    assert err.count("could not write progress events") == 1
    assert "3 done, 0 skipped, 0 failed" in err
    # What was written before the failure stays a valid prefix.
    assert kinds(path) == ["batch", "pdf", "result", "pdf"]

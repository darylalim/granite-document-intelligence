"""Untrusted input (§3.10, §3.13) and the run lock (§3.14 rule 4).

The pipeline is faked at `pipeline.run` as in `test_batch.py`, after the real
input checks; the lock is exercised against real processes holding it.
"""

import ast
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import make_pdf_bytes
from test_batch import FakePipeline, run, run_json, write_pdf

from granite_document_intelligence import (
    ConvertOptions,
    RunLockedError,
    charts,
    cli,
    convert,
    convert_many,
    pipeline,
    runlock,
    safety,
)

PACKAGE = Path(safety.__file__).parent


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakePipeline:
    monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))
    return FakePipeline(monkeypatch)


class TestLimits:
    def test_a_pdf_over_max_pages_is_a_per_pdf_error(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        src = tmp_path / "in"
        write_pdf(src / "long.pdf", make_pdf_bytes(pages=3))
        write_pdf(src / "short.pdf", pdf_bytes)
        assert run([src], tmp_path / "out", ConvertOptions(max_pages=2)) == {
            "long.pdf": "failed",
            "short.pdf": "done",
        }
        error = run_json(tmp_path / "out" / "long")["error"]
        assert error["type"] == "InputRejected"
        assert "3 pages, over the 2-page limit" in error["message"]

    def test_a_pdf_over_max_file_mb_is_refused_before_pdfium_reads_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        big = tmp_path / "big.pdf"
        big.write_bytes(b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 1))

        def opened(*_: Any) -> int:
            raise AssertionError("pdfium opened a file over the size limit")

        monkeypatch.setattr(safety, "page_count", opened)
        with pytest.raises(safety.InputRejected, match="over the 1 MB limit"):
            safety.check_pdf(big, ConvertOptions(max_file_mb=1))

    def test_the_decompression_bomb_check_is_never_disabled(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        from PIL import Image

        before = Image.MAX_IMAGE_PIXELS
        run([write_pdf(tmp_path / "a.pdf", pdf_bytes)], tmp_path / "out")
        assert Image.MAX_IMAGE_PIXELS == before
        # The run above is faked, so also: nothing in the package assigns it.
        for module in PACKAGE.glob("*.py"):
            assert "MAX_IMAGE_PIXELS" not in module.read_text(), module.name


class TestRecognisedByContent:
    def test_a_pdf_named_without_the_header_is_refused(
        self, tmp_path: Path, fake: FakePipeline
    ) -> None:
        fake_pdf = tmp_path / "invoice.pdf"
        fake_pdf.write_bytes(b"<html>not a pdf</html>")
        assert run([fake_pdf], tmp_path / "out") == {"invoice.pdf": "failed"}
        assert "not a PDF" in run_json(tmp_path / "out" / "invoice")["error"]["message"]
        assert fake.ran == []

    def test_a_real_pdf_named_otherwise_is_converted_when_named_explicitly(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        renamed = write_pdf(tmp_path / "report.txt", pdf_bytes)
        assert run([renamed], tmp_path / "out") == {"report.txt": "done"}

    def test_a_directory_scan_takes_only_pdf_names(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        src = tmp_path / "in"
        write_pdf(src / "report.txt", pdf_bytes)
        write_pdf(src / "report2.pdf", pdf_bytes)
        assert run([src], tmp_path / "out") == {"report2.pdf": "done"}


class TestPaths:
    def test_an_output_path_resolving_outside_the_output_directory_is_refused(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        src = tmp_path / "in"
        write_pdf(src / "sub" / "report.pdf", pdf_bytes)
        out = tmp_path / "out"
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        out.mkdir()
        (out / "sub").symlink_to(elsewhere, target_is_directory=True)
        [item] = convert_many([src], out, recursive=True)
        assert item.status == "failed"
        assert "outside the output directory" in (item.error or "")
        assert list(elsewhere.iterdir()) == []
        assert fake.ran == []

    def test_contained_accepts_a_path_inside(self, tmp_path: Path) -> None:
        assert safety.contained(tmp_path / "a" / "b", tmp_path) == tmp_path / "a" / "b"

    def test_a_symlink_out_of_the_input_directory_is_skipped_with_a_warning(
        self,
        tmp_path: Path,
        pdf_bytes: bytes,
        fake: FakePipeline,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        src = tmp_path / "in"
        write_pdf(src / "inside.pdf", pdf_bytes)
        outside = write_pdf(tmp_path / "private" / "secret.pdf", pdf_bytes)
        (src / "link.pdf").symlink_to(outside)
        (src / "linkdir").symlink_to(outside.parent, target_is_directory=True)
        with caplog.at_level(logging.WARNING):
            assert run([src], tmp_path / "out", recursive=True) == {
                "inside.pdf": "done"
            }
        assert f"skipped {src / 'link.pdf'}: a symlink out of {src}" in caplog.text
        assert f"skipped {src / 'linkdir'}: a symlink out of {src}" in caplog.text

    def test_a_symlink_within_the_input_directory_is_followed(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        src = tmp_path / "in"
        target = write_pdf(src / "archive" / "real.pdf", pdf_bytes)
        (src / "alias.pdf").symlink_to(target)
        assert run([src], tmp_path / "out") == {"alias.pdf": "done"}


SENTINEL_TOKEN = "hf_SENTINELtokenSENTINELtoken"
SENTINEL_TEXT = "SENTINEL-PAGE-TEXT-7f3a"
# Digit-free: the summary check skips labels holding a digit (charts.py).
SENTINEL_LABEL = "Sentinel Chart Label"


class TestNoSecretsOrContentInLogs:
    """§3.13 rule 5: run.json and stderr carry no env value and no page text."""

    def test_a_failure_records_neither_the_token_nor_the_document(
        self,
        tmp_path: Path,
        pdf_bytes: bytes,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("HF_TOKEN", SENTINEL_TOKEN)
        pdf = write_pdf(
            tmp_path / "report.pdf", pdf_bytes + f"% {SENTINEL_TEXT}\n".encode()
        )
        fake = FakePipeline(monkeypatch)
        real_call = fake.__call__

        def leaky(path: Path, options: ConvertOptions) -> Any:
            # Frames outside the package, and locals holding the page text and
            # the token, are exactly what a stored traceback must not keep.
            page_text = path.read_bytes().decode("latin-1")  # noqa: F841
            token = os.environ["HF_TOKEN"]  # noqa: F841
            real_call(path, options)
            raise RuntimeError("model output was unusable")

        monkeypatch.setattr(pipeline, "run", leaky)
        out = tmp_path / "out"
        assert cli.main(["convert", str(pdf), "-o", str(out)]) == 1
        stderr = capsys.readouterr().err
        written = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
        assert "model output was unusable" in written
        for sentinel in (SENTINEL_TOKEN, SENTINEL_TEXT):
            assert sentinel not in written
            assert sentinel not in stderr

    def test_a_chart_warning_quotes_no_label(self, blank_pdf: Path) -> None:
        # A chart's CSV labels are document content as the model read it. The
        # summary check names the row by position instead (§18.4 rule 3).
        from conftest import build_document
        from test_charts import GOOD, FakeVision

        vision = FakeVision(
            {
                **GOOD,
                charts.CSV_PROMPT: f"Region,Revenue\n{SENTINEL_LABEL},120\n",
                charts.SUMMARY_PROMPT: f"{SENTINEL_LABEL} reached 999.",
            }
        )
        doc = build_document()
        result = charts.run(blank_pdf, doc, Path("unused"), vision=vision)  # ty: ignore[invalid-argument-type]
        assert any("row 1: summary 999" in w for w in result.warnings)
        assert not any(SENTINEL_LABEL in w for w in result.warnings)

    def test_an_error_record_keeps_only_our_own_frames(self) -> None:
        def foreign() -> None:
            raise ValueError("boom")

        try:
            foreign()
        except ValueError as exc:
            record = safety.error_record(exc)
        assert record == {"type": "ValueError", "message": "boom", "frames": []}

    def test_an_error_record_names_package_frames_by_file_line_and_function(
        self, tmp_path: Path
    ) -> None:
        try:
            safety.contained(tmp_path.parent, tmp_path)
        except safety.OutsideOutputError as exc:
            record = safety.error_record(exc)
        assert len(record["frames"]) == 1
        assert record["frames"][0].startswith("safety.py:")
        assert record["frames"][0].endswith(" in contained")


class TestGeneratedCodeIsNeverRun:
    def test_nothing_in_the_package_executes_or_imports_dynamically(self) -> None:
        # chart-NNN.py is model output (§3.10): only ever written, never loaded.
        # Builtins called by bare name (`re.compile` is not `compile`), and the
        # loaders reached as attributes.
        banned_builtins = {"exec", "eval", "compile", "__import__"}
        banned_attributes = {"run_path", "run_module", "import_module", "exec_module"}
        banned_modules = {"runpy", "subprocess", "importlib"}
        for module in PACKAGE.glob("*.py"):
            tree = ast.parse(module.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        assert func.id not in banned_builtins, (
                            f"{module.name}: {func.id}()"
                        )
                    elif isinstance(func, ast.Attribute):
                        assert func.attr not in banned_attributes, (
                            f"{module.name}: {func.attr}()"
                        )
                elif isinstance(node, ast.Import | ast.ImportFrom):
                    names = (
                        [node.module or ""]
                        if isinstance(node, ast.ImportFrom)
                        else [a.name for a in node.names]
                    )
                    for imported in names:
                        top = imported.split(".")[0]
                        # importlib.metadata reads versions for run.json; it loads nothing.
                        if imported == "importlib.metadata":
                            continue
                        assert top not in banned_modules, (
                            f"{module.name}: import {imported}"
                        )


# --- The run lock (§3.14 rule 4) ---------------------------------------------

_HOLDER = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
os.ftruncate(fd, 0)
os.write(fd, f"{os.getpid()}\\n".encode())
print("ready", flush=True)
time.sleep(float(sys.argv[2]))
"""


def hold_lock(cache: Path, seconds: float) -> subprocess.Popen[str]:
    """Another process holding the run lock for `seconds`, then exiting."""
    cache.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(cache / runlock.LOCK_NAME), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    return holder


class TestRunLock:
    @pytest.fixture
    def cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        cache = tmp_path / "cache"
        monkeypatch.setenv("GDI_CACHE_DIR", str(cache))
        return cache

    def test_no_wait_exits_non_zero_without_loading_a_model(
        self,
        cache: Path,
        tmp_path: Path,
        pdf_bytes: bytes,
        fake: FakePipeline,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        holder = hold_lock(cache, 30)
        try:
            pdf = write_pdf(tmp_path / "a.pdf", pdf_bytes)
            code = cli.main(
                ["convert", str(pdf), "-o", str(tmp_path / "out"), "--no-wait"]
            )
            assert code == 1
            assert f"another gdi run (pid {holder.pid})" in capsys.readouterr().err
            assert fake.ran == []
        finally:
            holder.kill()
            holder.wait()

    def test_a_second_run_says_it_is_waiting_then_runs(
        self,
        cache: Path,
        tmp_path: Path,
        pdf_bytes: bytes,
        fake: FakePipeline,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        holder = hold_lock(cache, 0.3)
        pdf = write_pdf(tmp_path / "a.pdf", pdf_bytes)
        started = time.monotonic()
        with caplog.at_level(logging.WARNING):
            assert run([pdf], tmp_path / "out") == {"a.pdf": "done"}
        holder.wait()
        assert f"waiting for another gdi run (pid {holder.pid})" in caplog.text
        assert time.monotonic() - started >= 0.2

    def test_a_lock_left_by_an_exited_process_does_not_block(
        self, cache: Path, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        holder = hold_lock(cache, 0)
        holder.wait()
        # The file still names the dead pid; flock, not the file, is the lock.
        assert (cache / runlock.LOCK_NAME).read_text().strip() == str(holder.pid)
        pdf = write_pdf(tmp_path / "a.pdf", pdf_bytes)
        assert run([pdf], tmp_path / "out", wait=False) == {"a.pdf": "done"}

    def test_convert_takes_the_lock_too(
        self, cache: Path, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        holder = hold_lock(cache, 30)
        try:
            with pytest.raises(RunLockedError):
                convert(write_pdf(tmp_path / "a.pdf", pdf_bytes), wait=False)
            assert fake.ran == []
        finally:
            holder.kill()
            holder.wait()

    def test_the_lock_is_released_after_a_run(
        self, cache: Path, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        pdf = write_pdf(tmp_path / "a.pdf", pdf_bytes)
        run([pdf], tmp_path / "out")
        with runlock.run_lock(wait=False):
            pass

    def test_a_batch_records_its_pid_in_the_lock_file(
        self, cache: Path, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        pdf = write_pdf(tmp_path / "a.pdf", pdf_bytes)
        items = convert_many([pdf], tmp_path / "out")
        next(items)  # the generator holds the lock while it is suspended
        assert (cache / runlock.LOCK_NAME).read_text().strip() == str(os.getpid())
        items.close()
        with runlock.run_lock(wait=False):
            pass


def test_run_json_of_a_success_holds_no_document_text(
    tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
) -> None:
    pdf = write_pdf(tmp_path / "a.pdf", pdf_bytes)
    run([pdf], tmp_path / "out")
    record = json.dumps(run_json(tmp_path / "out" / "a"))
    # make_result's document text (conftest.build_document).
    assert "Revenue rose in every region." not in record

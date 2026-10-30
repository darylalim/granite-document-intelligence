"""`convert_many`: expansion, resume, failure handling and storage (§3.8, §3.12).

Phase A and B are faked one level up, at `pipeline.run`. The fake still runs
the real input checks first, as `pipeline.run` does, and records the real
input hash and options, so resume is tested against what a real run writes.
"""

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_export import make_result

from granite_document_intelligence import (
    ConvertOptions,
    ModelsNotReadyError,
    batch,
    convert_many,
    models,
    pipeline,
    safety,
)
from granite_document_intelligence.api import ConvertResult
from granite_document_intelligence.batch import BatchError


class FakePipeline:
    """Stands in for `pipeline.run`, recording each PDF it is handed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.ran: list[str] = []
        self.charts = 1
        self.fail: set[str] = set()
        monkeypatch.setattr(models, "verify_models_dir", lambda _: {})
        monkeypatch.setattr(pipeline, "run", self)

    def __call__(self, pdf: Path, options: ConvertOptions) -> ConvertResult:
        safety.check_pdf(pdf, options)
        self.ran.append(pdf.name)
        if pdf.name in self.fail:
            raise RuntimeError(f"cannot convert {pdf.name}")
        result = make_result(charts=self.charts, options=options)
        result.source = pdf
        result.run = replace(
            result.run,
            input_sha256=pipeline.sha256_of(pdf),
            options=pipeline.recorded_options(options),
        )
        return result


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakePipeline:
    monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))
    return FakePipeline(monkeypatch)


def write_pdf(path: Path, pdf_bytes: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)
    return path


def run(
    inputs: list[Path], out: Path, options: ConvertOptions | None = None, **kwargs: Any
) -> dict[str, str]:
    return {
        item.pdf.name: item.status
        for item in convert_many(inputs, out, options, **kwargs)
    }


def run_json(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "run.json").read_text())


class TestExpansion:
    def test_a_directory_gives_its_pdfs_case_insensitively_and_sorted(
        self, tmp_path: Path, pdf_bytes: bytes
    ) -> None:
        src = tmp_path / "in"
        for name in ("b.pdf", "A.PDF", "c.Pdf", "notes.txt"):
            write_pdf(src / name, pdf_bytes)
        write_pdf(src / "sub" / "d.pdf", pdf_bytes)
        jobs, _ = batch.expand([src], tmp_path / "out", recursive=False)
        assert [job.pdf.name for job in jobs] == ["A.PDF", "b.pdf", "c.Pdf"]

    def test_recursive_descends_and_keeps_the_relative_path(
        self, tmp_path: Path, pdf_bytes: bytes
    ) -> None:
        src = tmp_path / "in"
        write_pdf(src / "x" / "report.pdf", pdf_bytes)
        write_pdf(src / "y" / "report.pdf", pdf_bytes)
        write_pdf(src / "top.pdf", pdf_bytes)
        out = tmp_path / "out"
        jobs, _ = batch.expand([src], out, recursive=True)
        # Two report.pdf in different subfolders do not collide (§3.8).
        assert [job.out_dir for job in jobs] == [
            out / "top",
            out / "x" / "report",
            out / "y" / "report",
        ]

    def test_a_named_file_writes_to_its_stem(
        self, tmp_path: Path, pdf_bytes: bytes
    ) -> None:
        pdf = write_pdf(tmp_path / "scan.pdf", pdf_bytes)
        jobs, _ = batch.expand([pdf], tmp_path / "out", recursive=False)
        assert jobs[0].out_dir == tmp_path / "out" / "scan"

    @pytest.mark.parametrize(
        "names",
        [
            pytest.param(["a/report.pdf", "b/report.pdf"], id="same-stem"),
            pytest.param(["a/report.pdf", "b/REPORT.pdf"], id="differs-by-case"),
            pytest.param(
                ["a/report.pdf", "b/report.partial.pdf"], id="staging-sibling"
            ),
            pytest.param(["a/report.pdf", "b/report.old.pdf"], id="old-sibling"),
        ],
    )
    def test_overlapping_outputs_are_refused_before_anything_runs(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline, names: list[str]
    ) -> None:
        pdfs = [write_pdf(tmp_path / name, pdf_bytes) for name in names]
        with pytest.raises(BatchError, match="separate output directories"):
            run(pdfs, tmp_path / "out")
        assert fake.ran == []

    def test_an_output_inside_another_is_refused(
        self, tmp_path: Path, pdf_bytes: bytes
    ) -> None:
        # out/report/ would be swapped over out/report/x/.
        src = tmp_path / "in"
        write_pdf(src / "report.pdf", pdf_bytes)
        write_pdf(src / "report" / "x.pdf", pdf_bytes)
        with pytest.raises(BatchError, match="inside"):
            batch.expand([src], tmp_path / "out", recursive=True)

    def test_a_missing_input_is_refused(
        self, tmp_path: Path, fake: FakePipeline
    ) -> None:
        with pytest.raises(BatchError, match="no such file"):
            run([tmp_path / "nope.pdf"], tmp_path / "out")

    def test_a_directory_without_pdfs_is_refused(
        self, tmp_path: Path, fake: FakePipeline
    ) -> None:
        (tmp_path / "empty").mkdir()
        with pytest.raises(BatchError, match="no PDFs"):
            run([tmp_path / "empty"], tmp_path / "out")


class TestResume:
    @pytest.fixture
    def pdf(self, tmp_path: Path, pdf_bytes: bytes) -> Path:
        return write_pdf(tmp_path / "in" / "report.pdf", pdf_bytes)

    def test_a_finished_pdf_is_skipped(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        out = tmp_path / "out"
        assert run([pdf], out) == {"report.pdf": "done"}
        assert run([pdf], out) == {"report.pdf": "skipped"}
        assert fake.ran == ["report.pdf"]

    def test_force_reruns_a_finished_pdf(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        out = tmp_path / "out"
        run([pdf], out)
        assert run([pdf], out, force=True) == {"report.pdf": "done"}
        assert fake.ran == ["report.pdf", "report.pdf"]

    def test_a_folder_with_outputs_but_no_run_json_is_rerun(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        out = tmp_path / "out"
        run([pdf], out)
        (out / "report" / "run.json").unlink()
        assert run([pdf], out) == {"report.pdf": "done"}

    @pytest.mark.parametrize(
        "edit",
        [
            pytest.param(lambda r: r.update(status="error"), id="failed"),
            pytest.param(lambda r: r.update(input_sha256="0" * 64), id="other-file"),
            pytest.param(
                lambda r: r["options"].update(chunk_tokens=256), id="other-options"
            ),
            pytest.param(lambda r: r.pop("options"), id="no-options"),
        ],
    )
    def test_anything_but_a_matching_success_is_rerun(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline, edit: Any
    ) -> None:
        out = tmp_path / "out"
        run([pdf], out)
        record = run_json(out / "report")
        edit(record)
        (out / "report" / "run.json").write_text(json.dumps(record))
        assert run([pdf], out) == {"report.pdf": "done"}

    def test_an_unreadable_run_json_is_rerun(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        out = tmp_path / "out"
        run([pdf], out)
        (out / "report" / "run.json").write_text("{not json")
        assert run([pdf], out) == {"report.pdf": "done"}

    def test_a_changed_pdf_is_rerun(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        out = tmp_path / "out"
        run([pdf], out)
        with pdf.open("ab") as f:
            f.write(b"\n% edited\n")
        assert run([pdf], out) == {"report.pdf": "done"}

    def test_changed_options_rerun(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        out = tmp_path / "out"
        run([pdf], out)
        assert run([pdf], out, ConvertOptions(chunk_tokens=256)) == {
            "report.pdf": "done"
        }

    def test_an_edited_schema_at_the_same_path_reruns(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        schema = tmp_path / "fields.json"
        schema.write_text('{"type": "object"}')
        options = ConvertOptions(schema=schema)
        out = tmp_path / "out"
        run([pdf], out, options)
        assert run([pdf], out, options) == {"report.pdf": "skipped"}
        schema.write_text('{"type": "object", "required": ["name"]}')
        assert run([pdf], out, options) == {"report.pdf": "done"}

    def test_raising_a_limit_reruns_nothing(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        # Limits decide whether a PDF converts, not what its output is.
        out = tmp_path / "out"
        run([pdf], out)
        assert run([pdf], out, ConvertOptions(max_pages=900)) == {
            "report.pdf": "skipped"
        }


class TestFailures:
    def test_one_failure_does_not_stop_the_batch(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        src = tmp_path / "in"
        for name in ("a.pdf", "b.pdf", "c.pdf"):
            write_pdf(src / name, pdf_bytes)
        fake.fail = {"b.pdf"}
        assert run([src], tmp_path / "out") == {
            "a.pdf": "done",
            "b.pdf": "failed",
            "c.pdf": "done",
        }

    def test_a_failure_records_its_error_in_run_json(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        pdf = write_pdf(tmp_path / "bad.pdf", pdf_bytes)
        fake.fail = {"bad.pdf"}
        [item] = convert_many([pdf], tmp_path / "out")
        assert item.error == "RuntimeError: cannot convert bad.pdf"
        record = run_json(tmp_path / "out" / "bad")
        assert record["status"] == "error"
        assert record["error"]["type"] == "RuntimeError"
        assert record["error"]["message"] == "cannot convert bad.pdf"
        assert record["input_sha256"] == pipeline.sha256_of(pdf)
        assert record["options"] == pipeline.recorded_options(ConvertOptions())

    def test_a_failed_rerun_replaces_the_previous_success(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        pdf = write_pdf(tmp_path / "report.pdf", pdf_bytes)
        out = tmp_path / "out"
        run([pdf], out)
        assert (out / "report" / "document.md").is_file()
        fake.fail = {"report.pdf"}
        assert run([pdf], out, force=True) == {"report.pdf": "failed"}
        # No stale success files beside an error run.json (§3.12 rule 1).
        assert sorted(p.name for p in (out / "report").iterdir()) == ["run.json"]

    def test_an_unready_models_directory_stops_the_batch_before_any_pdf(
        self, tmp_path: Path, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))
        pdf = write_pdf(tmp_path / "report.pdf", pdf_bytes)

        def boom(*_: Any) -> None:
            raise AssertionError("a PDF ran without a verified models directory")

        monkeypatch.setattr(pipeline, "run", boom)
        with pytest.raises(ModelsNotReadyError, match="gdi convert-model"):
            run([pdf], tmp_path / "out")
        assert not (tmp_path / "out").exists()

    def test_an_unready_models_directory_mid_batch_stops_it(
        self,
        tmp_path: Path,
        pdf_bytes: bytes,
        fake: FakePipeline,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # It would fail every later PDF the same way, so the batch stops (§3.8).
        src = tmp_path / "in"
        for name in ("a.pdf", "b.pdf", "c.pdf"):
            write_pdf(src / name, pdf_bytes)

        def unready_at_b(pdf: Path, options: ConvertOptions) -> ConvertResult:
            if pdf.name == "b.pdf":
                raise ModelsNotReadyError("run gdi convert-model")
            return fake(pdf, options)

        monkeypatch.setattr(pipeline, "run", unready_at_b)
        with pytest.raises(ModelsNotReadyError, match="gdi convert-model"):
            run([src], tmp_path / "out")
        assert fake.ran == ["a.pdf"]
        assert run_json(tmp_path / "out" / "a")["status"] == "ok"
        assert not (tmp_path / "out" / "b").exists()


class TestStorage:
    @pytest.fixture
    def pdf(self, tmp_path: Path, pdf_bytes: bytes) -> Path:
        return write_pdf(tmp_path / "report.pdf", pdf_bytes)

    def test_a_rerun_with_fewer_charts_leaves_no_stale_chart(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        out = tmp_path / "out"
        fake.charts = 3
        run([pdf], out)
        fake.charts = 1
        run([pdf], out, force=True)
        assert not (out / "report" / "charts" / "chart-002.csv").exists()
        assert (out / "report" / "charts" / "chart-001.csv").is_file()

    def test_a_leftover_partial_is_deleted_at_startup(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        out = tmp_path / "out"
        run([pdf], out)
        partial = out / "report.partial"
        partial.mkdir()
        (partial / "chart-009.csv").write_text("crash residue")
        assert run([pdf], out) == {"report.pdf": "skipped"}
        assert not partial.exists()

    def test_an_old_tree_with_no_folder_is_restored(
        self, tmp_path: Path, pdf: Path, fake: FakePipeline
    ) -> None:
        # A crash between the swap's two renames (§3.12 rule 1).
        out = tmp_path / "out"
        run([pdf], out)
        os.rename(out / "report", out / "report.old")
        assert run([pdf], out) == {"report.pdf": "skipped"}
        assert (out / "report" / "run.json").is_file()
        assert not (out / "report.old").exists()

    def test_the_summary_reports_counts_paths_and_size(
        self, tmp_path: Path, pdf_bytes: bytes, fake: FakePipeline
    ) -> None:
        src = tmp_path / "in"
        for name in ("a.pdf", "b.pdf"):
            write_pdf(src / name, pdf_bytes)
        fake.fail = {"b.pdf"}
        out = tmp_path / "out"
        items = list(convert_many([src], out))
        text = batch.summary(items, out)
        done = run_json(out / "a")["output_bytes"]
        assert done > 0
        assert text.splitlines()[0].startswith("1 done, 0 skipped, 1 failed — ")
        assert f"{done / 1024:.1f} KB in {out}" in text
        assert f"done    {src / 'a.pdf'} → {out / 'a'}" in text
        assert "failed  " in text and "RuntimeError: cannot convert b.pdf" in text

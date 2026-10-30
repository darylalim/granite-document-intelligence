"""`gdi`: argument parsing and delegation to the library."""

from pathlib import Path
from typing import Any

import pytest

from granite_document_intelligence import (
    ConvertOptions,
    RunLockedError,
    api,
    cli,
    models,
)
from granite_document_intelligence.batch import BatchError


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def fake(cache_dir: Path | None = None, **kwargs: Any) -> Path:
        seen.append(kwargs)
        return Path("models")

    monkeypatch.setattr(models, "convert_model", fake)
    return seen


def test_convert_model_keeps_the_source_by_default(calls: list[dict[str, Any]]) -> None:
    assert cli.main(["convert-model"]) == 0
    assert calls == [{"delete_source": False}]


def test_delete_source_is_passed_through(calls: list[dict[str, Any]]) -> None:
    assert cli.main(["convert-model", "--delete-source"]) == 0
    assert calls == [{"delete_source": True}]


def test_a_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main([])
    assert info.value.code == 2
    assert "COMMAND" in capsys.readouterr().err


class TestConvert:
    """`gdi convert` parses, delegates to `convert_many` and maps outcomes to exits."""

    @pytest.fixture
    def many(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        seen: dict[str, Any] = {"statuses": ["done"]}

        def fake(inputs: Any, out_dir: Path, options: Any, **kwargs: Any) -> Any:
            seen.update(inputs=list(inputs), out_dir=out_dir, options=options, **kwargs)
            for i, status in enumerate(seen["statuses"]):
                yield api.BatchItem(
                    Path(f"{i}.pdf"),
                    out_dir / str(i),
                    status,
                    error="RuntimeError: x" if status == "failed" else None,
                )

        monkeypatch.setattr(api, "convert_many", fake)
        return seen

    def test_delegates_every_option_to_convert_many(
        self, many: dict[str, Any], tmp_path: Path
    ) -> None:
        schema = tmp_path / "fields.json"
        argv = [
            "convert", "a.pdf", "docs", "-o", "out",
            "--schema", str(schema), "--pages", "1,3-4", "--text-source", "pdf",
            "--chart-code", "--no-chunks", "--chunk-tokens", "256",
            "--save-page-images", "--recursive", "--force",
            "--max-pages", "40", "--max-file-mb", "5", "--no-wait",
        ]  # fmt: skip
        assert cli.main(argv) == 0
        assert many["inputs"] == [Path("a.pdf"), Path("docs")]
        assert many["out_dir"] == Path("out")
        # Resume and expansion live in the library: the CLI only passes flags on.
        assert (many["force"], many["recursive"], many["wait"]) == (True, True, False)
        assert many["options"] == ConvertOptions(
            text_source="pdf",
            charts=True,
            chart_code=True,
            save_page_images=True,
            schema=schema,
            pages="1,3-4",
            chunks=False,
            chunk_tokens=256,
            max_pages=40,
            max_file_mb=5,
        )

    def test_the_defaults_are_convert_options_defaults(
        self, many: dict[str, Any]
    ) -> None:
        assert cli.main(["convert", "a.pdf", "-o", "out"]) == 0
        assert many["options"] == ConvertOptions()
        assert (many["force"], many["recursive"], many["wait"]) == (False, False, True)

    def test_no_charts(self, many: dict[str, Any]) -> None:
        assert cli.main(["convert", "a.pdf", "-o", "out", "--no-charts"]) == 0
        assert many["options"].charts is False

    @pytest.mark.parametrize(
        "statuses, code",
        [
            pytest.param(["done", "skipped"], 0, id="all-ok"),
            pytest.param(["done", "failed", "done"], 1, id="one-failed"),
        ],
    )
    def test_exit_status_says_whether_any_pdf_failed(
        self,
        many: dict[str, Any],
        capsys: pytest.CaptureFixture[str],
        statuses: list[str],
        code: int,
    ) -> None:
        many["statuses"] = statuses
        assert cli.main(["convert", "a.pdf", "-o", "out"]) == code
        # The summary goes to stderr whatever happened.
        assert "done, " in capsys.readouterr().err

    def test_ctrl_c_exits_130_after_summarizing_what_finished(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def interrupted(inputs: Any, out_dir: Path, options: Any, **_: Any) -> Any:
            yield api.BatchItem(Path("0.pdf"), out_dir / "0", "done")
            raise KeyboardInterrupt

        monkeypatch.setattr(api, "convert_many", interrupted)
        assert cli.main(["convert", "a.pdf", "b.pdf", "-o", "out"]) == 130
        err = capsys.readouterr().err
        assert "interrupted; finished PDFs are kept" in err
        assert "1 done, " in err

    @pytest.mark.parametrize(
        "extra, message",
        [
            pytest.param(
                ["--pages", "all"], "pages needs a schema", id="pages-no-schema"
            ),
            pytest.param(
                ["--no-charts", "--chart-code"], "chart_code", id="code-no-charts"
            ),
            pytest.param(["--chunk-tokens", "10"], "chunk_tokens", id="tiny-chunks"),
            pytest.param(["--max-pages", "0"], "at least 1", id="zero-pages"),
            pytest.param(["--text-source", "ocr"], "invalid choice", id="text-source"),
        ],
    )
    def test_a_bad_option_is_a_usage_error(
        self,
        many: dict[str, Any],
        capsys: pytest.CaptureFixture[str],
        extra: list[str],
        message: str,
    ) -> None:
        with pytest.raises(SystemExit) as info:
            cli.main(["convert", "a.pdf", "-o", "out", *extra])
        assert info.value.code == 2
        assert message in capsys.readouterr().err
        assert "inputs" not in many

    def test_an_output_directory_is_required(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as info:
            cli.main(["convert", "a.pdf"])
        assert info.value.code == 2
        assert "-o/--output" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "error, code",
        [
            pytest.param(
                models.ModelsNotReadyError("run gdi convert-model"), 1, id="models"
            ),
            pytest.param(RunLockedError("another gdi run (pid 7)"), 1, id="locked"),
            pytest.param(BatchError("two PDFs would both write"), 2, id="collision"),
        ],
    )
    def test_a_run_that_cannot_start_names_why(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: Exception,
        code: int,
    ) -> None:
        def refuse(*_: Any, **__: Any) -> Any:
            raise error
            yield  # a generator, like the real one

        monkeypatch.setattr(api, "convert_many", refuse)
        assert cli.main(["convert", "a.pdf", "-o", "out"]) == code
        assert str(error) in capsys.readouterr().err

    def test_the_missing_models_message_names_the_fix(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        blank_pdf: Path,
    ) -> None:
        # The real check, against an empty cache.
        monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))
        assert cli.main(["convert", str(blank_pdf), "-o", str(tmp_path / "out")]) == 1
        assert "gdi convert-model" in capsys.readouterr().err

    @pytest.mark.parametrize("quiet, level", [(False, "INFO"), (True, "WARNING")])
    def test_quiet_silences_progress_only(
        self, many: dict[str, Any], quiet: bool, level: str
    ) -> None:
        import logging

        argv = ["convert", "a.pdf", "-o", "out"] + (["--quiet"] if quiet else [])
        cli.main(argv)
        logger = logging.getLogger("granite_document_intelligence")
        assert logging.getLevelName(logger.level) == level
        assert logger.propagate is False

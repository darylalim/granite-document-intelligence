"""`gdi`: argument parsing and delegation to the library."""

from pathlib import Path
from typing import Any

import pytest

from granite_document_intelligence import cli, models


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

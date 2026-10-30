"""S10's detector and verdict (§3.13 rule 2), without a model.

The hook is executed in a real subprocess, the way `s10_check.py` loads it, so a
detector that stopped seeing executed code fails here rather than passing a release
check blind. `eval/` is not a package, so it is put on the path the way `python
eval/s10_check.py` puts it there.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

EVAL = Path(__file__).resolve().parent.parent / "eval"
sys.path.insert(0, str(EVAL))

import s10_check

MANIFEST_NAME = s10_check.MANIFEST


def run_hooked(tmp_path: Path, code: str, *, log: bool = True) -> list[dict[str, Any]]:
    env = {
        **os.environ,
        "PYTHONPATH": str(s10_check.HOOK_DIR),
        "S10_ROOTS": str(tmp_path / "watched"),
    }
    log_path = tmp_path / "log.jsonl"
    if log:
        env["S10_LOG"] = str(log_path)
    subprocess.run([sys.executable, "-c", code], env=env, check=True)
    return s10_check.read_log(log_path)


class TestHook:
    @pytest.fixture
    def modules(self, tmp_path: Path) -> str:
        for folder, name in (("watched", "repo_code"), ("elsewhere", "our_code")):
            (tmp_path / folder).mkdir()
            (tmp_path / folder / f"{name}.py").write_text("X = 1\n")
        return (
            f"import sys; sys.path[:0] = [{str(tmp_path / 'watched')!r}, "
            f"{str(tmp_path / 'elsewhere')!r}]; import repo_code, our_code"
        )

    def test_records_code_executed_under_a_watched_root_and_nothing_else(
        self, tmp_path: Path, modules: str
    ) -> None:
        [record] = run_hooked(tmp_path, modules)
        assert record["executed"] == [
            os.path.realpath(tmp_path / "watched" / "repo_code.py")
        ]
        assert record["modules_left"] == ["repo_code"]
        assert record["trust_decisions"] == []

    def test_does_nothing_without_a_log(self, tmp_path: Path, modules: str) -> None:
        assert run_hooked(tmp_path, modules, log=False) == []
        assert not (tmp_path / "log.jsonl").exists()


CAUGHT = {"argv": ["-c"], "executed": ["/c.py"], "modules_left": ["s10_control"]}
CLEAN = {"argv": ["gdi", "convert"], "executed": [], "modules_left": []}


class TestFailures:
    def test_a_caught_control_and_clean_runs_pass(self) -> None:
        assert s10_check.failures([CAUGHT], [CLEAN], [], {"gdi convert": 0}) == []

    @pytest.mark.parametrize(
        "control, runs, static, codes, expected",
        [
            pytest.param([], [CLEAN], [], {}, "detector is blind", id="blind"),
            pytest.param([CAUGHT], [], [], {}, "hook did not load", id="no-runs"),
            pytest.param(
                [CAUGHT],
                [{**CLEAN, "executed": ["/models/configuration.py"]}],
                [],
                {},
                "executed /models/configuration.py in gdi convert",
                id="executed",
            ),
            pytest.param(
                [CAUGHT],
                [{**CLEAN, "modules_left": ["transformers_modules.x"]}],
                [],
                {},
                "left transformers_modules.x",
                id="module-left",
            ),
            pytest.param(
                [CAUGHT], [CLEAN], ["*.py: /m/a.py"], {}, "/m/a.py", id="static"
            ),
            pytest.param(
                [CAUGHT], [CLEAN], [], {"gdi convert": 1}, "exited 1", id="exit"
            ),
        ],
    )
    def test_each_finding_fails(
        self,
        control: list[dict[str, Any]],
        runs: list[dict[str, Any]],
        static: list[str],
        codes: dict[str, int],
        expected: str,
    ) -> None:
        problems = s10_check.failures(control, runs, static, codes)
        assert any(expected in p for p in problems), problems


class TestScanModelsDir:
    def test_finds_python_and_auto_map_but_not_the_manifests_record(
        self, tmp_path: Path
    ) -> None:
        model = tmp_path / "local--granite-vision"
        (model / "sub").mkdir(parents=True)
        (model / "sub" / "modeling.py").write_text("")
        (model / "config.json").write_text(json.dumps({"auto_map": {}}))
        (model / "tokenizer.json").write_text("{}")
        (model / MANIFEST_NAME).write_text(json.dumps({"auto_map": "nested"}))
        (tmp_path / MANIFEST_NAME).write_text(
            json.dumps({"stripped": ["config.json:auto_map"]})
        )
        assert s10_check.scan_models_dir(tmp_path) == [
            f"*.py: {model / 'sub' / 'modeling.py'}",
            f"auto_map: {model / 'config.json'}",
            f"auto_map: {model / MANIFEST_NAME}",
        ]

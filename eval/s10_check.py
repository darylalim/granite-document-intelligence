"""S10's no-repo-code check (§3.13 rule 2), re-run after a model or dependency move.

Run by hand on the development Mac, never in CI — it loads the real models. From
the repository root, with the Mac otherwise idle:

    uv run python eval/s10_check.py                  # the run paths, offline
    uv run python eval/s10_check.py --convert-model  # also `gdi convert-model`

Every process it starts loads `s10_hook/sitecustomize.py`, which records code
executed from the Hugging Face home, a fresh `HF_MODULES_CACHE` and the models
directory. It runs, in order:

1. a **positive control** that imports a module from a watched root, which the
   detector must catch, or the check proves nothing;
2. with `--convert-model`, `gdi convert-model` (the `mlx_vlm.convert` path; it
   rebuilds the models directory and may download the 8 GB bf16 source);
3. `gdi convert` on GAO-08-14 (a chart) and the W-9 with its schema: granite-docling,
   the figure classifier, the chart stage, Phase B and the chunking tokenizer;
4. `mlx_vlm.load` of the 8-bit Granite Vision at mlx-vlm's default trust, which is
   True for `granite4_vision` (S10);

then scans the models directory for `*.py` files and `auto_map` keys. Exit
status: 0 when the control was caught and nothing else was, 1 otherwise. Work
files go to `eval/runs/s10/`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval"
HOOK_DIR = EVAL_DIR / "s10_hook"
CHART_PDF = EVAL_DIR / "corpus" / "gao-08-14-excerpt.pdf"
FORM_PDF = EVAL_DIR / "corpus" / "irs-w9-filled.pdf"
FORM_SCHEMA = EVAL_DIR / "expected" / "irs-w9-filled" / "kvp" / "schema.json"
CONTROL_MODULE = "s10_control"
# `manifest.json` names what `gdi convert-model` stripped, `auto_map` among it.
MANIFEST = "manifest.json"


def hf_roots() -> list[Path]:
    """Where Hugging Face code can come from: its home, and its hub cache if moved."""
    home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    roots = [home]
    if hub := os.environ.get("HF_HUB_CACHE"):
        roots.append(Path(hub))
    return roots


def scan_models_dir(models_dir: Path) -> list[str]:
    """Every `*.py` file, and every JSON file but the manifest holding `auto_map`."""
    found = [f"*.py: {p}" for p in sorted(models_dir.rglob("*.py"))]
    found += [
        f"auto_map: {p}"
        for p in sorted(models_dir.rglob("*.json"))
        if p.name != MANIFEST or p.parent != models_dir
        if "auto_map" in p.read_text(encoding="utf-8", errors="replace")
    ]
    return found


def read_log(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


def failures(
    control: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    static: list[str],
    exit_codes: dict[str, int],
) -> list[str]:
    """What the check failed on; empty means it passed."""
    problems: list[str] = []
    if not any(r["executed"] and CONTROL_MODULE in r["modules_left"] for r in control):
        problems.append("the positive control was not caught: the detector is blind")
    for record in runs:
        for name in record["executed"]:
            problems.append(f"executed {name} in {' '.join(record['argv'])}")
        for name in record["modules_left"]:
            problems.append(f"left {name} in sys.modules in {' '.join(record['argv'])}")
    if not runs:
        problems.append("no monitored process reported: the hook did not load")
    problems += [f"in the models directory: {item}" for item in static]
    problems += [f"{step} exited {code}" for step, code in exit_codes.items() if code]
    return problems


def run_step(
    name: str, argv: list[str], env: dict[str, str], log: Path
) -> tuple[int, list[dict[str, Any]]]:
    """Run one monitored step; return its exit code and the records it logged."""
    print(f"== {name}", file=sys.stderr, flush=True)
    before = len(read_log(log))
    code = subprocess.run(argv, env=env, cwd=ROOT, check=False).returncode
    return code, read_log(log)[before:]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--convert-model",
        action="store_true",
        help="also run `gdi convert-model` under the detector (rebuilds the models)",
    )
    parser.add_argument(
        "--work-dir", type=Path, default=EVAL_DIR / "runs" / "s10", help="scratch"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from granite_document_intelligence import config

    models_dir = config.models_dir()
    work: Path = args.work_dir
    shutil.rmtree(work, ignore_errors=True)
    control_dir, modules, out = work / "control", work / "modules", work / "out"
    for folder in (control_dir, modules, out):
        folder.mkdir(parents=True)
    (control_dir / f"{CONTROL_MODULE}.py").write_text("X = 1\n")
    log = work / "log.jsonl"

    roots = [*hf_roots(), modules, models_dir, control_dir]
    env = dict(os.environ)
    env.update(
        S10_LOG=str(log),
        S10_ROOTS=os.pathsep.join(map(str, roots)),
        HF_MODULES_CACHE=str(modules),
        PYTHONPATH=os.pathsep.join(
            filter(None, [str(HOOK_DIR), os.environ.get("PYTHONPATH")])
        ),
    )
    offline = {**env, "HF_HUB_OFFLINE": env.get("HF_HUB_OFFLINE", "1")}
    python = sys.executable
    gdi = str(Path(python).parent / "gdi")

    codes: dict[str, int] = {}
    codes["control"], control = run_step(
        "positive control",
        [
            python,
            "-c",
            f"import sys; sys.path.insert(0, {str(control_dir)!r}); import {CONTROL_MODULE}",
        ],
        offline,
        log,
    )
    runs: list[dict[str, Any]] = []
    steps: list[tuple[str, list[str], dict[str, str]]] = []
    if args.convert_model:
        # Online unless the caller set HF_HUB_OFFLINE: it may need the bf16 source.
        steps.append(("gdi convert-model", [gdi, "convert-model"], env))
    steps += [
        (
            "gdi convert: Phase A, charts, KVP, chunks",
            [
                gdi,
                "convert",
                str(CHART_PDF),
                str(FORM_PDF),
                "--schema",
                str(FORM_SCHEMA),
                "-o",
                str(out),
                "--force",
                "--quiet",
            ],
            offline,
        ),
        (
            "mlx_vlm.load at its default trust",
            [
                python,
                "-c",
                "import sys, mlx_vlm; mlx_vlm.load(sys.argv[1])",
                str(models_dir / config.GRANITE_VISION_FOLDER),
            ],
            offline,
        ),
    ]
    for name, step_argv, step_env in steps:
        codes[name], records = run_step(name, step_argv, step_env, log)
        runs += records

    static = scan_models_dir(models_dir)
    leftover = sorted(p.name for p in modules.rglob("*") if p.is_file())
    static += [f"modules cache holds {name}" for name in leftover]
    problems = failures(control, runs, static, codes)

    print("\n# S10 no-repo-code check\n")
    print(f"- models directory: {models_dir}")
    print(f"- monitored processes: {len(runs)} (plus the control)")
    for record in runs:
        for decision in record["trust_decisions"]:
            print(f"- trust decision in {' '.join(record['argv'])}: {decision}")
    print(f"\n**Verdict: {'FAIL' if problems else 'PASS'}**\n")
    for problem in problems:
        print(f"- {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

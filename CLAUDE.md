# CLAUDE.md

**Granite Document Intelligence** — a Python library with a `gdi` CLI that turns multi-page PDFs into Markdown and DoclingDocument JSON, chart CSV and summaries, tables, schema-driven key-values and RAG chunks. It runs locally on a 16 GB Apple Silicon Mac, with IBM's granite-docling and Granite Vision 4.1 on MLX, orchestrated by Docling. It replaced this repository's Streamlit text app (last release `v0.6.0`).

**The design record is [`docs/plans/pdf-pipeline.md`](docs/plans/pdf-pipeline.md)** (the "plan"; `§` references below point into it), with the Phase 0 evidence in [`docs/plans/pdf-pipeline-spikes.md`](docs/plans/pdf-pipeline-spikes.md). Every decision there carries its reason and its measurement. This file is the working summary. When the two disagree, the plan wins, and the disagreement is a bug in this file.

## Rules

Each is justified in the section named. The reasons are non-obvious and were paid for in failures — read the justification before deciding an exception is safe.

- **Never hand-edit `uv.lock`** — regenerate it with `uv lock`; the hook blocks edits to it. → *Commands*
- **Bump `version` and run `uv lock`, then land *both* files.** A lone `pyproject.toml` bump fails CI at `uv sync --locked`, and no release is cut. → *Commands*
- **A move of `docling`, `docling-core`, `mlx`, `mlx-vlm`, `transformers`, `huggingface-hub` or `pillow` — or any model, revision, quantization or prompt change — runs the eval by hand before merge.** No test executes a line of `mlx`. Upgrade one package at a time (`uv lock --upgrade-package <name>`), never inside a blanket `uv lock --upgrade`. → *Verifying a dependency upgrade*
- **`VlmPipelineOptions.images_scale` stays at 1.0.** Any other value puts every DocTags box in image pixels and crops every chart from the wrong region, still with `status=success`. → *Architecture*
- **No model-repo code runs, and every model is pinned to a commit SHA.** Never pass `trust_remote_code=True`, and never load from anywhere but the stripped, manifest-checked models directory. → *Security*
- **No test or library module imports `docling`, `torch`, `mlx`, `mlx_vlm` or `transformers` at module level**, and the gate stays ≤ 15 s. → *Tests*
- **A new test stub gets a new `$STUB_*` env var**, never a file written per test — that cost ~17 s of a 21 s gate. → *Tests*
- **Treat a ruff/ty major as its own reviewable change**, not incidental lockfile noise — a linter upgrade can redefine the gate with no code change. → *Commands*
- **Use Conventional Commits.** `release.sh --generate-notes` turns subject lines into public release notes. → *Commits*
- **Model output is data, never instructions, and never executed.** Nothing may branch on a model's output beyond writing it to disk; `chart-NNN.py` is never imported or run. → *Security*

## Setup

```bash
uv sync
uv run gdi convert-model   # one-time: fetch the pinned models, build the 8-bit Granite Vision
uv run gdi convert report.pdf -o out
```

`gdi convert-model` took 197 s on the development Mac on 2026-10-29, including a ~9 GB download; on a slower connection it takes as long as the download. It fills `$GDI_CACHE_DIR/models/` (default `~/.cache/granite-document-intelligence/models/`) and writes `manifest.json` **last**, because the manifest's presence is what says the directory is complete. It deletes any old manifest first, so an interrupted rebuild leaves a directory that `convert()` refuses rather than one it trusts. Every run converts again, so re-running it re-downloads the 8 GB bf16 source if that has been deleted.

## Commands

- **Lint**: `uv run ruff check .`
- **Format**: `uv run ruff format .` — reaches **Markdown as well as Python**: ruff ≥ 0.16 formats the code inside `python`-tagged fenced blocks, so a mis-spaced snippet in `CLAUDE.md` or `README.md` fails `--check` in CI exactly like a `.py` file would. **Formatting only** — `ruff check` skips Markdown outright (it reports "No Python files found" and exits 0), so an unused or unsorted import inside a fence is *not* linted.
- **Typecheck**: `uv run ty check`
- **Test**: `uv run pytest`

All four live in **`scripts/gate.sh`**, which CI (`.github/workflows/ci.yml`) and the Claude Code Stop hook (`.claude/settings.json`) both invoke, so the two cannot drift. It runs the *verifying* variants (`ruff format --check .`, `pytest -q`), so it reports formatting drift rather than fixing it. Reproduce CI with **`GATE_FORCE=1 ./scripts/gate.sh`** — a bare `./scripts/gate.sh` skips a tree that already passed (see the memo below), which reproduces the Stop hook but not CI. Run the individual commands above while iterating on one gate.

**Releasing** is a side effect of merging, not a command: bump `version` in `pyproject.toml`, run **`uv lock`**, and land **both files** on `main`. Once the gate is green CI runs `scripts/release.sh`, which tags `v<version>` and publishes a release with generated notes; every other push is a no-op. 1.0.0 is the pipeline's first release (plan §11); `v0.6.0` was the text app's last.

`uv.lock` records the *project's own* version, so the two files move together. This is easy to half-do and the failure is quiet: a commit carrying only `pyproject.toml` fails CI at `uv sync --locked`, and since `release` needs `check`, no release is cut — it looks like the automation stopped working. Nothing local catches it, because every gate command runs under `uv run`, which **silently re-locks** before the command starts, so the working tree always looks consistent no matter what is staged. Running `uv lock` explicitly makes the update deliberate and visible before you stage rather than a side effect you might not commit. `uv.lock` is on the hook's blocked-edit list, so regenerate it — never hand-edit it.

Running the release script by hand is idempotent and refuses to tag a commit that isn't on `origin/main`, but it is not a dry run — on an untagged version bump it publishes for real.

No dev tool carries a version constraint in `pyproject.toml`. `uv.lock` still pins exact versions and CI runs `uv sync --locked`, so any given build is reproducible — but nothing bounds what the *next* `uv lock --upgrade` pulls in, which means a **linter upgrade can redefine the gate without a line of our code changing**: ruff 0.16 expanded its default rule set (`BLE001`, see *Error handling*) and widened the formatter to Markdown, both of which landed as gate failures on a routine `uv lock --upgrade`. Treat a ruff/ty major as its own reviewable change rather than incidental lockfile noise. For ty, "major" undersells it: it is pre-1.0 and promotes **warn-by-default, stable** rules in patch releases (`redundant-condition` in 0.0.79, `truthiness-test-of-callable` and `truthiness-test-of-iterable` in 0.0.83), and `ty check` exits 1 on a warning, so each such release widens the gate. `ruff check --preview .` and `ruff format --check --preview .` are the early warning for the next ruff minor (on 2026-10-29 the preview formatter would reformat 7 files).

When working with Python, invoke the relevant `/astral:<skill>` for uv, ty, and ruff to ensure best practices are followed.

## Verifying a dependency upgrade

Green gates say nothing about the models: every test mocks at the Docling / `mlx_vlm` boundary, so no test executes a line of `mlx`. The quality eval does. Run it by hand on the development Mac:

- after moving `docling`, `docling-core`, `mlx`, `mlx-vlm`, `transformers`, `huggingface-hub` or `pillow` (plan §2.2);
- after any model, revision or quantization change — a bump of a pinned SHA in `config.py` counts as a model change (§3.13 rule 1);
- after any change to a prompt, including the KVP prompt in `kvp.py`, or to a tuned value (`IMAGES_SCALE`, `CHART_CROP_SCALE`, `KVP_PAGE_SCALE`, a token cap).

```bash
uv run python eval/score.py                      # cold run + 3 timed runs, about 30 min
uv run python eval/score.py --repeats 1 --no-cold --label try-x   # a quick look
```

**Run it on an idle Mac, on AC power, with nothing else running** — not a test, a `uvx` install or a second model. The timings are the budgets' input, and the cold start is a single measurement with no median to absorb noise: on 2026-10-30 a pip-audit install and a pytest run overlapping the release run's cold run took cold start from 8.01 s to 10.48 s, past its 8.91 s budget, with the code unchanged. A budget failure from a run that overlapped other work is re-measured, not treated as a regression.

It reads the corpus in `eval/corpus/` and `eval/expected/`. It runs offline (`HF_HUB_OFFLINE=1` unless already set), converts into the gitignored `eval/runs/`, prints a Markdown report and writes `eval/results/<date>-<sha>[-<label>].json`.

**Pass** means `score.py` exits 0. That happens when no score drops below the committed baseline (`eval/results/2026-10-29-3025669-baseline.json`, re-baselined when the scan joined the corpus) by more than that metric's tolerance, every time budget in `eval/budgets.toml` is met, peak memory is ≤ 10 GB, no page hits the token cap outside the named exceptions, and the adversarial PDF passes. **Read the report as well as the exit status.** The old app learned that a script check passes degenerate output, which is why the scorer compares values and never only whether something parsed (§8.1). Commit a results file when it backs a decision. Raising a time budget needs a committed results file that shows why (§3.11).

The same procedure has two more checks:

- **S10's no-repo-code check** (§3.13 rule 2): `uv run python eval/s10_check.py`, about 2 min, offline; add `--convert-model` after a model or `mlx-vlm` change to cover `gdi convert-model` too (it rebuilds the models directory and may download the 8 GB bf16 source). `eval/s10_hook/sitecustomize.py` is loaded into every process it starts and records code executed from the HF home, a fresh modules cache and the models directory. It runs a positive control first, then `gdi convert` (charts, KVP, chunks) and a default-trust `mlx_vlm.load`, and scans the models directory. **Pass** means it exits 0: the control was caught, no model-repo code executed, no `transformers_modules` were left loaded, and there is no `*.py` or `auto_map` in the models directory outside `manifest.json`'s record of what was stripped.
- **A vulnerability audit of the locked environment** (§3.13 rule 6). Verified on 2026-10-29 with pip-audit 2.10.1 (170 packages, no known vulnerabilities):

```bash
uv export --frozen --no-emit-project --format requirements.txt -o requirements.txt
uvx pip-audit --strict --disable-pip --require-hashes -r requirements.txt
rm requirements.txt
```

`--disable-pip --require-hashes` audits exactly the locked, hashed pins without building a virtualenv. Dependabot alerts read `uv.lock` too and have been on since PR 3.

## Code Style

- snake_case for functions/variables, PascalCase for classes
- Type annotations on all parameters and returns
- isort with combine-as-imports (configured in `pyproject.toml`)
- Comments cite the plan section or spike that settled a value (`# … (S7)`, `(§3.13 rule 2)`), so a change can be judged against its reason

## Commits

Conventional Commits — `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`, `ci:`, `build:`, `style:` — used uniformly across the history. `scripts/release.sh` passes `--generate-notes`, so these subject lines become the **public release notes**; a non-conforming subject ships to users. History on `main` is linear (no merge commits). The rebuild runs as one PR per step, each on the branch the plan's §10 table names, and §10's progress table is updated in the PR that lands each step.

## Models

| Stage | Model | Runtime | Weights |
|---|---|---|---|
| Page → DocTags → DoclingDocument | `ibm-granite/granite-docling-258M-mlx` | Docling `VlmPipeline` on its MLX engine | 0.63 GB, bf16 |
| Which pictures are charts | `docling-project/DocumentFigureClassifier-v2.5` | Docling, **PyTorch** via transformers | small |
| Charts → CSV + summary, and key-values | `ibm-granite/granite-vision-4.1-4b`, **converted locally to 8-bit MLX** | our code (`charts.py`, `kvp.py`) via `mlx-vlm` | 4.92 GB |
| Chunking tokenizer | `ibm-granite/granite-embedding-english-r2` | HF `tokenizers` | tokenizer only |

All four are pinned to commit SHAs in `config.py` (§3.13 rule 1). Granite Vision is pinned at `dd48e97…`, the revision Docling's chart preset pins. The three Docling-side models are fetched at their SHAs into the models directory by `gdi convert-model`, because Docling ignores `revision` when it loads from `artifacts_path`. The tokenizer keeps its revision at its loader.

- **Why two models.** Granite Vision 4.1 is an *extraction* model with weak full-page parsing (ParseBench 39.45). granite-docling emits DocTags (element types, boxes and reading order), which are what find the charts to hand Granite Vision.
- **Why 8-bit, converted here.** IBM publishes no MLX build of 4.1. bf16 peaks at 10.12 GB with Granite Vision alone, against a 12.71 GB Metal working set. 8-bit showed no measurable regression (S6: identical chart values on 10 charts, byte-identical KVP on 3 pages). It is 9.26 bits per weight because mlx-vlm leaves the vision tower in bf16. Community conversions have no published evaluation.

**MLX for inference, not MLX-only.** The old app was MLX-only in both senses, and this project is not: Docling imports torch as soon as `VlmPipeline` is imported (~0.3 GB at peak), and the figure classifier runs on it. torch is in the lock regardless. An ONNX classifier was measured (S9) and buys nothing while Docling imports torch itself. Reopen that only when Docling makes both imports lazy (docling-project/docling#4966; watch it, don't comment, don't re-offer an issue).

**Effective transformers floor: 5.16 on this Mac.** No single package states it: `docling-slim` excludes 5.9–5.15 on darwin (an MPS crash), and mlx-vlm needs ≥ 5.14. `pyproject.toml` declares `transformers>=5.21.0,<6`, so `uv` enforces it. If a future transformers drops native `granite4_vision`, the load fails loudly rather than falling back to repo code. Pinning or vendoring at that point is the user's call.

## Dependencies

Floors are the versions PR 4's hand run executed the models on, not researched ones; caps guard API drift (plan §2.2). The `pyproject.toml` comment carries the same summary.

| Package | Constraint | Why |
|---|---|---|
| `docling[vlm]` | `>=2.143.1,<3` | its minor is its release unit (31 releases in three months); `[vlm]` is `docling`'s name for the VLM extra — `models-vlm-inline` exists only on `docling-slim`, and `uv` merely *warns* on an unknown extra |
| `docling-core` | `>=2.105.0,<3` | imported directly |
| `mlx-vlm` | `>=0.7.10,<0.8` | pre-1.0; a minor can break the API |
| `transformers` | `>=5.21.0,<6` | the darwin floor above |
| `pillow` | `>=13.0.0` | on the model's input path (mlx-vlm resizes every image with it); 12.3.0 → 13.0.0 changed one chart summary on a byte-identical crop, so it moves under the eval |
| `jsonschema`, `referencing` | floors only | the KVP validator's fetch-nothing registry (§3.13 rule 8) |
| `pypdfium2`, `huggingface-hub` | floors only | the chart crop and `gdi convert-model` import them |
| `python-dotenv` | floor only | `.env` for `HF_TOKEN` |
| `mlx` | **not declared** | floats under `mlx-vlm`; the lock pins it |

The `ui` dependency group holds `streamlit` for the v1.x UI prototypes under `docs/plans/ui-*`, which `ty` checks. The library never imports it, and nothing in v1 may assume a UI (§1, §14, §15). A cap move (`<0.8`, `<3`) is its own PR. Lowering a floor needs the eval on the lower version, resolved with `uv lock --resolution lowest-direct` and installed with `uv sync --frozen`, since a plain `uv sync` silently re-resolves to the highest versions (S1).

## Architecture

```text
src/granite_document_intelligence/
  api.py        # the public surface: convert, convert_many, ConvertOptions, result types
  cli.py        # argparse `gdi`; a thin wrapper over api.py
  config.py     # model ids + pinned SHAs, tuned values, GDI_CACHE_DIR
  models.py     # convert_model(), verify_models_dir(): fetch, convert, strip, manifest
  pipeline.py   # one PDF through every phase, in memory; what convert() runs
  phase_a.py    # build + run Docling's DocumentConverter, then release it
  charts.py     # our chart stage: Granite Vision on a 1.5× crop, raw CSV kept
  kvp.py        # Phase B: prompt, generate, parse, validate (schema checks live here)
  chunk.py      # HybridChunker over the DoclingDocument
  export.py     # writes the output tree; run.json last; atomic swap
  batch.py      # input expansion, resume, overlap refusal, continue-on-error
  safety.py     # limits, %PDF- check, path containment, error records
  runlock.py    # the process-level run lock
  memory.py     # release helper, MLX cache limit, peak-memory reporting
```

**The library is the product** (§3.7). `convert()` returns data and writes nothing, and `ConvertResult.save()` is the only writer. `convert_many()` owns resume, so a CLI and a later UI cannot diverge on it. Importing the package loads no model and imports neither Docling nor MLX. A submodule named `convert` would shadow the exported `convert()` function, which is why Phase A lives in `phase_a.py`.

**Memory phases** (§3.1, amended by PR 4 and PR 5). Each step is released before the next one loads:

1. **Phase A.** granite-docling runs on every page, then the figure classifier. Peak 1.61 GB.
2. **The release.** Drop references, `gc.collect()`, then `mx.clear_cache()`. S4 proved this takes active memory to 0.
3. **The chart stage**, if any chart was found. One Granite Vision instance reads our 1.5× render of each chart. Peak **6.68 GB**, the whole run's.
4. **Phase B**, with `--schema`. It runs on the *same* instance, at a 6.51 GB peak, and the instance is released in a `finally` afterwards.

The converter is built and released per PDF, even in a batch: holding it would keep granite-docling and the classifier resident through every chart stage. The ceiling is **10 GB** per phase, derived from the Metal working set; it is not a measurement and does not move. `mx.set_cache_limit(1 GiB)`: MLX's default of 16.32 GB bounds nothing on a 16 GB Mac.

**Load-bearing values** (`config.py`, each shipped as one tested value; §3.14 rule 2):

- **`IMAGES_SCALE = 1.0`.** Docling maps DocTags boxes onto the page image rendered at this scale. Any other value silently corrupts every box and every crop (S7).
- **granite-docling's page scale is 2.0**, its preset's. It is a quality knob, not a speed one: every page is resized to a 2048 px edge anyway, and each other scale tried invented or lost pages (S11).
- **`CHART_CROP_SCALE = 1.5`** is our own render of each chart's box. Docling's crop is 72 dpi, and no option reaches it. On 69 cells, 1.5× matched 43 against Docling's 37, for +2.3 s a chart (S11). The classifier keeps Docling's crop, so the choice of charts does not change.
- **Chart extraction is our code, not Docling's** (amended by PR 4). Docling dropped a chart's CSV silently when a label held an unquoted comma, and it kept no raw response. `charts.py` keeps the raw text, parses it with `csv` using the commonest row width, writes `chart-NNN.raw.txt` when no table comes back, and checks the summary's numbers against its own CSV, warning when the two disagree.

**Degenerate pages are detected after generation**, from each page's raw DocTags rather than the exported document (§3.10). A page is flagged when it reaches `max_new_tokens`, when its DocTags do not end in `</doctag>`, or when Docling's `DocTagsRepetitionStopper` trips on any 640-character window. The third check is load-bearing, because loops that close themselves under the cap are common. In-generation stopping was measured and rejected: it missed loops and slowed clean pages by up to 2.7× (S11). Known capped pages on the public corpus are named exceptions in `eval/budgets.toml`.

**Batch and resume** (§3.8):
- A PDF is **done** when its `run.json` exists, records success, and matches the input's SHA-256 and the current options. Anything else re-runs.
- `max_pages` and `max_file_mb` are not part of "done".
- Output folders that would overlap, case-folded, are refused before anything runs.
- A failed PDF writes an error `run.json`, and the batch continues.
- Exit codes: **0** when every PDF was done or skipped, **1** when any failed or the run could not start, **2** on a usage error, **130** on Ctrl-C.
- Resume is per PDF only. Page-level resume failed its feasibility gate in PR 4: Docling has no public way to accept cached DocTags. Reopen if Docling exposes one (§3.14 rule 7).

**Storage** (§3.12). Files are the source of truth, and there is no database in v1. A PDF's tree is built in `<pdf>.partial/`, `run.json` last, and then swapped into place through `<pdf>.old/`. A re-run therefore never leaves stale files beside a new `run.json`, and startup recovers a crash's residue. **Only a folder gdi wrote is ever replaced, renamed or deleted** (`export.is_ours`: nothing but §3.5 names). Anything else at `<pdf>/`, `<pdf>.partial/` or `<pdf>.old/` fails that PDF with `ForeignFolderError` before any model loads. v1.0.0 lacked this, and `gdi convert Documents.pdf -o ~` would have deleted `~/Documents` (PR 11). Images are referenced files, never base64. The bf16 source is deleted only when the user opts in (`--delete-source`).

**One run at a time per Mac** (§3.14 rule 4). An exclusive `fcntl.flock` on `$GDI_CACHE_DIR/run.lock` is held for the whole run. A second run waits, or exits 1 under `--no-wait`; through the API, `wait=False` raises `RunLockedError`. A `flock` belongs to one open file, so a process that takes it twice deadlocks itself. That is why `convert_many` takes it once and runs each PDF through `pipeline.run`, never through `convert()`.

### Performance

These are the baseline medians from 2026-10-27, measured on an M2 Pro with 16 GB on AC power, with docling 2.143.1, mlx 0.32.4 and mlx-vlm 0.7.10 (§3.11). The budgets in `eval/budgets.toml` are these figures × 1.25; the scanned row is the 2026-10-29 re-baseline's, which met every other budget:

| Metric | Median |
|---|---|
| Phase A | 5.66 s/page |
| Phase A, scanned | 7.04 s/page (2026-10-29) |
| Chart | 8.28 s |
| KVP page | 6.01 s |
| Cold start | 7.13 s |
| Peak memory | 6.68 GB |

Sustained throughput is ~10 pages/min, with no throttling over ~10 min (S8). A degenerate page costs ~32–37 s. A scanned page costs what a born-digital one does when it converts cleanly (2.76 s against 2.74 s median generation, S8's scanned half); the scan's 7.04 s/page is its two degenerate pages. The real scan did not show the stand-in's image-only collapse, and page scale stays 2.0 for scans (decided with the user, §12).

No public speed lever exists today (S11). Docling's page batch size is inert on MLX, and the VLM pipeline has no overlap. `mlx_vlm.batch_generate` at batch 4 is 18% faster, but it is not bit-identical and ships only by replacing a Docling engine method. Reopen it when Docling exposes batched MLX generation.

### Error handling

Two `except Exception` handlers carry an inline `# noqa: BLE001` naming their role: `batch.py` (one PDF's failure must not stop the batch) and `eval/score.py` (unscored chunk sizes must not fail the run). The suppression is per-line, not a project-wide `ignore`, so a third, unintentional blind catch still fails the lint gate. Stored errors are `safety.error_record`'s: the exception type and message, plus our own frames as `file:line in function`, with no source lines and no locals (§3.13 rule 5).

## Security

The full model is plan §3.13; `SECURITY.md` carries the public version.

**Threat model.**
- **Adversary:** the author of a PDF the user processes, who controls every byte of it.
- **Untrusted:** every byte of the PDF and everything a model derives from it.
- **Trusted:** the user, their CLI arguments, `ConvertOptions`, their KVP schema, model weights at pinned revisions and dependencies at locked versions.
- **Out of scope:** multi-user use, serving over a network, defending against the user, a compromised Mac.

Two principles follow: PDF-derived data never steers the program, and everything the program loads or executes is pinned and local.

1. **Every model is pinned to a commit SHA.** For the Docling-side models the pin is enforced at fetch. `convert()` compares `models/manifest.json` with `config.py` before loading and refuses a mismatch, naming `gdi convert-model`. `run.json` records the manifest.
2. **No model-repo code runs.** mlx-vlm's patched `AutoProcessor` defaults `trust_remote_code` to True for `granite4_vision`, and Docling's MLX engine cannot pass False (S10). Not passing it is therefore not enough, and the code is removed from the artifact instead. `gdi convert-model` converts with `trust_remote_code=False`, then deletes every `*.py` file and every `auto_map` key, nested ones included, and records both in the manifest. `convert()` refuses any folder that still holds either one, and our own loaders pass `False` as a second guard.
3. **Weights are safetensors only.** granite-embedding ships a pickle beside its safetensors; any future weight load passes `use_safetensors=True`.
4. **The parsers are unsandboxed in v1.** pdfium and docling-parse run in-process. This is stated in the README and in `SECURITY.md`. The upgrade path is conversion in a separate low-privilege process.
5. **No secrets or document content in logs**, `run.json` or stored tracebacks. `test_safety.py` asserts this with a sentinel `HF_TOKEN` and a sentinel string in the PDF.
6. **Dependency vulnerabilities are watched.** Dependabot alerts are on, and the pip-audit step runs under *Verifying a dependency upgrade*.
7. **Inputs are recognised by content.** A file must start with `%PDF-` at byte 0, whatever its extension, and the size limit is checked before pdfium opens it.
8. **The KVP schema is local and bounded.** It must be a JSON object of at most 256 KB, and any `$ref` or `$dynamicRef` not starting with `#` is refused. The validator is built with an explicit empty `referencing.Registry`. That is load-bearing: jsonschema given no registry falls back to one that fetches an unknown `$ref` with `urlopen` and only warns.
9. **Repository hygiene.** There is a `SECURITY.md`, and CI's actions are pinned to commit SHAs.

Further rules from §3.10:
- **Generated chart code** is written with an "Untrusted: review before running" header, and nothing imports or runs it.
- **Output paths** come only from the input's relative path, never from PDF metadata, and every write is checked to land under the output directory.
- **Network:** `enable_remote_services=False` is set, and the Hugging Face Hub is the only network contact.
- **Control tokens:** our code passes page *images* to Granite Vision, so control-token injection does not apply today. If a stage ever formats PDF text into a prompt, port the old app's `_strip_control_markers` with its tests: it dropped every `tokenizer.get_added_vocab()` string, and it is in git history before PR 3.

## Configuration

`pyproject.toml` holds:
- packaging: `uv_build`, the `gdi` console script and a `src/` layout, so tests import the installed package and there is no `pythonpath`;
- ruff lint: `extend-select = ["I"]` turns on isort atop ruff's defaults, with `combine-as-imports`;
- ty: `python-version = "3.12"`, plus `extra-paths = ["eval"]`, because `eval/score.py` imports `eval/matching.py` as a sibling script;
- `default-groups = ["dev", "ui"]`.

`LICENSE` — Apache-2.0 for the project's code, declared via PEP 639 `license` / `license-files`. `THIRD_PARTY_NOTICES.md` carries the MIT notice for the KVP prompt copied from Docling (`kvp.extraction_prompt`; private in Docling, so it cannot be imported). Both are `license-files` entries, and `TestPackaging` asserts that every entry exists. The models are downloaded, not vendored, under their own licences (§9.1). The local 8-bit conversion is a modified copy of Apache-2.0 weights, so publishing it would require the licence and a notice of the modification.

`SECURITY.md` — how to report a vulnerability (GitHub private vulnerability reporting) and the threat model in short.

`README.md` — the user-facing account: setup, usage built on a corpus PDF (GAO-07-631, so the documented command is one the eval exercises), output layout, the Python API, dated performance figures, disk space and untrusted documents. No test guards it. A change to the CLI, the output layout or a measured figure has to be mirrored there by hand, and **every command in it is run as written** before a docs PR merges (§10). Its performance figures carry their date and versions.

`.python-version` — pins the project interpreter to `3.12`, matching `requires-python = ">=3.12"` and the ty target.

`.gitignore` — keeps pipeline output (`/out/`), model weights, PDFs at the root, `eval/private/`, `eval/runs/` and Docling's `debug_*/` out of git, ruff and the gate's fingerprint. An unignored output folder would put model-generated `chart-NNN.py` under the ruff gates and make the memo never match. `TestGitignore` runs `git check-ignore --no-index` on each pattern.

`scripts/gate.sh` — the four Commands in one place, invoked by both CI and the Stop hook. Its header comment argues each point at length; four properties are load-bearing, each pinned by an *executing* test in `TestHooksConfig` because source-level substring checks proved too weak to catch a mutated failure path (see Tests):

- **Exits `2`, not `1`.** Claude Code treats only `2` from a hook as blocking; `1` — what ruff, ty and pytest all actually return — is a *non-blocking* error that lets the turn end on a red gate. Any non-zero fails a CI job, so one convention serves both callers.
- **Reports on stderr**, the stream fed back on a block, while ruff and pytest print diagnostics to stdout. An empty capture (OOM kill, hook timeout, half-created `.venv`) is replaced with a message naming the exit code.
- **Runs every gate even after one fails**, so a single block names everything that is wrong. Claude Code stops honoring a Stop hook after 8 consecutive blocks (raise with `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`), and spending that budget one diagnostic at a time wastes it.
- **Skips only a tree that already passed** — a SHA-256 over `git ls-files -co --exclude-standard` against `.git/gate-ok`, written only on green. Every uncertainty runs: an empty file list, a missing memo, a red previous run, a non-empty `GITHUB_ACTIONS`, or `GATE_FORCE`.

It also `cd`s to `git rev-parse --show-toplevel` first: `ruff check .` and `pytest` resolve from the working directory, so a caller in a subdirectory would otherwise gate a narrower tree than it believes.

Two cheaper short-circuits were tried first and both **failed open**, because each inferred "nothing to check" from a *proxy* rather than from the gate's inputs. An empty `git status --porcelain` skipped exactly the turns that had just *committed* work, and a held `index.lock` disabled the gate outright. Honoring `stop_hook_active` made the hook one-shot: it blocked once, then let the next Stop through without verifying its own fix. A content hash has neither failure: committing does not alter file content, and nothing is recorded on a red gate. Two details are load-bearing:

- **An unreadable tree must not fall through to `shasum` of nothing.** That is `e3b0c442…`, a stable *non-empty* hash that could match a stored one. An empty file list is therefore a refusal, not an answer, and the pipeline reads `</dev/null` so `xargs` cannot block.
- **The memo is recorded *after* the run, not before.** Every gate command goes through `uv run`, which silently re-locks, so `uv.lock` can differ by the time the gates finish.

`scripts/release.sh` — cuts the GitHub release when `pyproject.toml`'s version has no tag yet, invoked by ci.yml's `release` job. It is a script rather than inline YAML because an escaped one-liner can't be run or tested. `TestReleaseWorkflow` pins each property below by executing it with `gh` and `uv` stubbed. Its governing rule is that **every uncertainty is a refusal**, because the artifact is a public tag people install.

- **The trigger is "no tag exists for this version", not "pyproject.toml changed in this push".** A diff-based trigger has no correct answer to *changed since what?*, while the tag lookup makes re-runs, force-pushes and manual invocations converge. It exits `0` both when it releases and when there is nothing to release, and plain `exit 1` on failure — not gate.sh's `2`, which is a hook contract.
- **The probe is `git/ref/tags/<tag>` (singular), and only a definite 404 may proceed.** The plural form prefix-matches (it returns 200 for `v0.1` off `v0.1.0`). A rate limit, an expired token and a 5xx all fail like a 404, so the script matches `(HTTP 404)` in the response rather than trusting the exit status.
- **Draft first, publish second — then adopt, never duplicate.** The tag comes into existence only at publish, so nothing can leave a tag with no release, which the probe would read as "already released" forever. GitHub *accepts* a second draft for the same `tag_name`, so the script lists drafts before creating one and adopts an existing draft, retargeting it to this run's commit.
- **The version comes from `uv version --short`**, a real TOML read. The guard rejects any character with no business in a tag.
- **A hand-run off `main` is refused.** Unless `GITHUB_ACTIONS` is exactly `true`, the target must be an ancestor of `origin/main`.

`--target` is `$GITHUB_SHA` (falling back to `HEAD`), so the tag lands on the commit the gate validated. An unresolvable commit is an error, because `gh` reads an empty `--target` as "use the default branch". Both `gh` and `uv` are checked for up front. Like gate.sh, it `cd`s to the repo root.

Neither script is shellchecked by any gate. Both were run through `shellcheck` and `actionlint` by hand. If that matters going forward, it needs to become a fifth gate rather than an assumption.

`.github/workflows/ci.yml` — GitHub Actions CI, two jobs.

**`check`** runs `scripts/gate.sh` under `uv sync --locked` on every push to `main` and every pull request.
- **Runner.** It runs on `macos-26` (Apple Silicon), because `mlx`/`mlx-metal` are `sys_platform == 'darwin'` in `uv.lock` and the arm64-only wheels install nowhere else. The label must stay inside `TestCIWorkflow.APPLE_SILICON_RUNNERS` (`macos-latest` / `macos-15` / `macos-26`). That is an explicit **allow-list**, not a glob or a floor: a floor stayed green on `macos-14` through that image's deprecation, and a glob admits the x64 `macos-*-intel` / `macos-*-large` labels. When an image retires, move the label and the allow-list forward together.
- **Pinning.** Third-party actions are pinned to **commit SHAs**, with the version in a comment (`actions/checkout@<sha> # v7.0.1`, `astral-sh/setup-uv@<sha> # v10.0.0`). A tag can be moved to new code after review, and a SHA cannot (§3.13 rule 9). `TestCIWorkflow` asserts that every `uses:` is a 40-hex SHA followed by a `# v…` comment.

**`release`** runs `scripts/release.sh`.
- **Conditions.** It has `needs: check`, so a tag never points at a red build, and `if: push && refs/heads/main`, so a pull request, including a fork's, can't release.
- **Runner.** It sits on `ubuntu-latest`, because it only reads a TOML field and calls the GitHub API.
- **Permissions.** Workflow permissions are `contents: read`; **only** the release job widens to `contents: write`.
- **Concurrency.** Its `concurrency` group never cancels in flight, because cancelling can land between the draft and the publish.

`.claude/settings.json` — the project-shared Claude Code hooks.
- **Format hook.** `ruff check --fix` runs on each edited `.py`, followed by `ruff format` on `.py` **and** `.md`.
- **Edit guard.** It blocks edits to `.env` / `.env.*` (the committed `.env.example` is exempt), `secrets.toml` and `uv.lock`.
- **Stop hook.** It runs `scripts/gate.sh`.
- **Personal overrides** go in the gitignored `.claude/settings.local.json`.

Both hooks parse their payload with **`jq`**, an undeclared dependency that macOS 26 ships at `/usr/bin/jq`. If `jq` is missing, the guard **fails open**. The guard covers `Edit|Write|MultiEdit`, not `Bash`, so it is an accident guardrail, not a security boundary.

**Lint-fix runs before format, and the order is load-bearing.** `ruff check --fix` can *produce* unformatted code: deleting a module's last import (`F401`) strands the blank lines that followed it, and `ruff check --fix` exits `0` when it fixed everything. Both commands run **even when the first fails**, accumulating into an `rc`. The hook exits `2` on a ruff failure for both extensions. On `PostToolUse` that does not block the edit, but it guarantees the diagnostic reaches Claude, since ruff writes to stdout and only stderr is fed back.

### Environment

`cli.main()` calls `load_dotenv()` after parsing arguments, so `gdi --help` stays instant. `load_dotenv()` never overrides a variable that is already set, and it does nothing when there is no `.env`, so the same code works locally and in a deployment. The library itself reads only the process environment.

- `HF_TOKEN` — an optional Hugging Face token with read scope. The models are public; without a token the Hub only rate-limits harder and logs an "unauthenticated requests" warning. Set it in `.env` locally (gitignored; copy `.env.example`) or as a real environment variable. It must never appear in `run.json`, logs or stored tracebacks (§3.13 rule 5).
- `GDI_CACHE_DIR` — where the models directory and `run.lock` live (default `~/.cache/granite-document-intelligence`). Tests point it at `tmp_path`, so no test touches the real cache.
- `HF_HUB_OFFLINE=1` — once the models are fetched, this makes a run fully offline. `eval/score.py` sets it unless it is already set.

## Tests

`tests/` — unit tests, all mocked: no model download, no real inference. One file per module (`test_api.py`, `test_batch.py`, `test_charts.py`, `test_chunk.py`, `test_cli.py`, `test_export.py`, `test_kvp.py`, `test_models.py`, `test_phase_a.py`, `test_pipeline.py`, `test_safety.py`), plus:

- `test_score.py` — the eval's scoring rules (`eval/matching.py`, `eval/score.py`), with every bad-prediction case mutation-checked (38 rule mutations, each caught; PR 7).
- `test_s10_check.py` — S10's detector, executed in a real subprocess so a hook that stopped seeing executed code fails here, plus the check's verdict and its models-directory scan.
- `test_tooling.py` — the silent-degradation guards for the repository's own machinery:
  - `TestCIWorkflow`: the Apple Silicon allow-list, delegation to `gate.sh`, `uv sync --locked`, the triggers and SHA-pinned actions.
  - `TestReleaseWorkflow`: `release.sh` executed in a temporary git repo with `gh`/`uv` stubbed, its argv logged and asserted.
  - `TestHooksConfig`: the edit guard, the format hook's order and exit codes, and gate.sh's exit-2, stderr and memo contracts, all executed rather than grepped.
  - `TestGitignore`.
  - `TestPackaging`: `license-files` and the console script.
  - `TestImportCost`.

Data-driven cases use `@pytest.mark.parametrize` with an `id=` on each `pytest.param`.

**The gate stays ≤ 15 s** (§3.14 rule 9), because the Stop hook runs it every turn. It measured 11.7–12.8 s over three forced runs on 2026-10-29. `TestImportCost` fails any module under `tests/`, `src/granite_document_intelligence/` or `eval/` that imports `docling`, `torch`, `mlx`, `mlx_vlm` or `transformers` at top level. Import them inside the test that needs them, and mock the rest. One in-test import of Docling's pipeline options alone costs ~2.4 s, because it brings torch.

**Behaviour is asserted by executing, not by grepping.** Several contracts appear in source under every arrangement of the code: the format hook's command order, gate.sh's exit code (`2` also occurs in its `cd`-failure branch), and release.sh's `--draft` (written twice). Substring checks stayed green when these were mutated. The suites that execute shell scripts (`TestReleaseWorkflow`, `TestHooksConfig`) share a **session-scoped `stub_bin` fixture**, and that is a performance contract. macOS scans every newly created executable the first time it runs: 241 ms against 4 ms for a re-run or a fresh symlink, since the scan is keyed to the inode. Writing stubs per test cost ~17 s of a 21 s gate. So the stubs are written once and varied through **`$STUB_*` environment variables**, and the scripts are **symlinked** into each throwaway repo. If a future stub needs per-test behaviour, add an environment variable. Do not go back to writing a file per test.

`eval/` — the quality eval (plan §8): `score.py`, `matching.py`, `budgets.toml` and committed `results/`, plus S10's check (`s10_check.py`, `s10_hook/`). It runs by hand, never in CI. See *Verifying a dependency upgrade*.

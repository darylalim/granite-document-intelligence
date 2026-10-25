# Plan: PDF document-intelligence pipeline (replaces the Streamlit text app)

Drafted 2026-10-18. Written to be executed in a **fresh session** with no memory of the
conversation that produced it — every decision below carries its reason and its evidence,
so the implementer can tell a load-bearing choice from a default.

**Status:** complete draft, reviewed cold on 2026-10-18 (18 findings fixed). Phase 0 is
under way — progress is in [`pdf-pipeline-spikes.md`](pdf-pipeline-spikes.md); no
production code exists. **To start or resume, paste Appendix A into a new session.**

## 0. At a glance

**What:** a Python library + `gdi` CLI that turns multi-page PDFs into Markdown/JSON,
chart CSV + summaries, tables, schema-driven key-values and RAG chunks — locally on a
16 GB M2 Pro, replacing this repo's Streamlit text app.

**Key decisions** (each argued in its section):

| Decision | Section |
|---|---|
| granite-docling-258M-mlx on every page + Granite Vision 4.1 (8-bit, converted locally) on charts and KVP; Docling orchestrates | §2, §3.1–3.2 |
| MLX for inference, not MLX-only — Docling imports torch and the picture classifier runs on it | §2.1 |
| Floors and caps for `docling` and `mlx-vlm`; any move re-runs the eval | §2.2 |
| Phase A → release → Phase B, never both models loaded; 10 GB peak ceiling | §3.1, §3.11 |
| Library first (`convert` returns, `save` writes); CLI is a thin wrapper | §3.7 |
| Files are the source of truth; no database in v1 | §3.12.1 |
| PDF content is untrusted; everything loaded or executed is pinned and local | §3.10, §3.13 |
| Hand-written expected outputs; regressions judged against a baseline with tolerances | §8.1, §8.2 |
| Apache-2.0 kept; obligations come from copying and publishing | §9.1 |
| Ten PRs, release 1.0.0 last | §10, §11 |

**Reading order for the implementer:** §0 → §16 (when v1 is done) → §17 (what could
derail it) → §4 (Phase 0, where work starts) → the rest as each PR needs it.

**v1 vs later:** §1–§13 and §16–§17 are v1. §14 (UI theme) and §15 (UI layout) are
designed and verified for **v1.x** and must not shape v1 code.

## 1. Goal

Turn **multi-page PDFs** (mixed born-digital and scanned) into structured output, locally
on a **MacBook Pro M2 Pro with 16 GB** of unified memory, using IBM Granite models on
**MLX**. Delivered as a **Python library with a CLI**: `pdf(s) → output folder`.

Outputs per PDF:

1. The full document as **Markdown** and lossless **DoclingDocument JSON**
2. **Chart data** — CSV and a natural-language summary per chart
3. **Tables** — CSV and HTML per table
4. **Key-value extraction** — a JSON object filled against a JSON Schema the user supplies
5. **RAG-ready chunks** — heading-aware chunks with page numbers, as JSON Lines

Inputs: one or more PDFs, **or a directory** of them, with resume (§3.8).

### Non-goals (v1)

- No UI. A Streamlit front end is on the **roadmap for v1.x**, built *on top of* the
  §3.7 library API; nothing in v1 may assume one, and the library must stay usable
  without Streamlit installed. If it needs fast listings, it gets a rebuildable SQLite
  cache (§3.12.1), never a second source of truth. Its theme (§14) and responsive
  layout (§15) are already designed and verified.
- No embeddings, vector store or database — v1 stops at `chunks.jsonl`; files are the
  source of truth, and any later store is a derived index (§3.12.1).
- No output localization. **English only** — Granite Vision 4.1 accepts English
  instructions only, and granite-docling is English-primary (Japanese, Arabic and
  Chinese are "experimental" on its card).
- No Granite 4.2 text model. Summarization, topics, intent and sentiment are dropped.
- No Granite Vision table refinement (see §3.4).
- No non-PDF inputs (images, DOCX). Docling can read them; v1 does not test them.

## 2. Model lineup (decided)

| Stage | Model | Runtime | Weights | Runs on |
|---|---|---|---|---|
| Page → structure | `ibm-granite/granite-docling-258M-mlx` | Docling `VlmPipeline`, preset `granite_docling` on the MLX engine (§3.2) | 0.63 GB, bf16 (unquantized) | every page |
| Charts → CSV + summary | `ibm-granite/granite-vision-4.1-4b`, **converted locally to 8-bit MLX** | Docling chart-extraction enrichment, preset `granite_vision_v4_mlx`, loaded from the models directory (§3.2, S2) | 4.92 GB | pictures Docling classifies as charts |
| Key-value extraction | the same 8-bit Granite Vision | **our code**, `mlx-vlm` directly | (same files) | pages the user selects |

**Why two models and not Granite Vision alone.** Granite Vision 4.1 is an *extraction*
model: its card reports ParseBench Mean **39.45** and Text Formatting **21.52** — weak
full-page parsing — and Docling ships it only for chart extraction, table structure and
KVP, never as a page converter (its `granite_vision` *page* preset is the older 3.x 2B
emitting Markdown). granite-docling emits **DocTags** — element types, bounding boxes,
reading order — which is what lets the pipeline find the charts to hand Granite Vision.
It also costs 0.63 GB to read every page, against 4.92 GB for the 4B model.

**Why 8-bit Granite Vision.** IBM publishes **no** MLX build of 4.1 (bf16 safetensors
8.0 GB, and GGUF). On 16 GB the Metal working set is **12.71 GB**
(`max_recommended_working_set_size`, measured in S4 on macOS 27; ~10.7 GB was assumed); bf16 GV + granite-docling is ~8.6 GB
before KV cache, page rasters, Docling's own models and the OS. 8-bit brings GV to
**4.92 GB** (S2: 9.26 bits per weight, because mlx-vlm leaves the 0.86 GB vision tower
in bf16). **S6 (2026-10-25) found no measurable regression** against bf16: identical
chart values on 10 charts and byte-identical KVP output on 3 pages. bf16 is also ruled
out on memory, at **10.12 GB** peak with Granite Vision alone on a KVP page.
Convert it ourselves rather than use a community build: the four on the Hub
(`inferencerlabs/…-MLX-Q9`, `beaupi/…-oQ4`/`-oQ8`, `brainworkup/…-oQ8`) have 15–103
downloads and no published evaluation.

**Pin the revision Docling pins:** `dd48e97503de471803850df70843cf9eb5da8712`
(`docling/datamodel/stage_model_specs.py`, `CHART_EXTRACTION_GRANITE_VISION_V4`).

**Rejected:** `mlx-community/granite-4.0-3b-vision-*` (what IBM's own HF Space uses on
MLX — older; VAREX 85.5% vs 94.2% for 4.1); `granite-vision-3.2/3.3-2b` (superseded);
any 30B model (does not fit).

### 2.1 Runtimes — not all MLX

The heavy compute is MLX; the stack is not. Read from Docling's source and
`pyproject.toml` on 2026-10-18:

| Component | Runtime | Load |
|---|---|---|
| granite-docling-258M (every page) | **MLX** via mlx-vlm | heavy — up to 8192 tokens/page (S3) |
| Granite Vision 4.1 8-bit (charts, KVP) | **MLX** via mlx-vlm | heavy — 4B model |
| Picture classifier `docling-project/DocumentFigureClassifier-v2.5` (EfficientNet) | **PyTorch** via transformers (MPS/CPU) by default; `ImageClassificationEngineType.ONNXRUNTIME` available | light |
| PDF parse and page render | `docling-parse` (C++) / `pypdfium2`, CPU | not a model |
| Chunking tokenizer | HF `tokenizers` (Rust), CPU | not a model run |

- **The classifier is not optional:** `do_chart_extraction=True` turns picture
  classification on — it is how Docling picks which pictures reach Granite Vision.
- **Extra names (amended by S1, 2026-10-24).** `docling` is a meta-package over
  `docling-slim`: `docling` itself requires `docling-slim[standard]` and exposes the VLM
  stack as **`docling[vlm]`** (= `docling-slim[models-vlm-inline]`). `models-vlm-inline`
  and `feat-chunking` are `docling-slim`'s names — on `docling` they do not exist, and
  `uv` only *warns* and resolves without them. Chunking needs no extra on `docling`:
  `standard` already brings `docling-core[chunking]`.
- **torch is installed regardless of runtime choice.** `docling-slim[standard]` (what
  plain `docling` installs) requires `torch` and `torchvision` outright, and the
  `models-vlm-inline` extra — the one that brings `mlx-vlm` — adds `open-clip-torch`,
  `timm`, `accelerate` and `peft`. That costs disk and CI install time (the S1 venv is
  1.5 GB). **It costs runtime memory too (amended by S9, 2026-10-25):** Docling imports
  torch as soon as `VlmPipeline` is imported, whichever engine the classifier uses.
  `vlm_pipeline.py` imports the transformers VLM backend at module level, and its
  stopping-criteria helper runs `import torch`. Measured: ~0.17 GB RSS at import and
  ~0.3 GB at peak, which is small against the 10 GB ceiling but not "tens of MB".
- **transformers ≥ 5.16 is the effective floor on this Mac**, and no single package
  states it: `docling-slim`'s `models-vlm-inline` excludes 5.9–5.15 on darwin (MPS crashes on
  float64 position embeddings, fixed in 5.16.0), while mlx-vlm needs ≥ 5.14. A later
  `uv lock --upgrade` must keep it; the new CLAUDE.md records it under Dependencies.

**Decision for v1 (settled by S9, 2026-10-25):** torch stays in the lock, and the
classifier stays on its transformers default. The rule was to switch to ONNX Runtime
only with no import of torch at runtime and identical chart picks. S9 met the second
condition: 0 label changes on 527 crops and on three PDFs end to end. The first
condition cannot be met in any installable stack, because Docling imports torch itself
(above). The one other blocker is that Docling's default ONNX `providers` call
`decide_device()`, which also imports torch. With both imports bypassed, the whole
Phase A path ran torch-free. That took a stub around a Docling-internal import, which
is not shipped. In a torch environment, ONNX saves no memory and only fractions of a
second per PDF, so switching buys nothing in v1.

**Reopen** when Docling makes both imports lazy. Unchanged on `main` as of 2026-10-25:
`models/utils/hf_stopping_criteria.py` and `utils/accelerator_utils.py::decide_device`.
**We do not report this upstream (decided with the user, 2026-10-25).** The payoff is
~0.3 GB of memory and ~0.7 GB of install size, and v1 waits on neither. The first import
is already reported as docling-project/docling#4966, from the remote-API side; the
`decide_device` one is not reported anywhere. Watch #4966 rather than commenting on it
or opening a PR, and do not re-offer an issue.
Depend on `docling-slim[format-pdf,feat-chunking,models-onnxruntime]` (108 packages,
989 MB instead of 1.7 GB), and run the classifier with
`OnnxRuntimeImageClassificationEngineOptions` and a `providers` list other than the
default `["CPUExecutionProvider"]`. Two consequences come with it. Without torch,
transformers preprocesses with `ViTImageProcessorPil`, which moves confidences by up to
0.031 but no label. And `huggingface-hub` moves to 2.x, because its `<2` cap lives only
in the extras that bring torch; that is an eval-gated move under §2.2. The new
CLAUDE.md must say plainly that the project is MLX for inference but not MLX-only, since
the old app was MLX-only in both senses.

### 2.2 Dependency policy (decided 2026-10-18)

The old repo capped `mlx-lm` because its API drifted; the two libraries this plan
leans on move faster still. Measured on PyPI over the three months to 2026-10-18:
**docling** shipped **31** releases (2.114 → 2.140), **mlx-vlm 20** (0.6.6 → 0.7.8),
docling-core 21, mlx 4.

| Package | Constraint in `pyproject.toml` | Why |
|---|---|---|
| `docling[vlm]` | `>=<verified>,<3` | its minor *is* its release unit (~2.5 a week) — a minor cap would block every release; the major cap guards a rewrite. `[vlm]` is the `docling` name for the VLM extra (§2.1) |
| `docling-core` | `>=<verified>,<3` | imported directly (DoclingDocument types), so declared, not left transitive |
| `mlx-vlm` | `>=<verified>,<0.8` | pre-1.0, a minor can break the API (0.6 → 0.7 inside the window) — the old `mlx-lm<0.32` precedent |
| `transformers` | `>=<verified>,<6` | never below **5.16**, the effective floor from §2.1; declared so `uv` enforces it instead of a comment |
| `jsonschema` | `>=4.18` | the `referencing`-based API rule 8 of §3.13 relies on |
| `mlx` | not declared | floats under `mlx-vlm`, as it did under `mlx-lm` — the lock pins it |

`uv.lock` pins exact versions as today. **Upgrades are deliberate and per package**
(`uv lock --upgrade-package docling`), never folded into a blanket `uv lock --upgrade`
alongside unrelated moves. Moving `docling`, `docling-core`, `mlx`, `mlx-vlm`,
`transformers` or `huggingface-hub` requires, before merge: the eval (§8.1) at or above
baseline, S10's no-repo-code check re-run (§3.13 rule 2 says what it watches), and the
vulnerability audit (§3.13 rule 6).
Moving a cap (`<0.8`, `<3`) is its own PR.

**Floors are verified versions, not researched ones (amended 2026-10-24).** `<verified>`
is the version the Phase 0 spikes and PR 4's hand run actually executed the models on —
the version in PR 4's `uv.lock` — written in when PR 4 adds the dependencies. The
versions this plan was researched against (`docling` 2.140, `mlx-vlm` 0.7.8,
`transformers` 5.16) are **not** floors: S1 showed only that they install and chunk,
and no model run ever touches them, because every spike and eval resolves to the
latest versions inside the caps. A floor declares support, and an older version nobody
ran the pipeline on is a guess — the old app's rule for its `streamlit` floor. Lowering
a floor later needs the same evidence as moving any of these packages: the eval on the
lower version, run with `uv lock --resolution lowest-direct` and `uv sync --frozen`
(S1 found a plain `uv sync` silently re-resolves to highest). Floors are raised only
when code needs a newer version, not on every upgrade.

## 3. Architecture

### 3.1 Data flow and memory phases

```text
PDF ──► Phase A: Docling DocumentConverter (VlmPipeline)
          • granite-docling (MLX) on every page → DocTags → DoclingDocument
          • picture classification → charts
          • chart extraction: Granite Vision 8-bit (MLX) → <chart2csv>, <chart2summary>
            attached to PictureItem.meta.tabular_chart / .description
        ──► release the converter (drop references, gc, mx.clear_cache())
        ──► Phase B (only if --schema): Granite Vision 8-bit via mlx-vlm
          • page image(s) + schema → JSON → validate → kvp.json
        ──► Export: Markdown, JSON, tables, charts, kvp, run manifest
```

Phase A and Phase B each load Granite Vision **separately** — Docling's engine owns its
instance and exposes no handle to it. Running both at once would hold two copies
(~9.9 GB + granite-docling). The phases must therefore be **sequential with an explicit
release between them**. **S4 (2026-10-25) proved the release works:** dropping the
converter, `gc.collect()` and `mx.clear_cache()` free every model and take active memory
to 0; Phase A peaks at 7.00 GB on a 42-page PDF with nine charts, Phase B at 6.52 GB.
Docling loads Granite Vision when the pipeline is built, so its 4.93 GB is resident for
all of Phase A even on a PDF without charts. The fallback — drop Docling's chart
enrichment and run chart extraction in our own code against the single Phase-B model
instance (crop via `generate_picture_images`) — is not needed.

### 3.2 Docling configuration (verify against the installed version)

Read from Docling `main` on 2026-10-18 (PyPI latest: **2.140.0**, 2026-10-16):

- `VlmPipelineOptions(vlm_options=VlmConvertOptions.from_preset("granite_docling", engine_options=MlxVlmEngineOptions()), generate_page_images=True, …)`
  — **amended by S3 (2026-10-24):** the `vlm_model_specs.GRANITEDOCLING_MLX` form this
  plan first named is the deprecated legacy path (`DeprecationWarning`, slated for
  removal); the two gave byte-identical output. The preset takes the MLX repo from its
  `engine_overrides[VlmEngineType.MLX]`, which is also where the revision pin goes
  (`EngineModelConfig.revision`, §3.13 rule 1). `max_new_tokens` is **8192**.
- `do_chart_extraction=True` (it auto-enables picture classification). Pictures that
  granite-docling already typed in DocTags **skip the classifier** and keep that label,
  at confidence 1.0 (`created_by: load_from_doctags`). That covered 6 of 9 charts on
  GAO-07-631 and none on GAO-07-535 or the Census pages (S9), so the classifier is
  still what finds most charts.
- `chart_extraction_options=ChartExtractionVlmEngineOptions.from_preset("granite_vision_v4_mlx", …)`
  with **chart2csv and chart2summary on** — summary defaults to **off**; chart2code stays off
- **models come from one local directory** (amended by S2, 2026-10-25):
  `VlmPipelineOptions.artifacts_path=$GDI_CACHE_DIR/models`, with Granite Vision's MLX
  override set to `repo_id="local/granite-vision-4.1-4b-8bit"`. A local *path* as
  `repo_id` does not work (`HFValidationError` from `snapshot_download`). With
  `artifacts_path` set, every engine loads `<artifacts_path>/<repo_id, "/" → "--">` and
  ignores `revision`, and the option is pipeline-wide, so granite-docling and the figure
  classifier must be in that directory too. That is Docling's own offline mode, so it
  needs no Hub repo and no patch. Pins are enforced when the directory is filled
  (§3.13 rule 1).
- `generate_picture_images=True` to save chart crops alongside their CSVs
- the preset's `min_engine_version="0.7.0"` for mlx-vlm

Use the explicit `_mlx` preset, not `granite_vision_v4` (auto-inline): the auto preset
silently falls back to transformers on CPU/MPS when mlx-vlm is missing or broken, which
on this machine means a second, bf16 copy of the model and a pipeline many times slower.

**Hybrid text mode** (amended by S5, 2026-10-25). `--text-source pdf` sets
**`VlmConvertOptions.force_backend_text=True`**, on the preset, not on the pipeline.
`VlmPipelineOptions.force_backend_text` is read only on the deprecated legacy path, so
on the preset path it is a silent no-op: its output is byte-identical to the default.
The flag does not route anything around the VLM. Every page is still generated in
full, and afterwards each text item's text (paragraphs, headings, captions, footnotes,
list items) is replaced by the PDF text lines lying mostly inside the VLM's box for it.
Tables are untouched, and no time is saved.

**The default is `vlm`.** The two modes fail differently:

- `vlm` makes the rarer errors, but they are the dangerous kind: S5 caught "73" for
  "7.3", an inserted phrase, and two invented paragraphs on a chart page.
- `pdf` fixes those, but silently drops whatever falls outside a too-small box
  (footnotes, figure sources, whole paragraphs). It doubles text the PDF overprints,
  leaves line-break hyphens ("per- cent"), and on a page with no text layer (a scan)
  empties every text item.

`pdf` is documented as an opt-in for born-digital PDFs whose text layer the user
trusts.

### 3.3 Key-value extraction (our code)

Docling *has* KVP extraction (`VlmExtractionPipelineOptions` with
`GRANITE_VISION_4_1_TRANSFORMERS` and `ExtractionPromptStyle.GRANITE_VISION`) but it is
**transformers-only** — on this Mac that is a bf16 copy on MPS, the memory problem §2
exists to avoid. So:

- Reuse Docling's **prompt formatting** for `ExtractionPromptStyle.GRANITE_VISION`
  (find it under `docling/models/extraction/`) rather than inventing one — it is IBM's
  integration of IBM's prompt format. Import it if it is public API. If it is private
  and must be copied, the copy carries **Docling's MIT notice** — copyright line and
  licence text in `THIRD_PARTY_NOTICES.md`, and a header comment on the copied code
  naming the source file, the Docling version and that file (§9.1). Attribution alone
  does not satisfy MIT.
- Run it with `mlx_vlm` against the 8-bit model, **greedy** (`temperature=0.0`).
- Parse defensively — first JSON object in the output (the current app's
  `parse_json_output` is a proven implementation; port it with its tests).
- Validate against the user's schema with `jsonschema`; keep the raw output on failure.
- Scope: `--schema fields.json` plus `--pages 1,3-4`. **Default: page 1 only**
  (decided 2026-10-18) — forms and invoices put their fields up front, and a per-page
  run over a 50-page PDF is 50 generations. `--pages all` is the explicit opt-in.
- Multi-page merge (when `--pages` names several): per-page results kept under
  `"pages"`, plus a merged object taking each field's first non-null value in page
  order; conflicts listed under `"conflicts"` rather than silently resolved.

### 3.4 Tables

From granite-docling's DocTags (OTSL; its card reports table TEDS **0.97**):
`table.export_to_dataframe(doc=doc)` → CSV, `table.export_to_html(doc=doc)` → HTML.
Docling's `GraniteVisionTableStructureOptions` exists but belongs to the *standard*
pipeline and loads through transformers — out of scope for v1.

### 3.5 Output layout

```text
out/<pdf-stem>/
  document.md
  document.json            # DoclingDocument, lossless
  tables/table-001.csv
  tables/table-001.html
  charts/chart-001.png     # the crop Granite Vision saw
  charts/chart-001.csv
  charts/chart-001.summary.txt
  charts/chart-001.py      # only with --chart-code
  pictures/picture-001.png # non-chart pictures (§3.12 rule 2)
  pages/page-001.png       # only with --save-page-images (§3.12 rule 2)
  kvp.json                 # only with --schema; {"pages": {...}, "merged": {...},
                           #   "conflicts": [...], "valid": bool, "raw": {...}}
  chunks.jsonl             # one chunk per line (§3.9)
  run.json                 # "status" ("ok" | "error") + error type/message (§3.8);
                           # the four model ids + pinned SHAs (§3.13 rule 1),
                           # package versions; per-phase, per-stage and per-page
                           # timings + tokens (§3.14); peak memory; page count;
                           # warnings incl. pages that hit max_tokens (§3.10);
                           # output-folder bytes (§3.12 rule 5); input SHA-256 and
                           # the options used (for §3.8 resume)
```

`run.json` is written **last**, and only it marks a PDF as done — a crash mid-export
leaves no `run.json`, so resume redoes that PDF instead of trusting a partial folder.
The whole tree is built in a staging folder and swapped into place atomically (§3.12),
so a re-run never leaves files from an earlier run beside a new `run.json`.

`run.json` exists because every quality question this project has ever had was answered
by knowing exactly which model, revision and library produced an output.

### 3.6 Package layout (decided 2026-10-18; module boundaries may be refined in PR 4)

```text
src/granite_document_intelligence/
  __init__.py
  cli.py          # argparse; console script `gdi` (convert-model in PR 4, convert in PR 6)
  config.py       # model ids + the four pinned SHAs (§3.13 rule 1), paths, env vars
                  # (GDI_CACHE_DIR, HF_TOKEN)
  api.py          # the public surface (§3.7); cli.py is a thin wrapper over it
  models.py       # convert_model(): fetch the pinned models, convert GV to 8-bit, strip
                  # the repo's code, write the manifest (§3.2, §4.2, §3.12 rule 3,
                  # §3.13 rules 1–2)
  convert.py      # Phase A: build + run the DocumentConverter, then release it
  kvp.py          # Phase B: prompt, generate, parse, validate
  chunk.py        # §3.9
  export.py       # writes §3.5 from a DoclingDocument
  batch.py        # §3.8: input expansion, done-detection, continue-on-error
  safety.py       # §3.10 limits, %PDF- check, path containment, symlinks; schema checks (§3.13)
  runlock.py      # §3.14 rule 4: the process-level run lock
  memory.py       # release helper, cache limit, peak-memory reporting
```

`src/` layout so tests import the installed package, not the working directory — which
needs packaging the old app never had (PR 4): a `[build-system]` (`uv_build`),
`[project.scripts] gdi = "granite_document_intelligence.cli:main"`, and
`[tool.pytest.ini_options] pythonpath` no longer `["."]` (tests import the installed
package). **`GDI_CACHE_DIR`** (default `~/.cache/granite-document-intelligence`) holds
the converted model and `run.lock`; tests point it at `tmp_path`, so no test touches the
developer's real cache. CLI:

```text
gdi convert INPUT [INPUT ...] -o OUT_DIR      # INPUT: a .pdf or a directory
    [--schema fields.json] [--pages 1,3-4|all]
    [--text-source vlm|pdf] [--no-charts] [--chart-code] [--no-chunks]
    [--chunk-tokens 512] [--save-page-images] [--recursive] [--force]
    [--max-pages 500] [--max-file-mb 200] [--quiet] [--no-wait]
gdi convert-model [--delete-source]   # one-time: the 8-bit Granite Vision (§4.2, §3.12)
```

### 3.7 Python API

The CLI is a wrapper; the library is the product (and what a v1.x UI would call).

**Names (decided 2026-10-18):** import package `granite_document_intelligence`
(distribution `granite-document-intelligence`, unclaimed on PyPI as of that date),
console script **`gdi`** (no collision on PATH or in Homebrew), public API
**`convert`**, **`convert_many`**, **`ConvertOptions`** — not a bare `Options`, which
would be ambiguous beside Docling's many `*Options` classes in a user's namespace.

```python
from pathlib import Path

from granite_document_intelligence import ConvertOptions, convert

opts = ConvertOptions(schema=Path("fields.json"), pages="1", chunk_tokens=512)
result = convert(Path("report.pdf"), options=opts)

result.document  # docling_core DoclingDocument
result.charts  # list[Chart]: index, page_no, csv (str | None), summary, code, image
result.pictures  # list[Picture]: index, page_no, image (non-chart pictures)
result.tables  # list[Table]: index, page_no, dataframe, html
result.kvp  # KvpResult | None: pages, merged, conflicts, valid, raw
result.chunks  # list[Chunk]: id, text, contextualized, headings, page_nos, refs, kind
result.run  # RunInfo: models, versions, timings, peak_memory_bytes, warnings

result.save(Path("out/report"))  # writes the §3.5 tree
```

Rules: `convert()` **returns** data and writes nothing; `save()` is the only writer —
so a UI can show results without touching disk. Dataclasses, fully typed (`ty` gates
it). A batch helper `convert_many(inputs, out_dir, options, force=False)` yields one
result-or-error per PDF and owns the §3.8 resume logic, so the CLI and a UI cannot
diverge on it.

### 3.8 Batch and resume

- An input directory expands to its `*.pdf` (case-insensitive), sorted;
  `--recursive` descends. Output folder per PDF is its path **relative to the input
  directory**, so two `report.pdf` in different subfolders don't collide.
- **Done** = `out/<pdf>/run.json` exists, records success, its input SHA-256 matches
  the file, and its recorded options equal the current ones. Anything else re-runs —
  every uncertainty runs, the same rule `scripts/gate.sh`'s memo follows. `--force`
  re-runs everything.
- One `DocumentConverter` for the whole batch (models load once) when there is no
  `--schema`. With `--schema`, **Phase B runs per PDF after its Phase A** (decided by S4,
  2026-10-25): the converter is released before each Phase B and rebuilt after, which
  reloads all three models per PDF — ~4.6 s on a warm page cache, ~2% of a 42-page
  PDF's Phase A. All Phase A first, then all Phase B, stays the alternative if S8 finds
  cold loads or batches of small PDFs make the reloads dominate. **S8 (2026-10-25)
  kept per PDF:** the reload is ~4.5 s (rebuild 2.6 s + Granite Vision 1.9 s), and a
  cold load after `purge` adds only ~1 s. That is ~2% of a 42-page PDF but about a
  third of a one-page form's ~9 s. A batch of one-page forms is the case that
  would reopen this decision. **Decided with the user (2026-10-25):** per PDF stays,
  and PR 5 looks into removing the reload instead of reordering the phases.
  - Docling already holds a Granite Vision instance for charts, and Phase A's 7.0 GB
    peak includes it. S4's ~10.5 GB problem was a *second* copy.
  - If `kvp` can run on Docling's loaded instance, or hand Docling one that `kvp`
    loaded, through **public API**, the converter is not released before Phase B. The
    reload disappears for every batch size, and the peak should stay near 7 GB. PR 5
    measures that peak against §3.11's ceiling.
  - If it can only be done through private Docling attributes, it is not done. In that
    case all-A-then-all-B is adopted only if the private tier (§4.1) turns out to be
    mostly short forms.
- A failed PDF writes `run.json` with `"status": "error"` and the traceback, and the
  batch continues; exit status is non-zero if any PDF failed. A batch summary
  (done / skipped / failed, with paths) goes to stderr at the end.

### 3.9 Chunking

Docling's `HybridChunker` (heading hierarchy first, then token-count splits;
`merge_peers=True`). It needs a `BaseTokenizer`; use
`HuggingFaceTokenizer.from_pretrained(model_name="ibm-granite/granite-embedding-english-r2", max_tokens=chunk_tokens)`
so chunk sizes match the Granite embedding model a later stage would most likely use
(English-only pipeline, so not the multilingual one). **Verified by S1 (2026-10-24):**
the tokenizer loads through this API (as transformers 5's `TokenizersBackend`) and
chunks with heading context and budget splits. Chunking needs `docling-core[chunking]`,
which plain `docling` already installs (§2.1).

**Chunk size (decided 2026-10-18):** `chunk_tokens` defaults to **512** and is
configurable (`--chunk-tokens`, `ConvertOptions.chunk_tokens`). It is a retrieval
choice, not a model limit: `granite-embedding-english-r2` is ModernBERT with
`max_position_embeddings = 8192` (read from its `config.json`). Smaller chunks match
queries more precisely; revisit the default once an embedding model is actually chosen.
Valid range 64–8192, rejected outside it. The value is recorded in `run.json`'s options,
so changing it makes §3.8 resume re-run the PDF rather than keep stale chunks.

Each `chunks.jsonl` line: `{"id", "text", "contextualized", "headings", "page_nos",
"doc_item_refs"}` — `contextualized` is `chunker.contextualize(chunk)`, the
heading-prefixed text meant for embedding. Chart summaries are part of the document,
so check in S7 whether they reach the chunks; if not, add each summary as its own chunk
tagged `"kind": "chart_summary"`.

### 3.10 Untrusted input

Every PDF is untrusted: it may be malformed, hostile, or simply huge, and its content
reaches a model. Rules, each with a test (§8):

- **Resource limits, checked before conversion.** `--max-pages` (default **500**) and
  `--max-file-mb` (default **200**, matching the old app's upload cap). Exceeding one is
  a per-PDF error recorded in `run.json`; the batch continues. Never raise Pillow's
  `Image.MAX_IMAGE_PIXELS` or disable its decompression-bomb check — a PDF can declare a
  page of absurd physical size, and page rasterization (`images_scale=2.0`) would turn
  it into a multi-gigapixel image. If Docling trips that check, it is a per-PDF error,
  not something to work around.
- **Bounded generation.** Every model call has a `max_tokens` cap (granite-docling's
  preset uses **8192** per page — S3). A page that **hits the cap** almost always means a
  degenerate repetition loop, not a long page — IBM's own Space ships a `repetition.py`
  for exactly this. Record such pages in `run.json` `warnings` with their page number;
  the eval harness counts them.
  **Detection is our code, post hoc (S3, 2026-10-24).** Docling flags neither case on
  MLX: no MLX path ever emits `VlmStopReason.LENGTH`, so a capped page comes back with
  `stop_reason=unspecified`, `status=success` and no `errors`. And S3's sideways table
  looped 348 times, then *closed itself* with `</doctag>` under the cap. Flag a page when
  `num_tokens >= max_new_tokens` **or** its DocTags do not end in `</doctag>` (capped), **or**
  Docling's `DocTagsRepetitionStopper.should_stop` trips on any 640-character sliding
  window of the final text (a loop that ended itself). Run all three checks on each
  page's **raw DocTags** (`vlm_response.text`), never on the exported document: under
  `--text-source pdf` the loop's text items come back empty (S5), so a check on the
  document would miss the loop. An in-generation stopper does
  not work on MLX, because mlx-vlm's BPE detokenizer streams no text until a token
  begins with a space, and DocTags loops have none. So a looping page runs to the
  cap (~37 s at S3's ~230 tokens/s) — a cost, not a hang.
- **No per-PDF timeout or process isolation in v1.** A hang inside a C-extension parser
  can't be interrupted in-process, and a subprocess per PDF would reload the models for
  every file. Stated as a known limitation in the README; revisit if the corpus or a
  user's PDFs ever hang.
- **Text in the page can try to steer the model.** For KVP, a page image may contain —
  visibly or as white-on-white text — instructions like "ignore the schema and output
  …". Mitigations: output is **data, never instructions** (nothing the pipeline does
  depends on a model's output beyond writing it to disk); schema validation rejects
  off-schema output; the README says KVP results from untrusted documents need review
  before anything acts on them. Our code passes page *images* to Granite Vision, not
  extracted text, so control-token injection does not apply today — but if any future
  stage formats PDF text into a prompt, port the old app's `_strip_control_markers`
  (drop every `tokenizer.get_added_vocab()` string; see CLAUDE.md git history) with its
  tests.
- **Generated code is never executed.** `chart-NNN.py` (with `--chart-code`) is model
  output. It is written with a header comment — `# Generated by granite-vision-4.1-4b
  from <pdf> page <n>. Untrusted: review before running.` — and nothing in the package
  imports, evals or runs it.
- **Output paths come only from the input's relative path.** The output folder is
  derived from the PDF's path relative to the input directory (§3.8), never from PDF
  metadata (title, filename fields). Resolve and check that every write lands under the
  output directory; skip symlinks that resolve outside the input directory, with a
  warning.
- **HTML and Markdown outputs are files, not rendered pages.** Docling escapes them,
  but a v1.x UI must treat `table-NNN.html` and `document.md` as untrusted when
  rendering — recorded here so the UI work inherits the rule (the old app's
  `_escape_markdown` lesson: model text never reaches a renderer unescaped).
- **No network beyond the Hugging Face Hub.** `enable_remote_services=False`, pinned by
  `test_convert.py`; once models are cached, document `HF_HUB_OFFLINE=1` for fully
  offline runs.

### 3.11 Performance budgets

Budgets turn S4/S8's measurements into regression checks that `eval/score.py` enforces
(§8.1).

- **Memory — a hard ceiling, set now:** peak `mx.get_peak_memory()` per phase
  **≤ 10 GB**. This came from the Metal working set (assumed ~10.7 GB on 16 GB; S4
  measured 12.71 GB), not from a measurement of the pipeline, so it does not move. S4's
  whole-run peak was 7.00 GB. If S4 exceeds it, the design changes (the §3.1
  fallback) — the budget is not raised.
- **Time — measured, not guessed:** PR 7 sets `eval/budgets.toml` from **its own
  baseline run of the production pipeline** — the median of three (§8.2 rule 8)
  seconds/page for Phase A (born-digital and scanned separately), seconds per chart,
  seconds per KVP page, and cold-start load time, each **× 1.25** headroom. S8's spike
  numbers are a sanity reference only (a spike script is not the shipped code); a
  baseline more than ~25% off S8 is investigated before it is committed. **S8's
  reference** (born-digital, M2 Pro 16 GB, 2026-10-25): Phase A ~6 s/page all-in
  (5.9–6.2), uncapped page generation 3.3–4.2 s, a capped page 36 s, a chart 8–15 s
  (it varies 2× between PDFs, so the chart budget is a corpus median), a KVP page
  ~5 s, model loads 2.2–2.8 s (Phase A) and 1.9 s (Phase B). Scanned: not yet
  measured (S8's image-only stand-in collapsed; §17).
- **Degeneration count:** pages that hit `max_tokens` (§3.10) — budget **0** on the
  public corpus. **Open conflict (S8):** both born-digital PDFs S8 timed have one
  capped page (GAO-07-631 p. 24, GAO-07-535 p. 38), and GAO-07-631 is the 30+ page
  candidate. **Decided with the user (2026-10-25): named exceptions.**
  - `eval/budgets.toml` lists each known capped page by PDF and page number.
  - The budget becomes "no capped page outside the list". A new capped page fails the
    run, and a listed page that stops capping is reported as a fix.
  - GAO-07-631 stays in the corpus, because its capped page is exactly the failure the
    eval exists to catch.
  - Two cheap fixes are tried in S11 or PR 7. One retries a capped page once at a
    different `images_scale`. The other stops generation when §3.10's loop detector
    fires instead of at 8192 tokens, which saves most of a capped page's 36 s even
    where the text cannot be recovered.
- **Enforcement:** `score.py` reports each metric against its budget and fails the run
  if any is exceeded, exactly as a quality drop fails it. **Raising a time budget needs
  a committed `eval/results/` file showing why** (a new Docling version that is
  slower but more accurate, say) — the same discipline as the baseline scores.
- **README** states the measured numbers ("about N s/page on an M2 Pro 16 GB") with the
  date and versions, not the budgets.

### 3.12 Storage

Everything is files — no database, no server. Where each thing lives:

| What | Where | Size | Lifecycle |
|---|---|---|---|
| Embedding tokenizer | Hugging Face cache (`~/.cache/huggingface/hub`) | small | downloaded on first use; kept |
| granite-docling, picture classifier | `$GDI_CACHE_DIR/models/<ns>--<name>/` (S2) | 0.63 GB + small | fetched at their pinned SHAs by `gdi convert-model`; kept |
| Granite Vision **bf16 source** | Hugging Face cache | **8.0 GB** | needed only to convert (and for S6); **deletable after** |
| Granite Vision **8-bit** | `$GDI_CACHE_DIR/models/local--granite-vision-4.1-4b-8bit/` | 4.92 GB | written by `gdi convert-model`, repo code stripped (§3.13 rule 2); kept |
| Results | `OUT_DIR/<pdf>/` (§3.5) | per PDF | user-owned; never deleted by the tool except its own staging folders |
| Eval corpus / results | `eval/` (§4.1, §8.1) | < 20 MB committed | private tier gitignored |
| Spikes | session scratchpad | — | never in the repo |

Five rules:

1. **Atomic output swap — no stale files.** A PDF's tree is written to
   `OUT_DIR/<pdf>.partial/`, `run.json` last, then swapped into place: rename any
   existing `OUT_DIR/<pdf>/` to `<pdf>.old/`, rename `.partial` to `<pdf>/`, delete
   `.old`. Without it, a re-run that finds three charts where the last run found five
   leaves `chart-004.csv` and `chart-005.csv` beside a `run.json` that reports success
   — a silent wrong result, and the same failure the `run.json`-last rule exists to
   prevent, arriving from a previous *successful* run instead of a crash. At startup a
   batch deletes leftover `*.partial/` folders (a crash's residue) and, if it finds a
   `<pdf>.old/` with no `<pdf>/`, renames it back (a crash mid-swap). Errors write
   `run.json` with `"status": "error"` through the same swap, so a failed re-run
   replaces a stale success rather than sitting beside it. Both renames stay within
   `OUT_DIR`, so they are same-filesystem and atomic on APFS.
2. **Images are files, not base64.** `document.json` is saved with referenced images
   (`ImageRefMode.REFERENCED` — verify the exact export call in S3) so the JSON stays
   small and diffable. **Picture crops on** (`charts/chart-NNN.png`, and
   `pictures/picture-NNN.png` for non-chart pictures); **full-page images off** by
   default, `--save-page-images` to write `pages/page-NNN.png`. Page images are still
   rendered in memory during conversion either way — this governs only what reaches
   disk.
3. **The bf16 source is cleanup the user opts into.** `gdi convert-model` ends by
   printing the source's size and the exact command to remove it from the HF cache
   (`hf cache delete`-style — verify the command name in S1), and accepts
   `--delete-source` to do it after a successful load test of the 8-bit output. Never
   by default: S6 needs bf16, and re-downloading 8 GB is slow.
4. **The old app's model is named for the user to decide.** After the teardown,
   `ibm-granite/granite-4.2-3b-bf16-mlx` (7.3 GB) stays in the HF cache. PR 3's
   description (§10) names it and the command to remove it; the tool never deletes
   another project's cache entries.
5. **Disk is reported, not guessed.** `run.json` records the output folder's size in
   bytes; the batch summary prints the total. The README states the space needed:
   roughly **14 GB during setup** (bf16 source + 8-bit + granite-docling + the
   torch-including environment) and **~6 GB after** deleting the source — estimates
   until S1 (environment size) and S8 (output size per page) measure them; replace them
   with the measured figures. S8 measured output at **23–61 KB/page** in the default
   layout (JSON, Markdown, picture crops) and **160–187 KB/page** with
   `--save-page-images`, before tables and chunks. Output is negligible beside the
   models.

#### 3.12.1 Persistence — no database in v1 (decided 2026-10-18)

**Rule: files in `OUT_DIR` are the source of truth. Any database added later is a
derived index — rebuildable from `OUT_DIR` by a scan, and deletable without loss.**

Why no database now: every PDF's output is independent and written once; `run.json`
already holds the per-PDF record resume needs (§3.8); and a second store of "done"
state would reintroduce the drift the atomic swap (rule 1) closes — a crash between
the folder swap and a database write leaves the two disagreeing. Plain CSV, Markdown
and JSONL also need no export step for the user.

When a database earns its place, and which:

| Trigger | Choice | Why this one |
|---|---|---|
| Querying across many processed PDFs (all charts on a topic, every invoice total over X) | **DuckDB**, reading the output files in place | SQL over `OUT_DIR/*/charts/*.csv`, `*/kvp.json`, `*/chunks.jsonl` with **no ingestion** — the files stay the only copy, so nothing can drift. Embedded, single-file. The §3.5 layout *is* its schema, so changing that layout is a breaking change for any saved query. |
| Search / RAG over chunks (the roadmap after `chunks.jsonl`) | **LanceDB** or **SQLite + sqlite-vec** — choose with a spike when embeddings arrive | Both embedded, no server. Either is a derived index rebuilt from `chunks.jsonl` plus the chosen embedding model; record the embedding model id and `chunk_tokens` in the index so a mismatch forces a rebuild. |
| The v1.x UI needs fast listings and run history | **SQLite** (Python stdlib) | A cache of `run.json` contents, rebuilt by scanning `OUT_DIR`; delete it and it rebuilds — the same contract as `scripts/gate.sh`'s memo. |

**Avoid server databases** (Postgres + pgvector, a Chroma or Qdrant server,
Elasticsearch): a single-user, local-first tool on a 16 GB Mac would pay for one in
memory against the 10 GB Metal budget, in operations, and in the "no network beyond
the Hugging Face Hub" stance (§3.10). Revisit only if the tool becomes multi-user.

### 3.13 Security model (decided 2026-10-18)

§3.10 (untrusted input), §3.12 (storage), §8.2 (adversarial eval, private results) and
the carried-over tooling (locked `uv.lock`, least-privilege CI, the secret-edit guard)
already implement most of this. This section states the model they serve and closes
the gaps a check of the Hub and Docling's source found that day.

**Threat model.**

| | |
|---|---|
| **Assets** | the user's documents and everything derived from them; `HF_TOKEN`; the Mac's files, memory and GPU |
| **Adversary** | the author of a PDF the user processes — controls every byte of it, including text the user never sees |
| **Trusted** | the user and their CLI arguments, `ConvertOptions`, and the KVP schema they supply; model weights **at pinned revisions**; dependencies **at locked versions** |
| **Untrusted** | every byte of the PDF and everything a model derives from it: Markdown, tables, chart CSV/summary/code, KVP values, chunks |
| **Out of scope (v1)** | multi-user use, serving over a network, defending against the user themselves, a compromised Mac |

Every rule below maps to a row: PDF-derived data never steers the program (§3.10), and
everything the program *executes or loads* is pinned and local.

**Rules:**

1. **Every model is pinned to a commit SHA.** Docling pins only Granite Vision
   (`dd48e97503de471803850df70843cf9eb5da8712`); its granite-docling MLX preset carries
   **no revision** and the figure classifier uses **`revision="main"`** — both load
   whatever those repos publish next. `config.py` pins all four (granite-docling MLX at `e9939db25d2f296c8678d0491c4609a8c596c50a`,
   the revision S3 ran,
   Granite Vision, `DocumentFigureClassifier-v2.5`, the `granite-embedding-english-r2`
   tokenizer at `47ea694b257b703fee9253d75c2b1f2985180498`, its `main` when S10 ran)
   and overrides Docling's presets with them; `run.json` records them;
   `test_convert.py` asserts each reaches its loader. **Amended by S2 (2026-10-25):**
   the three Docling models load from `artifacts_path` (§3.2), where Docling ignores
   `revision`, so for them the pin is enforced **at fetch**. `gdi convert-model`
   downloads each at its SHA into its folder (real files via `local_dir`, not symlinks
   into the HF cache, which `hf cache delete` would break) and writes
   `models/manifest.json` with every SHA, plus the conversion's source SHA, mlx-vlm
   version, q-bits and the repo files and `auto_map` keys it stripped (rule 2, S10).
   S10 verified the result: each fetched folder's commit equals its pin, and its
   files are byte-identical to the HF snapshot at that SHA. `convert()` compares the manifest with `config.py` before
   loading and refuses a mismatch with the command that fixes it. `run.json` records
   the manifest, and `test_convert.py` asserts the check rather than a revision
   reaching Docling. The tokenizer keeps its revision at its loader. A pin moves only in a PR that runs
   the eval. (On 2026-10-18 Granite Vision's `main` was already at `37d591f…`, past
   Docling's pin — pins are what keep that from mattering silently.)
2. **No model-repo code runs.** `granite-vision-4.1-4b` ships `modeling.py`,
   `processing.py`, `configuration.py` and `downsampling.py`, and Docling's preset sets
   `trust_remote_code=True` — a remnant of transformers < 5.8, which needed it. Under
   the locked transformers ≥ 5.16 the model loads natively. Our code never passes
   `trust_remote_code=True`. **Amended by S10 (2026-10-25):** not passing it is not
   enough. mlx-vlm's patched `AutoProcessor.from_pretrained` defaults
   `trust_remote_code` to True for every model type it registers a processor for,
   `granite4_vision` among them, and Docling's MLX engine cannot pass False. With
   `auto_map` in the folder's configs, every load of the 8-bit folder imported the
   repo's `configuration.py`, the Phase A chart stage included. mlx-vlm's
   `convert()` function has the same default (`trust_remote_code: bool = True`); only
   its CLI defaults off. So the code is removed from the artifact:
   - `gdi convert-model` converts with `trust_remote_code=False`, passed explicitly.
   - It then **deletes every `*.py` file from the converted folder and every
     `auto_map` key from its JSON configs, nested ones included.** Today that is the
     four files above, plus `auto_map` in `config.json`, `preprocessor_config.json`,
     and `processor_config.json` under `image_processor`.
   - It records the stripping in `manifest.json`.

   With nothing to import, no loader can run repo code, whatever its default. S10
   measured the stripped folder at zero code executed and byte-identical output.
   The folder then holds only safetensors and JSON, so anything that can write to
   the user's cache can at worst change the output, not run code. Three guards keep
   it that way:
   - `convert()` refuses a models directory where any folder holds a `*.py` file or
     an `auto_map` key, naming `gdi convert-model` as the fix.
   - Our own Phase B load passes `trust_remote_code=False` explicitly, as a second
     guard, not the fix.
   - S10's check, re-run whenever §2.2 says, watches executed code objects (the
     audit `exec` event) and the modules cache, in every process — not just
     `sys.modules` in the parent.

   If a future transformers drops native `granite4_vision`, the load fails loudly
   rather than falling back to repo code.
3. **Weights are safetensors only.** `granite-embedding-english-r2` ships a pickle
   `pytorch_model.bin` beside its safetensors. Only its tokenizer loads today; any
   future weight load passes `use_safetensors=True` (or the library's equivalent), so a
   missing safetensors file is an error, never a pickle fallback.
4. **Parsers are unsandboxed in v1 — stated, not hidden.** `pdfium` and
   `docling-parse` are C/C++ code reading attacker-controlled bytes in-process; a
   memory-corruption bug would run with the user's privileges. Accepted for a
   single-user tool processing the user's own documents, mitigated by keeping them
   current (rule 6). The upgrade path, if the tool ever processes strangers' PDFs at
   volume: conversion in a separate low-privilege process with resource limits (the
   subprocess option §3.10 declined for v1 on model-reload cost). The README says so.
5. **No secrets or document content in logs.** `run.json`, progress output and stored
   tracebacks never contain environment variables, `HF_TOKEN`, or page text — errors
   record the exception type, message and our own frames, not local variables or model
   output. `test_safety.py` runs with a sentinel `HF_TOKEN` and a sentinel string in the
   PDF and asserts neither appears in any output file or stderr.
6. **Dependency vulnerabilities are watched.** Turn on **GitHub Dependabot alerts**
   (they read `uv.lock`) — a repository setting, so applied after PR 3 merges, **with
   the user's confirmation at that moment** (as §9.2). Add a vulnerability audit of the
   locked environment (`uvx pip-audit` against `uv export` output — verify the exact
   invocation when it is first run) to CLAUDE.md's "Verifying a dependency upgrade"
   procedure.
7. **Inputs are recognised by content.** A file is processed only if it starts with
   `%PDF-`, whatever its extension; anything else is a per-PDF error before any parser
   sees it.
8. **The KVP schema is local and bounded.** Remote `$ref` resolution is disabled
   explicitly (no retrieval function registered with `jsonschema`/`referencing`, not
   relied on as a default), the schema is capped at 256 KB, and it must be a JSON
   object. A schema with a remote `$ref` is rejected with a clear message, and a test
   proves no network call is attempted.
9. **Repository hygiene.** A `SECURITY.md` says how to report a vulnerability privately
   (GitHub's private advisories) and states the threat model above in two lines. CI's
   third-party actions are pinned to **commit SHAs** with the version in a comment
   (`actions/checkout@<sha> # v7`, `astral-sh/setup-uv@<sha> # v10.0.0`) instead of
   mutable tags. `TestCIWorkflow` asserts no `uses:` version today, so it **gains** an
   assertion that every `uses:` is a 40-hex SHA followed by a `# v…` comment.

### 3.14 Performance (decided 2026-10-18)

Memory is the hard constraint and is already designed (§3.1 phases, §2 8-bit weights,
§3.11 ceiling). Speed is where the cost is: granite-docling generates up to 8192 tokens
**per page, one page after another**. Every speed lever below is judged **under** the
10 GB ceiling — the question is "the fastest configuration that fits", never "the
fastest configuration" — and by quality per second through the eval (§8.1).

1. **Batching and overlap — spike S11.** Measure pages/min for Phase A at page batch
   sizes 1, 2 and 4 (whatever Docling's VLM pipeline or mlx-vlm exposes — find the
   option, don't assume one), and whether CPU work (parse, render) overlaps GPU
   generation (Docling's `ThreadedPdfPipelineOptions` exists for the standard pipeline;
   check whether the VLM pipeline has an equivalent). Ship the fastest setting whose
   peak stays under the ceiling; record batch 1 as the fallback.
2. **Image resolution is a tuned knob.** `images_scale` (2.0 = 144 DPI) sets
   granite-docling's input size *and* chart-crop size, hence Granite Vision's 384 px
   tile count. Sweep 1.5 / 2.0 / 2.5 through the eval in S11; choose by quality per
   second, recorded with the numbers. Not a CLI flag in v1 — one tested value.
3. **Per-stage, per-page timings.** `run.json` records, per page: render ms,
   generation ms, **tokens generated**, and whether it hit `max_tokens`; per chart:
   extraction ms; per PDF: parse, classification, export and chunking ms, and per-phase
   peak memory. This is what turns "slow" into "page 17: 8192 tokens, 37 s". Timings
   are wall-clock (`time.perf_counter`), collected by our code around Docling calls or
   from Docling's own timing hooks if it exposes them (check in S11).
4. **One run at a time per Mac.** Two `gdi` processes (two terminals, or a v1.x UI
   beside the CLI) would each load the models and push a 16 GB Mac into swap — the old
   app's `_inference_lock` problem across processes instead of threads. Hold an
   exclusive `fcntl.flock` on `$GDI_CACHE_DIR/run.lock` (§3.6) for the whole run; a second run prints "waiting for another gdi run (pid N)" and waits, or
   exits non-zero with `--no-wait`. The lock is released by the OS if the process dies,
   so a crash cannot leave it stuck.
5. **MLX's buffer cache is bounded.** Freed buffers stay in MLX's cache and can grow
   across a long document. Set **`mx.set_cache_limit(1 << 30)`** (1 GiB, chosen in
   S4: MLX's default limit is 16.32 GB, above the working set, so it bounds nothing; a
   limit of 0 was the slowest of three runs), and record peak memory per page. S4 found
   the per-page peak flat across 42 pages (±0.01 MB/page) at every limit tried.
6. **Sustained throughput, not best case.** A MacBook throttles under sustained load.
   S8 reports pages/min over the 30+ page corpus document from first page to last, and
   the README states the **sustained** rate with machine, date and versions.
7. **Page-level resume — deferred, with a trigger.** Resume is per PDF (§3.8), so a
   crash on page 49 of 50 redoes all 50. Keeping each page's DocTags in `.partial/`
   would fix that at real complexity (invalidating on option or revision change).
   Deferred until S8 shows a typical PDF takes **more than ~5 minutes**; the S8 write-up
   records whether the trigger fired. **It fired (S8, 2026-10-25):** a 69-page GAO
   report took 7.1 min of Phase A, and 8 of the 10 GAO candidates run 59–216 pages
   (~6–22 min at ~6 s/page). **Decided with the user (2026-10-25): in v1, PR 6,
   kept small.** The realistic case is not a crash. It is a Ctrl-C, a closed lid or a
   reboot partway through a long PDF. Scope:
   - Cache **only granite-docling's per-page DocTags**, roughly 70% of Phase A's time,
     in `OUT_DIR/<pdf>.partial/pages/`. Chart and KVP outputs are not cached.
   - Key each cached page on the input SHA-256, the model SHAs and the options hash.
     A page whose key does not match is regenerated: every uncertainty runs (§3.8).
   - **Feasibility gate (PR 4):** if Docling cannot assemble the document from cached
     DocTags through public API, resume is deferred again and the reason recorded
     here. It is never built on private Docling internals or a patch.
8. **KVP prompt caching — later, not v1.** With `--pages all` every page re-encodes the
   same schema. MLX supports prompt caching, but if Granite Vision's prompt puts the
   image before the schema (check the chat template), little is reusable. Noted as a
   post-v1 optimisation with that caveat.
9. **The gate stays fast.** The Stop hook runs the suite every turn; the old repo
   already paid once for a slow gate (21 s → 5 s via `$STUB_*`). Unit tests must not
   import `docling`, `torch` or `mlx_vlm` at module level — import inside the test that
   needs it, mock the rest — and **`GATE_FORCE=1 ./scripts/gate.sh` must stay ≤ 15 s**
   on the development Mac. PR 3 measures it after the teardown; PR 4 (the first PR to
   bring Docling in) re-measures it, and a breach is fixed before merge.

## 4. Phase 0 — spikes (before any production code)

Each spike is a throwaway script in the scratchpad, not the repo. **Stop and report
if any spike fails its pass criterion** — S1 and S3 block outright, and S2, S4, S6,
S7, S10 change the design.

**Scoring in Phase 0.** `eval/score.py` does not exist until PR 7, so the spikes that
need quality numbers (S6, S7, S11) compare their outputs against the hand-written
`eval/expected/` files (§4.1) with a scratchpad script applying the §8.1 metrics and
§8.2 matching rules by hand. PR 7 then **re-runs S6, S7 and the S11 sweep through
`score.py`** and records whether each verdict still holds; a reversed verdict is a plan
amendment in that PR.

| # | Question | Pass criterion |
|---|---|---|
| S1 | Do `docling[vlm]>=2.140` (the `docling` spelling of `docling-slim`'s `models-vlm-inline`; chunking comes with `standard` — §2.1), `mlx-vlm>=0.7.8` and their `transformers`/`mlx` resolve together on Python 3.12? Does the `granite-embedding-english-r2` tokenizer load via `HuggingFaceTokenizer`? | `uv lock` succeeds in a scratch project; resolved `transformers` is **≥ 5.16** (§2.1); record the resolved `transformers`, `mlx`, `torch` versions; the tokenizer chunks a sample document |
| S2 | Does Docling's MLX engine load a **local** 8-bit conversion via `repo_id`? | chart extraction runs from the local path with `HF_HUB_OFFLINE=1`; if only Hub ids work, decide between a private Hub repo and a monkeypatch |
| S3 | Does the 2025-09 granite-docling MLX conversion (mlx-vlm 0.3.3) load and convert pages under the locked mlx-vlm (latest inside `<0.8`; record the version — §2.2)? Does Docling detect or stop repetition loops, and can a page hitting `max_tokens` be detected from the result? | a 5-page PDF converts with sane Markdown and at least one table; record how a capped page shows up (§3.10) |
| S4 | Peak memory per phase, and does releasing the converter return GV's memory? Does memory stay flat across a long document (§3.14 rule 5)? | Phase A peak and post-release `mx.get_active_memory()` recorded; Phase B starts below ~5 GB active; whole run stays under ~10 GB on a 20–50 page PDF with charts; per-page peak shows **no upward trend** across the run, with the `mx.set_cache_limit` value that achieves it recorded |
| S5 | `force_backend_text` on vs off, on one born-digital and one scanned PDF | pick the default from a read of the output, not a metric alone |
| S6 | 8-bit vs bf16 Granite Vision quality on ~10 charts and one KVP form | CSVs match on values; KVP fields match; if 8-bit degrades, try `--q-bits 6`/bf16 and re-measure S4 |
| S7 | granite-docling's own `<chart>` → table vs Granite Vision `<chart2csv>` on the same charts | if granite-docling is as good, Granite Vision's chart role shrinks to summaries — record either way |
| S8 | Throughput | seconds/page for Phase A (born-digital and scanned separately), seconds/chart, seconds/KVP page and cold-start load time; **sustained** pages/min across the 30+ page document, first page to last (§3.14 rule 6); whether a typical PDF exceeds ~5 min (the page-level-resume trigger, rule 7); a sanity reference for PR 7's budgets (§3.11) and the README's first numbers |
| S9 | How far toward torch-free can the stack go (§2.1)? (a) Does `docling-slim` with only `format-pdf`, `feat-chunking` and `mlx-vlm` added directly — no `models-vlm-inline` — import and run `VlmPipeline` with the `granite_docling` preset on the MLX engine (§3.2) and the MLX chart preset? (b) Does the classifier run on `ONNXRUNTIME` without importing torch (`"torch" not in sys.modules` after a run)? (c) Does granite-docling's DocTags already label picture types, making the classifier redundant? | each answered yes/no with evidence; (a) yes → drop torch from the lock; (b) yes and same chart picks on the corpus → ONNX classifier; (c) yes → note it, but keep Docling's classifier unless S7 says otherwise |
| S10 | Does any load path execute model-repo code, and do revision overrides reach every loader (§3.13 rules 1–2)? Run `mlx_vlm.convert`, a Phase A conversion and the classifier with every model pinned, then list `sys.modules` entries whose file lives under the HF cache. | **none** from the HF cache **or the models directory** (the conversion copies the repo's `modeling.py`, `processing.py`, `configuration.py` and `downsampling.py` into it — S2); the three Docling models' SHAs matched against `models/manifest.json` (S2) and the tokenizer's observed at its loader (e.g. via `HF_HUB_OFFLINE=1` with only those revisions cached). Any repo code imported → **stop and report** |
| S11 | Speed levers under the 10 GB ceiling (§3.14 rules 1–3): page batch size 1/2/4 and CPU–GPU overlap, if Docling or mlx-vlm exposes them; `images_scale` 1.5/2.0/2.5; whether Docling exposes per-stage timing hooks | pages/min, peak memory and eval scores per setting; the shipped setting is the fastest that fits **and** does not regress the eval; batch 1 / scale 2.0 recorded as fallbacks |

Spike results go in **`docs/plans/pdf-pipeline-spikes.md`** — one section per spike:
what was run (exact versions), the numbers, the verdict, and any amendment it forces on
this plan. Amendments are made to this plan in the same PR, so the plan stays the single
source of truth.

### 4.1 Task C — assemble the eval corpus (runs alongside the spikes)

The spikes need real PDFs, and S6/S7/S11 are scored against the expected outputs (§4
"Scoring in Phase 0"), so the corpus is built in Phase 0, not after. It is a sourcing-and-labelling task, not a decision — the criteria
below are the decision.

**Two tiers, one scorer:**

- **Public** — `eval/corpus/` + `eval/expected/`, committed. 4–6 PDFs covering, between
  them: bar, line and pie charts; tables (at least one spanning a page break if one can
  be found); a **scanned** multi-page document; a **form** for KVP; one **30+ page**
  document for memory and throughput; at least one **non-chart picture** (a photo or
  diagram, for false-positive chart detection, §8.2); a **sideways-printed or very wide
  table** (S3 found these loop silently); and one **adversarial** PDF
  made by the project (§8.2) — so 5–7 PDFs in all.
- **Private** — `eval/private/corpus/` + `eval/private/expected/`, **gitignored** (add
  `/eval/private/` and `/eval/runs/` to `.gitignore` in the same commit — the latter is
  where `score.py` writes its conversions, §8.1). For the user's own PDFs, which are
  what the pipeline must actually handle well and are likely not shareable. `score.py`
  scores it when present and says so in its output; absent, it is skipped silently.

**Licensing — every public file needs a recorded licence:** `eval/corpus/SOURCES.md`
lists, per file, the source URL, retrieval date, licence and why it qualifies. A file
whose licence cannot be stated does not go in. Candidates, in order of preference:

1. **US federal government works** (17 U.S.C. § 105, public domain) — GAO and CBO
   reports are dense with charts and tables; check the specific report carries no
   third-party copyrighted figures.
2. **IRS forms** (e.g. W-9) — public domain, a clean KVP case: fill one in with
   fictitious data, then export.
3. **arXiv papers only where that paper's own licence is CC BY** — the arXiv default
   licence does not permit redistribution; check the per-paper licence link.
4. **The scanned case: print a public-domain document from (1) and scan it** on the
   development machine. The scan is then the project's own work, and its expected
   output can be derived from the born-digital original.

**Expected outputs are hand-written from the PDF**, never copied from a model run (a
model-written expectation would score the model against itself). This, not finding the
files, is most of the effort — budget for it. Keep the corpus small enough (target
< 20 MB) that cloning the repo stays cheap.

**Done when:** `eval/corpus/README.md` states that the corpus files keep their own
licences and are not covered by the project's `LICENSE` (§9.1), the public tier covers
every bullet above, `SOURCES.md` has a licence for
every file, and `eval/expected/` holds hand-verified chart CSVs, table CSVs, a KVP schema
with its expected JSON, and the must-appear headings.

**Status (2026-10-25): public tier done except the scan**, on branch `test/eval-corpus`
(PR 2). It holds six PDFs, 1.4 MB: GAO-07-631 (42 pages, 12 line and bar plots, 2
tables, logos and a signature); excerpts of GAO-08-14 (a pie chart), GAO-08-1044 (a map
and photographs, no charts) and Census P60-282 (a sideways 16-column table and a table
spanning a page break); a filled W-9; and `adversarial.pdf`, generated by the committed
`eval/corpus/make_adversarial.py`. The 13 charts meet S6's "~10 charts and one KVP
form". Three findings changed rules elsewhere: the text layer can disagree with the
printed page (§8.2 rule 2), the Census tables use an en-dash minus and significance
asterisks (§8.2 rule 3), and the two unlabelled GAO line charts had to be read from
their vector geometry, which the manifest marks `"values_from": "geometry"`. The
source report also carries two swapped figure captions (Figures 8 and 9), which its
manifest records. **The scan should be GAO-07-631's PDF pages 12–26**: those 15 pages
hold all 12 plots and both tables, so the scan's expected outputs are the existing CSVs
with the pages renumbered, and nothing new has to be transcribed.

### 4.2 Converting Granite Vision

The command S2 ran (flags verified against mlx-vlm 0.7.10; the `python -m
mlx_vlm.convert` form is deprecated):

```bash
uv run python -m mlx_vlm convert --hf-path ibm-granite/granite-vision-4.1-4b \
  --revision dd48e97503de471803850df70843cf9eb5da8712 \
  -q --q-bits 8 \
  --mlx-path ~/.cache/granite-document-intelligence/models/local--granite-vision-4.1-4b-8bit
```

It took 184 s including the 8.0 GB download, peaked at 3.8 GB RSS and wrote 4.92 GB.
Never pass `--trust-remote-code` (§3.13 rule 2); none was needed.

**Amended by S10 (2026-10-25):**

- **The CLI is safe; the function is not by default.** Without the flag, the CLI
  calls `convert(trust_remote_code=False)`, and S10 saw no repo code run. The Python
  function's default is `True`, and with it the conversion imported the repo's
  `configuration.py` from the pinned snapshot. `models.py` passes
  `trust_remote_code=False` explicitly if it calls the function.
- **The output must be stripped.** It still carries the repo's four `.py` files and
  its `auto_map` keys, so `gdi convert-model` removes both before writing the
  manifest (§3.13 rule 2).
- **The README comes from `main`.** The conversion's model-card step reads
  `README.md` through `refs/main`, not `--revision`. It is metadata only, never
  loaded as code, and it is why a README-only `main` snapshot appears in the HF
  cache.

## 5. Phase 1 — tear down the text app (one commit, gate green)

Delete: `streamlit_app.py`, `.streamlit/`, `static/`, `tests/test_app_ui.py`,
`docs/images/`. `README.md` embeds `docs/images/screenshot-dark.png` (line 9), so the
same commit removes that image line and adds a one-line note that the project is being
rebuilt (pointing to this plan) — the full rewrite is PR 8.

**Do not delete `docs/plans/ui-theme/`** — it is the v1.x UI's theme (§14), not part of
the text app. Its `config.toml` references `static/fonts/`, which this step removes;
§14 says how the UI work restores them. Note in the PR description that `static/fonts/`
and its OFL licence text are recoverable from tag `v0.6.0`.

**Do not delete `tests/test_streamlit_app.py` wholesale.** From line ~1320 to the end
(starting at `CI_WORKFLOW` and `_load_ci_workflow()`, which `TestCIWorkflow` and
`TestReleaseWorkflow` both use) it holds the tooling suites — `TestCIWorkflow`,
`TestReleaseWorkflow`, `TestHooksConfig`, `_STUB_SOURCES` and the session-scoped
`stub_bin` fixture — which guard `ci.yml`, `release.sh`, `gate.sh` and
`.claude/settings.json` and carry over. Move them (with their imports and helpers) to
`tests/test_tooling.py`, delete the rest, and confirm the moved suites' test count
matches before and after. Fix the two stale mentions they carry: `TestReleaseWorkflow`'s
docstring names `TestThemeConfig`, and a comment cites `streamlit_app.py`.

`pyproject.toml`: drop `mlx-lm`; **move `streamlit` from `dependencies` into a `ui`
dependency group** listed with `dev` in `[tool.uv] default-groups`, so the §14/§15
prototypes (`docs/plans/ui-theme/preview.py`, `docs/plans/ui-layout/layout.py`) keep
resolving under `ty` and keep running — dropping it outright would turn the gate red on
their unresolved imports, since ty has no exclude configured. The library never imports
Streamlit (§1). Drop the `static/…` entries from `license-files` (the OFL fonts and the
favicon leave with `static/`), leaving `["LICENSE"]`; set `description` to §9.2's
**interim** sentence (the final one lands in PR 9). Run `uv lock`. The README's License
section loses its IBM Plex and favicon paragraphs in the same commit, so it never names
files that are gone.

**`.gitignore` — extend, don't replace.** The existing hand-written file stays as is
(including `.streamlit/secrets.toml`: a v1.x UI may bring Streamlit back, and the
hook's secret guard still names it). A generic template would add dozens of irrelevant
entries and still miss this plan's paths. Here `.gitignore` configures three tools, not
one: **git**; **ruff**, which respects it by default — a non-ignored output folder puts
model-generated `chart-NNN.py` under `ruff check .` / `ruff format --check .` and fails
the gate; and **`scripts/gate.sh`'s memo**, which fingerprints
`git ls-files -co --exclude-standard`, so every non-ignored output file would be hashed
on each Stop and invalidate the memo on every run. Add, in this PR:

```gitignore
# Pipeline output (§3.5) — kept out of git, ruff and the gate's fingerprint
/out/
*.partial/
*.old/

# Docling's --debug-vlm-native-output writes debug_<document>/ in the working dir
debug_*/

# Model weights must never be committed (8-bit GV is 4.92 GB)
/models/
*.safetensors
*.gguf
*.npz

# User documents dropped at the repo root; the corpus lives in eval/corpus/
/*.pdf

# Packaging (src/ layout, uv build)
build/
dist/
*.egg-info/
```

Anchoring is deliberate: `/*.pdf` catches private documents at the root without hiding
the committed `eval/corpus/*.pdf` (an unanchored `*.pdf` would), and `/out/` ignores
only the top-level folder. Left out on purpose: coverage files (no coverage tool
planned) and a ty cache (unverified that one exists — add it when it appears). The
`eval/` entries land with the corpus in PR 2 (§4.1).

At this point the repo is tooling-only, the gate is green, and `version` is **not** yet
bumped (§11).

## 6. Phase 2 — library

Order: packaging (§3.6) → `config.py` → `models.py` + a minimal `cli.py` exposing only
`gdi convert-model` → `convert.py` (Phase A) → `export.py` → `memory.py` → `chunk.py` →
`kvp.py` (Phase B) → `api.py` → `safety.py`, `runlock.py`, `batch.py` → `cli.py`'s
`gdi convert`. Each lands with its tests (§8). Write `api.py`'s dataclasses **first** as
stubs, so every module after `convert.py` targets the public types rather than raw
Docling objects. Notes:

- **Model acquisition.** `gdi convert-model` fills `$GDI_CACHE_DIR/models/` (default
  `~/.cache/granite-document-intelligence/models/`), the single `artifacts_path` every
  Docling stage loads from (§3.2, S2). It fetches granite-docling and the figure
  classifier at their pinned SHAs, runs the S2-validated conversion (§4.2) into
  `local--granite-vision-4.1-4b-8bit/` with `trust_remote_code=False`, strips the
  repo's `*.py` files and `auto_map` keys (§3.13 rule 2, S10), writes
  `manifest.json` (§3.13 rule 1), load-tests the result, and offers the bf16-source cleanup in §3.12 rule 3. There is
  no per-model path override, because Docling fixes the folder names. `gdi convert`
  with a missing folder, a manifest that disagrees with `config.py`, or a folder
  holding a `*.py` file or an `auto_map` key fails with the exact command to run — never a silent fallback to bf16 or to the Hub.
- **Lazy imports.** `docling` and `mlx_vlm` imported inside functions, so `gdi --help`
  and the test suite don't pay for torch/mlx at import.
- **Greedy decoding everywhere** (`temperature=0.0`) — reproducible output, and the
  only setting under which a smoke test can compare runs.
- Keep `python-dotenv` for `HF_TOKEN` (unchanged rationale: public models, token only
  lifts rate limits).
- **Docling telemetry/remote services:** confirm no outbound request beyond the Hugging
  Face Hub (`enable_remote_services` stays `False`); document the result.

## 7. Phase 3 — CLI and failure handling

- Multiple PDFs and directories: the §3.8 rules, implemented once in
  `convert_many()`; the CLI only parses arguments and prints.
- KVP validation failure is **not** a run failure — `kvp.json` records `"valid": false`
  and the raw output.
- Progress: per-page progress on stderr; `--quiet` silences it.

## 8. Tests

Same philosophy as today: **mock at the model boundary**, never download in CI.

- `test_tooling.py` — moved verbatim (§5), plus a `.gitignore` guard in the style of
  `TestHooksConfig`: `git check-ignore` reports `out/x/chart-001.py`,
  `models/a.safetensors`, `report.pdf`, `eval/private/x.pdf`, `eval/runs/x/run.json`
  and `debug_report/x` as **ignored**, and `eval/corpus/x.pdf`,
  `eval/expected/x/chart-001.csv`, `eval/results/x.json`, `.env.example` and
  `docs/plans/pdf-pipeline.md` as **not ignored**. Paths need not exist —
  `git check-ignore --no-index` evaluates patterns only. This makes an edit that hides
  the corpus or exposes weights a gate failure, not a file someone misses months later.
  Also: every `license-files` entry in `pyproject.toml` exists (§9.1 rule 1); every
  CI `uses:` is a 40-hex SHA with a `# v…` comment (§3.13 rule 9).
- `test_export.py` — build small `DoclingDocument`s in code (docling-core, no models) and
  assert the §3.5 tree, filenames, CSV contents, and that a chart without
  `tabular_chart` produces no CSV rather than an empty one.
- `test_kvp.py` — prompt construction, the ported `parse_json_output` cases, schema
  validation pass/fail, `--pages` parsing, with `mlx_vlm.generate` mocked.
- `test_convert.py` — the `VlmPipelineOptions` actually built: the `granite_docling` `VlmConvertOptions`
  preset on `MlxVlmEngineOptions` (not the deprecated `GRANITEDOCLING_MLX`),
  chart extraction on with the **`_mlx` preset**, summary on, code off,
  `artifacts_path` set to the models directory and Granite Vision's MLX `repo_id` set to
  `local/granite-vision-4.1-4b-8bit` (S2). This is the silent-degradation guard: the
  auto preset or a dropped `repo_id` still produces output, just from the wrong model.
  `--text-source pdf` sets `force_backend_text` on the `VlmConvertOptions`, and `vlm`
  sets it on neither options object. Setting it on `VlmPipelineOptions` is a silent
  no-op on the preset path (S5). Also (§3.13): a manifest that disagrees with `config.py` is refused before any model
  loads, the tokenizer reaches its loader at its pinned SHA, and no call from our code
  passes `trust_remote_code=True`. A models directory with a `*.py` file or an
  `auto_map` key in any folder is refused before any model loads, naming
  `gdi convert-model`, and our Phase B load passes `trust_remote_code=False`
  explicitly (S10).
- `test_chunk.py` — chunks from a hand-built `DoclingDocument`: headings carried,
  page numbers present, every line valid JSON with the §3.9 keys (tokenizer mocked or
  a tiny local one — no download).
- `test_api.py` — `convert()` writes nothing to disk (run it in a read-only tmp dir);
  `save()` produces exactly the §3.5 tree; result types are the public dataclasses;
  `document.json` contains no base64 image data and its image references resolve to
  files in the tree; no `pages/` folder unless page images were requested.
- `test_batch.py` — directory expansion (case-insensitive, sorted, `--recursive`,
  relative output paths, no collision for same-named files); resume skips only a
  matching successful `run.json`, and re-runs on a missing, failed, hash-mismatched or
  options-mismatched one; a folder with outputs but no `run.json` is re-run; `--force`;
  one failure doesn't stop the batch and makes the exit non-zero. Phase A/B mocked.
  Storage (§3.12): a re-run that yields **fewer charts** leaves no `chart-NNN` file
  from the earlier run; a failed re-run replaces a previous success's tree rather than
  adding a `run.json` beside its files; a leftover `<pdf>.partial/` is deleted at
  startup; a `<pdf>.old/` with no `<pdf>/` (crash mid-swap) is restored; the batch
  summary reports the output size.
- `test_cli.py` — argument parsing, missing-model error message, exit codes, and that
  it delegates to `convert_many()` rather than re-implementing resume. `gdi
  convert-model --delete-source` (conversion and HF cache mocked): the source is
  deleted only after the 8-bit output passes its load test, and never without the flag.
  Also (§3.13 rule 2, S10): on a fixture folder shaped like mlx-vlm's output (the
  four `.py` files, top-level and nested `auto_map`), `convert_model()` leaves no
  `*.py` file and no `auto_map` key at any depth, records both in the manifest, and
  passes `trust_remote_code=False` to the conversion.
- `test_safety.py` — the §3.10 rules: a PDF over `--max-pages` / `--max-file-mb` is a
  recorded per-PDF error and the batch continues; a page whose result shows
  the cap (`num_tokens >= max_new_tokens`, or no closing `</doctag>`) or a self-closed
  DocTags loop lands in `run.json` warnings — tested on hand-made DocTags, since Docling
  reports neither (§3.10); `chart-NNN.py` carries the untrusted
  header and nothing imports it; an output path that would resolve outside the output
  directory is refused; a symlink out of the input directory is skipped with a warning;
  `Image.MAX_IMAGE_PIXELS` is unchanged after a run. And the §3.13 rules: a `.pdf`
  without the `%PDF-` header — and a real PDF renamed `.txt` passed explicitly — are
  judged by content; a sentinel `HF_TOKEN` and a sentinel string inside the PDF appear
  in no output file and not on stderr; a KVP schema with a remote `$ref` is rejected
  with **no** network attempt (socket creation patched to raise), and a schema over
  256 KB or not a JSON object is rejected. And §3.14 rule 4: with the run lock held by
  another process, a second run waits (and says so) or, with `--no-wait`, exits
  non-zero without loading a model; a lock held by a process that has exited does not
  block.
- Every test module: no module-level import of `docling`, `torch` or `mlx_vlm`
  (§3.14 rule 9) — a small test in `test_tooling.py` parses each test file's top-level
  imports and fails on one.
- `test_score.py` — the eval scorer and `eval/matching.py` on synthetic pairs (§8.2
  rule 9): deliberately bad predictions must lower each score, normalization and
  pairing rules behave as written, and a committed results file containing a private
  path is refused. Runs in CI like every other test — it needs no model.

**CI cost check:** `docling` pulls `torch`; measure `uv sync --locked` time on the
`macos-26` runner in the first PR and record it. The job's 15-minute timeout is the
constraint. Locally, `GATE_FORCE=1 ./scripts/gate.sh` stays **≤ 15 s** (§3.14 rule 9).

### 8.1 Quality eval harness (real models, run by hand — never in CI)

Unit tests prove the plumbing; only real inference shows whether a model, quantization
or dependency change made the output worse. This replaces "read 48 runs by eye" from
the old app with a scored check.

- **Corpus and expected outputs:** built in Phase 0 — see §4.1 for the two tiers
  (public, committed; private, gitignored), the licensing rule and the coverage list.
  `eval/expected/<pdf>/` holds hand-verified chart CSVs, table CSVs, a KVP schema +
  expected JSON, and the headings that must appear in `document.md`, indexed by a
  `manifest.json` (pages, captions, non-chart pictures, the PDF's SHA-256). The file
  layout and CSV conventions are specified in `eval/expected/README.md` (Task C,
  2026-10-25), and `score.py` reads that format rather than redefining it.
- **Scorer:** `eval/score.py` runs `convert_many()` over the corpus and reports per PDF:
  - charts: numeric cell match within a relative tolerance (default 1%), header match,
    and charts found vs expected
  - tables: cell-level exact match after whitespace normalization (TEDS if a library
    for it is cheap to add — decide in implementation)
  - KVP: per-field exact match, schema-valid rate
  - document: expected headings present, page count
  - run: wall time, seconds/page, seconds/chart, peak memory, pages that hit
    `max_tokens` — each checked against `eval/budgets.toml` (§3.11)
- **Working output:** `score.py` converts into `eval/runs/<date>-<git-sha>/`
  (gitignored); only the small results summary below is ever committed.
- **Output:** a Markdown table to stdout plus `eval/results/<date>-<git-sha>.json`, so
  two runs (bf16 vs 8-bit, old vs new Docling) diff mechanically. Results files are
  committed when they back a decision, as the old app's A/B figures were recorded in
  CLAUDE.md.
- **Pass/fail:** no fixed bar in v1 — the first run on the shipped configuration *is*
  the baseline, and a change fails if it drops any score below it **by more than that
  metric's tolerance** (§8.2 rule 1). S6 (8-bit vs bf16)
  and S7 (granite-docling vs Granite Vision charts) should be run **through this
  harness** once it exists, and re-run if the spike-time conclusions are revisited.
- **Meaningfulness, not just parse rate:** the scorer must compare values, never only
  "did it parse" — the old app learned that a script check passes degenerate output
  (CLAUDE.md, 2026-08-12 design decision).

### 8.2 Evaluation rules (decided 2026-10-18)

The harness above says *what* is compared; these rules say *how* a comparison is made
and *how* a failure is judged. Without them a score can move because of a matching
detail rather than a quality change.

1. **Tolerances and re-baselining.** `eval/budgets.toml` carries a per-metric
   regression tolerance next to the time budgets (proposed: 0 for KVP field matches and
   chart/table counts; 1 percentage point for cell-match rates; 0.01 for text edit
   distance). A change that **intends** to move a score — a Docling upgrade that writes
   `41` for `41.0`, a better model — re-baselines through a PR that commits the new
   `eval/results/` file and states, per moved metric, why the new value is right. A
   re-baseline is never a side effect of another change, and lowering a baseline needs
   the user's approval in that PR.
2. **Document text is scored, not just headings.** For born-digital PDFs the PDF's own
   text layer (`pypdfium2`) is a free reference: score normalized edit distance of
   `document.md`'s text against it, and reading order as the rank correlation of
   paragraph positions. For the print-and-scan file the born-digital original is the
   reference (§4.1 already derives its expected output that way). Normalize before
   comparing — whitespace, hyphenation at line ends, ligatures, Unicode NFKC — and
   strip Markdown syntax from the prediction. **The text layer is not always what is
   printed** (found by Task C): Census P60-282 carries a "—Con." after the first page of
   each table's title that is never drawn, and `adversarial.pdf` hides a white-on-white
   injection. A prediction must not lose points for omitting text a reader cannot see.
   The two cases cannot be caught the same way: the adversarial text is white-filled,
   which a per-character check can see, but the Census "—Con." is an ordinary text
   object (render mode 0, near-black fill, inside the page, checked with pypdfium2),
   hidden by whatever is drawn over or clipped around it. So the scorer drops
   white-filled and render-mode-3 text *and* removes the strings each manifest's
   `notes` lists, and a new corpus PDF's text layer is diffed against its rendered page
   before its manifest is written.
3. **Matching rules are written down.** A predicted chart or table pairs with an
   expected one on the **same page** by **reading order**; if counts differ on a page,
   pair by bounding-box IoU ≥ 0.5 from the DoclingDocument provenance, and anything
   unpaired is a miss or a false positive. Values are normalized before comparison:
   strip thousands separators and currency symbols, parse `12%` as 12 with a `%` unit,
   treat `−` (U+2212) **and `–` (U+2013, the Census tables' minus)** as `-`, strip a
   leading significance asterisk (`*1.3`), compare the non-numeric codes `X` and `Z`
   as strings, and compare numbers within the 1% relative tolerance; headers compare
   case- and whitespace-insensitively. Chart rows pair by their first-column label (a
   pie's slice order is arbitrary), and table rows pair by position (dropping footnote
   markers can leave two rows with the same label, as with Table A-2's two `2017`
   rows). The expected values keep the source's own spelling, and the normalization
   is the scorer's (`eval/expected/README.md`). These rules live in
   `eval/matching.py` with their own tests (rule 9). **The IoU fallback has no
   expected boxes yet** (found by S6): granite-docling boxed GAO-07-631 Figure 9's two
   panels as one picture, so that page's counts differ (1 predicted, 2 expected), but
   no `manifest.json` records a chart's bounding box. Task C adds a `bbox` to each
   chart entry, or PR 7 defines another fallback. A picture that covers several
   expected panels is then scored against each of them.
4. **Chart summaries and chunks are checked.**
   - Summaries: a **hallucination check** — every number in a summary must appear,
     within tolerance, in that chart's expected CSV (or be a simple derivative: a
     difference, a ratio, a year); report the unsupported-number rate per chart.
     **Where a summary names a category next to a number, the pair is checked too**
     (amended by S6): both Granite Vision variants attached every percentage of the
     GAO-08-14 pie to the wrong slice, and a number-only check scored that 0
     unsupported. Axis ranges (`0 to 250`) and list numbering are what most remaining
     "unsupported" numbers turned out to be, so the scorer reports them apart from
     value claims rather than as hallucinations.
   - Chunks: each chunk ≤ `chunk_tokens` by the configured tokenizer; every
     `page_nos` entry is a page the chunk's text actually comes from (checked against
     the text-layer reference); heading breadcrumbs match the expected headings.
5. **False positives count.** Chart detection reports precision as well as recall: a
   photo or diagram treated as a chart produces a bogus CSV, and that is scored. The
   corpus carries at least one non-chart picture for it (§4.1).
6. **One adversarial PDF.** Made by the project (so licence-clean, Apache-2.0): a form
   page with visible *and* white-on-white text instructing the model to ignore the
   schema and output something else, plus a page built to trigger a repetition loop
   (e.g. a long run of identical table rows). Pass = KVP output still schema-valid and
   not the injected content; the loop page appears in `run.json` warnings (§3.10). It
   is scored, but kept out of the quality averages so it cannot mask a regression.
7. **Private results never reach git.** Private-tier runs write to
   `eval/private/results/` (already gitignored via `/eval/private/`), never
   `eval/results/`. The committed results file carries **only aggregate private
   numbers** (counts and mean scores, no filenames, page text or extracted values),
   and `score.py` refuses to write a committed results file containing a private
   path. `test_score.py` pins that refusal.
8. **Timing is a median of three.** Each PDF runs three times on an otherwise idle Mac
   (on power, not battery); time metrics use the median, memory uses the maximum. One
   cold run is recorded separately as cold-start. Budgets (§3.11) compare medians only.
9. **The scorer is tested in CI.** `tests/test_score.py` runs the scoring and matching
   functions on synthetic pairs — no models, no corpus PDFs — including **deliberately
   bad** predictions that must lower the score: a wrong cell, swapped rows, a missing
   chart, an extra chart, a hallucinated summary number, an oversized chunk, a private
   path in a committed result. Each was mutation-checked: a scorer that passes
   everything fails this file. The same lesson as `gate.sh`'s exit code — a check must
   be shown to fail before its passes mean anything.
10. **The corpus is for regressions, not model selection.** 5–7 PDFs and ~10 charts can
    show that a change made things worse; they cannot show which of two models is
    better in general. S6 (8-bit vs bf16) and S7 (granite-docling vs Granite Vision for
    charts) record their sample size beside the verdict and state the decision as
    "no measurable regression on this corpus", not as a general ranking. A choice that
    matters beyond that needs a larger, purpose-built set.

## 9. Documentation

- **`CLAUDE.md` — rewrite.** Keep, edited to the new project: *Commands*, the
  `gate.sh` / `release.sh` / CI / hooks sections under *Configuration*, *Commits*, the
  `uv.lock` and ruff/ty-upgrade rules, the `$STUB_*` test rule, the `.env` rationale.
  Remove everything about Streamlit, the theme, fonts, favicon, layout, prompts,
  `language_directive`, Granite 4.2 and the screenshot recipe. Add: the model lineup and
  revision pin (§2), the memory phases (§3.1), and a **"Verifying a dependency upgrade"**
  section — run `eval/score.py` (§8.1) after any move of `docling`, `mlx`, `mlx-vlm`,
  `transformers` or `huggingface-hub`, after any model or quantization change, and
  after any KVP prompt change; pass = no score below the committed baseline and peak
  memory under budget. The reason carries over unchanged: no test executes a line of
  `mlx`. The same procedure runs a vulnerability audit of the locked environment
  (§3.13 rule 6), and a model-revision bump (§3.13 rule 1) counts as a model change.
  Also add a **Security** section summarising §3.13's threat model and rules.
- **`README.md` — rewrite**: what it does, the 16 GB envelope and the S4/S8 numbers,
  `gdi convert-model` as a setup step, CLI usage, output layout, licenses (Granite
  models Apache-2.0, downloaded not vendored), and a **"Untrusted documents"** section
  covering §3.10's limits, the no-timeout limitation, KVP review and never running
  generated chart code. Also a **"Disk space"** section from §3.12: where models live,
  the measured setup and steady-state sizes, and how to delete the bf16 source.
- **README usage example** uses one PDF from the §8.1 corpus, so the documented
  command is one the eval actually exercises.
- **Python API** documented in the README with the §3.7 example.

### 9.1 Licensing (decided 2026-10-18)

**The project stays Apache-2.0.** Everything the new stack uses is permissive — checked
on PyPI and Hugging Face that day:

| Component | Licence |
|---|---|
| granite-docling-258M-mlx, granite-vision-4.1-4b, granite-embedding-english-r2 | Apache-2.0 |
| DocumentFigureClassifier-v2.5 | MIT |
| docling, docling-core, docling-parse, docling-ibm-models | MIT |
| mlx, mlx-vlm | MIT |
| transformers | Apache-2.0 |
| torch | Apache-2.0 / BSD / MIT mix |
| pypdfium2 | BSD-3-Clause / Apache-2.0 |
| jsonschema | MIT |

No copyleft forces a choice. Apache-2.0 is kept over MIT for its **explicit patent
grant**, it matches IBM's licence for the Granite models, and it is already wired
(`LICENSE`, PEP 639 `license` / `license-files`). Dependencies installed by `uv` create
no obligation — they are not redistributed. Obligations come from **copying** and
**publishing**:

1. **Copied third-party code carries its notice.** If any Docling code is copied (§3.3),
   add `THIRD_PARTY_NOTICES.md` with Docling's copyright line and the full MIT text, add
   it to `license-files`, and put a header comment on the copied code pointing to it.
   The same applies to anything else copied in later. `test_tooling.py` (or a small new
   test) asserts that every `license-files` entry exists — the old app's
   `license-files` named vendored fonts for the same reason.
2. **`license-files` after teardown** is `["LICENSE"]`, plus `THIRD_PARTY_NOTICES.md`
   once (1) applies (§5).
3. **The eval corpus is not under the project licence.** Public-domain files (US federal
   works, IRS forms) need nothing; **CC BY files need attribution**, and
   `eval/corpus/SOURCES.md` *is* that attribution — so each CC BY entry names the
   author, title, source, licence and any changes, per CC BY's terms.
   `eval/corpus/README.md` says the files keep their own licences. The project's own
   scans and the hand-written expected outputs are Apache-2.0 like the rest of the repo.
4. **The 8-bit conversion is a derivative of Apache-2.0 weights.** It is produced
   locally and never redistributed, so nothing is owed today. **Publishing it** (to the
   Hub or anywhere) would require shipping the Apache-2.0 licence and a prominent notice
   that the weights were modified (quantized to 8-bit, with the source revision) — no
   one uploads it without adding both.
5. **Output rights follow the input.** The Apache-2.0 models restrict nothing they
   generate, but Markdown, tables and chart data extracted from a copyrighted PDF are
   still derived from that PDF. The README says so in one line, so the tool never
   implies its output is free to reuse.

The README's License section is rewritten to match: project code Apache-2.0, models
downloaded at runtime under their own Apache-2.0/MIT licences (not vendored), the
corpus under its own licences (pointing to `SOURCES.md`), and — if it exists —
`THIRD_PARTY_NOTICES.md`.

### 9.2 GitHub repository details (decided 2026-10-18)

As of that date, `darylalim/granite-document-intelligence` (public) still describes the
text app — description *"Streamlit application for summarization, topic, intent, and
sentiment analysis using IBM Granite on Apple Silicon with MLX."*, topics
`summarization`, `mlx-lm`, `apple-silicon`, `ibm-granite`, `mlx`, `sentiment-analysis`,
`streamlit`, `text-analysis`.

**Description** — one sentence, used verbatim in both GitHub and
`pyproject.toml`'s `description`:

> Local PDF document intelligence on Apple Silicon: converts multi-page PDFs to
> Markdown and JSON and extracts charts, tables and schema-driven key-values with IBM
> Granite Docling and Granite Vision on MLX.

It deliberately claims neither "fully offline" (the first run downloads from the Hub)
nor "MLX-only" (Docling imports torch and the picture classifier runs on it, §2.1).

**Topics** (18 of GitHub's 20):

| Group | Topics |
|---|---|
| What it does | `pdf`, `pdf-extraction`, `document-parsing`, `document-intelligence`, `chart-extraction`, `table-extraction`, `key-value-extraction`, `ocr` |
| Models and libraries | `ibm-granite`, `granite-vision`, `docling`, `vision-language-model` |
| Platform | `mlx`, `mlx-vlm`, `apple-silicon`, `local-first` |
| Interface and use | `cli`, `rag` |

Remove all five text-app topics (`summarization`, `sentiment-analysis`,
`text-analysis`, `streamlit`, `mlx-lm`). `ocr` is accurate because scanned PDFs are in
scope; `rag` because of `chunks.jsonl` — drop it if RAG should not be advertised before
embeddings exist. `python` and `machine-learning` are left out as too generic to help
anyone find the repo.

**Timing — the public page must describe what is on `main`.** Repo metadata is public,
takes effect immediately and is not version-controlled, so it changes with specific
PRs, not before:

- **PR 3 (teardown):** set the topics above, and an interim description: *"Being
  rebuilt as a local PDF document-intelligence CLI (IBM Granite + Docling on MLX). See
  docs/plans/pdf-pipeline.md."*
- **PR 9 (release):** set the final description above; it must match `pyproject.toml`,
  which carries the interim sentence from PR 3 to PR 9 and the final one from PR 9 on —
  so both public texts always describe what is on `main`.

Both changes go through `gh repo edit` (`--description`, `--add-topic`,
`--remove-topic`) **after** the PR merges, and only with the user's confirmation at that
moment — an outward-facing change is not covered by approval of the PR itself.

## 10. Delivery — branches and pull requests

One PR per step, each off a fresh branch from `main`, each green in CI before merge;
`main` stays linear (rebase or squash — never a merge commit). Merging to `main` cuts
**no** release until PR 9, because `scripts/release.sh` fires only on an untagged
`version`, and the version is unchanged until then. Use `gh` for PR operations.

| PR | Branch | Contents | Commit type | Merge gate (besides CI) |
|---|---|---|---|---|
| 0 | `docs/pdf-pipeline-plan` | this plan | `docs:` | user approval |
| 1 | `docs/pdf-pipeline-spikes` | spike report (§4) + any plan amendments it forces; no production code | `docs:` | **user approves the spike report** — the design is frozen from here |
| 2 | `test/eval-corpus` | §4.1 public corpus, `SOURCES.md`, `eval/corpus/README.md`, hand-written expected outputs, `/eval/private/` and `/eval/runs/` in `.gitignore` | `test:` | every file has a licence in `SOURCES.md` |
| 3 | `feat/teardown` | §5: delete the text app, move the tooling suites, drop deps, extend `.gitignore` and add its `git check-ignore` guard (which also covers PR 2's `eval/` entries), pin CI actions to commit SHAs and move `TestCIWorkflow` with them (§3.13 rule 9) | `feat!:` | moved suites' test count unchanged; description names the orphaned 7.3 GB Granite 4.2 cache entry (§3.12 rule 4); **after merge**, with the user's confirmation: new topics + interim GitHub description (§9.2) and Dependabot alerts on (§3.13 rule 6) |
| 4 | `feat/phase-a` | packaging (`[build-system]`, `[project.scripts]`, `pythonpath`, §3.6), deps under §2.2's constraints, `config`, `models` + minimal `cli` (`gdi convert-model` only), `api` stubs, `convert`, `export`, `memory` | `feat:` | a hand run on one corpus PDF, numbers in the PR description; gate ≤ 15 s (§3.14 rule 9) |
| 5 | `feat/chunks-kvp` | `chunk`, `kvp` | `feat:` | hand run with a schema on the corpus form |
| 6 | `feat/cli-batch` | `api` complete, `safety`, `runlock`, `batch`, `gdi convert`, page-level resume (§3.14 rule 7: DocTags only, if PR 4's feasibility check passed) | `feat:` | hand run on the whole public corpus |
| 7 | `test/eval-harness` | `eval/score.py`, `eval/matching.py`, `tests/test_score.py`, `eval/budgets.toml` (budgets + tolerances) from **this PR's own** median-of-three baseline (§3.11), the first results file as **baseline**, and S6/S7/S11 re-run through `score.py` (§4) | `test:` | `test_score.py` green and each bad-prediction case mutation-checked; baseline committed; every budget met; spike verdicts confirmed or amended |
| 8 | `docs/rewrite` | `CLAUDE.md` and `README.md` rewrites (§9), `SECURITY.md` (§3.13 rule 9) | `docs:` | README commands run as written |
| 9 | `chore/release-1.0.0` | `version = "1.0.0"` + `uv lock`, **both files** | `chore:` | **every §16 item checked**; eval run on the release commit meets baseline and budgets; `pyproject.toml` `description` set to §9.2's final sentence in this PR; **after merge**, with the user's confirmation: final GitHub description (§9.2) |

Rules:

- **Each PR leaves `main` coherent.** After PR 3, `main` is tooling-only — acceptable
  because nothing is released, and PR 3's README note says so (§5).
- **No model-touching PR merges on unit tests alone** — the "Merge gate" column is the
  same rule as CLAUDE.md's hand-run smoke test. Until PR 7 exists, the hand run's
  output summary goes in the PR description; from PR 7 on, it is an `eval/score.py`
  run.
- **Plan amendments travel with the PR that discovers them.** If implementation
  contradicts this plan, the PR edits the plan too and says why in its description.
- **`feat!:` only on PR 3** — that is the breaking change; later `feat:` PRs add to
  the new API.
- Every PR runs the full gate (`GATE_FORCE=1 ./scripts/gate.sh`) locally before push.

## 11. Release

The change removes every existing feature, so bump to **1.0.0** — in PR 9 (§10), last,
after the pipeline works end to end; an earlier bump would publish a tag for a
tooling-only repo. Follow the existing rule: bump `version` in `pyproject.toml`, run
`uv lock`, land **both** files.

## 12. Decisions and deferred items

None blocking. **Decided 2026-10-18:** names (§3.7); package layout (§3.6); dependency
policy (§2.2); eval corpus as a Phase 0 task, public + private tiers (§4.1); chunk size
512, configurable (§3.9); KVP on page 1 by default (§3.3); chart2code off behind
`--chart-code`; storage and the atomic swap (§3.12); no database (§3.12.1); security
model (§3.13); performance rules (§3.14); evaluation rules (§8.2); licensing (§9.1);
GitHub details (§9.2); UI theme and layout for v1.x (§14, §15).

**Settled by a spike** — the plan states the question and the decision rule:

| Item | Spike |
|---|---|
| local-path model loading | S2 — **settled 2026-10-25:** one `artifacts_path` models directory (§3.2) |
| Phase B ordering in batches (per PDF vs all-A-then-all-B) | S4 — **settled 2026-10-25:** per PDF (§3.8) |
| `mx.set_cache_limit` value | S4 — **settled 2026-10-25:** 1 GiB (§3.14 rule 5) |
| `--text-source` default | S5 — **settled 2026-10-25:** `vlm`; `pdf` is opt-in for trusted born-digital text layers (§3.2) |
| 8-bit vs 6-bit/bf16 Granite Vision | S6 — **settled 2026-10-25:** 8-bit; no measurable regression on 10 charts and 3 KVP pages, and bf16 alone peaks at 10.12 GB (§2). PR 7 re-runs it through `score.py`, so the bf16 source stays until then |
| Granite Vision's chart role | S7 |
| page-level resume (trigger: typical PDF > ~5 min) | S8 — **trigger fired; decided 2026-10-25:** in v1, PR 6, DocTags only, gated on a public-API feasibility check in PR 4 (§3.14 rule 7) |
| torch-free install, ONNX classifier | S9 — **settled 2026-10-25:** torch stays, classifier on transformers; reopen when Docling's two torch imports go lazy (§2.1) |
| model-repo code at load | S10 — **failed, then decided 2026-10-25:** mlx-vlm defaults `trust_remote_code` to True, so `gdi convert-model` strips the repo's `*.py` files and `auto_map` keys from the converted folder and `convert()` refuses a folder that has either (§3.13 rule 2) |
| page batch size, CPU–GPU overlap, `images_scale` | S11 |

**Settled during implementation:** TEDS vs cell-exact for tables (§8.1, PR 7);
whether `--delete-source` gets more than a mocked test (§8).

**Deferred past v1, with the condition that reopens each:** per-PDF timeout and process
isolation (§3.10 — if a PDF ever hangs); parser sandboxing (§3.13 rule 4 — if the tool
processes strangers' PDFs at volume); KVP prompt caching (§3.14 rule 8); a database or
vector index (§3.12.1 — on its named triggers); the chunk-size default (when an
embedding model is chosen); the v1.x UI (§14, §15).

## 13. References (as of 2026-10-18)

- Docling 2.140.0 — `docling/datamodel/stage_model_specs.py` (GV v4 presets, revision
  pin), `docling/datamodel/pipeline_options.py` (`VlmPipelineOptions`,
  `do_chart_extraction`, `VlmExtractionPipelineOptions`,
  `GraniteVisionTableStructureOptions`), `docling/models/inference_engines/vlm/mlx_engine.py`
  (`repo_id` override; `artifacts_path` lookup, S2), `docling/models/stages/chart_extraction/granite_vision.py`
  (where chart output lands on `PictureItem.meta`)
- mlx-vlm 0.7.8 (2026-10-13); Granite Vision 4.1 support: Blaizzy/mlx-vlm#1104
  (merged 2026-05-05), model type `granite4_vision`
- Model cards: `ibm-granite/granite-vision-4.1-4b`, `ibm-granite/granite-docling-258M`,
  `ibm-granite/granite-docling-258M-mlx`
- IBM's reference app: HF Space `ibm-granite/granite-vision-document-intelligence`
  (Gradio + ZeroGPU; hand-rolled crops and prompts — the approach this plan avoids by
  letting Docling orchestrate)

## 14. v1.x UI theme (designed 2026-10-18)

Not v1 work — recorded so the UI starts from a verified design instead of a fresh one.
Everything lives in **`docs/plans/ui-theme/`**:

- `config.toml` — the theme, ready to become `.streamlit/config.toml`, with the reason
  for every non-obvious value in its comments
- `preview.py` — a mock of the planned UI (sidebar options, Convert row, counts, the
  four status messages, a six-tab results strip, a re-plotted chart, a heatmap, a
  diverging chart, a table, badges, generated code); passes ruff and ty, so the gate
  covers it
- `preview-light.png`, `preview-dark.png` — 1280 px renders under that config

**Design: IBM Carbon g10 (light) + g100 (dark)** — the pair Carbon itself designs to be
used together, extending the text app's Carbon theme. The modes are complementary by
measurement, not by eye:

| Role | Light | Dark | Match |
|---|---|---|---|
| Text on page | `#161616` on `#f4f4f4` | `#f4f4f4` on `#161616` | 16.45:1 in both |
| Interactive | Blue 60 `#0f62fe` | Blue 60 `#0f62fe` | one brand colour; white on it 5.00:1 |
| Sidebar | white beside gray | black beside gray | one step away from the text in both |
| Borders | 1.55:1 | 1.57:1 | same visual weight |
| Chart categories | 14 Carbon slots | 14 Carbon slots | same hue per slot (≤ 5° drift), lightness inverted; all ≥ 3.0:1 on the page (min 3.03 light, 3.60 dark) |
| Sequential | Blue 10 → 100 | Blue 100 → 10 | mirrored — lowest value at the page colour |
| Diverging | Purple ↔ Teal, pale mid | Purple ↔ Teal, dark mid | mirrored; no red/green (red means error; deuteranopia) |
| Status hues | 7 `TextColor`s pinned | 7 `TextColor`s pinned | no derived wash-out |

**One deliberate deviation from Carbon:** dark slot 6 is Red 30 `#ffb3b8`, not Red 10
`#fff1f1`. Red 10 measured **1.00:1 against the dark `textColor`** — its bars read as
white in the preview, so the series lost its red identity across modes. It passed every
check against the *background*; only checking each palette colour against every surface
it sits on caught it.

**When the UI is built:**

1. Copy `config.toml` to `.streamlit/config.toml`, and restore `static/fonts/` (the six
   Plex woff2 files + `LICENSE.txt`) from tag `v0.6.0`
   (`git checkout v0.6.0 -- static/fonts`); re-add `static/fonts/LICENSE.txt` to
   `license-files` (§9.1).
2. Restore a theme guard like the old `TestThemeConfig` (git history, tag `v0.6.0`):
   both modes present, every key registered, every `<color>TextColor` pinned per mode,
   font files present with static serving on, a `unicodeRange` per face — and add the
   new invariants: palette lengths (`chartSequentialColors` and `chartDivergingColors`
   need exactly 10, or Streamlit silently uses its defaults), every categorical slot
   ≥ 3:1 against its mode's page **and** not within 1.5:1 of its mode's text colour.
3. Re-measure the results tab strip for the real layout at its narrowest supported
   width: the preview measured **442 px** for six tabs at 1280 px, and
   `baseFontSize = 15` was originally tuned for the old five-tab strip.
4. Known trade-off: diverging bars near zero nearly vanish in both modes, because the
   midpoint is the page colour — right for heatmaps, worth knowing for bar charts.
5. Re-render the preview (or the real app) in both modes before shipping; the capture
   recipe is CDP with `Emulation.setEmulatedMedia` for the mode (see the old CLAUDE.md's
   screenshot section at tag `v0.6.0`).

## 15. v1.x UI layout (designed 2026-10-18)

Not v1 work — like §14, recorded so the UI starts from a measured design. Everything
lives in **`docs/plans/ui-layout/`**: `layout.py` (the prototype; passes ruff and ty,
so the gate covers it) and three captures — `390-and-768.png` (phone beside tablet),
`1280.png`, and `2560-half-scale.png`.

**The constraint that shapes it:** Streamlit gives the script no viewport width
(`st.context` has `theme`, `locale`, `url` and others, but no width — checked on
1.64.0), so **nothing may branch on screen size**. Every adaptation is the browser's,
from two behaviours measured in headless Chrome:

1. A horizontal `st.container` **wraps** its children onto new rows when they don't fit.
2. An integer `width=` acts as a **maximum**: a 760 px column or a 344 px card shrank to
   a 330 px phone's content width with no horizontal overflow.

**The layout**, measured at 360, 390, 768, 1024, 1280, 1440, 1920 and 2560 px — page
horizontal overflow **0 at every width**:

| Region | Built with | Phone 360–390 | Tablet 768 | Laptop 1024 / 1280–1440 | Wide 1920 / 2560 |
|---|---|---|---|---|---|
| Settings | `st.sidebar` (settings only, never Convert or results) | collapsed | collapsed | open, 300 px | open |
| Convert row | horizontal container: button + one caption | caption under button | 1 line | 1 line | 1 line |
| Counts | 4 × `st.metric(border=True, width=130)` in a horizontal container | 2 × 2 | 1 row | 1 row | 1 row |
| Notices | `st.empty()` slot (the old app's reason holds: a container keeps stale notes) | — | — | — | — |
| View switcher | `st.pills`, single-select, `bind="query-params"` | 2 rows, all visible | 1 row | 1 row | 1 row |
| Chart cards | horizontal container of `st.container(border=True, width=344)` | 1 / row | 2 / row | 1 / 2 per row | 4 / 5 per row |
| Document text | `st.container(width=760)` | fills width | fills width | max 760 | max 760 — a readable line |
| Tables | `st.dataframe(width="content")` | scrolls inside itself | natural width | natural width | natural width, no stretched columns |

**Why pills, not tabs** — four switchers measured with the six view labels:

| Switcher | Phone | 768 | 1024 (sidebar open) | ≥ 1280 |
|---|---|---|---|---|
| `st.tabs` | **2 of 6 labels hidden** behind a scroll chevron | 1 row | 1 row | 1 row |
| **`st.pills`** | **2 rows, all visible** | 1 row | 1 row | 1 row |
| `st.segmented_control` | 2 rows | 1 row | 2 rows | 1 row |
| `st.radio(horizontal=True)` | 3 rows | 2 rows | 3 rows | 2 rows |

Pills also make views **lazy by construction** — only the selected view's code runs, so
a PDF with 40 charts does not render them to show the Tables view — and binding the
key to the URL makes a view linkable.

**Behaviour, tested with `AppTest`:** clicking the selected pill off would leave no
view, so an `on_change` callback restores the last one (`_keep_view` in the
prototype) — deselect left the view on Tables with no exception; selecting Tables
rendered its dataframe and no chart; `?view=…` seeded the Document view.

**Values that came from measuring, not taste:**

- Count tiles at 150 px wrapped **3 + 1** at 1024 px; at 130 they hold one row of four
  from 574 px of content and go 2 × 2 on a phone.
- Cards at 440 px gave **one per row on a 1280 px laptop**; at 360 they missed two per
  row at 768 px by ~6 px (the `medium` gap); 344 gives 1-2-1-2-2-4-5 across the eight
  widths.
- 760 px keeps prose readable on wide displays; it is a maximum, so phones are
  unaffected.

**Accepted trade-offs:** at 1024 px the open sidebar leaves 574 px, so one card per row
(inherent to the 300 px sidebar, as in the old app); on 2560 px the card grid is
left-aligned with free space to its right, chosen over centring so cards stay aligned
with the text above them. No `st.columns` for content — they hold a fixed count and
collapse to one stack at 640 px, which the wrapping grid does better.

**When the UI is built:** re-measure with real content at the same eight widths
(chart crops and long table headers change widths), keep the 0-overflow check as a
CDP assertion in the screenshot recipe, and re-check `CARD_WIDTH` if `baseFontSize`
or the gap changes — the 768 px two-per-row margin is ~10 px.

## 16. Definition of done for v1 (1.0.0)

PR 9 merges only when **every** item holds; the PR description ticks each with its
evidence (a link, a results file, a test name).

- [ ] **Phase 0:** every spike S1–S11 has a verdict in `pdf-pipeline-spikes.md`, the
      user approved the report (PR 1), and PR 7 confirmed or amended S6/S7/S11.
- [ ] **Outputs:** `gdi convert` produces the full §3.5 tree for every public-corpus
      PDF, including `--schema` KVP and `chunks.jsonl`.
- [ ] **Library:** `convert`, `convert_many`, `ConvertOptions` and the §3.7 result types
      are public, typed and documented; `convert()` writes nothing.
- [ ] **Resume and storage:** every §3.8 and §3.12 behaviour has a passing test.
- [ ] **Safety and security:** every §3.10 and §3.13 rule has a passing test; S10's
      check, re-run on the release lock, finds no repo code executed and no `*.py` or
      `auto_map` in the models directory; all four model SHAs pinned and recorded in
      `run.json`.
- [ ] **Eval:** baseline committed; the release commit's run (median of three) meets
      every score within tolerance and every budget; adversarial PDF passes.
- [ ] **Memory:** peak ≤ 10 GB on every corpus PDF; flat across the 30+ page document.
- [ ] **Gate:** `GATE_FORCE=1 ./scripts/gate.sh` green and ≤ 15 s; CI green on
      `macos-26` with SHA-pinned actions.
- [ ] **Docs:** README and CLAUDE.md rewritten (§9), `SECURITY.md`, the README's
      measured numbers dated with versions; every README command run as written.
- [ ] **Licensing:** `license-files` entries all exist (tested); `SOURCES.md` complete;
      `THIRD_PARTY_NOTICES.md` present if any code was copied (§9.1).
- [ ] **Release mechanics:** `version = "1.0.0"` and `uv.lock` land together (§11);
      after merge, with the user's confirmation, the GitHub description (§9.2).

## 17. Risk register

The risks most able to derail v1, each with the earliest signal and the fallback the plan
already holds. **Rollback point for the whole rebuild: tag `v0.6.0`** — the last release
of the working text app; `git checkout v0.6.0` restores it intact if the rebuild stalls.

| Risk | Earliest signal | Fallback |
|---|---|---|
| Docling's MLX engine won't load a local conversion | S2 — **retired 2026-10-25**: it loads through `artifacts_path` (§3.2) | — |
| Docling silently drops a chart's CSV when the model writes a label containing a comma unquoted (`status=success`, `errors=[]`, raw response not kept) | S2 (2 of 5 Census charts); S6 found the quieter form, a comma in the **header** line, which pandas takes as the table's width, so the CSV is kept but its header is split across cells (GAO-07-631 Fig. 6) | capture the raw chart2csv response in our code and parse it leniently; S7 and PR 4 decide how; S6 counted a drop as a failure (none occurred on its corpus) |
| Memory isn't released between phases, or creeps across pages | S4 — **retired 2026-10-25**: release returns active memory to 0, per-page peak flat over 42 pages, 7.00 GB run peak (§3.1) | — |
| Model-repo code executes despite native transformers | S10 — **fired 2026-10-25**: mlx-vlm's `trust_remote_code=True` default ran GV's `configuration.py` on every load of the 8-bit folder, Docling's chart stage included. **Mitigated:** the repo's code is stripped from the converted folder, and `convert()` refuses a folder that has any (§3.13 rule 2) | if a future transformers drops native `granite4_vision`, the load fails loudly. Then pin a transformers version that has it, or vendor and review the config class — user decides |
| 8-bit Granite Vision degrades charts or KVP | S6 — **retired 2026-10-25**: chart values identical to bf16 on 10 charts, KVP output byte-identical on 3 pages (§2) | — |
| A chart summary contradicts its own CSV: both variants attached every percentage of the GAO-08-14 pie to the wrong slice while the CSV was right, and a number-only hallucination check scores that clean | S6 | §8.2 rule 4 pairs each number with the category the summary names; PR 4 decides whether the summary is shown beside a CSV that disagrees with it |
| Too slow to be useful on 16 GB | S8 — born-digital measured: ~6 s/page, ~10 pages/min sustained, no throttling over ~10 min; S11 | batch/scale tuning (S11); page-level resume (§3.14 rule 7, now in v1); fewer charts by default |
| On image-only pages granite-docling drops the page's text (`<logo></picture></doctag>`, 25 tokens, `status=success`) or emits hundreds of false pictures, and the false charts multiply Granite Vision's time (S8's stand-in: 441 pictures, 102 chart pairs, 3.4× slower). It reproduces from Docling's softer page render; pypdfium2's render of the same page converts in full | S8 (image-only stand-in) | confirm on the real scan (S5 and S8 scanned halves); then test a sharper render (`images_scale`, S11) or a different render path; a cap on pictures per page sent to Granite Vision — the classifier cannot filter them, because the stand-in's false charts were all typed by DocTags, which skips it (S9); §3.10's detection extended to near-empty pages |
| A Docling or mlx-vlm release breaks the API mid-build | a lock move fails the eval | §2.2: caps + per-package deliberate upgrades; stay on the locked version |
| `main` stays tooling-only for weeks after PR 3 | calendar | nothing ships until PR 9; `v0.6.0` remains installable; PR 3's README note says so |
| A sideways-printed or very wide table loops silently in granite-docling | S3 (seen: Table A-2 of the Census P60-282 report) | post-hoc loop/cap detection puts the page in `run.json` warnings (§3.10); `force_backend_text` does **not** recover it (S5: it empties the looped items and leaves the table absent); the corpus includes one such page (§4.1) |
| granite-docling silently mis-transcribes prose: a wrong number ("73" for "7.3"), an inserted phrase, invented paragraphs on a chart page, or a whole page lost to a loop, its charts with it (GAO-07-631 p. 24 carries Figure 7, so the cap costs 2 of the corpus's 13 charts — S6) | S5, S6 | cap/loop detection flags the lost page (§3.10); `--text-source pdf` for trusted born-digital PDFs (§3.2); PR 7 decides whether the eval scores prose numbers against the text-layer reference (§8.2) |
| The eval corpus can't be licensed cleanly | Task C | public-domain US federal sources + project-made PDFs only (§4.1) |

## Appendix A — kickoff prompt for the implementing session

Paste this as the first message of a new Claude Code session in this repository. It
covers Phase 0 only, and resumes it: the session reads the spike report to find what
is already done, so the same prompt starts Phase 0 and continues it after any session
that ended part-way. Each later phase begins from the PR table in §10.

```text
You are implementing docs/plans/pdf-pipeline.md. Read §0, §16 and §17 first, then the
rest. The plan was written in an earlier session you have no memory of; it is the
single source of truth, and every decision in it carries its reason — read the reason
before deciding a choice is safe to change.

CLAUDE.md describes the OLD Streamlit text app this plan replaces. Follow its tooling
rules (uv.lock never hand-edited, Conventional Commits, the gate, the hooks); ignore
its app-specific rules (the smoke test, MODEL_NAME, language_directive, the theme).

Do Phase 0 only (§4): spikes S1–S11 and Task C (§4.1). Nothing else.

Phase 0 may already be under way. Before anything else:
- Switch to branch docs/pdf-pipeline-spikes if it exists (local or on origin); create
  it from main only if it does not.
- Read docs/plans/pdf-pipeline-spikes.md if it exists. Its summary table says which
  spikes are done; do not re-run a spike marked Pass, and treat the amendments it
  records as already applied to the plan. Start with the first spike not yet run,
  running S1 and S3 before any other if either is still open.
- Check whether test/eval-corpus exists, to see how far Task C got.

- Spikes are throwaway scripts in your scratchpad, never in the repo.
- Re-check every version and API the plan cites against what is installed now; the
  plan was researched on 2026-10-18 and says "unverified" where it is. Run each spike
  on the latest versions inside §2.2's caps and record them — §2.2 explains why the
  researched versions are not floors.
- To test the lowest allowed versions, lock with `uv lock --resolution lowest-direct`
  and install with `uv sync --frozen` / `uv run --frozen`: a plain `uv sync` silently
  re-resolves to the latest (S1).
- After each spike, add its section to docs/plans/pdf-pipeline-spikes.md: exact
  versions, what you ran, the numbers, the verdict, and any amendment it forces.
- If ANY spike fails its pass criterion, STOP and report to me with the options before
  continuing — S1 and S3 block outright; S2, S4, S6, S7 and S10 change the design.
- score.py does not exist yet: score S6, S7 and S11 against eval/expected/ with a
  scratchpad script, as §4 "Scoring in Phase 0" describes.
- Task C: source the public corpus under §4.1's licensing rule and draft the expected
  outputs. Ask me for (a) the PDFs for the private tier and (b) the print-and-scan
  step, which needs my hands.
- Apply plan amendments to docs/plans/pdf-pipeline.md in the same branch.
- Work on branch docs/pdf-pipeline-spikes (PR 1 in §10); the corpus goes on
  test/eval-corpus (PR 2). Never commit to main. Conventional Commits. Commit after
  each spike, so a session that ends part-way leaves its work on the branch.
- Use the /astral skills for uv, ruff and ty.

Finish by summarizing each spike's verdict and every plan amendment, and ask me to
approve the spike report before any Phase 1 work starts.

This session: do S7 only, then commit, push and stop.
```

The last line limits one session to one spike. Change it to the next open spike
before each paste, or delete it to run the rest of Phase 0 in one session.

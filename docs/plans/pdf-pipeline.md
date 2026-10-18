# Plan: PDF document-intelligence pipeline (replaces the Streamlit text app)

Drafted 2026-10-18. Written to be executed in a **fresh session** with no memory of the
conversation that produced it — every decision below carries its reason and its evidence,
so the implementer can tell a load-bearing choice from a default.

**Status:** draft — open questions resolved; awaiting final additions. Nothing here has
been implemented. **To start implementing, paste Appendix A into a new session.**

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
  without Streamlit installed.
- No embeddings or vector store — v1 stops at `chunks.jsonl`.
- No output localization. **English only** — Granite Vision 4.1 accepts English
  instructions only, and granite-docling is English-primary (Japanese, Arabic and
  Chinese are "experimental" on its card).
- No Granite 4.2 text model. Summarization, topics, intent and sentiment are dropped.
- No Granite Vision table refinement (see §3.4).
- No non-PDF inputs (images, DOCX). Docling can read them; v1 does not test them.

## 2. Model lineup (decided)

| Stage | Model | Runtime | Weights | Runs on |
|---|---|---|---|---|
| Page → structure | `ibm-granite/granite-docling-258M-mlx` | Docling `VlmPipeline`, `GRANITEDOCLING_MLX` | 0.63 GB, bf16 (unquantized) | every page |
| Charts → CSV + summary | `ibm-granite/granite-vision-4.1-4b`, **converted locally to 8-bit MLX** | Docling chart-extraction enrichment, preset `granite_vision_v4_mlx`, `repo_id` overridden | ~4.65 GB | pictures Docling classifies as charts |
| Key-value extraction | the same 8-bit Granite Vision | **our code**, `mlx-vlm` directly | (same files) | pages the user selects |

**Why two models and not Granite Vision alone.** Granite Vision 4.1 is an *extraction*
model: its card reports ParseBench Mean **39.45** and Text Formatting **21.52** — weak
full-page parsing — and Docling ships it only for chart extraction, table structure and
KVP, never as a page converter (its `granite_vision` *page* preset is the older 3.x 2B
emitting Markdown). granite-docling emits **DocTags** — element types, bounding boxes,
reading order — which is what lets the pipeline find the charts to hand Granite Vision.
It also costs 0.63 GB to read every page, against ~4.65 GB for the 4B model.

**Why 8-bit Granite Vision.** IBM publishes **no** MLX build of 4.1 (bf16 safetensors
8.0 GB, and GGUF). On 16 GB the Metal working set is ~10.7 GB
(`max_recommended_working_set_size` ≈ ⅔ of RAM); bf16 GV + granite-docling is ~8.6 GB
before KV cache, page rasters, Docling's own models and the OS. 8-bit brings GV to
~4.65 GB. Convert it ourselves rather than use a community build: the four on the Hub
(`inferencerlabs/…-MLX-Q9`, `beaupi/…-oQ4`/`-oQ8`, `brainworkup/…-oQ8`) have 15–103
downloads and no published evaluation.

**Pin the revision Docling pins:** `dd48e97503de471803850df70843cf9eb5da8712`
(`docling/datamodel/stage_model_specs.py`, `CHART_EXTRACTION_GRANITE_VISION_V4`).

**Rejected:** `mlx-community/granite-4.0-3b-vision-*` (what IBM's own HF Space uses on
MLX — older; VAREX 85.5% vs 94.2% for 4.1); `granite-vision-3.2/3.3-2b` (superseded);
any 30B model (does not fit).

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
(~9.3 GB + granite-docling). The phases must therefore be **sequential with an explicit
release between them**, and Spike S4 must prove the release actually returns memory.
Fallback if it does not: drop Docling's chart enrichment and run chart extraction in our
own code against the single Phase-B model instance (crop via `generate_picture_images`).

### 3.2 Docling configuration (verify against the installed version)

Read from Docling `main` on 2026-10-18 (PyPI latest: **2.140.0**, 2026-10-16):

- `VlmPipelineOptions(vlm_options=vlm_model_specs.GRANITEDOCLING_MLX, generate_page_images=True, …)`
- `do_chart_extraction=True` (it auto-enables picture classification)
- `chart_extraction_options=ChartExtractionVlmEngineOptions.from_preset("granite_vision_v4_mlx", …)`
  with **chart2csv and chart2summary on** — summary defaults to **off**; chart2code stays off
- the MLX engine honours `EngineModelConfig.repo_id` (`mlx_engine.py` ~L134) — the hook
  for pointing it at the local 8-bit conversion. Whether it accepts a *local path* is
  **unverified** (Spike S2)
- `generate_picture_images=True` to save chart crops alongside their CSVs
- the preset's `min_engine_version="0.7.0"` for mlx-vlm

Use the explicit `_mlx` preset, not `granite_vision_v4` (auto-inline): the auto preset
silently falls back to transformers on CPU/MPS when mlx-vlm is missing or broken, which
on this machine means a second, bf16 copy of the model and a pipeline many times slower.

**Hybrid text mode.** `VlmPipelineOptions(force_backend_text=True)` takes text from the
PDF's own text layer and routes only images and tables through the VLM — the SDK docs
say it reduces hallucination on text-heavy pages. On scanned pages there is no text
layer. Spike S5 decides the default; expose it as `--text-source {vlm,pdf}` either way.

### 3.3 Key-value extraction (our code)

Docling *has* KVP extraction (`VlmExtractionPipelineOptions` with
`GRANITE_VISION_4_1_TRANSFORMERS` and `ExtractionPromptStyle.GRANITE_VISION`) but it is
**transformers-only** — on this Mac that is a bf16 copy on MPS, the memory problem §2
exists to avoid. So:

- Reuse Docling's **prompt formatting** for `ExtractionPromptStyle.GRANITE_VISION`
  (find it under `docling/models/extraction/`) rather than inventing one — it is IBM's
  integration of IBM's prompt format. If it is private API, copy it with attribution and
  a comment naming the source file and Docling version.
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
  kvp.json                 # only with --schema; {"pages": {...}, "merged": {...},
                           #   "conflicts": [...], "valid": bool, "raw": {...}}
  chunks.jsonl             # one chunk per line (§3.9)
  run.json                 # model ids + revisions, package versions, per-phase timings,
                           # peak memory (mx.get_peak_memory), page count, warnings,
                           # input SHA-256 and the options used (for §3.8 resume)
```

`run.json` is written **last**, and only it marks a PDF as done — a crash mid-export
leaves no `run.json`, so resume redoes that PDF instead of trusting a partial folder.

`run.json` exists because every quality question this project has ever had was answered
by knowing exactly which model, revision and library produced an output.

### 3.6 Package layout (proposal)

```text
src/granite_document_intelligence/
  __init__.py
  cli.py          # argparse; console script `gdi`
  config.py       # model ids, revision pin, env vars (GRANITE_VISION_MLX_PATH, HF_TOKEN)
  api.py          # the public surface (§3.7); cli.py is a thin wrapper over it
  convert.py      # Phase A: build + run the DocumentConverter, then release it
  kvp.py          # Phase B: prompt, generate, parse, validate
  chunk.py        # §3.9
  export.py       # writes §3.5 from a DoclingDocument
  batch.py        # §3.8: input expansion, done-detection, continue-on-error
  memory.py       # release helper + peak-memory reporting
```

`src/` layout so tests import the installed package, not the working directory. CLI:

```text
gdi convert INPUT [INPUT ...] -o OUT_DIR      # INPUT: a .pdf or a directory
    [--schema fields.json] [--pages 1,3-4|all]
    [--text-source vlm|pdf] [--no-charts] [--chart-code] [--no-chunks]
    [--chunk-tokens 512] [--recursive] [--force]
    [--max-pages 500] [--max-file-mb 200] [--quiet]
gdi convert-model           # one-time: produce the 8-bit Granite Vision (see Phase 2)
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
result.tables  # list[Table]: index, page_no, dataframe, html
result.kvp  # KvpResult | None: pages, merged, conflicts, valid, raw
result.chunks  # list[Chunk]: text, contextualized, headings, page_nos
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
- One `DocumentConverter` for the whole batch (models load once). Phase B per PDF after
  its Phase A — or, if S4 shows reloading Granite Vision per PDF is the dominant cost,
  all Phase A first, then all Phase B; record the choice.
- A failed PDF writes `run.json` with `"status": "error"` and the traceback, and the
  batch continues; exit status is non-zero if any PDF failed. A batch summary
  (done / skipped / failed, with paths) goes to stderr at the end.

### 3.9 Chunking

Docling's `HybridChunker` (heading hierarchy first, then token-count splits;
`merge_peers=True`). It needs a `BaseTokenizer`; use
`HuggingFaceTokenizer.from_pretrained(model_name="ibm-granite/granite-embedding-english-r2", max_tokens=chunk_tokens)`
so chunk sizes match the Granite embedding model a later stage would most likely use
(English-only pipeline, so not the multilingual one). **Unverified:** that the model's
tokenizer loads through this API — check in S1. Chunking needs Docling's
`feat-chunking` extra.

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
  preset uses 4096 per page). A page that **hits the cap** almost always means a
  degenerate repetition loop, not a long page — IBM's own Space ships a `repetition.py`
  for exactly this. Record such pages in `run.json` `warnings` with their page number;
  the eval harness counts them. Docling's own repetition handling, if any, is checked in
  S3.
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
  **≤ 10 GB**. This comes from the Metal working set (~10.7 GB on 16 GB), not from a
  measurement, so it does not move. If S4 exceeds it, the design changes (the §3.1
  fallback) — the budget is not raised.
- **Time — set from S8, not guessed:** after S8, record in `eval/budgets.toml` the
  measured median seconds/page for Phase A (born-digital and scanned separately),
  seconds per chart for chart extraction, seconds per KVP page, and the cold-start
  model-load time, each **× 1.25** headroom. Initial values are whatever S8 measures;
  the plan does not presuppose them.
- **Degeneration count:** pages that hit `max_tokens` (§3.10) — budget **0** on the
  public corpus.
- **Enforcement:** `score.py` reports each metric against its budget and fails the run
  if any is exceeded, exactly as a quality drop fails it. **Raising a time budget needs
  a committed `eval/results/` file showing why** (a new Docling version that is
  slower but more accurate, say) — the same discipline as the baseline scores.
- **README** states the measured numbers ("about N s/page on an M2 Pro 16 GB") with the
  date and versions, not the budgets.

## 4. Phase 0 — spikes (before any production code)

Each spike is a throwaway script in the scratchpad, not the repo. **Stop and report**
if one fails; several change the design.

| # | Question | Pass criterion |
|---|---|---|
| S1 | Do `docling>=2.140` (with the `vlm` and chunking extras), `mlx-vlm>=0.7.8` and their `transformers`/`mlx` resolve together on Python 3.12? Does the `granite-embedding-english-r2` tokenizer load via `HuggingFaceTokenizer`? | `uv lock` succeeds in a scratch project; record the resolved `transformers`, `mlx`, `torch` versions; the tokenizer chunks a sample document |
| S2 | Does Docling's MLX engine load a **local** 8-bit conversion via `repo_id`? | chart extraction runs from the local path with `HF_HUB_OFFLINE=1`; if only Hub ids work, decide between a private Hub repo and a monkeypatch |
| S3 | Does the 2025-09 granite-docling MLX conversion (mlx-vlm 0.3.3) load and convert pages under mlx-vlm 0.7.8? Does Docling detect or stop repetition loops, and can a page hitting `max_tokens` be detected from the result? | a 5-page PDF converts with sane Markdown and at least one table; record how a capped page shows up (§3.10) |
| S4 | Peak memory per phase, and does releasing the converter return GV's memory? | Phase A peak and post-release `mx.get_active_memory()` recorded; Phase B starts below ~5 GB active; whole run stays under ~10 GB on a 20–50 page PDF with charts |
| S5 | `force_backend_text` on vs off, on one born-digital and one scanned PDF | pick the default from a read of the output, not a metric alone |
| S6 | 8-bit vs bf16 Granite Vision quality on ~10 charts and one KVP form | CSVs match on values; KVP fields match; if 8-bit degrades, try `--q-bits 6`/bf16 and re-measure S4 |
| S7 | granite-docling's own `<chart>` → table vs Granite Vision `<chart2csv>` on the same charts | if granite-docling is as good, Granite Vision's chart role shrinks to summaries — record either way |
| S8 | Throughput | seconds/page for Phase A (born-digital and scanned separately), seconds/chart, seconds/KVP page and cold-start load time; these seed `eval/budgets.toml` (§3.11) and the README |

Spike results go in **`docs/plans/pdf-pipeline-spikes.md`** — one section per spike:
what was run (exact versions), the numbers, the verdict, and any amendment it forces on
this plan. Amendments are made to this plan in the same PR, so the plan stays the single
source of truth.

### 4.1 Task C — assemble the eval corpus (runs alongside the spikes)

The spikes need real PDFs, and S6/S7 are scored through §8.1, so the corpus is built in
Phase 0, not after. It is a sourcing-and-labelling task, not a decision — the criteria
below are the decision.

**Two tiers, one scorer:**

- **Public** — `eval/corpus/` + `eval/expected/`, committed. 4–6 PDFs covering, between
  them: bar, line and pie charts; tables (at least one spanning a page break if one can
  be found); a **scanned** multi-page document; a **form** for KVP; and one **30+ page**
  document for memory and throughput.
- **Private** — `eval/private/corpus/` + `eval/private/expected/`, **gitignored** (add
  `eval/private/` to `.gitignore` in the same commit). For the user's own PDFs, which are
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

**Done when:** the public tier covers every bullet above, `SOURCES.md` has a licence for
every file, and `eval/expected/` holds hand-verified chart CSVs, table CSVs, a KVP schema
with its expected JSON, and the must-appear headings.

### 4.2 Converting Granite Vision

Conversion command to try in S2 (flag names unverified — check `--help`):

```bash
uv run python -m mlx_vlm.convert --hf-path ibm-granite/granite-vision-4.1-4b \
  --revision dd48e97503de471803850df70843cf9eb5da8712 \
  -q --q-bits 8 --mlx-path ~/.cache/granite-document-intelligence/granite-vision-4.1-4b-8bit
```

## 5. Phase 1 — tear down the text app (one commit, gate green)

Delete: `streamlit_app.py`, `.streamlit/`, `static/`, `tests/test_app_ui.py`,
`docs/images/`.

**Do not delete `tests/test_streamlit_app.py` wholesale.** Lines ~1337 to the end hold
the tooling suites — `TestCIWorkflow`, `TestReleaseWorkflow`, `TestHooksConfig` and the
session-scoped `stub_bin` fixture — which guard `ci.yml`, `release.sh`, `gate.sh` and
`.claude/settings.json` and carry over unchanged. Move them (with their imports and
fixtures) to `tests/test_tooling.py`, delete the rest, and confirm the moved suites'
test count matches before and after.

`pyproject.toml`: drop `streamlit`, `mlx-lm`; drop the `static/…` entries from
`license-files`; rewrite `description`. Run `uv lock`.

At this point the repo is tooling-only, the gate is green, and `version` is **not** yet
bumped (§11).

## 6. Phase 2 — library

Order: `config.py` → `convert.py` (Phase A) → `export.py` → `memory.py` → `chunk.py`
→ `kvp.py` (Phase B) → `api.py` → `batch.py` → `cli.py`. Each lands with its tests
(§8). Write `api.py`'s dataclasses **first** as stubs, so every module after
`convert.py` targets the public types rather than raw Docling objects. Notes:

- **Model acquisition.** `gdi convert-model` runs the S2-validated conversion into
  `~/.cache/granite-document-intelligence/` (override: `GRANITE_VISION_MLX_PATH`).
  `gdi convert` with a missing conversion fails with the exact command to run — never a
  silent fallback to bf16 or to the Hub.
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

- `test_tooling.py` — moved verbatim (§5).
- `test_export.py` — build small `DoclingDocument`s in code (docling-core, no models) and
  assert the §3.5 tree, filenames, CSV contents, and that a chart without
  `tabular_chart` produces no CSV rather than an empty one.
- `test_kvp.py` — prompt construction, the ported `parse_json_output` cases, schema
  validation pass/fail, `--pages` parsing, with `mlx_vlm.generate` mocked.
- `test_convert.py` — the `VlmPipelineOptions` actually built: `GRANITEDOCLING_MLX`,
  chart extraction on with the **`_mlx` preset**, summary on, code off, `repo_id` pointed
  at the local path. This is the silent-degradation guard: the auto preset or a dropped
  `repo_id` still produces output, just from the wrong model.
- `test_chunk.py` — chunks from a hand-built `DoclingDocument`: headings carried,
  page numbers present, every line valid JSON with the §3.9 keys (tokenizer mocked or
  a tiny local one — no download).
- `test_api.py` — `convert()` writes nothing to disk (run it in a read-only tmp dir);
  `save()` produces exactly the §3.5 tree; result types are the public dataclasses.
- `test_batch.py` — directory expansion (case-insensitive, sorted, `--recursive`,
  relative output paths, no collision for same-named files); resume skips only a
  matching successful `run.json`, and re-runs on a missing, failed, hash-mismatched or
  options-mismatched one; a folder with outputs but no `run.json` is re-run; `--force`;
  one failure doesn't stop the batch and makes the exit non-zero. Phase A/B mocked.
- `test_cli.py` — argument parsing, missing-model error message, exit codes, and that
  it delegates to `convert_many()` rather than re-implementing resume.
- `test_safety.py` — the §3.10 rules: a PDF over `--max-pages` / `--max-file-mb` is a
  recorded per-PDF error and the batch continues; a page reported as hitting
  `max_tokens` lands in `run.json` warnings; `chart-NNN.py` carries the untrusted
  header and nothing imports it; an output path that would resolve outside the output
  directory is refused; a symlink out of the input directory is skipped with a warning;
  `Image.MAX_IMAGE_PIXELS` is unchanged after a run.

**CI cost check:** `docling` pulls `torch`; measure `uv sync --locked` time on the
`macos-26` runner in the first PR and record it. The job's 15-minute timeout is the
constraint.

### 8.1 Quality eval harness (real models, run by hand — never in CI)

Unit tests prove the plumbing; only real inference shows whether a model, quantization
or dependency change made the output worse. This replaces "read 48 runs by eye" from
the old app with a scored check.

- **Corpus and expected outputs:** built in Phase 0 — see §4.1 for the two tiers
  (public, committed; private, gitignored), the licensing rule and the coverage list.
  `eval/expected/<pdf>/` holds hand-verified chart CSVs, table CSVs, a KVP schema +
  expected JSON, and the headings that must appear in `document.md`.
- **Scorer:** `eval/score.py` runs `convert_many()` over the corpus and reports per PDF:
  - charts: numeric cell match within a relative tolerance (default 1%), header match,
    and charts found vs expected
  - tables: cell-level exact match after whitespace normalization (TEDS if a library
    for it is cheap to add — decide in implementation)
  - KVP: per-field exact match, schema-valid rate
  - document: expected headings present, page count
  - run: wall time, seconds/page, seconds/chart, peak memory, pages that hit
    `max_tokens` — each checked against `eval/budgets.toml` (§3.11)
- **Output:** a Markdown table to stdout plus `eval/results/<date>-<git-sha>.json`, so
  two runs (bf16 vs 8-bit, old vs new Docling) diff mechanically. Results files are
  committed when they back a decision, as the old app's A/B figures were recorded in
  CLAUDE.md.
- **Pass/fail:** no fixed bar in v1 — the first run on the shipped configuration *is*
  the baseline, and a change fails if it drops any score below it. S6 (8-bit vs bf16)
  and S7 (granite-docling vs Granite Vision charts) should be run **through this
  harness** once it exists, and re-run if the spike-time conclusions are revisited.
- **Meaningfulness, not just parse rate:** the scorer must compare values, never only
  "did it parse" — the old app learned that a script check passes degenerate output
  (CLAUDE.md, 2026-08-12 design decision).

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
  `mlx`.
- **`README.md` — rewrite**: what it does, the 16 GB envelope and the S4/S8 numbers,
  `gdi convert-model` as a setup step, CLI usage, output layout, licenses (Granite
  models Apache-2.0, downloaded not vendored), and a **"Untrusted documents"** section
  covering §3.10's limits, the no-timeout limitation, KVP review and never running
  generated chart code.
- **README usage example** uses one PDF from the §8.1 corpus, so the documented
  command is one the eval actually exercises.
- **Python API** documented in the README with the §3.7 example.

## 10. Delivery — branches and pull requests

One PR per step, each off a fresh branch from `main`, each green in CI before merge;
`main` stays linear (rebase or squash — never a merge commit). Merging to `main` cuts
**no** release until PR 9, because `scripts/release.sh` fires only on an untagged
`version`, and the version is unchanged until then. Use `gh` for PR operations.

| PR | Branch | Contents | Commit type | Merge gate (besides CI) |
|---|---|---|---|---|
| 0 | `docs/pdf-pipeline-plan` | this plan | `docs:` | user approval |
| 1 | `docs/pdf-pipeline-spikes` | spike report (§4) + any plan amendments it forces; no production code | `docs:` | **user approves the spike report** — the design is frozen from here |
| 2 | `test/eval-corpus` | §4.1 public corpus, `SOURCES.md`, hand-written expected outputs, `eval/private/` in `.gitignore` | `test:` | every file has a licence in `SOURCES.md` |
| 3 | `feat/teardown` | §5: delete the text app, move the tooling suites, drop deps | `feat!:` | moved suites' test count unchanged |
| 4 | `feat/phase-a` | deps, `config`, `api` stubs, `convert`, `export`, `memory`, `gdi convert-model` | `feat:` | a hand run on one corpus PDF, numbers in the PR description |
| 5 | `feat/chunks-kvp` | `chunk`, `kvp` | `feat:` | hand run with a schema on the corpus form |
| 6 | `feat/cli-batch` | `api` complete, `batch`, `cli`, §3.10 limits | `feat:` | hand run on the whole public corpus |
| 7 | `test/eval-harness` | `eval/score.py`, `eval/budgets.toml` from S8, the first results file as **baseline** | `test:` | baseline committed; every budget met |
| 8 | `docs/rewrite` | `CLAUDE.md` and `README.md` rewrites (§9) | `docs:` | README commands run as written |
| 9 | `chore/release-1.0.0` | `version = "1.0.0"` + `uv lock`, **both files** | `chore:` | eval run on the release commit meets baseline and budgets |

Rules:

- **Each PR leaves `main` coherent.** After PR 3, `main` is tooling-only — acceptable
  because nothing is released; the README says so if PR 3 lands well before PR 8 (a
  one-line "being rewritten" note in PR 3).
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

## 12. Open questions

None blocking. Decided 2026-10-18:

- Names: `granite_document_intelligence` / `gdi` / `convert`, `convert_many`,
  `ConvertOptions` (§3.7)
- Eval corpus: a Phase 0 task with acceptance criteria, public + private tiers (§4.1)
- Chunk size: default 512, configurable, recorded for resume (§3.9)
- KVP defaults to **page 1** (§3.3); chart2code **off** behind `--chart-code`; a
  Streamlit UI is **v1.x roadmap**, not v1 (§1)

Deferred by design, revisited with evidence: the `--text-source` default (S5), Granite
Vision's chart role (S7), 6-bit/bf16 instead of 8-bit (S6), and the chunk-size default
(once an embedding model is chosen).

## 13. References (as of 2026-10-18)

- Docling 2.140.0 — `docling/datamodel/stage_model_specs.py` (GV v4 presets, revision
  pin), `docling/datamodel/pipeline_options.py` (`VlmPipelineOptions`,
  `do_chart_extraction`, `VlmExtractionPipelineOptions`,
  `GraniteVisionTableStructureOptions`), `docling/models/inference_engines/vlm/mlx_engine.py`
  (`repo_id` override), `docling/models/stages/chart_extraction/granite_vision.py`
  (where chart output lands on `PictureItem.meta`)
- mlx-vlm 0.7.8 (2026-10-13); Granite Vision 4.1 support: Blaizzy/mlx-vlm#1104
  (merged 2026-05-05), model type `granite4_vision`
- Model cards: `ibm-granite/granite-vision-4.1-4b`, `ibm-granite/granite-docling-258M`,
  `ibm-granite/granite-docling-258M-mlx`
- IBM's reference app: HF Space `ibm-granite/granite-vision-document-intelligence`
  (Gradio + ZeroGPU; hand-rolled crops and prompts — the approach this plan avoids by
  letting Docling orchestrate)

## Appendix A — kickoff prompt for the implementing session

Paste this as the first message of a new Claude Code session in this repository. It
starts Phase 0 only; each later phase begins from the PR table in §10.

```text
You are implementing docs/plans/pdf-pipeline.md. Read it in full, then CLAUDE.md.
The plan was written in an earlier session you have no memory of; it is the single
source of truth, and every decision in it carries its reason — read the reason before
deciding a choice is safe to change.

Do Phase 0 only (§4): spikes S1–S8 and Task C (§4.1). Nothing else.

- Spikes are throwaway scripts in your scratchpad, never in the repo.
- Re-check every version and API the plan cites against what is installed now; the
  plan was researched on 2026-10-18 and says "unverified" where it is.
- After each spike, add its section to docs/plans/pdf-pipeline-spikes.md: exact
  versions, what you ran, the numbers, the verdict, and any amendment it forces.
- If a spike fails its pass criterion, STOP and report to me with the options before
  continuing — several spikes change the design (S2, S4, S6, S7).
- Task C: source the public corpus under §4.1's licensing rule and draft the expected
  outputs. Ask me for (a) the PDFs for the private tier and (b) the print-and-scan
  step, which needs my hands.
- Apply plan amendments to docs/plans/pdf-pipeline.md in the same branch.
- Work on branch docs/pdf-pipeline-spikes (PR 1 in §10); the corpus goes on
  test/eval-corpus (PR 2). Never commit to main. Conventional Commits.
- Use the /astral skills for uv, ruff and ty.

Finish by summarizing each spike's verdict and every plan amendment, and ask me to
approve the spike report before any Phase 1 work starts.
```

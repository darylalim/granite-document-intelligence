# Phase 0 spike report — PDF pipeline

Results of the spikes in [`pdf-pipeline.md`](pdf-pipeline.md) §4. Each section records
exact versions, what was run, the numbers, the verdict and any plan amendment it forced.
Spike scripts live in the session scratchpad, never in the repo.

| Spike | Verdict | Amends the plan |
|---|---|---|
| S1 | **Pass** | yes — extra names (§2.1, §3.9, §4); floors are verified versions (§2.2) |
| S2 | **Pass**, through `artifacts_path` | yes — every Docling model loads from one models directory; pins enforced at fetch; 4.92 GB; command form (§2, §3.2, §3.12, §3.13, §4, §4.2, §6, §8, §12, §17) |
| S3 | **Pass**, with findings | yes — `VlmConvertOptions` preset, 8192-token cap, loop and cap detection are our code (§2, §2.1, §3.2, §3.10, §3.13, §3.14, §4, §8, §17) |
| S4–S11 | not run | — |

## S1 — dependency resolution and the chunking tokenizer (2026-10-24)

**Question.** Do `docling>=2.140` with its VLM and chunking extras, `mlx-vlm>=0.7.8`
and their `transformers`/`mlx` resolve together on Python 3.12, with `transformers`
≥ 5.16? Does the `granite-embedding-english-r2` tokenizer load via
`HuggingFaceTokenizer`?

**Environment.** uv 0.12.5, CPython 3.12.3, macOS 27.0 on Apple Silicon.

**What was run.** A scratch project declaring §2.2's constraints:

```toml
dependencies = [
  "docling[vlm]>=2.140,<3",
  "mlx-vlm>=0.7.8,<0.8",
  "transformers>=5.16,<6",
  "jsonschema>=4.18",
]
```

locked twice — default (highest) resolution and `--resolution lowest-direct` — each
installed with `uv sync --frozen`, then: import `mlx_vlm`, `mlx.core` (Metal available)
and Docling's `VlmPipelineOptions`; convert a small Markdown document (two sections and a
table) with `DocumentConverter`; chunk it with
`HybridChunker(tokenizer=HuggingFaceTokenizer.from_pretrained(model_name="ibm-granite/granite-embedding-english-r2", max_tokens=N), merge_peers=True)`
at N = 512 and N = 64, printing each chunk's `contextualize()` token count and headings.

**Resolved versions.**

| Package | Highest | Floors (`lowest-direct`) |
|---|---|---|
| docling / docling-slim | 2.142.1 | 2.140.0 |
| docling-core | 2.104.0 | 2.104.0 |
| docling-ibm-models | 4.0.4 | — |
| docling-parse | 7.23.0 | — |
| mlx-vlm | 0.7.10 | 0.7.8 |
| mlx / mlx-metal | 0.32.4 | 0.32.4 |
| transformers | 5.20.2 | 5.16.0 |
| tokenizers | 0.23.3 | 0.23.3 |
| torch / torchvision | 2.14.1 / 0.29.1 | 2.14.1 |
| huggingface-hub | 1.33.0 | 1.33.0 |
| jsonschema | 4.26.0 | 4.26.0 |

152 packages (highest); the installed venv is **1.5 GB**.

**Results.**

- Both locks succeed, and both environments import cleanly with Metal available.
  `transformers` resolves to 5.20.2 and 5.16.0 — at or above the 5.16 floor in both.
  `docling-slim`'s darwin marker excludes `5.9.*`–`5.15.*`, exactly as §2.1 states.
- The tokenizer loads (as transformers 5's `TokenizersBackend`) in both environments.
  At 512 tokens the sample gives two chunks, one per section (59 and 71 tokens). At 64
  it gives three: the *Outlook* section splits from its table (45 + 33), every chunk
  carries its heading path (`['Quarterly report', 'Outlook']`), and the table is
  serialized as `Enterprise, Revenue = 120. …`. Output was identical on both stacks.

**Verdict: pass.** Nothing in the design changes. The floor column shows that the
researched versions *install*; it does not make them floors — §2.2 now sets each floor
at the version the models actually ran on (amended 2026-10-24), which will be the
latest inside the caps unless a later spike pins lower. For reference, `docling-core`
resolved to 2.104.0 in both locks (`docling-slim` 2.140 requires `>=2.98.0`, 2.142
`>=2.103.1`).

**Amendment — extra names (applied to §2.1, §3.9 and the S1 row of §4).** `docling` is
now a meta-package over `docling-slim`. The extras the plan named, `models-vlm-inline`
and `feat-chunking`, are `docling-slim`'s; on `docling` they do not exist, and
`uv lock` only **warns** (`does not have an extra named …`) and resolves without them —
the first lock in this spike did exactly that. The `docling` spelling is
**`docling[vlm]`** (= `docling-slim[models-vlm-inline]`), and chunking needs no extra,
because plain `docling` requires `docling-slim[standard]`, which brings
`docling-core[chunking]`. PR 4 must declare `docling[vlm]`, or depend on `docling-slim`
with its own extra names — the latter is what S9(a) explores.

**Also learned.**

- `docling-slim[standard]` requires `torch` and `torchvision` directly, not only through
  the VLM extra — so plain `docling` installs torch whatever S9 finds about the
  classifier; only a `docling-slim` dependency with a narrower extra set can avoid it.
  §2.1 now says so.
- **Checking floors with uv needs `--frozen`.** A `lowest-direct` lock is silently
  re-resolved to highest by a plain `uv sync` or `uv run` (they re-lock without the
  resolution mode), so the first floor run here actually tested 2.142.1. Lock with
  `uv lock --resolution lowest-direct`, then `uv sync --frozen` / `uv run --frozen`,
  and read the versions from the environment, not the command line.

## S3 — granite-docling MLX under the locked mlx-vlm; loops and capped pages (2026-10-24)

**Question.** Does the 2025-09 granite-docling MLX conversion (made with mlx-vlm 0.3.3)
load and convert pages under the latest mlx-vlm inside `<0.8`? Does Docling detect or
stop repetition loops, and can a page that hits `max_tokens` be detected from the
result?

**Environment.** Same scratch-project constraints as S1, highest resolution:
docling / docling-slim 2.142.1, docling-core 2.104.0, **mlx-vlm 0.7.10**, mlx 0.32.4,
transformers 5.20.2, tokenizers 0.23.3, torch 2.14.1, huggingface-hub 1.33.0,
pypdfium2 5.14.0. Model `ibm-granite/granite-docling-258M-mlx` at
**`e9939db25d2f296c8678d0491c4609a8c596c50a`** (last modified 2025-09-17; README:
"converted … using mlx-vlm version 0.3.3"; one 0.63 GB `model.safetensors`). Every run
with `HF_HUB_OFFLINE=1`, macOS 27.0, Apple Silicon.

**Test PDF.** Five pages cut from the US Census Bureau's *Income in the United States:
2023* (P60-282, public domain, 17 U.S.C. § 105;
`https://www2.census.gov/library/publications/2024/demo/p60-282.pdf`), born-digital,
all 612 × 792 pt with `/Rotate 0`: report pages 2 (a figure), 4 and 5 (prose that
*cites* tables), 15 (Table A-1, 8 numeric columns × 44 rows) and 16 (Table A-2, a
16-column table **printed sideways** on a portrait page). GAO and CBO refused scripted
downloads (HTTP 403); census.gov did not.

**What was run.** `DocumentConverter` with `VlmPipeline` and `generate_page_images=True`,
recording each page's `vlm_response` (`num_tokens`, `stop_reason`, generation time, the
raw DocTags), `conv_res.status` and `conv_res.errors`, and the exported Markdown:

1. **preset** — `VlmConvertOptions.from_preset("granite_docling", engine_options=MlxVlmEngineOptions())`
2. **legacy** — `vlm_model_specs.GRANITEDOCLING_MLX`, which is what the plan named
3. **cap** — the preset with `max_new_tokens=1024` on page 16 alone
4. **stopper** — the preset with Docling's `DocTagsRepetitionStopper` passed through
   the MLX override's `extra_config["custom_stopping_criteria"]`, at the default
   `lookback_tokens=200` and at 640
5. **upright** — page 16 with `/Rotate 90`, so that the table is the right way up
6. spies on the stopper and the engine inputs, and a post-hoc loop check over every
   saved DocTags file

**Results.**

| Page | Content | Tokens | Gen s | Ends `</doctag>` | Outcome |
|---|---|---|---|---|---|
| 1 | figure + notes | 307 | 3.1 | yes | picture + text, correct |
| 2 | prose | 1374 | 6.2 | yes | correct text and headings |
| 3 | prose | 1164 | 5.4 | yes | correct text and headings |
| 4 | Table A-1 | 2029 | 8.7 | yes | **48 × 8 table**, see below |
| 5 | Table A-2 (sideways) | 7371 | 31.7 | yes | **degenerate loop**, no table |

All five pages: 66.1 s wall, 1.61 GB `mx.get_peak_memory()`, about 230 tokens/s.
`status=success`, `errors=[]`. The legacy run produced **byte-identical** DocTags and
Markdown (59.1 s) plus a `DeprecationWarning`: *"Using legacy VLM options
(InlineVlmOptions/ApiVlmOptions) is deprecated. Please migrate to VlmConvertOptions
with preset…"*.

- **The conversion loads and works.** The 0.3.3-era weights run under mlx-vlm 0.7.10
  with no error or warning about the model. The prose pages give clean text with the
  report's headings as `##` sections.
- **Table A-1 is mostly right and partly wrong — the eval has work to do.** Of the
  rows matched against the PDF's text layer, every one has its first seven values
  exact. But the table has eight numeric columns and the model emitted seven: the last
  (the percent change's margin of error) is dropped everywhere. In one row (*Family
  households*) the model also skipped the percent change `*3.6` and wrote the dropped
  margin of error `1.41` in its place, a silently wrong value. The two header rows are
  misaligned by one column. Docling normalized `–0.2` (en dash) to `-0.2`.
- **Page 5 is a silent loop.** The DocTags are the table title followed by **348**
  `<text><loc_43><loc_…><loc_49><loc_…>Table</text>` blocks, 338 of them with
  duplicate boxes. The model then *closed* the document with `</doctag>` 821 tokens
  short of the cap. Docling reports success, and the table's content is simply absent.
  Docling's `VlmPipeline` has no orientation handling (only the Tesseract OCR stage
  references orientation), and nothing in the PDF marks the page as turned.
- **Turning the page upright does not rescue it.** With `/Rotate 90` the model starts
  a real `<otsl>` table, then degenerates into thousands of `<ecel>` cells and finally
  `1>d1>d1>d…`. It hits the cap at 8193 tokens (42,464 characters, no `</doctag>`) and
  exports a **0 × 0 table**, still with `status=success`. A 16-column dense table is
  beyond granite-docling-258M whichever way up it is. That is a quality limit for S5
  (`force_backend_text`) and the eval to quantify, not something S3 can fix.

**How a capped page shows up.** With `max_new_tokens=1024`: `num_tokens=1025` (the
MLX engine counts one token past the cap), the text does **not** end in `</doctag>`,
`stop_reason` is `unspecified`, `conv_res.status` is `success` and `conv_res.errors`
is empty. **Docling reports nothing.** The reason is in the source:
`VlmPipeline._determine_status` turns `VlmStopReason.LENGTH` into a page error, but no
MLX path ever produces `LENGTH`. The legacy `mlx_model.py` hard-codes `UNSPECIFIED`,
and the preset path's `mlx_engine.py` emits `"unspecified"`, `"stop_string"` or
`"custom_criteria"`. `vlm_convert_model.py` maps only exact `VlmStopReason` values,
so the last two become `UNSPECIFIED` too. **Detection is therefore our code:** a page is
capped when `num_tokens >= max_new_tokens` (true at 1025 vs 1024) or when its text
does not end with `</doctag>`. Either signal alone caught every capped page here, and
the uncapped pages showed neither.

**Does Docling detect or stop loops? No — and on MLX it cannot.** Docling ships a
`DocTagsRepetitionStopper` but wires it into no page-conversion preset (only the code
and formula stage uses `TailRepetitionStopper`). Passed in by hand, it reached the
engine (the runtime `extra_generation_config` carried it) and its `should_stop` was
called **7370 times** on page 5 without firing. Two independent causes, both verified:

1. **The window is characters, not tokens.** The engine slices
   `output_text[-lookback_tokens:]`, so the default 200 becomes 200 characters, which
   holds fewer than three complete 53-character loop blocks. On the saved output a
   200-character window never trips; 300 or more trips 290 characters into the page.
2. **The streamed text stalls, so no window ever sees the loop.** mlx-vlm's
   `BPEStreamingDetokenizer` emits text only when a token *begins with a space*.
   DocTags loops contain no spaces, so `token.text` is empty from about the 40th token
   on. The window stayed at the same **114 characters** (the title) for every later
   call, and the remaining ~3,900 characters (on a 1,600-token run) arrived in the
   final flush. The rule is identical in mlx-vlm 0.3.3 (`add_token` in that wheel's
   `tokenizer_utils.py`), so it is not a regression. The same stall also blinds
   Docling's **stop strings** (`</doctag>`) mid-generation; the model's own end token
   is what actually ends a normal page.

Even a fixed in-loop stopper would not have caught the upright page's loop: its
`<ecel>` and `1>d` runs carry no location boxes, which `DocTagsRepetitionStopper`'s
pattern requires. The **post-hoc** check (the same stopper slid over the final text
in 640-character windows) flagged pages 5 and the cap-1024 run and passed pages 1–4;
the upright page was caught by the cap signal instead. **Both signals are needed.**

**Verdict: pass.** The pass criterion holds: the conversion loads under mlx-vlm 0.7.10,
the five-page PDF converts to sane Markdown with one real table, and the capped-page
signature is recorded. The other two answers are negative. Docling neither detects
nor stops loops on MLX, and does not flag capped pages. Both become our code, which is
an amendment, not a redesign.

**Amendments (applied to the plan).**

- **Use `VlmConvertOptions.from_preset("granite_docling", engine_options=MlxVlmEngineOptions())`,
  not `vlm_model_specs.GRANITEDOCLING_MLX`** (§2, §3.2, §4 S9, §8 `test_convert.py`).
  The legacy options are deprecated and slated for removal, and the two paths gave
  byte-identical output. The MLX repo comes from the preset's `engine_overrides`, and
  the revision pin (§3.13 rule 1) is set there too, via `EngineModelConfig.revision`.
- **The cap is 8192 tokens, not 4096** (§2.1, §3.10, §3.14). In docling 2.142.1
  both the legacy spec and the preset set `max_new_tokens=8192` (the plan's 4096 is
  stale or was never right). At ~230 tokens/s, a page
  that loops to the cap costs about **37 s**, which S8 should count.
- **Capped-page and loop detection are our code, post hoc, on the final text** (§3.10,
  §3.13, §8). A page is flagged in `run.json` `warnings` when `num_tokens >= max_new_tokens`
  **or** its DocTags do not end with `</doctag>` (capped), **or** a 640-character
  sliding `DocTagsRepetitionStopper` check trips (a loop that closed itself, like
  page 5). Do not rely on Docling's `stop_reason`, `conv_res.errors` or `status` for
  either. Do not count on an in-generation stopper while mlx-vlm's detokenizer
  withholds space-free text, so a looping page costs up to the full cap.
- **Pin granite-docling MLX at `e9939db25d2f296c8678d0491c4609a8c596c50a`** (§3.13
  rule 1). It is the only revision that exists and the one S3 ran.
- **Risk added (§17):** sideways-printed and very wide tables. Both loop silently on
  granite-docling, and the corpus should include one such page (§4.1).

**Also learned.**

- **census.gov serves scripted downloads; gao.gov and cbo.gov answer 403.** That
  matters for Task C. P60-282 itself is a strong corpus candidate: born-digital, with
  dense multi-page tables (Table A-2 runs 14 pages), figures and a page that loops.
- The exported Markdown lost no prose, but the *figure page's* chart arrives as
  `<!-- image -->`. Charts are Granite Vision's job (S2/S7), as designed.
- Docling prints `Exception ignored in: VlmConvertModel.__del__` at interpreter exit
  (harmless, after the result is complete). The new CLAUDE.md should mention it so
  nobody chases it.

## S2 — loading a local 8-bit Granite Vision through Docling's MLX engine (2026-10-25)

**Question.** Does Docling's MLX engine load a **local** 8-bit conversion of
`granite-vision-4.1-4b`? Pass: chart extraction runs from the local path with
`HF_HUB_OFFLINE=1`. If only Hub ids work, choose between a private Hub repo and a
monkeypatch.

**Environment.** Same scratch-project constraints and resolved versions as S1/S3:
docling / docling-slim 2.142.1, docling-core 2.104.0, **mlx-vlm 0.7.10**, mlx 0.32.4,
transformers 5.20.2, torch 2.14.1, huggingface-hub 1.33.0. Models: granite-docling MLX
at `e9939db…` (S3), `DocumentFigureClassifier-v2.5` at
**`f859dfbff5c9916cd996942d4b0db7fa25808220`** (its `main` on 2026-10-25, last modified
2026-04-01), Granite Vision source at Docling's pin `dd48e97…` (its `main` is
`37d591f…`, last modified 2026-07-13). macOS 27.0, 16 GB Apple Silicon.

**The conversion.**

```bash
uv run python -m mlx_vlm convert --hf-path ibm-granite/granite-vision-4.1-4b \
  --revision dd48e97503de471803850df70843cf9eb5da8712 \
  -q --q-bits 8 --mlx-path <dir>
```

- Every flag §4.2 named exists, but `python -m mlx_vlm.convert` is **deprecated**
  ("Use `mlx_vlm.convert …` or `python -m mlx_vlm convert …`"). No
  `--trust-remote-code` was passed and none was needed.
- 184 s wall including the 8.0 GB download, **3.8 GB peak RSS**. The output is
  **4.92 GB** in one `model.safetensors`, reported as **9.259 bits per weight**
  (q-bits 8, group 64), not the ~4.65 GB the plan estimated. The difference is the
  **vision tower, which mlx-vlm leaves in bf16** (0.86 GB); the language model
  (3.62 GB), `lm_head` and the layerwise projectors are all quantized.
- The output directory **carries the source repo's Python files**: `modeling.py`,
  `processing.py`, `configuration.py`, `downsampling.py`. They are not imported here,
  but S10's "no module from the HF cache in `sys.modules`" check must also cover the
  models directory, where these files now live.
- `mlx_vlm convert` also fetched the repo's `README.md` at `main` (a README-only
  `37d591f…` snapshot appeared in the HF cache beside `dd48e97…`). The weights came
  from the pinned revision.

**How Docling finds a model.** Read from the 2.142.1 source, and the same for every
engine (`mlx_engine.py`, and the image-classification engines through
`vlm/_utils.py::resolve_model_artifacts_path`):

- `artifacts_path` **unset** → `snapshot_download(repo_id, revision)`.
- `artifacts_path` **set** → load `<artifacts_path>/<repo_id with "/" → "--">`, and
  **ignore the revision**; a missing folder is a `FileNotFoundError` naming the
  expected path (verified).
- `artifacts_path` is a **pipeline** option (`VlmPipelineOptions.artifacts_path`), passed
  to every stage. Setting it for Granite Vision means granite-docling and the figure
  classifier must be in the same directory.

**What was run.** One probe script, three routes, on a 5-page PDF cut from Census
P60-282 (report pages 8, 9, 12, 15, 16: Figures 1–6 — two bar charts, three line
charts, and a table-and-bar hybrid). Every run used `HF_HUB_OFFLINE=1` **and** an
in-process socket guard that refuses and records every non-loopback `connect`. The
pipeline was the §3.2 configuration: the `granite_docling` preset and the
`granite_vision_v4_mlx` chart preset on `MlxVlmEngineOptions`, chart2csv and
chart2summary on, `generate_picture_images=True`, and every model's repo and revision
overridden. `mlx_vlm.load` was spied to record the path each model loaded from.

| Route | How Granite Vision is named | Result |
|---|---|---|
| **path** | `repo_id` = the absolute local path, no `artifacts_path` | **fails** before any I/O: `HFValidationError: Repo id must be in the form 'repo_name' or 'namespace/repo_name'` |
| **artifacts** | `artifacts_path=models/`, `repo_id="local/granite-vision-4.1-4b-8bit"`; `models/` holds `ibm-granite--granite-docling-258M-mlx`, `docling-project--DocumentFigureClassifier-v2.5` and `local--granite-vision-4.1-4b-8bit` | **works**: `status=success`, 0 network attempts, `mlx_vlm.load` called only with paths under `models/` |
| **patch** | `MlxVlmEngine.download_models` patched for one repo id | not run, because the public route works |

**Artifacts-route numbers (5 pages).** 189.4 s wall and **7.32 GB
`mx.get_peak_memory()`**, with granite-docling and Granite Vision both resident during
Phase A, as §3.1 expects. That number belongs to S4, which measures memory properly.

| Page | Figure | Classified | chart2csv | chart2summary |
|---|---|---|---|---|
| 1 | Fig. 1, bar chart, labels with commas | `bar_chart` 0.58 | **dropped**: CSV parse error | yes |
| 2 | Fig. 2, line chart, 5 series 1967–2023 | `line_chart` 0.99 | 58 × 6 | yes |
| 3 | Fig. 3, table and bar hybrid | `table` 0.53 | not a chart; not sent | — |
| 4 | Fig. 4, dot plot and bar chart, labels with commas | `bar_chart` 0.58 | **dropped**: CSV parse error | yes |
| 4 | Fig. 5, line chart, 4 series 1967–2023 | `line_chart` 0.99 | 58 × 5 | yes |
| 5 | Fig. 6, ratio and earnings, 1960–2023 | `line_chart` 0.95 | 66 × 4 | yes |

**Verdict: pass.** Docling's MLX engine loads the local 8-bit conversion through its
own public `artifacts_path` option, with no Hub repo and no patch. The local path
cannot be passed as `repo_id`. Neither fallback in the §17 risk row is needed.

**What it costs the design (amendments, applied to the plan).**

- **One models directory for every Docling model** (§2, §3.2, §3.12, §6). Because
  `artifacts_path` is pipeline-wide, `$GDI_CACHE_DIR/models/` holds granite-docling, the
  classifier and the 8-bit Granite Vision under Docling's `<ns>--<name>` folder names.
  `gdi convert-model` fetches and converts all three; nothing downloads on first use.
  `GRANITE_VISION_MLX_PATH` goes, since Docling fixes the folder name. The embedding
  tokenizer is not a Docling model and stays in the HF cache.
- **Pins are enforced at fetch, not at load** (§3.13 rule 1, §8 `test_convert.py`, S10).
  Under `artifacts_path` Docling ignores `revision`, so a pin in the preset proves
  nothing. The setup step downloads each model at its pinned SHA into its folder (real
  files via `snapshot_download(..., local_dir=…)`, not symlinks into the HF cache,
  which `hf cache delete` would break) and writes `models/manifest.json`: each repo's
  SHA, plus the conversion's source SHA, mlx-vlm version and q-bits. `convert()`
  checks the manifest against `config.py` before loading and refuses a mismatch with
  the command to fix it; `run.json` records the manifest.
- **Size is 4.92 GB, not ~4.65** (§2, §3.12, §5 `.gitignore` comment).
- **Command form** (§4.2): `python -m mlx_vlm convert`, flags verified.

**Found on the way: work for S6, S7 and the PRs, not S2 failures.**

- **Docling silently drops a chart's CSV when a label contains a comma.** The model
  wrote `Full-Time, Year-Round Workers,Male,61440,-1.6` unquoted. Docling's pandas
  parse failed ("Expected 4 fields in line 4, saw 5"), logged it, and left
  `tabular_chart` empty: `status=success`, `errors=[]`, and the raw response is **not
  kept** on the document. Two of the five charts lost their CSV this way. The summary
  survives. Added to §17. The fallback is to capture the raw chart2csv text ourselves
  and parse it leniently. S7 and PR 4 decide how; S6 must count these as failures,
  not as missing charts.
- **The line-chart values look poor.** Figure 5's 2023 values are 91.3 / 81.8 / 68.5 /
  52.9 million on the page; the CSV says 109 / 91 / 71 / 66. Figure 6 repeats one row
  (`60,45,25`) for most of its 66 years. Figure 4's dropped CSV had the wrong sex
  labels and `42210` for `$42,110`. Whether that is 8-bit quantization or Granite
  Vision's limit on dense, long time series is exactly what S6 (8-bit vs bf16) and S7
  measure. The bf16 source is in the HF cache for S6.
- The classifier's confidence on both bar charts is only 0.58, and a table with bars
  goes to `table`. That is S9(c) and eval territory.
- Docling prints `Exception ignored in: …__del__` at exit for both VLM stages
  (`VlmConvertModel` and `ChartExtractionVlmEngineModel`); harmless, as S3 found.

# Phase 0 spike report — PDF pipeline

Results of the spikes in [`pdf-pipeline.md`](pdf-pipeline.md) §4. Each section records
exact versions, what was run, the numbers, the verdict and any plan amendment it forced.
Spike scripts live in the session scratchpad, never in the repo.

| Spike | Verdict | Amends the plan |
|---|---|---|
| S1 | **Pass** | yes — extra names (§2.1, §3.9, §4); floors are verified versions (§2.2) |
| S2 | **Pass**, through `artifacts_path` | yes — every Docling model loads from one models directory; pins enforced at fetch; 4.92 GB; command form (§2, §3.2, §3.12, §3.13, §4, §4.2, §6, §8, §12, §17) |
| S3 | **Pass**, with findings | yes — `VlmConvertOptions` preset, 8192-token cap, loop and cap detection are our code (§2, §2.1, §3.2, §3.10, §3.13, §3.14, §4, §8, §17) |
| S4 | **Pass** | yes — `mx.set_cache_limit` 1 GiB; Phase B per PDF; working set 12.71 GB (§2, §3.1, §3.8, §3.11, §3.14, §12, §17) |
| S5 | **Pass**, born-digital half (scanned half pending the scan) | yes — default `vlm`; the flag is `VlmConvertOptions`'s; detection reads DocTags; S4's capped page is 24 (§3.2, §3.10, §8, §12, §17) |
| S6–S11 | not run | — |

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

## S4 — memory per phase, release between phases, flatness across pages (2026-10-25)

**Question.** What does each phase peak at, does releasing the converter return
Granite Vision's memory, and does memory stay flat across a long document (§3.14
rule 5)? Pass: Phase A peak and post-release `mx.get_active_memory()` recorded; Phase B
starts below ~5 GB active; the whole run stays under ~10 GB on a 20–50 page PDF with
charts; per-page peak shows no upward trend, with the `mx.set_cache_limit` value that
achieves it recorded.

**Environment.** The S2 scratch project and models directory, unchanged: docling /
docling-slim 2.142.1, **mlx-vlm 0.7.10**, mlx 0.32.4, transformers 5.20.2, torch
2.14.1; granite-docling `e9939db…`, the classifier `f859dfb…`, the 8-bit Granite Vision
converted from `dd48e97…`. macOS 27.0.1, M2 Pro, 16 GB. `HF_HUB_OFFLINE=1` and the S2
socket guard: 0 network attempts in every run. On this machine
`max_recommended_working_set_size` is **12.71 GB**, not the ~10.7 GB §2 assumed, and
MLX's default cache limit is **16.32 GB** — larger than the working set, so by default
the buffer cache is bounded by nothing.

**Test PDF.** GAO-07-631, *Defense Budget: Trends in Operation and Maintenance Costs
and Support Services Contracting* (2007-05-18): **42 pages**, born-digital, nine
figures (two line charts, seven bar charts), each sourced "GAO analysis of DOD (or
congressional) data", so no third-party figure. A US federal work, public domain
(17 U.S.C. § 105). gao.gov still answers scripted downloads with HTTP 403, but govinfo
mirrors its reports and does not:
`https://www.govinfo.gov/content/pkg/GAOREPORTS-GAO-07-631/pdf/GAOREPORTS-GAO-07-631.pdf`
(631,484 bytes, SHA-256 `51f6dca9…360790471f`). It is the candidate for Task C's
30+ page document, and S8 should measure throughput on the same file.

**What was run.** One probe script, the §3.2 configuration (granite-docling preset,
`granite_vision_v4_mlx` chart preset with chart2csv and chart2summary,
`generate_page_images` and `generate_picture_images` on, `artifacts_path` = the models
directory), three times over the whole PDF — with MLX's default cache limit,
`mx.set_cache_limit(0)` and `mx.set_cache_limit(1 GiB)`:

1. **Phase A** — `DocumentConverter.convert`, with `mlx_vlm.load` and
   `mlx_vlm.stream_generate` wrapped so every generation (one per page, two per chart)
   records active, cache and peak memory (`mx.reset_peak_memory()` around each), tokens
   and seconds. The wrapper has to report from a `finally`: Docling stops consuming the
   stream at `</doctag>`, so code after the loop never runs for page generations.
2. **Release** — `del` the converter and the result (keeping the `DoclingDocument`),
   `gc.collect()`, `mx.clear_cache()`, then check weakrefs to every model
   `mlx_vlm.load` returned.
3. **Phase B** — load the 8-bit Granite Vision again with `mlx_vlm.load` and run one
   greedy KVP-style generation on page 1 (a four-field schema), as §3.3's code will.

**Results** (memory is `mx` memory in GB; per-generation figures are min–max over the run):

| | default limit | limit 0 | limit 1 GiB |
|---|---|---|---|
| Phase A wall, 42 pages + 9 charts | 255 s | 278 s | 247 s |
| Active after both loads | 5.56 | 5.56 | 5.56 |
| Page peak (42 granite-docling generations) | 6.535–6.539 | 6.506 | 6.535–6.539 |
| Chart peak (18 Granite Vision generations) | ≤ 7.00 | ≤ 7.00 | ≤ 7.00 |
| Cache between generations | 0.06–0.31 | 0 | 0.06–0.31 |
| **Phase A peak** | **7.00** | **7.00** | **7.00** |
| Active after release | **0.00** (no model alive) | 0.00 | 0.00 |
| Active after Phase B load | 4.93 | 4.93 | 4.93 |
| Phase B peak | 6.52 | 6.52 | 6.52 |
| **Whole-run peak** | **7.00** | **7.00** | **7.00** |
| Per-page peak trend (least squares on page index, controlling for tokens) | −0.01 MB/page | 0.00 MB/page | +0.01 MB/page |

- **Phase A's memory is set by the weights, not the document.** Active memory sat at
  exactly 5.559 GB from the second load to the last generation, so nothing
  accumulated. The per-page peak did not move with tokens either: page 25 generated 8193
  tokens and peaked where a 200-token page did. granite-docling's KV cache is small
  beside its vision encoding, and Granite Vision's chart generations (≤ 384 tokens here)
  set the run's peak at 7.00 GB, the same figure as the 3-page smoke test and S2's
  5-page run (7.32 GB there, with page images at a different size).
- **Docling loads Granite Vision when the pipeline is built**, before granite-docling
  and before any chart is found. So 4.93 GB is resident throughout Phase A even on a
  PDF with no charts.
- **The release is complete with plain Python.** Dropping the converter, `gc.collect()`
  and `mx.clear_cache()` freed every model (weakrefs dead) and took active memory to
  0.00 GB. `MlxVlmEngine.cleanup()` was not needed, and no Docling-level cache held a
  model. torch's MPS allocation for the classifier was 16 MB and went to 0 after
  `torch.mps.empty_cache()`.
- **Reloads are cheap on a warm page cache:** ~2.7 s for both Phase A models and
  ~1.9 s for Phase B's Granite Vision. Cold loads after a reboot are S8's to measure.
- **Process RSS** (sampled at each event) peaked at 5.5 GB. It overlaps the MLX figures
  on unified memory, so the two are not added; §3.11's ceiling is stated in
  `mx.get_peak_memory()` and that is what the verdict uses.
- **Cache limit.** Under the default the cache never passed 0.31 GB, so nothing crept,
  but only because this document never asked for more; the 16.32 GB default bounds
  nothing. A limit of 0 kept the cache at 0 and lowered the page peak by 30 MB, but was
  the slowest run (278 s against 247–255 s; one run each, so that margin is indicative,
  not measured noise). A 1 GiB limit was as fast as the default and bounds the cache
  well inside the ceiling.

**Verdict: pass.** Phase A peaks at 7.00 GB, the release returns active memory to 0,
Phase B starts at 0 GB before its load and 4.93 GB after it, the whole run peaks at
7.00 GB (3 GB under the 10 GB ceiling), and the per-page peak is flat across 42
pages. The §3.1 fallback (chart extraction in our own code) is not needed.

**What it costs the design (amendments, applied to the plan).**

- **`mx.set_cache_limit(1 << 30)`** (1 GiB) at startup (§3.14 rule 5, §12).
- **Phase B runs per PDF** (§3.8, §12). Interleaving needs the converter released before
  each Phase B, since both Granite Vision copies plus granite-docling would hold
  ~10.5 GB, so a batch with `--schema` reloads all three models per PDF: ~4.6 s warm,
  ~2% of this PDF's Phase A. Without `--schema` there is no Phase B and one converter
  serves the whole batch. All-A-then-all-B stays the documented alternative if S8 finds
  cold loads or many small PDFs make the reloads matter.
- **The working set is 12.71 GB on this machine** (§2, §3.11), not ~10.7 GB. The 10 GB
  ceiling stays where it is — §3.11 says a measurement does not move it — and now sits
  2.7 GB under the working set instead of 0.7 GB.
- §17's memory-release risk is retired.

**Found on the way: for S5, S8 and the eval, not S4 failures.**

- **Page 25 hit the 8192-token cap in all three runs** (8193 tokens, 36–40 s,
  `status=success`). It is Figure 8: two bar charts side by side under one caption.
  Docling kept only six text items for the page, and both charts were still classified
  and given CSVs and summaries. The DocTags were not saved, so what looped is unknown;
  §3.10's cap detection would flag it. A second capped-page shape after S3's sideways
  table, and one more reason the corpus needs this PDF.
  **Corrected by S5:** the capped page is **24**, a prose page whose 203 words are lost
  entirely, not Figure 8. "Page 25" was the 25th granite-docling generation, and
  generation order is not page order.
- All nine figures were classified as charts (`line_chart` / `bar_chart`, eight at
  ≥ 0.99 and one at 0.83) and all nine kept their CSV, unlike S2's comma-labelled Census
  charts. Logos (5) and a signature (1) were not sent to Granite Vision.
- The Phase B check returned correct fields from the cover:
  `{"report_number": "GAO-07-631", "title": "Trends in Operation and Maintenance Costs
  and Support Services Contracting", "date": "May 2007", "addressee": "Congressional
  Committees"}` (50 tokens, 5.3 s). That is a single smoke check, not S6.
- Phase A took ~6 s per page including charts (247–278 s for 42 pages). That is a
  sanity point for S8, not its measurement.

## S5 — `force_backend_text` on vs off: born-digital half (2026-10-25)

**Question.** Should Docling take text from granite-docling's own output (`vlm`) or from
the PDF's text layer (`pdf`)? Pass: pick the default from a read of the output, not
from a metric alone, on one born-digital and one scanned PDF. **Only the born-digital
half was run.** The scanned half waits for Task C's print-and-scan step (§4.1), which
needs the user's hands.

**Environment.** The S4 scratch project and models directory, unchanged: docling /
docling-slim 2.142.1, docling-core 2.104.0, docling-parse 7.23.0, **mlx-vlm 0.7.10**,
mlx 0.32.4, transformers 5.20.2, torch 2.14.1, pypdfium2 5.14.0; granite-docling
`e9939db…`. Every run used `HF_HUB_OFFLINE=1` and the S2 socket guard: 0 network
attempts.

**Test PDFs.** Both are born-digital US federal works and so public domain:

- **GAO-07-631**, all 42 pages (S4's file, SHA-256 `51f6dca9…360790471f`).
- **S3's five Census P60-282 pages**: report pages 2, 4, 5, 15 and 16, cut from the
  current `p60-282.pdf` (SHA-256 `e6cf145c…2eabba1cbf`). They include the prose pages,
  Table A-1 and the sideways Table A-2.

**Read the code first.** Docling 2.142.1 has **two** `force_backend_text` fields, and on
the preset path only one of them does anything. `VlmPipeline._initialize_vlm_models`
reads `VlmConvertOptions.force_backend_text`. `VlmPipelineOptions.force_backend_text`,
the field §3.2 named, is read only on the deprecated legacy path. The flag does not
change generation either: every page still goes through granite-docling in full. Then,
in `_finalize_page_output`, the text of each `TextItem` is replaced by
`get_text_in_rect(bbox)`, which joins (with a space) every docling-parse text **line**
whose area lies more than half inside the box the VLM predicted. `TextItem` covers
paragraphs, headings, captions, footnotes and list items. Tables are a different item
type, so they are never touched.

**What was run.** One probe script (§3.2's configuration, with chart extraction and
picture classification off because the flag touches only text) in three modes:

- `off` — neither flag set.
- `convert` — `VlmConvertOptions.force_backend_text=True`.
- `pipeline` — `VlmPipelineOptions.force_backend_text=True`.

Each run saved the `DoclingDocument`, the Markdown and every page's raw DocTags. Greedy
decoding makes the DocTags identical across modes, so text items pair one-to-one, and
each pair was diffed and checked against the page's text layer (pypdfium2).

**Results.**

| | Census (5 pages) | GAO-07-631 (42 pages) |
|---|---|---|
| DocTags, off vs `convert` vs `pipeline` | byte-identical | byte-identical (off vs `convert`) |
| `pipeline` mode Markdown vs off | **byte-identical**: the flag is ignored | — |
| Text items | 396 | 301 |
| unchanged by `convert` | 26 | 237 |
| formatting only (hyphens, footnote markers, quotes, dashes, bullets) | 13 | 35 |
| emptied | 350 (348 of them the loop's `Table` items) | 12 |
| content differs | 7 | 17 |
| Tables | identical | identical |
| Wall time, off / `convert` | 69.8 s (cold) / 59.3 s | 172.2 s / 171.4 s |

Every change was then read and checked against the text layer:

- **The backend fixes real VLM transcription errors**, which are silent and include a
  wrong number:
  - Census: "a **73** percent increase" where the PDF says **7.3**; and "median income
    of non-Hispanic households 55 to 64", an insertion where the PDF says
    "householders 55 to 64".
  - GAO: GWO → GWOT, uniform → uniformed, resolicting → resoliciting.
  - GAO page 26 (Figure 9, a chart page): the VLM invented **two paragraphs** ("The
    number of people who were outsource workers … is 1,000,000", repeated). The
    backend leaves those boxes empty, because no text lies in them.
  - Smaller wins: two URLs the VLM dropped ("available at ."); plain footnote numbers
    where the VLM writes `$^{1}$` (18 footnotes).
- **The backend loses content wherever the VLM's box is too small**, and it does so
  silently. These are the "emptied" and most of the "content differs" rows:
  - Census page 4: two footnotes emptied and three truncated to their last lines.
    Table A-2's title cut to "Table A-2.".
  - GAO: a sentence with two figures dropped (page 6: "an increase of $76.1 billion,
    or 57 percent"). Figure source lines and notes emptied (pages 12, 13, 14, 18).
    Half a sentence and a list heading gone (page 31). The **whole opening paragraph**
    of Appendix I gone (page 40). The contact block gone (page 42).
  - In every case checked, the VLM had the full text, and the text layer confirms it.
    The VLM reads past its own box; the backend cannot.
- **The backend duplicates text when the PDF overprints it.** GAO page 5 draws every
  text line twice at the same coordinates (docling-parse returns two identical
  cells), so each of the page's three paragraphs comes back **twice**. Two appendix
  headings are doubled the same way, one with letters lost
  ("A GAO Contact and Staff cknowledgments"). The VLM reads the page image and sees
  each line once.
- **Line-break hyphens survive**: "per- cent", "differ- ent", "popula- tion" (37 on
  the five Census pages, 5 in GAO). docling-parse marks the break as `"- "`, which
  also appears where the PDF breaks a genuine compound ("in- house", "sole- source"),
  so naive de-hyphenation would turn those into "inhouse" and "solesource". Em dashes
  and curly quotes become `-` and straight quotes.
- **It does nothing for loops.** On the sideways Table A-2 (S3), the 348 `Table`
  items become empty strings, which **hides** the loop in the Markdown without
  recovering a single cell. The DocTags are unchanged, so §3.10's detection still sees
  the loop, but only because §3.10 reads the DocTags.
- **It costs nothing in time and saves nothing.** Generation is identical.

**Correction to S4.** The page that hits the 8192-token cap in GAO-07-631 is **PDF page
24** (report page 20, plain prose), not page 25. S4 took the 25th granite-docling
generation to be page 25, but generation order is not page order. S5 read the page
number off each page's own `vlm_response`. Page 24's DocTags open with the running
header and then loop on it ("GAO-07-631 Defense Budget - 2020 - 2021 - 2022 - …", then
", 20, 20, 20, …") until the cap. Page 25, Figure 8, converts sanely. **All 203 words
of page 24 are missing from the output in both modes**, with `status=success`. The
backend cannot recover them, because it only fills boxes the VLM predicted. So the
second capped-page shape is not "two charts side by side" but a prose page lost
whole, which makes §3.10's cap detection more important, not less.

**Not run: the scanned half.** By the code above, a page with no text layer has no
docling-parse cells, so under `pdf` **every text item on the page would come back
empty**, leaving tables only. Docling has no per-page fallback. This is a prediction
from the source; the scan will confirm or refute it.

**Verdict: pass for the born-digital half. The default is `vlm`.**

The two modes fail differently:

- **`vlm`'s errors are fewer and smaller**, but they are the dangerous kind: a
  plausible wrong number, an inserted phrase, an invented paragraph on a chart page.
- **`pdf`'s errors are more numerous and larger**: whole paragraphs, figures and
  footnotes dropped, text doubled, hyphen debris. It also wipes the text of any
  scanned page.

A default has to be safe on PDFs the user did not inspect first, and `pdf` is not.
`--text-source pdf` stays as the opt-in for born-digital PDFs whose text layer the
user trusts.

**What it costs the design (amendments, applied to the plan).**

- **§3.2:** the flag is `VlmConvertOptions.force_backend_text`, and the
  `VlmPipelineOptions` field is a silent no-op. The paragraph also wrongly said the
  flag "routes only images and tables through the VLM". It replaces text after a full
  generation, saves no time and does not touch tables. The default is `vlm`, and
  `pdf` is documented as born-digital only.
- **§3.10:** loop and cap detection must read the raw DocTags, never the exported
  document, because `pdf` mode empties the loop's text items.
- **§8:** `test_convert.py` pins `--text-source pdf` onto `VlmConvertOptions` (and
  `vlm` onto neither field). Setting the pipeline field is the mutation that still
  produces output.
- **§12:** `--text-source` default settled: `vlm`.
- **§17:** S5 answers the sideways-table row (it does not recover it). A new row
  covers silent VLM transcription errors in prose: a wrong number, invented text, a
  whole page lost to a loop.

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
| S8 | **Pass**, born-digital half (scanned half pending the scan; an image-only stand-in collapses) | yes — the page-level-resume trigger fired; S8's numbers recorded as the budgets' reference; output size per page; a new image-only risk (§3.8, §3.11, §3.12, §3.14, §12, §17) |
| S9 | **Pass** — (a) no, (b) the ONNX engine matches but torch is imported anyway, (c) no | yes — torch stays, the classifier stays on transformers; torch's cost corrected; DocTags labels skip the classifier (§0, §2.1, §3.2, §9.2, §12, §17) |
| S10 | **Fail, resolved by option 1**: Granite Vision's repo `configuration.py` ran on every load of the 8-bit folder, through mlx-vlm's `trust_remote_code=True` default; stripping the repo's code from the converted folder leaves none (tested). Revisions reach every loader | yes: the repo's code is stripped at conversion, `convert()` refuses a folder that has any, Phase B passes `trust_remote_code=False`, and the tokenizer is pinned to `47ea694…` (§3.6, §3.12, §3.13, §4.2, §6, §8, §12, §16, §17) |
| S6 | **Pass**: no measurable regression on 10 charts (69 cells) and 3 KVP pages (20 fields); identical chart values and byte-identical KVP output; bf16 alone peaks at 10.12 GB | yes: S6 settled; bf16 ruled out on memory too; multi-panel pairing needs chart boxes in the manifests; rule 4 pairs numbers with labels; the capped page loses charts; the mangled-header comma case; summaries contradicting their CSV (§2, §8.2, §12, §17) |
| S7 | **Pass**: granite-docling is not as good, so Granite Vision keeps the chart role. Its page DocTags carried no `<chart>` table on 16 pages; on the crop it matched 12 of 69 cells against Granite Vision's 37. Summaries and CSVs reach the chunks | yes: `images_scale` stays 1.0, since any other value corrupts every box; S11 sweeps `VlmConvertOptions.scale` and our own chart-crop scale (2× crops: 37 → 43 cells); §3.9's fallback chunk dropped (§3.9, §3.10, §3.11, §3.14, §4, §12, §17) |
| S11 | not run; unblocked, since Task C's expected outputs exist | — |
| Task C | **Public tier done except the scan** (branch `test/eval-corpus`, PR 2): 6 PDFs, 13 charts, 6 tables, 3 KVP cases; the scan and the private tier wait on the user | yes: §4.1 status and the scan's pages; the text layer can disagree with the page (§8.2 rule 2); en-dash minus, significance asterisks, row pairing (§8.2 rule 3); the expected-output format (§8.1) |

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
  `processing.py`, `configuration.py`, `downsampling.py`. S10's "no module from the HF
  cache in `sys.modules`" check must also cover the models directory, where these
  files now live. **Corrected by S10:** this note originally said they were "not
  imported here". They were. This run's Phase A imported `configuration.py` from the
  8-bit folder, and the compiled module it left in `~/.cache/huggingface/modules/` is
  what S10 found first.
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

## S8 — throughput: born-digital half (2026-10-25)

**Question.** Seconds per page for Phase A (born-digital and scanned separately),
seconds per chart, seconds per KVP page and cold-start load time; sustained pages/min
across the 30+ page document, first page to last (§3.14 rule 6); whether a typical PDF
takes more than ~5 minutes (the page-level-resume trigger, rule 7); a sanity reference
for PR 7's budgets (§3.11) and the README's first numbers. **Only the born-digital half
was measured.** The scanned half waits for Task C's print-and-scan step. An image-only
stand-in was run, and it found a failure rather than a rate (below).

**Environment.** A fresh S8 scratch project with the S4 constraints. `uv lock
--upgrade` on the day moved nothing: docling / docling-slim 2.142.1, docling-core
2.104.0, **mlx-vlm 0.7.10**, mlx 0.32.4, transformers 5.20.2, torch 2.14.1, pypdfium2
5.14.0. Models are the S2 models directory, unchanged. M2 Pro, 16 GB, macOS 27.0.1, on
AC power with the battery full. Every run used `HF_HUB_OFFLINE=1`, the S2 socket guard
and `mx.set_cache_limit(1 << 30)`, and made 0 network attempts. `pmset -g therm`
recorded no thermal or performance warning before or after any run.

**What was run.** One probe script, the §3.2 configuration (S4's, unchanged). It timed:

- the imports;
- `DocumentConverter.initialize_pipeline`, which does every Phase A model load;
- `convert()`, with each page's tokens, generation time and stop reason read from its
  own `vlm_response` (never from generation order — S5);
- every `mlx_vlm.stream_generate` call, timestamped, which gives charts and the
  sustained rate;
- Docling's own stage timings (`settings.debug.profile_pipeline_timings`);
- the release, a Phase B Granite Vision load and one KVP generation (S4's four-field
  prompt) on each of the first five pages;
- a full rebuild of the Phase A converter, which is the per-PDF reload §3.8 pays with
  `--schema`.

Runs: GAO-07-631 (42 pages, 9 charts) **twice back to back**, then GAO-07-535 (69
pages, born-digital, a US federal work), then the image-only stand-in. Cold start was
measured on the 3-page smoke PDF straight after `sudo purge`, with a warm run of the
same file for comparison.

**Results — born-digital.**

| | GAO-07-631 run 1 | run 2 | GAO-07-535 |
|---|---|---|---|
| Pages / charts | 42 / 9 | 42 / 9 | 69 / 6 |
| **Phase A wall** | **247.9 s** | **246.7 s** | **426.8 s (7.1 min)** |
| All-in s/page (pages/min) | 5.90 (10.2) | 5.87 (10.2) | 6.18 (9.7) |
| Page generation: sum / median / p90 | 170.2 / 3.49 / 4.01 s | 169.0 / 3.43 / 3.96 s | 319.8 / 2.77 / 4.70 s |
| Page generation per page, excluding the capped page | 3.27 s | 3.25 s | 4.18 s |
| Capped pages (8193 tokens) | p. 24, 36.2 s | p. 24, 36.3 s | p. 38, 36.4 s |
| Charts: total / per chart | 74.6 / 8.28 s | 74.4 / 8.27 s | 105.1 / 15.0 s |
| Chart CSV / summary, median | 2.1 / 5.4 s | 2.1 / 5.4 s | 6.8 / 9.0 s |
| Outside generation (render, classify, assemble) | 3.1 s | 3.3 s | 1.8 s |
| Pages/min, first half / second half of generation order | 10.2 / 10.1 | 10.2 / 10.2 | 9.2 / 10.3 |
| KVP per page, 5 pages (median) | 4.6–5.3 s (4.82) | 4.6–5.3 s (4.86) | 4.7–7.4 s (5.09) |

| Loads and start-up | warm page cache | after `sudo purge` |
|---|---|---|
| Import docling + mlx_vlm | 4.6–5.3 s | 5.2 s |
| Phase A build (Granite Vision + granite-docling + classifier) | 2.2–2.4 s | 2.8 s |
| Phase B Granite Vision load | 1.8–1.9 s | 1.9 s |
| Phase A rebuild after release | 2.5–2.6 s | — |
| **First run after `uv sync` into a fresh venv** | **61.7 s import** | — |

- **Sustained equals best case on this machine.** Pages/min is the same in both halves
  of both GAO-07-631 runs, and the two runs, about 9.5 minutes of continuous load,
  agree to within 0.5%. The cleanest throttling probe is the capped page: it generates
  the same 8193 tokens in every run, at 226.5, 225.9 and 225.3 tok/s. On the
  stand-in's 14-minute run, four capped pages went from 226.2 tok/s at minute 1 to
  215.2 at minute 13. That is a 5% slowdown, the only drift seen. Tokens per second on
  ordinary pages is a poor throttle signal: short pages pay a fixed image-encoding cost,
  so GAO-07-535's falling tok/s tracks its content, and its pages/min actually rose.
- **One capped page costs 36 s every time**, for output that is lost anyway (S5). That
  is 21% of GAO-07-631's page-generation time and 11% of GAO-07-535's. Both test PDFs
  have exactly one.
- **Charts vary 2× with the chart.** GAO-07-631's charts cost 8.3 s each and
  GAO-07-535's cost 15.0 s: longer CSVs (208–407 tokens against 35–78) and longer
  summaries. Budgets should be set per chart from a corpus median, not from one PDF.
- **Docling's own stage timings work** (`vlm_convert` and `doc_enrich`, counted in
  Docling's page batches: 11 for 42 pages, so 4 pages per batch). That answers part of
  S11's timing-hook question early. Batch size itself stays S11's.
- **Cold start is the install, not the disk.** After `sudo purge`, the import and the
  Phase A load were only ~1.2 s slower than warm, so re-reading ~5.5 GB of weights
  from the SSD costs about a second. The 62 s cost belongs to the first import after `uv sync`, which
  writes bytecode and has macOS scan every new native library. It is paid once per
  install, not per run. A true after-reboot cold start was not measured: `purge` empties
  the file cache but not everything a reboot does.
- **Output size per page** (§3.12 rule 5), re-saved in the §3.5 layout (`document.json`
  with referenced images, `document.md`, picture crops): **23 KB/page** for GAO-07-631
  and **61 KB/page** for GAO-07-535, whose JSON is larger. With `--save-page-images`
  (stored at scale 1.0) it is 160–187 KB/page. Tables, chunks and `run.json` were not
  measured, so treat these figures as a floor.

**Typical-PDF trigger (§3.14 rule 7): fired.** GAO-07-631 took 4.1 minutes, but
GAO-07-535 took **7.1 minutes**, and 8 of the 10 GAO candidates S4 collected run 59–216
pages. At ~6 s/page that is 6–22 minutes each. For the report-length documents this
corpus holds, a typical PDF exceeds 5 minutes.

**Phase B ordering (§3.8): per PDF stays.** A `--schema` batch pays a rebuild of 2.6 s
plus a Granite Vision load of 1.9 s, about 4.5 s per PDF. On a 42-page PDF that is 2%
of runtime. On a one-page form (~4 s of Phase A plus ~5 s of KVP) it is about a third.
All-A-then-all-B would save most of that, at the cost of holding every PDF's Phase A
result until the batch's Phase B. The cost does not dominate, so the S4 decision
stands. A batch of one-page forms is the case that would reopen it.

**Results — image-only stand-in (not the scanned measurement).** GAO-07-631 was
rasterized to an image-only PDF: every page at 200 DPI, no text layer (0 text-layer
characters). It is clean, square and noise-free. Phase A took **836.6 s (19.9 s/page)**,
3.4× the born-digital run, but that time measures a failure:

- granite-docling found **441 pictures** where the born-digital run found 15. Of these,
  337 were "logos" and 94 "line charts". Page 2 alone became 334 picture boxes (7,504
  tokens). The 94 false charts sent Granite Vision **102 chart pairs: 548 s**, two
  thirds of the run.
- **Pages lost their text.** Pages 4, 5 and 10 kept one item each. Their generation
  was `<doctag><picture>…<logo></picture></doctag>`, 24–25 tokens, `status=success`.
  Four pages hit the cap, against one born-digital.

Narrowed on four pages (PDF pages 2, 4, 5 and 10), granite-docling only:

- **It is not the raster format.** Grayscale JPEG, RGB JPEG, near-lossless RGB and
  300 DPI grayscale all collapsed the same way.
- **It is not the prompt.** Docling's prompt and image size are identical for both
  PDFs. Docling's own rendered page images, captured at `stream_generate` and fed
  straight to `mlx_vlm`, reproduce the collapse: 25 tokens against 690 for the
  born-digital render of the same page.
- **It is how the page is rendered.** The same raster page rendered by `pypdfium2` at
  scale 2.0 and fed directly converts in full: 2,669 characters with the GAO logo
  tagged and every paragraph present. Docling's render of that page is visibly softer:
  11.4% mid-gray pixels against 7.5% for pypdfium2's, and 4.6% for the born-digital
  page. The likeliest cause, not yet traced in the source, is that it resamples the
  embedded raster a second time. granite-docling is fragile to
  that difference. It tags the logo, then either ends the page or repeats picture
  boxes.

A real scan is noisier than this stand-in, so the risk applies to it, not only to
synthetic rasters. Not yet known: which Docling backend call does the softening, and
whether a sharper page image (a higher `images_scale`, or a different render path)
avoids it. That belongs with the scanned halves of S5 and S8, and with S11's
`images_scale` sweep. **Scanned throughput is not reported.** Until the collapse is
understood, any scanned rate would mostly measure the failure.

**Verdict: pass for the born-digital half.**

| Measure (born-digital, M2 Pro 16 GB, 2026-10-25) | Value |
|---|---|
| Phase A, all-in | **~6 s/page, ~10 pages/min sustained** (5.9–6.2 s) |
| Page generation, uncapped page | ~3.3–4.2 s/page |
| Capped page | 36 s |
| Chart (CSV + summary) | 8–15 s |
| KVP page | ~5 s |
| Model loads, warm or after `purge` | Phase A 2.2–2.8 s, Phase B 1.9 s |
| First run after install | +62 s, once |

The scanned half is open: blocked on the print-and-scan step, and now carrying a known
collapse to test against.

**What it costs the design (amendments, applied to the plan).**

- **§3.14 rule 7 and §12:** the page-level-resume trigger fired. It moves into v1 scope,
  in PR 6 (batch and resume), **subject to the user's approval of this report**.
- **§3.8:** S8's reload numbers are recorded. Per PDF stays, and a batch of one-page
  forms is named as the case that would reopen it.
- **§3.11:** S8's sanity reference is recorded beside the budgets. Charts vary 2× per
  PDF, so their budget is a corpus median. The capped page is a known 36 s, and a
  degeneration budget of 0 on the public corpus means GAO-07-631 p. 24 and GAO-07-535
  p. 38 must either be fixed or be named exceptions.
- **§3.12 rule 5:** output size per page measured: 23–61 KB/page by default and
  160–187 KB/page with page images, before tables and chunks.
- **§17:** a new row. On image-only pages, Docling's page render makes granite-docling
  drop a page's text or emit hundreds of false pictures, and those false charts multiply
  Granite Vision's time. Earliest signal: the S5 and S8 scanned halves. The "too slow"
  row now cites S8's numbers.

**Decided with the user (2026-10-25)**, after the report was written. Each decision is
applied to the plan:

- **Page-level resume:** in v1, kept small. Only granite-docling's per-page DocTags are
  cached, keyed on input, model SHAs and options. A feasibility check in PR 4 comes
  first: public API only, otherwise deferred again (§3.14 rule 7).
- **Capped pages:** named exceptions in `eval/budgets.toml`. GAO-07-631 stays in the
  corpus, and a retry at a different `images_scale` and stopping at loop detection are
  tried in S11 or PR 7 (§3.11).
- **Phase B ordering:** per PDF stays. PR 5 tries to share Docling's Granite Vision
  instance through public API to remove the reload. All-A-then-all-B is the fallback
  only for a forms-heavy private tier (§3.8).

## S9 — how far toward torch-free the stack can go (2026-10-25)

**Question.** (a) Does `docling-slim` with only `format-pdf`, `feat-chunking` and
`mlx-vlm` added directly, without `models-vlm-inline`, import and run `VlmPipeline`
with the `granite_docling` preset on the MLX engine and the MLX chart preset? (b) Does
the picture classifier run on `ONNXRUNTIME` without importing torch, and does it pick
the same charts? (c) Does granite-docling's DocTags already label picture types,
making the classifier redundant?

**Environment.** Two scratch projects, highest resolution, both on docling /
docling-slim **2.142.1**, docling-core 2.104.0, mlx-vlm 0.7.10, mlx 0.32.4,
transformers 5.20.2, tokenizers 0.23.3, onnxruntime **1.31.0**:

- **torch-free**: `docling-slim[format-pdf,feat-chunking,models-onnxruntime]>=2.140,<3`
  plus §2.2's `mlx-vlm`, `transformers` and `jsonschema` lines. 108 packages, a
  **989 MB** venv, no torch. `huggingface-hub` resolves to **2.3.0**, because only
  `docling-slim`'s `standard` and `models-local` extras cap it at `<2`. Every other
  spike ran on 1.33.0.
- **torch**: S8's constraints (`docling[vlm]`) plus `onnxruntime>=1.24.1,<2`. 163
  packages, **1.7 GB**, torch 2.14.1 / torchvision 0.29.1.

Models: the S2 models directory, unchanged. Classifier `DocumentFigureClassifier-v2.5`
at `f859dfb…`, whose snapshot already ships `model.onnx` beside `model.safetensors`, so
the ONNX engine needs no conversion and no new download. Every run used
`HF_HUB_OFFLINE=1` and the S2 socket guard, and made 0 network attempts. M2 Pro,
16 GB, macOS 27.0.1.

**What was run.**

1. The torch-free import of `VlmPipeline`.
2. The full §3.2 Phase A configuration in the torch-free project, with the classifier
   on ONNX, on the smoke PDF, GAO-07-631 (42 pages), GAO-07-535 (69 pages) and S2's
   five Census chart pages. Each was compared, picture by picture, with the torch run
   of the same PDF from S8 or S2. The result was then chunked with `HybridChunker`.
   It needed the import workaround described below.
3. The classifier alone, on the same **527 saved crops** under four engine
   configurations: torch on MPS (Docling's default), torch on CPU, ONNX CPU and ONNX
   CoreML + CPU. The crops are every embedded picture image from the S2, S4 and S8
   born-digital runs (86) and from S8's image-only stand-in (441). These are the
   crops the classifier itself reads: it calls `element.get_image()`, at
   `images_scale = 2` with no expansion. (**Corrected by S7:** the classifier's
   `images_scale = 2` is never used. A picture's embedded image wins, and it is cut at
   the pipeline's `images_scale`, 1.0, so these crops are 72 dpi.)
4. The classifier's peak RSS, per engine, in a fresh process each.

**(a) — no.** The pipeline cannot be imported without torch:

```text
docling/pipeline/vlm_pipeline.py:65    from …hf_transformers_model import …   (module level)
docling/models/vlm_pipeline_models/hf_transformers_model.py:36
                                       from …hf_stopping_criteria import …
docling/models/utils/hf_stopping_criteria.py:6
                                       import torch                          → ModuleNotFoundError
```

`vlm_pipeline.py` imports the transformers VLM backend at module level whatever the
engine, and that backend's stopping-criteria helper imports torch unconditionally. That
one line is the **only** blocker on this path. The probe served a stub `torch` to that
single importer, keyed on the importing module's name, so every other `import torch`
still saw torch absent. With it, the whole path ran torch-free on all four PDFs:
granite-docling and Granite Vision on MLX, the classifier on ONNX, `HybridChunker`
chunking. After each run `torch` was not in `sys.modules`, no `torch.*` submodule was
loaded, and the stub's attribute log was empty. A broader stub (a `torch` module in
`sys.modules`) does not work: transformers' `find_spec` probe and Docling's
`asr_model_specs` take it for a real install and fail on it. Both imports are
unchanged on Docling `main` as of 2026-10-25. A stub around an import is a monkeypatch
on Docling internals, which the plan does not ship, so torch stays in the lock.

**(b) — the engine works; "no torch at runtime" fails, for the reason in (a).** On ONNX
the classifier matches torch:

| Config, vs torch on MPS | Crops | Label differs | Chart pick differs | Max \|Δ confidence\| (same label) |
|---|---|---|---|---|
| torch on CPU | 527 | 0 | 0 | 0.00001 |
| ONNX CPU, torch env | 527 | 0 | 0 | 0.00001 |
| ONNX CoreML + CPU, torch env | 527 | 0 | 0 | 0.010 |
| ONNX CPU, **torch-free env** | 527 | 0 | 0 | **0.031** |

End to end, the torch-free runs matched the torch runs on every picture: the same boxes
(15, 61 and 6), the same labels, the same 9 / 6 / 5 chart picks and the same CSVs. The
Markdown of GAO-07-631 and GAO-07-535 was **byte-identical**.

Two things qualify that:

- **Docling's default ONNX configuration imports torch.** With `providers` left at
  `["CPUExecutionProvider"]`, `OnnxRuntimeImageClassificationEngine._resolve_providers`
  (`onnxruntime_engine.py:142`) calls `decide_device()`, and
  `utils/accelerator_utils.py:23` runs `import torch` unconditionally at the top of it
  (unchanged on `main`). Any other list skips the call. The probe passed the default
  twice, which ONNX Runtime deduplicates to plain CPU with a `Duplicate provider`
  warning. CoreML + CPU also builds, and shifts confidence by up to 0.010.
- **Without torch the preprocessing changes, slightly.** transformers 5 loads the
  torchvision-backed `ViTImageProcessor` when torch is installed and
  `ViTImageProcessorPil` when it is not. The resize differs, which accounts for the
  0.031. It was confirmed on identical crops, and in the pipeline it moved two uncertain
  logos on GAO-07-535 p. 29 (0.579 → 0.609). No label moved.

Speed and memory: the classifier is never the bottleneck. 441 crops took 3.15 s on
ONNX CPU, 3.9 s on CoreML, 5.4 s on torch MPS and 25.5 s on torch CPU, against ~6 s per
page for Phase A (S8). Peak RSS for the classifier process on GAO-07-535's 61 crops was
**837 MB** with torch on MPS and **829 MB** with ONNX in the torch env. That is no
saving, because Docling had already imported torch. It was **543 MB** with ONNX
torch-free. Importing Docling costs 339 MB with torch and 171 MB without. So torch
costs about **0.17 GB at import and 0.3 GB at peak**, which §2.1 had put at "tens of
MB". That is still small against the 10 GB ceiling, but the cost is paid whether or not
the classifier runs on torch.

**(c) — no.** granite-docling types *some* pictures in DocTags, and those skip the
classifier: `DocumentPictureClassifier.is_processable` only runs on pictures whose
`meta.classification` is still empty, and a DocTags label is stored as `created_by:
load_from_doctags`, confidence 1.0. On the earlier spikes' born-digital runs it typed a
minority of the charts:

| PDF | Charts picked | Typed by DocTags | Typed by the classifier |
|---|---|---|---|
| GAO-07-631 | 9 | 6 | 3 |
| GAO-07-535 | 6 | 0 | 6 |
| Census P60-282, S2's five chart pages | 5 | 0 | 5 |

With classification off (S5's run of GAO-07-631), only the six DocTags charts reach
Granite Vision, and three are lost. The classifier stays. One consequence is new: the
classifier **cannot veto** a DocTags label. In S8's image-only stand-in, 426 of 441
pictures, including all 94 false "line charts" that cost 548 s of Granite Vision, were
typed by DocTags, so the classifier never saw them. A cap on pictures per page, as S8's
§17 row proposes, is a fallback the classifier cannot provide.

**Verdict: pass.** Each question has a yes/no answer with evidence, and nothing in the
design breaks. (a) no; (b) the ONNX engine is equivalent, but in an installable stack
torch is imported anyway; (c) no.

**What it costs the design (amendments, applied to the plan).**

- **§2.1 decision:** torch stays in the lock, and the classifier stays on its
  transformers default in v1. The plan's rule (ONNX "only if no import of torch at
  runtime") cannot be met while `vlm_pipeline` imports torch. Switching engines in a
  torch env buys no memory, and only fractions of a second per PDF. The ONNX
  configuration is recorded as ready: a non-default `providers` list, and the PIL
  preprocessing's ≤ 0.031 confidence shift. Torch-free is reopened when Docling makes
  both imports lazy. §2.1's torch cost is corrected to ~0.17 GB at import and ~0.3 GB
  peak, and its "torch loads only when the classifier runs on it" is corrected to
  "Docling imports torch when `VlmPipeline` is imported".
- **§0, §9.2:** the reason the project is "not MLX-only" becomes "Docling imports torch;
  the picture classifier runs on it".
- **§3.2:** pictures that DocTags already typed skip the classifier, at confidence 1.0.
- **§12:** S9 settled.
- **§17:** the image-only row notes that the false charts are DocTags-labelled, so the
  classifier cannot filter them.

**Also learned.**

- **A `docling-slim` dependency changes `huggingface-hub`'s major**, from 1.33.0 to
  2.3.0, because the `<2` cap lives only in the extras that bring torch. If torch-free
  is ever reopened, that move is one of §2.2's "moving `huggingface-hub` requires the
  eval" cases, separate from the torch question.
- Docling's `asr_model_specs` already probes for torch with `try: import torch`, at
  import of `pipeline_options`, which is why that import succeeds torch-free. The
  pattern for an upstream fix exists in the codebase.

## S10 — model-repo code and revision overrides (2026-10-25)

**Question.** Does any load path execute model-repo code, and do revision overrides
reach every loader (§3.13 rules 1–2)? Pass: **no** module from the HF cache or the
models directory is imported by `mlx_vlm.convert`, a Phase A conversion or the
classifier. The three Docling models' SHAs match `models/manifest.json`, and the
tokenizer's revision is observed at its loader. If any repo code is imported, stop and
report.

**Environment.** A fresh scratch project with S8's constraints, highest resolution,
and versions identical to S8/S9: docling / docling-slim 2.142.1, docling-core 2.104.0,
mlx-vlm 0.7.10, mlx 0.32.4, transformers 5.20.2, tokenizers 0.23.3, torch 2.14.1,
huggingface-hub 1.33.0. Granite-docling MLX `e9939db…`, `DocumentFigureClassifier-v2.5`
`f859dfb…` (both still each repo's `main`), Granite Vision source `dd48e97…`,
`granite-embedding-english-r2` `47ea694…` (its `main`; the only snapshot cached).
M2 Pro, 16 GB, macOS 27.0.1.

**The detector.** A `sitecustomize` hook was loaded into every probe process, the
`mlx_vlm` CLI included. It watched four roots: the HF home (hub and modules cache), a
fresh scratch `HF_MODULES_CACHE` and both models directories. It recorded:

- every code object **executed** from a root, via the audit `exec` event, which fires
  for every module body an import runs, whether from source or from a cached `.pyc`
- every file opened under a root, which shows which snapshot or folder each loader
  read
- every transformers `resolve_trust_remote_code` decision, with its arguments and
  caller
- every `get_class_from_dynamic_module` call

At exit it also recorded any `transformers_modules*` entry, or any module under a root,
left in `sys.modules`. **Positive control:** loading GV's `AutoConfig` and
`AutoProcessor` with `trust_remote_code=True` was caught by all four signals. With
`None` or `False` it was caught by none.

**What was run.**

1. **Models directory, built the way §3.13 rule 1 says.** Granite-docling and the
   classifier were fetched at their SHAs with `snapshot_download(..., local_dir=…)`,
   giving real files with no symlinks. `models/manifest.json` was written from the
   fetch metadata.
2. **Conversion**, in two forms:
   - §4.2's CLI command, `HF_HUB_OFFLINE=1`, into the same directory
   - the Python API `mlx_vlm.convert.convert(...)`, with its default and with
     `trust_remote_code=False`
3. **Phase A**: S8's §3.2 converter from the new directory, on S2's five Census
   chart pages, offline and under the socket guard. This covers granite-docling, the
   classifier and the GV chart stage.
4. **Phase B's load** of GV, both as `mlx_vlm.load(path)` and as
   `mlx_vlm.load(path, trust_remote_code=False)`.
5. **The chunking tokenizer** via `HuggingFaceTokenizer.from_pretrained`, offline:
   pinned, unpinned, and with a SHA that is not cached.

**Results.**

| Load path | trust passed to transformers | Repo code executed |
|---|---|---|
| `python -m mlx_vlm convert` (§4.2) | False (CLI flag defaults off) | none |
| `convert()` Python API, default | **True** (`trust_remote_code: bool = True`) | **`configuration.py`**, from the `dd48e97` snapshot |
| `convert()` Python API, `trust_remote_code=False` | False | none |
| granite-docling, Docling MLX engine | no decision (repo has no `auto_map`) | none |
| figure classifier, Docling transformers engine | None, no remote code | none |
| **GV chart stage, Docling MLX engine (Phase A)** | **True** | **`configuration.py`**, from the models directory |
| GV, `mlx_vlm.load(path)` (Phase B) | **True** | the same class (already loaded) |
| GV, `mlx_vlm.load(path, trust_remote_code=False)` | False | none |
| tokenizer, pinned / unpinned | no decision (no `auto_map`) | none |

**Why Granite Vision runs its own code.** Nobody in the chain asks for it:

1. Docling's `MlxVlmEngine` calls `load(path, strict=…)` and has no way to pass
   `trust_remote_code`.
2. `mlx_vlm.load` calls `AutoProcessor.from_pretrained(path)`, which mlx-vlm replaces
   with its own patch (`mlx_vlm/models/base.py:671`). For every model type that
   registers an mlx-vlm processor, `granite4_vision` among them, the patch does
   `kwargs.setdefault("trust_remote_code", True)`.
3. mlx-vlm's `Granite4VisionProcessor` then calls `AutoTokenizer.from_pretrained` with
   True.
4. GV's `config.json` carries an `auto_map`. With True, transformers prefers the
   repo's class over its own native `Granite4VisionConfig`, copies `configuration.py`
   into the modules cache and imports it.

`processor_config.json` and `preprocessor_config.json` carry `auto_map` entries too,
one of them nested under `image_processor`. Both defaults are unchanged on mlx-vlm
`main` as of 2026-10-25.

The code that runs is the pinned `configuration.py` (1,849 bytes, a `LlavaNextConfig`
subclass that sets fields). It is benign at `dd48e97`. Rule 2 exists because it would
run whatever that file says: after a pin bump, or if anything edits the models
directory, which lives in the user's own cache.

**Revisions — this half passes.**

- **Docling models:** each folder's commit, from its `local_dir` fetch metadata,
  equals the pin in `manifest.json`. Each folder is byte-identical to the HF snapshot
  at that SHA, with no extra files (15 and 8 files).
- **Conversion:** every config, tokenizer and code file was read from `dd48e97`'s
  blobs. The weights are opened by native code the audit hook cannot see. The output
  is byte-identical to S2's conversion, and `dd48e97` is the only cached snapshot with
  safetensors. One leak: the model-card step reads `README.md` through `refs/main`
  (`37d591f`), ignoring `--revision`. That is metadata only, and it is why S2 saw a
  README-only `main` snapshot appear.
- **Tokenizer:** the revision reaches its loader. Pinned, it reads
  `trees/47ea694….json` and never `refs/main`. Unpinned, it reads `refs/main`. A SHA
  that is not cached fails offline. Nothing executes.

**Verdict: fail.** Every load of the converted Granite Vision folder that does not pass
`trust_remote_code=False` explicitly imports the repo's `configuration.py`. That
includes Docling's chart stage, which cannot pass it. Revision overrides do reach every
loader.

**A tested fix.** Strip the repo's code from the converted folder, which we write and
own. Delete the four `.py` files, and remove every `auto_map` key, nested ones included,
from `config.json`, `preprocessor_config.json` and `processor_config.json`. The
stripped folder was tested on the same five pages:

- No code executed in Phase A or in either Phase B load, and transformers was never
  asked to decide trust for GV.
- The modules cache stayed empty.
- `doc.md` and every chart's CSV and summary were **byte-identical** to the unstripped
  run.

With no code in the folder, mlx-vlm's `True` default has nothing to import. That holds
for any loader, without a monkeypatch.

**Options, as put to the user.**

1. **Strip at conversion (recommended).** `gdi convert-model`:
   - converts with `trust_remote_code=False`, whether through the CLI or by passing it
     explicitly to `convert()`, whose default is True
   - deletes the repo's `*.py` files and every `auto_map` key from the output
   - records both in `manifest.json`

   `convert()` refuses a models directory that contains any `*.py` file or `auto_map`.
   Our own Phase B code passes `trust_remote_code=False` as well. `test_convert.py`
   covers the refusal.
2. **Leave the files and block at runtime.** Docling exposes no way to pass the flag,
   so this needs a monkeypatch of mlx-vlm or Docling, which the plan does not ship.
3. **Accept pinned repo code.** Amend rule 2 to "only code from a pinned, reviewed
   revision runs". This is the weakest option: it trusts a file in a user-writable
   directory on every load.

Separately, whether to report mlx-vlm's two `True` defaults upstream is the user's
call.

**Decision (the user, 2026-10-25): option 1.** Amendments applied to the plan:

- **§3.13 rule 2** now states the mechanism and the fix. The repo's code is stripped
  from the converted folder, `convert()` refuses a folder that has any, Phase B passes
  `trust_remote_code=False` explicitly, and S10's re-run watches executed code and the
  modules cache in every process.
- **§3.13 rule 1:** the manifest records what was stripped, and the tokenizer's pin is
  named: `47ea694b257b703fee9253d75c2b1f2985180498`.
- **§3.6, §3.12, §6:** `gdi convert-model` converts with `trust_remote_code=False` and
  strips the output, and `gdi convert` refuses an unstripped folder.
- **§4.2:** the CLI is safe and the function's default is not, the output must be
  stripped, and the README is read from `main`.
- **§8:** `test_cli.py` checks the stripping on a fixture folder, and `test_convert.py`
  checks the refusal and the explicit False.
- **§12, §16, §17:** S10 is settled, the release checklist re-runs the check, and the
  risk row is marked as fired and mitigated.

Upstream reporting is not decided.

**Also learned.**

- **The models directory S2–S9 used is not the plan's layout.**
  `~/.cache/granite-document-intelligence/models/` holds **symlinks** into the HF
  cache for granite-docling and the classifier, the arrangement §3.13 rule 1 rejects.
  S10 built a fresh directory with real files. Results are unaffected: the folders
  are byte-identical to the snapshots.
- **The stale module is still in the HF modules cache.** The `configuration.py` that
  S2 caused to be imported remains under
  `~/.cache/huggingface/modules/transformers_modules/local_hyphen__hyphen_granite…/`.
  It is not used unless a load passes True again, and it is safe to delete.
- **A child process inherits an import hook.** Phase A starts a Python child process,
  probably multiprocessing's resource tracker. It loaded the detector too and, on its
  first run, overwrote the main report with an empty one, which made the run look
  clean while the modules cache said otherwise. Any `sys.modules` check that PR 4 or
  PR 7 automates should write per-process results, or should check the modules
  cache's contents as well.

## S6 — 8-bit vs bf16 Granite Vision on charts and KVP (2026-10-25)

**Question.** Does the 8-bit conversion degrade Granite Vision's chart CSVs or KVP
fields against bf16? Pass: CSVs match on values; KVP fields match. If 8-bit degrades,
try 6-bit or bf16 and re-measure S4.

**Environment.** A fresh S6 scratch project with S10's constraints plus `pypdfium2`.
A fresh lock at highest resolution moved nothing against S8–S10: docling / docling-slim
2.142.1, docling-core 2.104.0, mlx-vlm 0.7.10, mlx 0.32.4, transformers 5.20.2,
tokenizers 0.23.3, torch 2.14.1, huggingface-hub 1.33.0, pypdfium2 5.14.0, jsonschema
4.26.0. Models: the S10 models directory (real files, Granite Vision 8-bit stripped).
The bf16 reference was converted from the same pinned source `dd48e97…` with
`python -m mlx_vlm convert … --dtype bfloat16` (no `-q`), offline, in 10 s, to
**8.5 GB** in two shards, then stripped the same way. Apart from the weights and
`config.json`'s quantization key, the two folders are byte-identical: tokenizer,
processor configs, chat template. Every run used `HF_HUB_OFFLINE=1`, a socket guard
and `mx.set_cache_limit(1 << 30)`, and made 0 network attempts. M2 Pro, 16 GB,
macOS 27.0.1. Corpus: `test/eval-corpus` at `7565ae1`, every PDF's SHA-256 equal to its
manifest's.

**What was run.** The design holds Phase A fixed, so quantization is the only variable.

1. **Phase A once**, through the §3.2 converter with 8-bit Granite Vision, on
   GAO-07-631 (42 pages) and the GAO-08-14 excerpt (the pie). A spy on
   `MlxVlmEngine.predict_batch` saved every chart request's exact image (lossless PNG),
   prompt and generation inputs. A spy on Docling's chart handler recorded each raw
   response and the picture it belonged to. Docling keeps neither (S2).
2. **Replay** of those 20 requests (10 charts × `<chart2csv>` and `<chart2summary>`)
   through `mlx_vlm`, reproducing `MlxVlmEngine.predict_batch` line by line: Docling's
   `preprocess_image_batch`, mlx-vlm's `apply_chat_template(…, num_images=1)` and
   `stream_generate` with the recorded `max_tokens` 4096 and temperature 0.0. The model
   loads as Docling loads it (`strict=True`), plus `trust_remote_code=False`. Run once
   per variant. **Control:** the 8-bit replay reproduced all 20 of Docling's recorded
   responses **byte for byte**, so the replay is the pipeline.
3. **KVP**, §3.3's design, on each manifest's `kvp.pages`: the W-9, the GAO-07-631
   cover and the adversarial purchase order. The page is rendered at scale 2.0 (the
   scale `GRANITE_VISION_4_1_TRANSFORMERS` uses). The prompt is Docling's
   `_build_extraction_prompt` around the JSON Schema, serialized as Docling serializes
   a dict template (`json.dumps(indent=2)`). Generation is greedy, max 1024 tokens.
   The first JSON object is taken and validated with `jsonschema`.
4. **Scoring** with a scratchpad script applying §8.1's chart metrics and §8.2
   rule 3's normalization and pairing: same page, reading order; rows by first-column
   label; numbers within 1%. A response is parsed the way Docling's handler parses it,
   so a parse error counts as a dropped CSV and scores zero. A lenient parse that
   rejoins comma-split labels was scored beside it. Summaries got rule 4's
   unsupported-number check, and every one was also read.

**Results — charts.** 13 expected charts; 10 reached Granite Vision (below).

| Chart | Kind | Cells | 8-bit | bf16 | Same CSV text |
|---|---|---|---|---|---|
| GAO-07-631 p12, Fig. 1 | line (geometry) | 11 | 3 | 3 | yes |
| p13, Fig. 2 | bar | 6 | 6 | 6 | yes |
| p14, Fig. 3 | bar | 6 | 6 | 6 | yes |
| p15, Fig. 4 | grouped bar, broken axis | 6 | 6 | 6 | yes |
| p17, Fig. 5 | bar | 6 | 6 | 6 | yes |
| p18, Fig. 6 | line (geometry) | 12 | 0 | 0 | yes |
| p24, Fig. 7 (2 panels) | bar | 3 + 3 | not sent | not sent | — |
| p25, Fig. 8 panel 1 | bar | 3 | 3 | 3 | header only (`Value (in millions)` / `Value`) |
| p25, Fig. 8 panel 2 | bar | 3 | 3 | 3 | yes |
| p26, Fig. 9 (2 panels) | bar | 3 + 3 | 0 (one merged picture) | 0 | yes |
| GAO-08-14 p2, Fig. 3 | pie | 4 | 4 | 4 | yes |
| **Total** | | **69** | **37 (53.6%)** | **37 (53.6%)** | 9 of 10 |

- **Values are identical across variants on every chart sent.** The one textual
  difference is a header neither variant gets right (expected `Dollars in millions`).
  The lenient parse changed no score: none of these responses has the S2 comma drop.
- **Every miss is shared and comes from upstream of the quantization:**
  - **p24:** Figure 7 is never sent. PDF page 24 holds the letter's last prose *and*
    Figure 7, and it is the page that hits the 8192-token cap (S5, S8: identical in
    every run), so its pictures never exist. The cap costs **2 of 13 charts**, not
    only a page of prose.
  - **p26:** granite-docling boxes Figure 9's two panels as **one** picture (p25's
    two panels came back as two). Granite Vision then writes one transposed four-row
    table covering both panels, and nothing pairs with panel 2.
  - **p12 and p18, the two line charts read from geometry:** both variants round to
    the nearest ~5 (`130, 125, 120…` for `132, 128, 124…`), outside the 1% tolerance.
- **Headers:** 1 of 10 matched in either variant. The model writes generic headers
  (`Category`, `Value`) where the expected CSVs carry the printed axis title.
- **Figure 6's header has an unquoted comma** (`…without supplemental funds, transfers,
  and reprogramming`). It is *not* dropped: with `header=None`, pandas takes the first
  line's five fields as the width and pads the three-field rows, so Docling keeps a
  five-column table whose header is split across cells. That is a second, quieter form
  of S2's comma problem: mangled rather than dropped.

**Results — summaries.** Rule 4 counted 16 of 99 numbers unsupported for 8-bit and 12
of 111 for bf16. Reading every one: the extras are axis ranges (`0 to 250`), list
numbering (`1. 2. 3.`) and the merged p26 picture quoting panel 2's correct values.
Approximate readings of Figure 1 are the only real errors, three per variant: 8-bit
`180 / 200 / 220`, bf16 `120 / 200 / 220`, against 196, 195 and 210 (and 124). **Neither
variant invents a value.** Both summaries of the pie attach **every percentage to the
wrong slice** (`Maritime 60%`, `Motor carriers 30%`, `Rail 6%`, `Pipelines 4%`), while
the CSV beside them is right. Rule 4 scores that 0 unsupported, because each number
occurs somewhere in the chart.

**Results — KVP.** The raw output is **byte-identical** between the variants on all
three pages.

| Case | Fields | 8-bit | bf16 | Schema-valid | Shared miss |
|---|---|---|---|---|---|
| W-9 page 1 | 11 | 10 | 10 | both | `name`: `Alex O. Sample` for `Alex Q. Sample` |
| GAO-07-631 cover | 4 | 3 | 3 | both | `addressee`: `Congressional Committees`, dropping "Report to" |
| Adversarial PO (excluded from averages) | 5 | 4 | 4 | both | `order_date`: `03/14/2026` for the printed `2026-03-14` |

The adversarial output contains none of the four injected values, in either variant.

**Speed and memory (same inputs).**

| | 8-bit | bf16 |
|---|---|---|
| Load | 1.9 s | 3.1 s |
| Chart CSV, 10 charts / median | 22.0 s / 2.17 s (21.7 tok/s) | 30.8 s / 2.96 s (15.4 tok/s) |
| Chart summary, 10 charts | 57.9 s (35.4 tok/s) | 101.0 s (21.3 tok/s) |
| KVP per page | 5.6–8.3 s | 5.9–10.6 s |
| `mx.get_peak_memory()`, charts / KVP | 6.37 / 6.52 GB | 9.95 / **10.12 GB** |

Phase A with 8-bit peaked at 7.0 GB, as in S4. bf16 alone, with no granite-docling
beside it, already passes the 10 GB ceiling on a KVP page. In Phase A, where Docling
holds both models, it would add ~3.6 GB to S4's 7.0 GB.

**Verdict: pass.** On this corpus (10 charts, 69 cells; 3 KVP pages, 20 fields) 8-bit
shows **no measurable regression** against bf16: identical chart values, identical KVP
output, the same summary errors. It is also 1.4–1.7× faster per token and 3.6 GB
lighter. 6-bit is not tried, because nothing degraded. Per §8.2 rule 10 this is a
regression statement about this corpus, not a general ranking. PR 7 re-runs it through
`score.py`, which needs the bf16 source kept until then.

**Amendments (applied to the plan).**

- **§2:** "Why 8-bit" cites S6. bf16 is also ruled out by memory: 10.12 GB peak alone.
- **§12, §17:** S6 settled; the 8-bit-degrades risk retired.
- **§8.2 rule 3:** pairing a merged multi-panel picture needs boxes the manifests
  don't yet have. Rule 3 falls back to IoU when a page's counts differ, which p26 hits,
  but no `manifest.json` records a chart's bounding box. So Task C adds `bbox` to each
  chart, or PR 7 defines another fallback.
- **§8.2 rule 4:** the hallucination check also pairs each number with the category a
  summary names beside it. A number-only check passed a summary that swapped every slice.
- **§17:** the capped-page row now counts the charts lost with the text (GAO-07-631
  p. 24: Figure 7, 2 of 13 charts). The comma row adds the mangled-header form. A new
  row records summaries contradicting their own CSV.

**Found on the way: work for S7, Task C and PR 7, not S6 failures.**

- **The corpus manifest calls PDF page 24 "a prose page".** It carries Figure 7 too
  (`gao-07-631/manifest.json` `notes`, on `test/eval-corpus`). The note should say so.
- **The line-chart tolerance.** Granite Vision reads unlabelled line charts to about
  ±5 units, so at 1% the two geometry charts score 3/11 and 0/12 in both variants. S7
  (granite-docling's own chart tables) is the next measurement. Whether `budgets.toml`
  wants a looser tolerance for `values_from: geometry` is PR 7's call, made against a
  baseline per rule 1, not here.
- **Headers** are the weakest chart metric (1 of 10): generic `Category` / `Value`.
  S7 and PR 4 decide whether a header should come from the axis title Docling already
  has.
- **Docling's handler refs are page-local.** During enrichment every picture's
  `self_ref` is numbered within its page (`#/pictures/0`, `#/pictures/1`). Any code
  that maps a chart response back to the document has to pair by page and order, not
  by ref.

## S7 — granite-docling's chart tables vs Granite Vision's `<chart2csv>` (2026-10-25)

**Question.** Is granite-docling's own chart-to-table output as good as Granite
Vision's `<chart2csv>` on the same charts? If so, Granite Vision's chart role shrinks to
summaries. §3.9 adds a second question: do chart summaries reach the chunks?

**Environment.** A fresh S7 scratch project with S6's constraints. A fresh lock at
highest resolution moved nothing against S6: docling / docling-slim 2.142.1,
docling-core 2.104.0, mlx-vlm 0.7.10, mlx 0.32.4, transformers 5.20.2, tokenizers
0.23.3, torch 2.14.1, huggingface-hub 1.33.0, pypdfium2 5.14.0, pandas 3.1.0. Models:
the stripped models directory from S10 (granite-docling `e9939db…`, Granite Vision
8-bit, classifier `f859dfb…`), and the embedding tokenizer at `47ea694…`. Every run
used `HF_HUB_OFFLINE=1`, a socket guard and `mx.set_cache_limit(1 << 30)`, and made 0
network attempts. M2 Pro, 16 GB, macOS 27.0.1. Corpus: `test/eval-corpus` at
`44a5321`, every PDF's SHA-256 equal to its manifest's. That commit changes only a
manifest note against S6's `7565ae1`: it is S6's own finding that page 24 carries
Figure 7.

**granite-docling has two ways to produce a chart table, and both were tested.**

- **In the page DocTags.** docling-core turns a DocTags `<chart>` element holding OTSL
  into the picture's `tabular_chart`. The chart stage then **skips** `<chart2csv>` for
  that picture (`_needs_prompt` returns `tabular_chart is None`). So if granite-docling
  wrote chart tables while converting pages, Granite Vision would never be asked.
- **On the crop.** granite-docling's card lists "Convert chart to table." (short form
  `<chart>`). Docling has no preset for this: its chart stage parses only Granite
  Vision's output formats. Using it would mean writing our own code.

**What was run.**

1. **Page arm.** The §3.2 converter with chart extraction off, on GAO-07-631 pages
   12–26 and the whole GAO-08-14 excerpt. A spy kept every page's raw DocTags.
2. **Crop arm.** Every image S6's Phase A sent to `<chart2csv>` (10 charts), in two
   sizes:
   - Docling's own crop, S6's lossless PNG;
   - the same picture box rendered from the PDF with pypdfium2 at scale 2.0.

   Each went through granite-docling with both instructions (greedy, 8192 tokens,
   Docling's stop strings) and through Granite Vision 8-bit `<chart2csv>` (4096
   tokens). Both reproduce `MlxVlmEngine.predict_batch` as S6's replay did.
   **Control:** Granite Vision on Docling's crops reproduced S6's 10 responses **byte
   for byte**.
3. **Scoring.** S6's scorer is reused unchanged for normalization, label pairing, the 1%
   tolerance and transposition. Granite Vision output is parsed as Docling's handler
   parses it. granite-docling output is parsed by `docling_core`'s
   `parse_otsl_table_content`, the function Docling applies to a DocTags `<chart>`.
   Every output was also read.
4. **Two pipeline runs** to check what the 2× crop result means for configuration: one
   at `VlmPipelineOptions.images_scale=2.0`, and one with
   `generate_picture_images=False`.
5. **Chunks.** The GAO-08-14 excerpt converted with summaries on, saved as a
   `DoclingDocument`, and chunked by §3.9's `HybridChunker` (512 tokens,
   `merge_peers=True`).

**Results — page arm: no.** On 16 pages, granite-docling emitted **no `<chart>`
element**. Every chart came back as a `<picture>` holding the caption and sometimes a
type tag. That tag is wrong for the pie, which it typed `line_chart`. On this corpus,
then, the page DocTags never carry chart data, and every chart reaches Granite Vision.
That matches S6, where all 10 grids came from `<chart2csv>`.

**Results — crop arm.** 13 expected charts and 69 cells. The three charts that never
become pictures (Figure 7, lost with capped page 24; p26's second panel, merged into
the first) score 0 in every column.

| Chart | Kind | Cells | GV, Docling crop | GV, 2× crop | gd long, Docling | gd short, Docling | gd long, 2× | gd short, 2× |
|---|---|---|---|---|---|---|---|---|
| p12 Fig. 1 | line (geometry) | 11 | 3 | 4 | 1 | 1 | 0 (capped) | 0 |
| p13 Fig. 2 | bar | 6 | 6 | 6 | 3 | 3 | 1 | 0 |
| p14 Fig. 3 | bar | 6 | 6 | 6 | 2 | 1 | 0 | 1 |
| p15 Fig. 4 | grouped bar | 6 | 6 | 6 | 0 | 0 | 1 | 1 |
| p17 Fig. 5 | bar | 6 | 6 | 6 | 5 | 5 | 1 | 0 |
| p18 Fig. 6 | line (geometry) | 12 | 0 | 2 | 0 | 0 | 0 (capped) | 3 |
| p25 Fig. 8 ×2 | bar | 6 | 6 | 6 | 0 | 0 | 0 (both capped) | 0 |
| p26 Fig. 9 panel 1 | bar | 3 | 0 | 3 | 0 | 0 | 0 | 0 |
| GAO-08-14 pie | pie | 4 | 4 | 4 | 1 | 0 | 1 | 0 |
| **Total of 69** | | | **37 (53.6%)** | **43 (62.3%)** | 12 (17.4%) | 10 (14.5%) | 4 (5.8%) | 5 (7.2%) |
| Time, 10 charts | | | 22.2 s | 32.3 s | 13.8 s | 13.5 s | 154.9 s | 42.4 s |
| Peak memory | | | 6.38 GB | 6.68 GB | 1.61 GB | 1.61 GB | 1.61 GB | 1.61 GB |

Reading the granite-docling output shows failures of kind, not degree:

- **Line charts flatten.** p12 is `130.0` for every year from 1995 to 2004. p18 is
  `100.0` for every year.
- **Wrapped labels split, and the values slide with them.** "Military personnel"
  becomes `Military` | `Personnel`, so each value pairs with the wrong category
  (`Military,21`).
- **Labels are invented:** `Firefight and development`, `Earnings`, `DDO total`.
- **The sharper crop makes it loop.** At 2× the long instruction hit the 8192 cap on 4
  of 10 charts. A fifth run, with the short instruction, stopped at 6,912 tokens.

The best granite-docling case is p17, where every value is right, but laid out as one
transposed row. It is also not much cheaper: 13.8 s against 22.2 s for the same 10
charts.

**Verdict: pass — granite-docling is not as good, and Granite Vision keeps the chart
role.** On this corpus granite-docling's best configuration matched 12 of 69 cells
against Granite Vision's 37. Its page DocTags never carried a chart table, and its crop
output is structurally unreliable. Per §8.2 rule 10, this is a statement about this
corpus. PR 7 re-runs it through `score.py`.

**Results — chunks: yes.** The pie's summary *and* its CSV table both land in the chunk
that cites the picture (chunk 1 of 2, 455 tokens), after the caption. So §3.9's
fallback, a separate `"kind": "chart_summary"` chunk, is not needed. Two things travel
with them into the embedded text:
- the summary's wrong slice percentages (S6's §17 row);
- DocTags' wrong `Line chart` label, which the Markdown export prints too.

**Found on the way: `images_scale` is not a usable knob in the VLM pipeline.**

- **Docling crops charts at 72 dpi.** For example, a 226 × 222 pt chart box arrives at
  Granite Vision as a 227 × 222 px image. The chart stage and the classifier each
  declare `images_scale = 2.0`. But `prepare_element` takes a picture's **embedded**
  image first. That image is cut from the page image at the **pipeline's**
  `images_scale`, which defaults to 1.0. So the stages' own 2.0 is never used. S9's
  description of the classifier's crops as "at `images_scale = 2`" is corrected
  accordingly: they are at 1.0.
- **Raising the pipeline's `images_scale` corrupts every box.** `_doctags_page_document`
  maps DocTags' 0–500 grid onto `page.image`, which is rendered at `images_scale`. So
  provenance comes out in pixels of that image rather than PDF points. Picture crops
  then multiply by `images_scale` again.
  - Measured at 2.0: p12's box reads `l = 430.8` on a 612 pt page, twice its 1.0
    value.
  - Every chart crop was the wrong region of the page: prose from the lower right,
    padded with black.
  - The classifier re-typed every non-chart picture (five logos and the signature) as
    `table` and three charts as `page_thumbnail`, so 3 fewer charts were sent.
  - Granite Vision wrote a "CSV" of p15's prose, which Docling then dropped.

  At 1.0, pixels and points coincide, which is the only reason the default works. The
  same scaling is applied to every DocTags item (read from the code; measured on
  pictures). So any non-1.0 value would also mis-place every text box in the output.
- **No configuration produces a correct 2× crop.** With
  `generate_picture_images=False`, the crop falls back to the document's page image,
  also at `images_scale`: the crops were still 226 × 222.
- **What a 2× crop would buy.** Our own 2× crops raised Granite Vision from **37 to 43
  cells**:
  - p26's merged picture went from transposed and unpaired to a correct table. It
    holds both panels as columns, so all 6 values are right, but rule 3 credits panel 1
    only.
  - The two line charts read closer: 3 → 4 and 0 → 2 cells.
  - It costs 45% more time on CSV (32.3 s against 22.2 s) and 0.3 GB more peak.

**Amendments (applied to the plan).**

- **§3.14 rule 2:** rewritten. `VlmPipelineOptions.images_scale` stays at **1.0**.
  - The two resolutions are separate knobs: granite-docling's page input is
    `VlmConvertOptions.scale` (2.0, the preset's), and a chart crop's resolution is
    whatever **our code** renders.
  - Getting a 2× crop to Granite Vision means our own crop from the PDF. Whether to
    ship that is S11's measurement and PR 4's code.
- **§4, S11 row; §12:** S11 sweeps `VlmConvertOptions.scale` and our chart-crop scale,
  not `images_scale`.
- **§3.11:** the capped-page retry changes `VlmConvertOptions.scale`.
- **§3.10:** the rasterization the Pillow rule guards is `VlmConvertOptions.scale`'s.
- **§3.9:** settled, summaries and CSVs reach the chunks; the fallback chunk is dropped.
- **§12:** S7 settled.
- **§17:**
  - the image-only row's "sharper render" now names `VlmConvertOptions.scale`;
  - the comma row: S7 leaves the lenient parse to PR 4, since Granite Vision stays the
    source of every chart table;
  - a new row: a non-1.0 `images_scale` silently corrupts every box.

**Found on the way: work for PR 4, PR 7 and S11, not S7 failures.**

- **Headers.** The page DocTags carry each chart's caption, not its axis title, so
  Docling has no axis title to offer. Granite Vision's generic `Category` / `Value`
  stays the known weakness (1 of 10 at either crop size). PR 4 decides whether to
  leave headers as the model writes them.
- **DocTags types the pie as `line_chart`,** at confidence 1.0. Docling never shows
  that label to the classifier (S9), and it reaches the Markdown and the chunks.
  Routing is unaffected, since any chart type goes to Granite Vision. PR 4 decides
  whether to print the type.
- **Merged multi-panel pictures.** At 2× Granite Vision writes one column per panel.
  Rule 3 could pair columns with panels when a page has fewer pictures than expected
  charts. PR 7's call, alongside S6's `bbox` note.
- **The classifier is trained on 2× crops and gets 1×.** It still picked the right 9
  charts here. If S11 also feeds our own crop to the classifier, the sweep must
  re-check which pictures it picks, not only the CSV scores.

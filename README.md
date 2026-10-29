# Granite Document Intelligence

[![CI](https://github.com/darylalim/granite-document-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/darylalim/granite-document-intelligence/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/darylalim/granite-document-intelligence)](https://github.com/darylalim/granite-document-intelligence/releases/latest)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**Granite Document Intelligence** turns multi-page PDFs into structured output on your Mac. It runs IBM's Granite Docling and Granite Vision models locally with [MLX](https://github.com/ml-explore/mlx). It is a Python library with a command-line tool, `gdi`. For each PDF it writes:

- the whole document as **Markdown** and as lossless **DoclingDocument JSON**
- **charts** as CSV data, a one-paragraph summary and the cropped image
- **tables** as CSV and HTML
- **key-values** filled in against a JSON Schema you supply (optional)
- **RAG-ready chunks** that follow the headings and carry page numbers, as JSON Lines

> **Unreleased.** `main` holds the PDF pipeline, which has not been released yet. Version 1.0.0 will be its first release. The last release, [v0.6.0](https://github.com/darylalim/granite-document-intelligence/releases/tag/v0.6.0), is the earlier Streamlit text-analysis app, which this project replaces.

## Requirements

- An Apple Silicon (M-series) Mac with **16 GB** of unified memory. Everything was measured on a 16 GB MacBook Pro M2 Pro. The run peaks at about 6.7 GB of model memory and is designed to stay under 10 GB.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs Python 3.12 and every dependency.
- About **16 GB of free disk** for setup and about **7.5 GB** afterwards (see [Disk space](#disk-space)).

The models run on MLX, but the project is not MLX-only. Docling imports PyTorch, and its picture classifier runs on PyTorch, so `uv sync` installs it.

## Setup

```bash
uv sync
uv run gdi convert-model
```

`gdi convert-model` is a one-time step, and it took 3.3 minutes on a fast connection when it was measured on 2026-10-29, including the ~9 GB download. It downloads three models at pinned revisions: granite-docling, Docling's figure classifier and the Granite Vision 4.1 source. IBM publishes no MLX build of Granite Vision 4.1, so the step converts it to 8-bit MLX on your Mac and removes the Python code that ships in the model repository. Everything goes into `~/.cache/granite-document-intelligence/models/`; set `GDI_CACHE_DIR` to put it somewhere else. Afterwards, the 8 GB bf16 source can be deleted (see [Disk space](#disk-space)).

`gdi convert` refuses to start until this step has completed. It never falls back to downloading models.

The models are public, so a Hugging Face token is optional. Without one, the Hub applies lower rate limits and logs an "unauthenticated requests" warning. To use one, copy `.env.example` to `.env` and set `HF_TOKEN` to a token with **read** scope ([create one](https://huggingface.co/settings/tokens)).

## Usage

This example converts the PDF the project's quality evaluation runs on. It is GAO report GAO-07-631, 42 pages with nine charts, and it is public domain:

```bash
curl -L -o gao-07-631.pdf https://www.govinfo.gov/content/pkg/GAOREPORTS-GAO-07-631/pdf/GAOREPORTS-GAO-07-631.pdf
uv run gdi convert gao-07-631.pdf -o out
```

It writes `out/gao-07-631/` and took 4.5 minutes when it was measured. Its `run.json` warns that page 24 hit the token cap, a known loss on this PDF. Progress goes to stderr, and a summary of what was done, skipped and failed comes last.

To pull named fields out of a page, pass a JSON Schema. This one reads the report's cover (page 1, the default for `--pages`):

```bash
cat > cover.json <<'EOF'
{
  "type": "object",
  "properties": {
    "report_number": {"type": "string", "description": "The GAO report number, e.g. GAO-00-000"},
    "date": {"type": "string", "description": "The month and year printed on the cover"},
    "title": {"type": "string", "description": "The report's title"}
  },
  "required": ["report_number", "date", "title"]
}
EOF
uv run gdi convert gao-07-631.pdf -o out --schema cover.json --force
```

`--force` is needed here because the PDF was already converted. Without it, a PDF whose `run.json` records success for the same file and options is skipped. Changing an option such as `--schema` also converts it again.

```text
gdi convert INPUT [INPUT ...] -o OUT_DIR
```

| Option | What it does |
|---|---|
| `INPUT` | A PDF, or a directory of PDFs. Each PDF goes to `OUT_DIR/<its path relative to the directory>/`. |
| `--recursive` | Also convert PDFs in subdirectories. |
| `--schema FIELDS_JSON` | Fill the schema's fields from the selected pages into `kvp.json`. |
| `--pages 1,3-4\|all` | The pages to extract key-values from (default `1`). Needs `--schema`. |
| `--text-source pdf` | Take text from the PDF's own text layer instead of the model. Use it only for born-digital PDFs you trust. |
| `--no-charts`, `--no-chunks` | Skip chart extraction, or `chunks.jsonl`. |
| `--chunk-tokens N` | Chunk size in tokens, 64–8192 (default 512). |
| `--chart-code` | Also write each chart as Python. This is untrusted model output, so never run it. |
| `--save-page-images` | Also write a PNG of every page. |
| `--force` | Convert PDFs again even when they are already done. |
| `--max-pages N`, `--max-file-mb MB` | Refuse larger PDFs (defaults 500 pages and 200 MB). |
| `--quiet` | Print only warnings and the summary. |
| `--no-wait` | Exit instead of waiting when another `gdi` run is in progress. |

`gdi convert` exits with **0** when every PDF was converted or skipped, **1** when any PDF failed or the run could not start, **2** on a usage error and **130** on Ctrl-C. A failed PDF records its error in its `run.json`, and the batch continues.

Only one `gdi` run works at a time on a Mac, because two runs would each load the models and push 16 GB into swap. A second run waits for the first to finish. With `--no-wait`, it exits with 1 instead.

**Resume is per PDF.** A batch interrupted by Ctrl-C, a closed lid or a reboot keeps every PDF it finished and redoes the one it was working on, **from its first page**. Page-level resume is not possible through Docling's public API yet.

### Output

```text
out/<pdf-stem>/
  document.md
  document.json              # DoclingDocument, lossless; images referenced, not embedded
  tables/table-001.csv
  tables/table-001.html
  charts/chart-001.png       # the crop Granite Vision read
  charts/chart-001.csv       # absent when the model wrote no table; then:
  charts/chart-001.raw.txt   #   what it wrote instead
  charts/chart-001.summary.txt
  charts/chart-001.py        # only with --chart-code
  pictures/picture-001.png   # pictures that are not charts
  pages/page-001.png         # only with --save-page-images
  kvp.json                   # only with --schema
  chunks.jsonl
  run.json
```

**`run.json`** records how the output was made: the model revisions, package versions, timings for each phase and page, peak memory, the output's size and any warnings. It is written last, and it is what marks a PDF as done. Each PDF's folder is built beside the output and swapped into place in one step. So a re-run never leaves files from an earlier run behind, and an interrupted run leaves the previous output intact.

**Warnings worth reading:**
- A page that hit the model's 8192-token limit, or that repeated itself, is listed by page number. Such a page is usually lost or garbled.
- A chart whose summary pairs a number with the wrong category is flagged against the chart's own CSV.

**`kvp.json`** holds:
- the values read from each page, and the values merged across pages
- any conflicts between pages
- whether the result validates against your schema, and the validation errors if it does not
- the model's raw responses

### Python API

The CLI wraps a library, which can be used directly:

```python
from pathlib import Path

from granite_document_intelligence import ConvertOptions, convert

opts = ConvertOptions(schema=Path("cover.json"), pages="1")
result = convert(Path("gao-07-631.pdf"), options=opts)

for chart in result.charts:
    print(chart.page_no, chart.summary)
print(result.kvp.merged if result.kvp else None)
print(len(result.chunks), "chunks;", result.run.peak_memory_bytes)

result.save(Path("out/gao-07-631"))  # writes the tree above
```

`convert()` returns data and writes nothing; `save()` is the only call that writes. The result holds:

| Field | Contents |
|---|---|
| `document` | The DoclingDocument |
| `charts` | Each chart's page, CSV, raw CSV, summary and image |
| `pictures` | The pictures that are not charts |
| `tables` | Each table as a pandas DataFrame and as HTML |
| `kvp` | The key-value result, or `None` without a schema |
| `chunks` | The chunks |
| `run` | What `run.json` records |

`convert_many(inputs, out_dir, options)` is the batch form that `gdi convert` uses. It is a generator, so it does nothing until you iterate it, and it yields one `BatchItem` per PDF whose status is `done`, `skipped` or `failed`. It also handles resume and saving.

Both functions take the run lock described above; pass `wait=False` to raise `RunLockedError` instead of waiting. Before any model loads, both raise `ModelsNotReadyError` if `gdi convert-model` has not completed.

## Performance

These figures are from a 16 GB M2 Pro on AC power, measured on 2026-10-27 with docling 2.143.1, mlx 0.32.4 and mlx-vlm 0.7.10. They are the medians of three runs of the project's evaluation corpus.

| | |
|---|---|
| Reading pages | ~5.7 s per page, about 10 pages a minute sustained |
| Each chart (CSV and summary) | ~8.3 s |
| Each key-value page | ~6.0 s |
| Loading the models | ~7 s for the first PDF of a run |
| Peak model memory | 6.68 GB |

A page where the model falls into a repetition loop costs 30–40 s, and its text is usually lost. Such a page shows up in `run.json`'s warnings. These figures are for born-digital PDFs. Scanned PDFs are in scope, but they have not been measured yet.

## Disk space

| What | Where | Size |
|---|---|---|
| Python environment (includes PyTorch) | `.venv/` | 1.9 GB |
| granite-docling and the figure classifier | `~/.cache/granite-document-intelligence/models/` | 0.67 GB |
| Granite Vision, 8-bit | the same folder | 4.94 GB |
| Granite Vision bf16 source | the Hugging Face cache | 8.0 GB, **needed only to convert** |
| Chunking tokenizer | the Hugging Face cache | small |

Output is small next to the models: roughly 25–60 KB per page by default, and 160–190 KB per page with `--save-page-images`.

`gdi convert-model` ends by printing the size of the bf16 source and the command that removes it. Delete it afterwards with:

```bash
uv run hf cache rm model/ibm-granite/granite-vision-4.1-4b
```

Alternatively, run `uv run gdi convert-model --delete-source`, which deletes the source once the 8-bit model has loaded. Keep the source if you expect to convert again, since downloading it again takes 8 GB.

## Untrusted documents

Treat every PDF as untrusted, and everything derived from one, because a PDF can be malformed, hostile or simply huge.

- **Limits.** PDFs over 500 pages or 200 MB are refused before conversion (`--max-pages`, `--max-file-mb`). A file is processed only if it starts with `%PDF-`, whatever its extension.
- **No timeout.** A PDF that hangs a parser hangs the run, and only Ctrl-C stops it. There is no per-PDF timeout or process isolation in this version.
- **Parsers run in-process.** PDFs are parsed by pdfium and docling-parse, which are C and C++ code. They run with your privileges and without a sandbox. Keep the dependencies current, and do not use this tool on strangers' PDFs at volume.
- **Text in a page can try to steer the model.** That includes text you cannot see, such as white text on white. The pipeline treats model output as data, never as instructions, and schema validation rejects off-schema output. Still, **review key-values from an untrusted document before anything acts on them.**
- **Generated chart code is never run.** `--chart-code` writes model output that starts with an "Untrusted: review before running" header. Nothing in this package runs it, and you should read it before you do.
- **Outputs are files, not web pages.** `table-NNN.html` and `document.md` contain text from the PDF. Escape them before rendering them in a browser or UI.
- **Network.** The only network access is to the Hugging Face Hub, and only to download models. To run fully offline once the models are cached, set `HF_HUB_OFFLINE=1`.

Output that a model extracts from a copyrighted PDF is still derived from that PDF. Being able to extract it does not make it free to reuse.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Development

```bash
uv run ruff check .    # lint
uv run ruff format .   # format
uv run ty check        # typecheck
uv run pytest          # test
```

All four run in CI on every push to `main` and every pull request, on an Apple Silicon (macOS) runner. They also run as `scripts/gate.sh`, which the project's Claude Code Stop hook invokes. The unit tests mock every model, so they need no download. The quality evaluation (`eval/score.py`) runs the real models on a corpus of public PDFs and is run by hand, never in CI.

Contributions are welcome — open an issue or a pull request. Run the four checks above before submitting.

### Releases

CI cuts releases; nobody tags by hand. Bump `version` in `pyproject.toml`, run `uv lock`, and commit **both files** to `main`. Once the checks pass, CI tags `v<version>` and publishes a [release](https://github.com/darylalim/granite-document-intelligence/releases) with generated notes. A push that does not change the version does nothing. A commit carrying `pyproject.toml` without `uv.lock` fails CI at `uv sync --locked`, so it cuts no release.

## License

The project's code is licensed under the [Apache License 2.0](LICENSE). The key-value extraction prompt is copied from Docling under the MIT License; [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) carries that notice.

The models are downloaded at setup and are not part of this repository. Each is under its own license:
- **Apache-2.0:** granite-docling-258M, Granite Vision 4.1 and granite-embedding-english-r2.
- **MIT:** Docling's DocumentFigureClassifier.

The 8-bit Granite Vision that setup builds is a modified version of Apache-2.0 weights. It stays on your Mac. Anyone who publishes it must include the license and a notice that it was quantized.

The evaluation corpus keeps its own licenses. They are listed in `eval/corpus/SOURCES.md`, which arrives with the corpus itself.

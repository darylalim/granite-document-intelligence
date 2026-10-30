# Expected outputs

Hand-verified ground truth for each corpus PDF (plan §4.1, §8.1 and §8.2). `eval/score.py`
(PR 7) compares the pipeline's output against these files. Until it exists, spikes S6, S7
and S11 score against them with a scratchpad script.

## How the values were obtained

**Never from a model run.** A model-written expectation would score the model against
itself. Every value came from the PDF itself, in one of three ways:

- **Printed values** (table cells, chart data labels, form entries) were transcribed from
  the PDF's text layer, then checked against the rendered page by eye. A deterministic
  script did the transcription for the large Census tables. Every non-label cell was
  checked to occur on its page's text layer. Each Table A-2 row's nine percent shares
  also sum to 100 within the table's rounding (±0.25).
- **Unlabelled line charts** (`"values_from": "geometry"`) were read from the vector paths
  drawn in the PDF. The axis was calibrated from the positions of the tick labels and
  the zero line, and each point converted. Every computed value fell within 0.05 of a
  whole number, so the CSVs give whole numbers. For a labelled bar chart, the same
  method reproduces the labels exactly (checked on GAO-07-631 Figure 5).
- **The scan** (`gao-07-631-scan`) reuses its original's expected outputs, renumbered to
  the scan's pages. Nothing in it was transcribed again.
- **The adversarial PDF** is the project's own construction, so its expected values are
  the ones `make_adversarial.py` draws.

Where the text layer and the rendered page disagree, **the rendered page wins**, because
that is what a reader sees. Each `manifest.json` records every such disagreement found
under `notes` or an item's `note`.

## Layout

```text
<pdf-stem>/
  manifest.json      # what the PDF contains: pages, charts, tables, pictures, KVP
  headings.txt       # headings that must appear in document.md, one per line
  charts/pNNN-K.csv  # one per chart (or per plot of a multi-panel figure)
  tables/pNNN-K.csv  # one per table per page
  kvp/schema.json    # only where the PDF has a form or cover worth extracting
  kvp/expected.json
```

`NNN` is the **1-based page index in the committed PDF**, zero-padded, not the folio
printed on the page. `K` is the item's position in reading order on that page, starting
at 1. Together they are the pairing key of §8.2 rule 3, which pairs a prediction with an
expectation on the same page by reading order.

## manifest.json

| Key | Meaning |
|---|---|
| `pdf`, `sha256`, `pages` | the corpus file, its hash and its page count. A hash mismatch means the PDF changed under its expectations |
| `text_layer` | `true` when the PDF has a usable text layer. §8.2 rule 2 then scores `document.md` against it |
| `hidden_text` | optional: strings in the text layer that the rendered page never shows, removed from it before scoring (§8.2 rule 2). A white-filled or invisible character is dropped without being listed; this is for text hidden by what is drawn over or clipped around it, such as the Census `—Con.` |
| `scan_of` | only on a print-and-scan: the born-digital `pdf` it was scanned from and the first and last of its `pages` scanned. Scan page N is that PDF's page N + first − 1 |
| `charts[]` | `id`, `page`, `caption` as printed, `kind` (`bar` / `line` / `pie`), `values_from` (`labels` or `geometry`, above), `csv`. Optional: `panel`, the plot's own subtitle when one caption covers several plots, and `note` |
| `tables[]` | `id`, `page`, `caption`, `csv`. Optional: `rotated` for a table printed sideways, `continues_on` / `continued_from` linking the pages of a table that spans a page break, and `note` |
| `pictures[]` | non-chart pictures (`kind`: `logo`, `signature`, `map`, `photo`). A chart CSV produced for one is a **false positive** (§8.2 rule 5) |
| `kvp` | `schema`, `expected`, and `pages`, the pages the extraction runs on (the CLI's `--pages`) |
| `headings` | the headings file |
| `adversarial` | only in `adversarial/`: `exclude_from_averages`, `injected_values` that must not appear in the KVP output, `loop_pages` that must appear in `run.json` warnings, and the `pass` criterion in words |
| `notes` | facts about the PDF a scorer or a reader needs, such as a caption that contradicts its chart |

## CSV conventions

All files are UTF-8 with `\n` line endings, quoted only where a cell needs it.

**Charts.** The first column holds the categories (the x-axis, or the slices of a pie).
Each further column is one series, with the axis title or legend entry as printed for
its header. Match rows by their first-column label, not by position: a pie's slice order
is arbitrary. Values are numbers without units. The unit is in the header.

**Tables.**

- **One header row.** Multi-level column headers are flattened top-down, joined with
  `" / "` (`Contract costs / Fiscal year 2000`).
- **Footnote markers are dropped** from headers and row labels (`Race² …` → `Race …`,
  `2017⁴` → `2017`). That can leave two rows with the same label, so match table rows by
  position.
- **Values stay as printed:** `$14.6`, `(0.2)`, `31,481`, the Census significance asterisk
  `*1.3`, the en-dash minus `–0.7`, and the codes `X` (not applicable) and `Z` (rounds
  to zero). Normalization (§8.2 rule 3) belongs to the scorer, not the ground truth.
- **Section rows** that carry only a label (`Sex`, `Age`) are kept, with empty cells.
- **Wrapped row labels** are joined onto one line, and dot leaders are dropped.
- **A table that spans a page break** has one CSV per page, because rule 3 pairs within
  a page. The manifest links the pages.

## headings.txt

Each line is a heading, as printed, that `document.md` must contain. Each was checked to
occur on the PDF's text layer, or for a scan on its original's. Compare case- and whitespace-insensitively, since some
are set in capitals on the page (`ENERGY MARKETS`).

## kvp/

`schema.json` is the JSON Schema passed to `--schema`. `expected.json` is the exact
object a correct extraction returns, with `null` for a field the document leaves empty.

# Evaluation corpus (public tier)

The PDFs the quality evaluation runs on (plan §4.1 and §8.1). Their expected outputs,
written by hand from each PDF, are in [`../expected/`](../expected/README.md).

## Licences

**The corpus files keep their own licences. The project's `LICENSE` does not cover
them.** [`SOURCES.md`](SOURCES.md) records, per file, the source URL, retrieval date,
licence, why it qualifies, and every change made. Five files are U.S. government works
in the public domain. `gao-07-631-scan.pdf`, `adversarial.pdf` and its generator
`make_adversarial.py` are this project's own work, under Apache-2.0 like the expected outputs. Output the pipeline
extracts from a PDF follows that PDF's rights, not the pipeline's (plan §9.1).

## Files

| File | Pages | What it is there for |
|---|---|---|
| `gao-07-631.pdf` | 42 | The **30+ page** document for memory and throughput. **Line** and **bar** charts (12 plots in 9 figures, including a broken axis, grouped bars and two-panel figures) and 2 tables. Also logos and a signature, which must not become charts. |
| `gao-08-14-excerpt.pdf` | 2 | A **pie** chart whose text layer pairs labels with the wrong values. |
| `gao-08-1044-excerpt.pdf` | 3 | **Non-chart pictures**, a map and a grid of photographs, for false-positive chart detection (§8.2 rule 5). No page has a chart. |
| `census-p60-282-excerpt.pdf` | 4 | A 16-column table **printed sideways** (S3 found these loop silently), and a table **spanning a page break**. |
| `gao-07-631-scan.pdf` | 15 | The **scanned** document: GAO-07-631's PDF pages 12–26, printed and scanned to an image-only PDF with no text layer. Those pages hold all 12 plots and both tables, so it scores against the original's expected outputs. |
| `irs-w9-filled.pdf` | 1 | A **form** filled with fictitious data, for key-value extraction. |
| `adversarial.pdf` | 2 | The project-made **adversarial** case (§8.2 rule 6): a form carrying a visible and a white-on-white prompt injection, and a table of 60 identical rows built to trigger a repetition loop. It is scored but kept out of the quality averages. |

2.6 MB in all, against the plan's 20 MB ceiling.

## Not here yet

- **The private tier.** The user's own PDFs go in `eval/private/corpus/` with expected
  outputs in `eval/private/expected/`. Both are gitignored through `/eval/private/`.

## Regenerating

`adversarial.pdf` is the one generated file: `uv run python eval/corpus/make_adversarial.py`
rewrites it byte for byte. Edit the generator, not the PDF, and update
`eval/expected/adversarial/` along with it.

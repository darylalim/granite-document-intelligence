# Demo video plan

Two captioned demo videos with music for a YouTube channel: a 16:9 main video and a 9:16 Short. Both show `gdi convert` and `gdi ui` and every output. Status: **draft**, 2026-11-07.

## Decisions

| Question | Answer |
|---|---|
| Where | YouTube: a main video and a Short |
| Surfaces | Command line and UI |
| Coverage | Every output in the main video: Markdown, tables, charts, key-values, chunks, run record. The Short shows the highlights. |
| Captions | Burned in on both. The main video also gets an uploaded captions track. |
| Sound | Background music, no voiceover |
| Content | Staged: real output from one earlier conversion, never edited |
| Production | Scripted end to end, re-runnable |
| Theme | Dark (Carbon dark). It reads better on YouTube, where most viewing is in dark mode, and in thumbnails. |
| Loop | Only the Short is built to loop, because YouTube loops Shorts and not regular videos |

## Deliverables

| File | Format | Length | Notes |
|---|---|---|---|
| `gdi-demo-1440p.mp4` | 16:9, **2560 × 1440**, H.264 + AAC | 75 s (30 bars) | YouTube gives uploads of 1440p and above a much higher bitrate, which keeps small UI text and CSV cells sharp even at 1080p playback. |
| `gdi-demo-short.mp4` | 9:16, 1080 × 1920, H.264 + AAC | 40 s (16 bars) | The last frame leads back to the first, and the music loops at the same point. |
| `captions.srt` | SubRip | — | Uploaded as the main video's captions track, so the text is searchable and accessible. |
| `thumbnail.png` | 1280 × 720, under 2 MB | — | One chart beside its CSV, with 3–4 words of large text, e.g. "Charts → CSV, locally". |

All captions use IBM Plex Sans from `ui/static/fonts/`. Each is two short lines at most and stays on screen for at least 2.5 s. The Short keeps its captions inside the middle of the frame, clear of the bottom 20% and right 15% that YouTube's own buttons cover.

## Source document

`eval/corpus/gao-07-631.pdf` (42 pages, 3 tables, 9 charts), with `eval/expected/gao-07-631/kvp/schema.json` as the key-value schema. This is the README's document, so the videos, the screenshots and the eval all show the same run.

## Storyboard: main video (16:9, 75 s = 30 bars)

Every shot boundary falls on a bar line (2.5 s at 96 BPM; see *Music*), and so does every caption change.

| Time | Bars | Shot | Caption |
|---|---|---|---|
| 0–5 s | 2 | Title card: PDF page → arrow → Markdown/CSV/JSON icons | "PDFs → structured data. Fully local on your Mac." |
| 5–15 s | 4 | Terminal: `gdi convert gao-07-631.pdf --schema schema.json -o gdi-out` (run from `~`), progress replayed fast | "42 pages, 9 charts, one command" / "Sped up — the real run took N min" |
| 15–20 s | 2 | Terminal: `ls -F gdi-out/gao-07-631` | "Markdown, JSON, tables, chart CSVs, key-values, RAG chunks" |
| 20–22.5 s | 1 | Terminal: `gdi ui` → browser opens | "Or browse it in the UI" |
| 22.5–30 s | 3 | UI **Document** view, slow scroll | "The whole document as Markdown, in reading order" |
| 30–35 s | 2 | UI **Tables** | "Tables extracted as data" |
| 35–47.5 s | 5 | UI **Charts**: two or three charts, each beside its CSV and summary | "Charts → CSV + summary, read by Granite Vision" |
| 47.5–52.5 s | 2 | Terminal: `jq '.properties \| map_values(.description)' schema.json`, the schema's fields and descriptions | "Fields from your own JSON Schema" |
| 52.5–55 s | 1 | UI **Key-values**: the same four fields, filled in and valid | (continues) |
| 55–60 s | 2 | UI **Chunks** | "Chunks ready for RAG" |
| 60–62.5 s | 1 | UI **Run** | "Every run recorded: models, versions, timings" |
| 62.5–75 s | 5 | End card: repo URL, channel handle `@darylalim-hnl`, "granite-docling + Granite Vision 4.1 on MLX · 16 GB Apple Silicon", space for YouTube's end-screen buttons | — |

The end card runs 12.5 s, at least the 10 s needed, because YouTube's clickable end-screen elements take the last 5–20 s of a video that is at least 25 s long. The layout leaves the right half free for them.

## Storyboard: Short (9:16, 40 s = 16 bars, loops)

The loop point is the end of bar 16, so the picture and the music wrap at the same bar line.

| Time | Bars | Shot | Caption |
|---|---|---|---|
| 0–2.5 s | 1 | A chart from the PDF, large, matching the last frame | "This chart is a picture in a PDF…" |
| 2.5–10 s | 3 | Terminal at phone width: the `gdi convert` command and the progress replay | "One command, all on my Mac" (sped up) |
| 10–22.5 s | 5 | UI **Charts** at 390 px: the same chart → its CSV → the summary | "…now it's a CSV and a summary" |
| 22.5–30 s | 3 | UI **Key-values** | "Plus fields from your own schema" |
| 30–37.5 s | 3 | Quick cuts, one bar each: Document, Tables, Chunks | "Markdown, tables, RAG chunks" |
| 37.5–40 s | 1 | Repo name, then back to the opening chart | "Open source · full demo linked below" |

Links in a Short's description and comments are not clickable, so the Short points at its related-video link to the main video, whose description holds the repo URL.

A vertical frame uses the UI's measured phone-width layout (390 px, plan §15), not a crop of the wide video.

## Music

- **Tempo first: 96 BPM (± 2).** At 96 BPM a 4/4 bar lasts exactly 2.5 s, so the Short is 16 bars and the main video 30. The loop seam, every cut and every caption change land on a bar line, and the 2.5 s caption minimum is one bar. Fitting the edit to the music this way avoids trims and fades later. If the best track is at another tempo, re-snap the storyboards to its bar length; 120 BPM (2 s bars, 20 bars for the Short) works just as well.
- **Source:** the YouTube Audio Library, filtered to **"Attribution not required"**, so there is no credit line to get wrong and no Content ID claim. Other filters:
  - Genre **Ambient** or **Electronic**; mood **Calm** or **Bright**.
  - **Instrumental only.** Vocals compete with captions.
  - **Steady throughout**, with no drop or silent break: the Short may start anywhere in the track, and the main video's 75 s may come from the middle of it.
  - **No strong melody hook**, which makes the loop seam obvious and wears thin by the third loop.
- **Choosing the track:**
  1. Shortlist three tracks that pass the filters.
  2. Measure each one's tempo with `aubio tempo` (`brew install aubio`), because listed BPMs are sometimes missing or wrong. Note where the first downbeat falls, which `render.py` needs as an offset.
  3. Render the Short with each one (`render.py --music <file> --offset <s> short`).
  4. Watch each **looped three times on a phone**, both muted and with sound. Most Short viewers see it more than once, so the seam and any tiresome phrase show up there.
  5. Render the main video with the winner only, and record it in `demo/README.md`.
- **Music is secondary.** Many viewers watch muted, so captions and pictures must work alone. The track only has to avoid sounding broken, which is why steady and calm beats catchy.
- **The track file is not committed.** The Audio Library licence covers use in YouTube videos, not redistribution from a public git repository. `demo/README.md` records the track's title, artist, licence and download date, and `render.py` reads the file from the gitignored `demo/assets/`.
- **Main video:** fade in over 1 s and out over 3 s at the end card, normalised to −14 LUFS integrated, YouTube's playback loudness, with true peak at or below −1 dBTP (`loudnorm` in two passes).
- **Short:** trim to a whole number of bars so the end meets the beginning, and use a short crossfade (about 50 ms) at the seam instead of a fade-out. A fade-out would make every loop dip in volume. Same loudness target.
- Music is the only sound: no typing sounds or UI clicks, which would need syncing to replayed footage that is not real time.

## Pipeline

Five scripts under `demo/`, each runnable alone, all writing to the gitignored `demo/build/`.

### 1. `stage.sh`: produce the real output once

1. Check the models directory against the pins with `verify_models_dir`, and run `gdi convert-model` only if that fails (about 3 min plus a ~9 GB download).
2. Copy the PDF and schema into a scratch `HOME` (`demo/build/home/`), so the UI shows the default `~/gdi-out`. From there, run:
   `gdi convert gao-07-631.pdf --schema schema.json -o gdi-out`, adding `--events`.
   The paths are relative because the replayed output prints `-o` as given and the UI's Run view prints the recorded `--schema` path. Absolute paths would put this Mac's home directory on screen. `GDI_CACHE_DIR` and `HF_HOME` stay pinned to the real caches.
   Output goes through `demo/timestamp.py`, which records each line with its time as JSON Lines. This replaced `script -r`, whose binary records carry a `^D` and header lines that the replay would have to strip.
3. Keep the tree, `events.jsonl`, `transcript.jsonl` and `stage.json` (command, wall time, commit, date). Running on an idle Mac on AC power, as with the eval, keeps the "real run took" caption honest.

This is the only step that runs the models. Everything after it replays.

### 2. `terminal.py`: render the command-line shots

This step was planned for VHS, but this Mac has no Homebrew, so VHS, its ttyd and `tree` are all missing (decided with the user on 2026-11-08). Instead, `terminal.html` draws a terminal window in IBM Plex Mono and Carbon g100 colours. Python plays the session into it and takes one screenshot per distinct screen through the same headless Chrome that step 3 uses (`demo/cdp.py`, a standard-library DevTools client).

- **Output.** Each shot is a set of PNG frames plus an ffconcat timeline that `render.py` encodes. Every change lands on a 30 fps frame, and every shot lasts a whole number of bars. A downscaled `preview.gif` per layout allows a check without ffmpeg, and `meta.json` records each shot's speed-up.
- **Layouts.**
  - Main video: 1280 × 720 CSS px at 2×, 90 × 14 characters. The window ends at 524 px, leaving the bottom band for captions.
  - Short: 360 × 640 at 3×, 40 × 14 characters. 360 × 640 at 3× is exactly 1080 × 1920, which 390 px wide cannot reach with a whole height. The window ends at 363 px, above the mid-frame captions.
- **The convert replay.** A re-run of `gdi convert` would print "skipped" (§3.8), so its shot replays stage.sh's transcript, compressed to fit: about 50× in the main video and 79× in the Short. The caption says "Sped up".
  - Only lines in gdi's own progress format are kept. A torch warning, a weights progress bar and a transformers deprecation notice are dropped, and each dropped line is printed.
  - `--events` is left out of the typed command, since it does not change stderr.
- **The other commands run for real.** `ls -F gdi-out/gao-07-631` stands in for `tree`, which a stock Mac lacks. `jq '.properties | map_values(.description)' schema.json`, with the jq macOS ships, shows the KVP schema before its results: the UI never shows the schema, and the plain file wraps past the 14 rows. It is 2 bars, typing included, which leaves the UI's Key-values view 1 bar. `gdi ui --no-browser` runs on the default port, and its output up to the URL line is shown at its own timing.
- **Wrapping.** Lines wrap between words rather than mid-word, as a terminal would. At the Short's 40 columns, mid-word wrapping split "3 tables" and "gao-07-631". Only the line breaks change, never the text.
- **Continuity.** In the main video, the scrollback carries across the three shots (4 + 2 + 1 bars), so the crossfades join near-identical frames.

### 3. `ui.py`: record the UI shots

- **Serving.** It runs the real `gdi ui --no-browser` on a free port under stage.sh's scratch `HOME`, so the sidebar reads `~/gdi-out`. Headless Chrome is set to `prefers-color-scheme: dark` through `demo/cdp.py`.
  - Main video: 1280 × 720 CSS px at 2×, which gives 2560 × 1440. The sidebar stays open, as the app opens on a desktop.
  - Short: 360 × 640 at 3×, which gives 1080 × 1920, as the terminal shots do. Checked by eye at 360 px: the counts sit 2 × 2, the pills wrap to two rows, and the chart cards and dataframes fit the width.
- **Readiness.** Each shot opens its view by link (`?view=<Label>`) and waits for the README recipe's signals (four `stMetricValue`s, every `/media/` image decoded) plus 1.5 s for the dataframes' canvases.
- **Motion.** The script only scrolls `stMain`, Streamlit's scroll container, along eased keyframes; nothing on the page is altered.
  - A keyframe names an element: the pills (`.st-key-view`) or a chart card (`.st-key-card_chart-001`).
  - A top edge rests 16 px below Streamlit's fixed header, which otherwise covers the top ~60 px. A bottom edge rests at the frame's bottom, lifted clear of the captions.
  - A PNG is taken only when the position changes by a whole device pixel. `demo/timeline.py` writes the ffconcat, so held positions cost one PNG.
- **What the shots show.**
  - Document opens at the top of the app, then scrolls into the document.
  - Charts rests on chart-001 and chart-002 side by side, then scrolls through their CSVs and summaries. On the Short, it scrolls through chart-001's card alone.
  - Key-values, Run and the main video's Chunks barely scroll, because their pages are short.
- **Cost.** It takes about 3 minutes, mostly the 2560 × 1440 screenshots.

### 4. `cards.py`: title card, end card, the Short's cards and the thumbnail

`cards.py` rather than `cards.sh`, and one `cards.html` holding all five layouts over shared CSS rather than a page each. `demo/page.py` renders it through the same headless Chrome: Plex fonts read from the UI's theme config and inlined, data placed with `textContent`, each PNG checked for its exact size. The captions in step 5 reuse it. Every picture comes from the staged run, and each output a card names is checked to exist in the tree.

| Card | Length | Content |
|---|---|---|
| Main title | 2 bars | Page 12 of the PDF (the page holding chart-001) → `document.md`, `charts/chart-001.csv`, `kvp.json`, `chunks.jsonl`; all above the caption band |
| Main end | 5 bars | Name, one-line pitch, repo URL, models, "a 16 GB Apple Silicon Mac, offline", channel handle; the right half left empty for YouTube's end-screen elements |
| Short opening | 1 bar | chart-001, large, with `gao-07-631.pdf · page 12` |
| Short end | 1 bar | Name and repo for 1.5 s, then the opening frame for 1 s, so the loop's last frame is its first (decoded, they differ by at most 6 of 255, encoder noise) |
| Thumbnail | — | "Charts → CSV, locally" over chart-001 and the first six rows of its CSV as Granite Vision wrote them; 1280 × 720, 79 KB |

- **The chart.** It is rendered from the PDF as `charts.render_crop` crops it, at 4× (1440 px wide) so it stays sharp at phone width.
  - The crop is padded by 10 points, because Docling's box clips the chart's "Source:" line. The pipeline's own crop is unchanged.
- **Arrows are SVG.** The UI's Plex subsets have no `→` (U+2192), and a fallback glyph would not match.

### 5. `render.py`: assemble both videos with ffmpeg

`render.py` rather than `render.sh`: the storyboard is data (its `MAIN` and `SHORT` segment tables), the script takes arguments, and Python gets ruff and ty, while no gate checks shell. ffmpeg comes from Homebrew (`brew install ffmpeg`, 9.0.2 on 2026-11-08).

**Done: the rough render (2026-11-08).** It joins every shot end to end, silent; since step 4 the cards fill what were black gaps.
- All segments go into one ffconcat, so ffmpeg reads each PNG once and encodes once. Both videos took 27 s.
- Each shot's frame count is checked against its bars, and the output's against the storyboard: 2,250 frames (75.0 s) and 1,200 (40.0 s), exact.
- **Encoding.** H.264 High, `yuv420p`, 30 fps, `+faststart`, converted from sRGB to BT.709 limited range and tagged as such.
  - The plan's 24 / 12 Mbps assumed a constant rate. Most frames are held still, so an average-bitrate target undershot to ~0.9 Mbps and meant nothing.
  - It encodes at **CRF 10** with 24 / 12 Mbps as a ceiling only: 6.2 MB and 2.2 MB.
  - Against the source PNGs it scores mean PSNR 65 / 64 dB (minimum 51 / 56) and worst-frame SSIM 0.99984. YouTube re-encodes the upload, so the master is kept near-lossless.

**Still to do:**
- Short crossfades between segments (`xfade`). Each one overlaps two segments, so the timeline has to absorb the overlap to keep the cuts on bar lines.
- The captions. Homebrew's `ffmpeg` is built without libass, so it has no `subtitles` filter. Either draw each caption as a PNG in Chrome, in the UI's own Plex Sans `woff2`, and composite it with `overlay` between its times, or install `ffmpeg-full`, which is keg-only and has libass, and convert Plex Sans to TTF for it. `captions.srt` is written either way, for YouTube's captions track.
- `--music <file>` and `--offset <seconds>` (the first downbeat to start from), mixed as described under *Music*: AAC at 192 kbps and 48 kHz.
- Check the Short's seam by concatenating two copies and playing them back.

## Repository changes

- `demo/` with the five scripts (`stage.sh`, `terminal.py`, `ui.py`, `cards.py`, `render.py`), `timeline.py`, `page.py`, `timestamp.py` (stage.sh's line recorder), `cdp.py` (the Chrome client), `terminal.html`, `cards.html`, `captions.srt`, `captions-short.srt`, and a `README.md` holding the rebuild command and the music credit record.
- `.gitignore`: `/demo/build/` and `/demo/assets/`, each with a `TestGitignore` case.
- The videos are **not** committed, because they are large binaries and go stale. They live on YouTube.
- `ui.py` passes ruff and ty. It needs no test unless it grows logic beyond driving the browser.

## Upload checklist

- **Title:** says what it does, e.g. "Turn PDFs into Markdown, chart CSVs and RAG chunks — locally on a Mac".
- **Description:** the repo URL on the first line (only the first lines show above "more", and only URLs are clickable), then a short "what you're seeing" with timestamps, the models and their licences, and the dated performance figures from the README.
- **Captions:** upload `captions.srt` to the main video.
- **Thumbnail:** upload `thumbnail.png` to the main video. Custom thumbnails need YouTube's intermediate features, which Studio showed enabled for @darylalim-hnl on 2026-11-08 (Settings → Channel → Feature eligibility).
- **End screen:** a "subscribe" element, plus a "Link" element to the repo if YouTube Studio offers one. The channel's phone verification is done (intermediate features enabled, 2026-11-08), but external-site links in end screens have also required YouTube Partner Program membership, so check the editor. If "Link" is missing, the repo URL printed on the end card and the description cover it.
- **Pinned comment:** the repo URL.
- **Short:** link the main video as its "related video", so viewers can go from the Short to the full walkthrough.

## Keeping it current

Like `docs/images/ui-*.png`, the videos go stale when the UI layout, theme or labels change, or when a model changes, because the chart CSVs and summaries on screen are model output. On any of those, re-run `stage.sh` and the rest. Note this beside the screenshot note in `CLAUDE.md`.

## Open questions

1. **Which music track.** Shortlist three at about 96 BPM from the Audio Library, then follow the steps under *Music*. The decision itself waits on rendered cuts.

Settled on 2026-11-08: the channel is `https://www.youtube.com/@darylalim-hnl` (corrected the same day from an earlier handle), and the repo URL is `https://github.com/darylalim/granite-document-intelligence`.

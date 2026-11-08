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
| 5–15 s | 4 | Terminal: `gdi convert gao-07-631.pdf --schema schema.json -o ~/gdi-out`, progress replayed fast | "42 pages, 9 charts, one command" / "Sped up — the real run took N min" |
| 15–20 s | 2 | Terminal: `tree -L 2 ~/gdi-out/gao-07-631` | "Markdown, JSON, tables, chart CSVs, key-values, RAG chunks" |
| 20–22.5 s | 1 | Terminal: `gdi ui` → browser opens | "Or browse it in the UI" |
| 22.5–30 s | 3 | UI **Document** view, slow scroll | "The whole document as Markdown, in reading order" |
| 30–35 s | 2 | UI **Tables** | "Tables extracted as data" |
| 35–47.5 s | 5 | UI **Charts**: two or three charts, each beside its CSV and summary | "Charts → CSV + summary, read by Granite Vision" |
| 47.5–55 s | 3 | UI **Key-values**, with the schema shown first | "Fields from your own JSON Schema" |
| 55–60 s | 2 | UI **Chunks** | "Chunks ready for RAG" |
| 60–62.5 s | 1 | UI **Run** | "Every run recorded: models, versions, timings" |
| 62.5–75 s | 5 | End card: repo URL, channel handle `@DarylLim-ii7rh`, "granite-docling + Granite Vision 4.1 on MLX · 16 GB Apple Silicon", space for YouTube's end-screen buttons | — |

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
  2. Measure each one's tempo with `aubio tempo` (`brew install aubio`), because listed BPMs are sometimes missing or wrong. Note where the first downbeat falls, which `render.sh` needs as an offset.
  3. Render the Short with each one (`render.sh --music <file> --offset <s> short`).
  4. Watch each **looped three times on a phone**, both muted and with sound. Most Short viewers see it more than once, so the seam and any tiresome phrase show up there.
  5. Render the main video with the winner only, and record it in `demo/README.md`.
- **Music is secondary.** Many viewers watch muted, so captions and pictures must work alone. The track only has to avoid sounding broken, which is why steady and calm beats catchy.
- **The track file is not committed.** The Audio Library licence covers use in YouTube videos, not redistribution from a public git repository. `demo/README.md` records the track's title, artist, licence and download date, and `render.sh` reads the file from the gitignored `demo/assets/`.
- **Main video:** fade in over 1 s and out over 3 s at the end card, normalised to −14 LUFS integrated, YouTube's playback loudness, with true peak at or below −1 dBTP (`loudnorm` in two passes).
- **Short:** trim to a whole number of bars so the end meets the beginning, and use a short crossfade (about 50 ms) at the seam instead of a fade-out. A fade-out would make every loop dip in volume. Same loudness target.
- Music is the only sound: no typing sounds or UI clicks, which would need syncing to replayed footage that is not real time.

## Pipeline

Five scripts under `demo/`, each runnable alone, all writing to the gitignored `demo/build/`.

### 1. `stage.sh`: produce the real output once

1. Make sure the models directory exists. It is currently absent from the default cache, so run `gdi convert-model` first, which takes about 3 min plus a ~9 GB download.
2. Under a scratch `HOME`, so the UI shows the default `~/gdi-out`, run:
   `gdi convert eval/corpus/gao-07-631.pdf --schema …/schema.json -o $HOME/gdi-out --events demo/build/events.jsonl`.
   Capture stderr with `script -r` (macOS) so each line keeps its timestamp.
3. Keep the tree, `events.jsonl`, the timed transcript and the wall time. Running on an idle Mac on AC power, as with the eval, keeps the "real run took" caption honest.

This is the only step that runs the models. Everything after it replays.

### 2. `terminal.sh`: record the command-line shots

- Tool: **VHS** (`brew install vhs`), which turns a `.tape` script into a video at a fixed size, font and typing speed. There are two tapes: 2560 × 1440 for the main video, and a narrow one with large type for the Short. Both use IBM Plex Mono and a dark Carbon-matched colour scheme.
- A plain re-run of `gdi convert` would print "skipped", because resume (§3.8) treats the PDF as done. So the tape types the real command, but what runs is a small `replay` helper that prints the captured transcript with its timing compressed (e.g. 20×). The caption says "Sped up".
- `tree` and `gdi ui --no-browser` run for real against the staged tree.

### 3. `ui.py`: record the UI shots

- Reuse the README screenshot recipe: `gdi ui --no-browser` against the staged tree, headless Chrome over CDP, and `Emulation.setEmulatedMedia` with `prefers-color-scheme: dark`.
  - Main video: 1280 × 720 CSS px at `deviceScaleFactor` 2, which gives 2560 × 1440.
  - Short: 390 × 693 CSS px at `deviceScaleFactor` 2.77, which gives 1080 × 1920.
- For each view, open `?view=<Label>`, wait for the same readiness signals (four `stMetricValue`s, every `/media/` image complete), then capture.
- For motion, take an evenly spaced frame sequence per view (scroll the main column in steps with `Runtime.evaluate`, at 30 fps) rather than `Page.startScreencast`, whose frame timing is irregular.
- Use the standard library only, or one client through `uvx`, with no model imports at module level, so `TestImportCost` and the 15 s gate are unaffected.

### 4. `cards.sh`: title card, end card and thumbnail

Small HTML pages in `demo/`, rendered to PNG through the same headless Chrome. Chrome loads the Plex `woff2` files directly, so the cards match the UI.

### 5. `render.sh`: assemble both videos with ffmpeg

- Join the segments in storyboard order with short crossfades (`xfade`).
- Burn in the captions with the `subtitles` filter. libass cannot read `woff2`, so first decompress Plex Sans to TTF (`uvx --from 'fonttools[woff]' fonttools ttLib.woff2 decompress`) into `demo/build/fonts/` and point `fontsdir` there.
- Takes `--music <file>` and `--offset <seconds>` (the first downbeat to start from) and a target, `main` or `short`, so one script renders each shortlisted track.
- Mix in the music as described above, encoding AAC at 192 kbps and 48 kHz.
- Encode H.264, High profile, `yuv420p`, 30 fps, with `-movflags +faststart`, at about 24 Mbps for 1440p and 12 Mbps for the Short.
- Check the Short's seam by concatenating two copies and playing them back.

## Repository changes

- `demo/` with the five scripts, two `.tape` files, `captions.srt`, `captions-short.srt`, `title.html`, `end.html`, `thumbnail.html`, and a `README.md` holding the rebuild command and the music credit record.
- `.gitignore`: `/demo/build/` and `/demo/assets/`, each with a `TestGitignore` case.
- The videos are **not** committed, because they are large binaries and go stale. They live on YouTube.
- `ui.py` passes ruff and ty. It needs no test unless it grows logic beyond driving the browser.

## Upload checklist

- **Title:** says what it does, e.g. "Turn PDFs into Markdown, chart CSVs and RAG chunks — locally on a Mac".
- **Description:** the repo URL on the first line (only the first lines show above "more", and only URLs are clickable), then a short "what you're seeing" with timestamps, the models and their licences, and the dated performance figures from the README.
- **Captions:** upload `captions.srt` to the main video.
- **End screen:** a "subscribe" element, plus a "Link" element to the repo if YouTube Studio offers one. The channel is verified, but external-site links in end screens have also required YouTube Partner Program membership, so check the editor. If "Link" is missing, the repo URL printed on the end card and the description cover it.
- **Pinned comment:** the repo URL.
- **Short:** link the main video as its "related video", so viewers can go from the Short to the full walkthrough.

## Keeping it current

Like `docs/images/ui-*.png`, the videos go stale when the UI layout, theme or labels change, or when a model changes, because the chart CSVs and summaries on screen are model output. On any of those, re-run `stage.sh` and the rest. Note this beside the screenshot note in `CLAUDE.md`.

## Open questions

1. **Which music track.** Shortlist three at about 96 BPM from the Audio Library, then follow the steps under *Music*. The decision itself waits on rendered cuts.

Settled on 2026-11-08: the channel is `https://www.youtube.com/@DarylLim-ii7rh` (verified), and the repo URL is `https://github.com/darylalim/granite-document-intelligence`.

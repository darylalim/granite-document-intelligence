# Demo videos

The scripts that build the YouTube demo videos. The plan, and the reason for each choice, is in [`docs/plans/demo-video.md`](../docs/plans/demo-video.md). Everything is written to the gitignored `demo/build/`.

## Rebuild

```bash
demo/stage.sh                         # step 1: the one real conversion (about 5 min); --force to redo
.venv/bin/python demo/terminal.py     # step 2: the terminal shots (about 30 s)
.venv/bin/python demo/ui.py           # step 3: the UI shots (about 3 min)
.venv/bin/python demo/cards.py        # step 4: the cards and thumbnail.png (about 10 s)
.venv/bin/python demo/render.py       # step 5: both videos (about 30 s; brew install ffmpeg)
```

`stage.sh` is the only step that runs the models. Run it on an idle Mac on AC power, because its wall time goes on screen. It converts `eval/corpus/gao-07-631.pdf` with the eval's key-value schema from a scratch `HOME` (`demo/build/home/`), running exactly the command the terminal shot types:

```text
gdi convert gao-07-631.pdf --schema schema.json -o gdi-out
```

The paths are relative so that neither the replayed output nor the UI's Run view shows this Mac's home directory. It keeps the timed transcript (`transcript.jsonl`), the `--events` stream and `stage.json` (command, wall time, commit, date).

`terminal.py` renders the terminal shots in headless Chrome (`/Applications/Google Chrome.app`), with no VHS or Homebrew needed. It writes PNG frames and an ffconcat timeline per shot under `build/terminal/<main|short>/`, plus a `preview.gif` per layout for a check without ffmpeg. The `gdi convert` shot replays the staged transcript with only gdi's own lines kept. `ls` and `gdi ui` run for real, and `gdi ui` needs port 8501 free.

`ui.py` serves the staged tree with the real `gdi ui` and scrolls each view in headless Chrome, writing the same frames-plus-timeline layout under `build/ui/<main|short>/`. Both scripts share `timeline.py` (bar and frame timing, the ffconcat writer, the preview GIF) and `cdp.py`.

`render.py` joins the shots in storyboard order into `build/gdi-demo-1440p.mp4` and `build/gdi-demo-short.mp4`, checking every shot's length against the storyboard. For now it is silent, with no captions or crossfades. `cards.py` draws the title, end and Short cards and `build/thumbnail.png` from `cards.html`, through `page.py`, which the captions will also use.

## Music credit

Not chosen yet (plan, *Music*). Record the track's title, artist, licence and download date here once it is. The file goes in the gitignored `demo/assets/`.

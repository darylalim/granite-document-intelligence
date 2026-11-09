# Demo videos

The scripts that build the YouTube demo videos. The plan, and the reason for each choice, is in [`docs/plans/demo-video.md`](../docs/plans/demo-video.md). Everything is written to the gitignored `demo/build/`.

## Rebuild

```bash
demo/stage.sh                         # step 1: the one real conversion (about 5 min); --force to redo
.venv/bin/python demo/terminal.py     # step 2: the terminal shots (about 30 s)
.venv/bin/python demo/ui.py           # step 3: the UI shots (about 3 min)
.venv/bin/python demo/cards.py        # step 4: the cards and thumbnail.png (about 10 s)
.venv/bin/python demo/tempo.py demo/assets/*.mp3      # measure candidate tracks (brew install aubio)
.venv/bin/python demo/render.py --music demo/assets/<track> --offset <s>   # step 5: both videos (about 45 s)
```

`stage.sh` is the only step that runs the models. Run it on an idle Mac on AC power, because its wall time goes on screen. It converts `eval/corpus/gao-07-631.pdf` with the eval's key-value schema from a scratch `HOME` (`demo/build/home/`), running exactly the command the terminal shot types:

```text
gdi convert gao-07-631.pdf --schema schema.json -o gdi-out
```

The paths are relative so that neither the replayed output nor the UI's Run view shows this Mac's home directory. It keeps the timed transcript (`transcript.jsonl`), the `--events` stream and `stage.json` (command, wall time, commit, date).

`terminal.py` renders the terminal shots in headless Chrome (`/Applications/Google Chrome.app`), with no VHS or Homebrew needed. It writes PNG frames and an ffconcat timeline per shot under `build/terminal/<main|short>/`, plus a `preview.gif` per layout for a check without ffmpeg. The `gdi convert` shot replays the staged transcript with only gdi's own lines kept. `ls` and `gdi ui` run for real, and `gdi ui` needs port 8501 free.

`ui.py` serves the staged tree with the real `gdi ui` and scrolls each view in headless Chrome, writing the same frames-plus-timeline layout under `build/ui/<main|short>/`. Both scripts share `timeline.py` (bar and frame timing, the ffconcat writer, the preview GIF) and `cdp.py`.

`render.py` joins the shots in storyboard order into `build/gdi-demo-1440p.mp4` and `build/gdi-demo-short.mp4`, checking every shot's length against the storyboard. It crossfades each join on its bar line and burns in the captions, drawn by `captions.html`, and writes `build/captions.srt` and `build/captions-short.srt` from the same table for YouTube's captions track. With `--music` it mixes in the track from `--offset` (its first downbeat, from `tempo.py`), loops the Short's sound seamlessly, normalises both to −14 LUFS and fails if the finished file misses that or −1 dBTP. Without `--music` the videos are silent. `tempo.py --click build/click.wav` makes a 96 BPM test track with known beat times. `cards.py` draws the title, end and Short cards and `build/thumbnail.png` from `cards.html`, through `page.py`, which the captions will also use.

## Music credit

| | |
|---|---|
| Track | *Pulsar* |
| Artist | The Grey Room / Density & Time |
| Source | YouTube Audio Library (Ambient, Calm, 4:08) |
| Licence | Audio Library, "Attribution not required": no credit line needed in the video or description |
| Downloaded | 2026-11-08, into the gitignored `demo/assets/pulsar.mp3` |

The track is not committed: the Audio Library licence covers use in YouTube videos, not redistribution from a public repository. To rebuild the final videos, download it again from the Audio Library as `demo/assets/pulsar.mp3` and run:

```bash
.venv/bin/python demo/render.py --music demo/assets/pulsar.mp3 --offset 102.243 --music-bpm 105.30
```

- **Tempo.** Pulsar measures 105.3 BPM and is stretched 8.8% to 96 with `atempo`, pitch kept. None of 12 screened Audio Library tracks was near 96, and Pulsar's beat was the steadiest. The user listened to the result and approved it on 2026-11-08.
- **Offset.** 102.243 s puts the start in the track's full, steady section (about 100–210 s), past its 20 s build. The value was corrected from 102.150 by measuring the stretched section, whose beats then fell at 0.102 s. Two bass-energy tests agree it is a downbeat. aubio resolves this track's beats to only about ±50 ms, so treat the last digits as approximate.

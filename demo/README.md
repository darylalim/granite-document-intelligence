# Demo videos

The scripts that build the YouTube demo videos. The plan, and the reason for each choice, is in [`docs/plans/demo-video.md`](../docs/plans/demo-video.md). Everything is written to the gitignored `demo/build/`.

## Rebuild

```bash
demo/stage.sh            # step 1: the one real conversion (about 10 min); --force to redo
```

`stage.sh` is the only step that runs the models. Run it on an idle Mac on AC power, because its wall time goes on screen. It converts `eval/corpus/gao-07-631.pdf` with the eval's key-value schema from a scratch `HOME` (`demo/build/home/`), running exactly the command the terminal shot types:

```text
gdi convert gao-07-631.pdf --schema schema.json -o gdi-out
```

The paths are relative so that neither the replayed output nor the UI's Run view shows this Mac's home directory. It keeps the timed transcript (`transcript.jsonl`), the `--events` stream and `stage.json` (command, wall time, commit, date).

## Music credit

Not chosen yet (plan, *Music*). Record the track's title, artist, licence and download date here once it is. The file goes in the gitignored `demo/assets/`.

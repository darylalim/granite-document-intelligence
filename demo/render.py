"""Step 5 of the demo videos: assemble the shots into the two videos with ffmpeg.

    .venv/bin/python demo/render.py [main|short|all]

docs/plans/demo-video.md has the storyboard; MAIN and SHORT below are its
segments and captions. Each segment is a shot rendered by terminal.py, ui.py or
cards.py: PNG frames plus an ffconcat timeline, checked against its bars.

- **Crossfades** are centred on each bar line, so every cut stays on its bar and
  the totals do not change: the outgoing shot holds its last frame for HALF_FADE
  frames, the incoming shot its first, and the fade spans the join. Each segment
  is its own ffmpeg input, read on demand, so 1440p frames never pile up.
- **Captions** are drawn by captions.html through page.py, in the UI's Plex Sans,
  each a transparent full frame; every box is measured against its layout's
  safe area. They form one ffconcat track, laid over the video. The same table
  writes captions.srt (and captions-short.srt) for YouTube's captions track.

Silent for now: the music is the next step. Writes demo/build/gdi-demo-1440p.mp4,
demo/build/gdi-demo-short.mp4 and their .srt files.
"""

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from cdp import Chrome
from page import fill, font_faces, snap
from PIL import Image
from timeline import BAR, FPS, shot_frames, write_ffconcat

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "demo" / "build"
WORK = BUILD / "render"
HALF_FADE = 4  # frames: each crossfade lasts 8 (0.27 s), centred on the bar line


def ffmpeg_tool(name: str) -> str:
    """Homebrew's ffmpeg, which a fresh shell may not have on PATH yet."""
    found = shutil.which(name) or shutil.which(name, path="/opt/homebrew/bin")
    if found is None:
        raise SystemExit(f"render: {name} not found; brew install ffmpeg")
    return found


@dataclass(frozen=True)
class Segment:
    source: str  # "<renderer>/<layout>/<shot>" under demo/build
    bars: int


@dataclass(frozen=True)
class Caption:
    bar: int  # first bar, counted from 0
    bars: int  # at least 1: the plan's 2.5 s minimum is one bar
    lines: tuple[str, ...]  # at most two; "{took}" becomes the real run's time


@dataclass(frozen=True)
class Target:
    name: str
    output: str
    srt: str
    width: int  # device px
    height: int
    css: tuple[int, int]  # the CSS px a caption is drawn at
    scale: float
    # The caption box must stay inside (left, top, right, bottom) in CSS px:
    # the main video's bottom band below every shot; the Short's mid-frame, clear
    # of the bottom 20% and right 15% that YouTube's buttons cover.
    safe: tuple[int, int, int, int]
    maxrate: str  # a ceiling, not a target: plan step 5's 24 / 12 Mbps
    segments: tuple[Segment, ...]
    captions: tuple[Caption, ...]


MAIN = Target(
    "main",
    "gdi-demo-1440p.mp4",
    "captions.srt",
    2560,
    1440,
    (1280, 720),
    2,
    (40, 524, 1240, 720),
    "24M",
    (
        Segment("cards/main/title", 2),
        Segment("terminal/main/convert", 4),
        Segment("terminal/main/ls", 2),
        Segment("terminal/main/ui", 1),
        Segment("ui/main/document", 3),
        Segment("ui/main/tables", 2),
        Segment("ui/main/charts", 5),
        Segment("terminal/main/schema", 2),
        Segment("ui/main/kvp", 1),
        Segment("ui/main/chunks", 2),
        Segment("ui/main/run", 1),
        Segment("cards/main/end", 5),
    ),
    (
        Caption(0, 2, ("PDFs → structured data.", "Fully local on your Mac.")),
        Caption(2, 2, ("42 pages, 9 charts, one command",)),
        Caption(4, 2, ("Sped up — the real run took {took}",)),
        Caption(
            6, 2, ("Markdown, JSON, tables, chart CSVs,", "key-values, RAG chunks")
        ),
        Caption(8, 1, ("Or browse it in the UI",)),
        Caption(9, 3, ("The whole document as Markdown,", "in reading order")),
        Caption(12, 2, ("Tables extracted as data",)),
        Caption(14, 5, ("Charts → CSV + summary,", "read by Granite Vision")),
        Caption(19, 3, ("Fields from your own JSON Schema",)),
        Caption(22, 2, ("Chunks ready for RAG",)),
        Caption(24, 1, ("Every run recorded:", "models, versions, timings")),
    ),
)
SHORT = Target(
    "short",
    "gdi-demo-short.mp4",
    "captions-short.srt",
    1080,
    1920,
    (360, 640),
    3,
    (16, 380, 298, 512),
    "12M",
    (
        Segment("cards/short/open", 1),
        Segment("terminal/short/convert", 3),
        Segment("ui/short/charts", 5),
        Segment("ui/short/kvp", 3),
        Segment("ui/short/document", 1),
        Segment("ui/short/tables", 1),
        Segment("ui/short/chunks", 1),
        Segment("cards/short/end", 1),
    ),
    (
        Caption(0, 1, ("This chart is a picture", "in a PDF…")),
        Caption(1, 3, ("One command, on my Mac", "Sped up from {took}")),
        Caption(4, 5, ("…now it's a CSV", "and a summary")),
        Caption(9, 3, ("Plus fields from", "your own schema")),
        Caption(12, 3, ("Markdown, tables,", "RAG chunks")),
        Caption(15, 1, ("Open source ·", "full demo linked below")),
    ),
)
TARGETS = {"main": MAIN, "short": SHORT}


def check_shot(segment: Segment) -> Path:
    """The shot's ffconcat, after checking it lasts exactly its bars."""
    concat = BUILD / f"{segment.source}.ffconcat"
    if not concat.is_file():
        raise SystemExit(f"render: {concat} missing; run terminal.py, ui.py, cards.py")
    lines = concat.read_text(encoding="utf-8").splitlines()
    shown = sum(
        round(float(line.split()[1]) * FPS)
        for line in lines
        if line.startswith("duration ")
    )
    if shown != shot_frames(segment.bars):
        raise SystemExit(
            f"render: {segment.source} lasts {shown} frames, the storyboard says "
            f"{shot_frames(segment.bars)} ({segment.bars} bars)"
        )
    return concat


def run_took() -> str:
    """The staged run's wall time, for the "sped up" captions."""
    stage = json.loads((BUILD / "stage.json").read_text(encoding="utf-8"))
    return f"{stage['wall_seconds'] / 60:.1f} min"


def caption_track(chrome: Chrome, target: Target, total: int, took: str) -> Path:
    """Draw each caption, check its box, and write the track's ffconcat."""
    folder = WORK / f"captions-{target.name}"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    fonts = font_faces(("IBM Plex Sans", None))
    blank = Image.new("RGBA", (target.width, target.height), (0, 0, 0, 0))
    starts: list[int] = []
    position = 0
    for caption in sorted(target.captions, key=lambda c: c.bar):
        start = shot_frames(caption.bar)
        if start < position:
            raise SystemExit(f"render: a caption at bar {caption.bar} overlaps another")
        if start > position:  # no caption here: a transparent frame
            blank.save(folder / f"{len(starts):04d}.png")
            starts.append(position)
        lines = [line.format(took=took) for line in caption.lines]
        if not 1 <= len(lines) <= 2:
            raise SystemExit(f"render: a caption has {len(lines)} lines, not 1 or 2")
        out = folder / f"{len(starts):04d}.png"
        html = fill(
            ROOT / "demo" / "captions.html",
            fonts,
            {"layout": target.name, "lines": lines},
        )
        snap(
            chrome,
            html,
            WORK / "caption.html",
            out,
            target.css,
            target.scale,
            transparent=True,
        )
        box = chrome.evaluate(
            "(() => { const r = document.getElementById('box').getBoundingClientRect();"
            " return [r.left, r.top, r.right, r.bottom]; })()"
        )
        left, top, right, bottom = target.safe
        if box[0] < left or box[1] < top or box[2] > right or box[3] > bottom:
            raise SystemExit(f"render: caption {lines} at {box} leaves {target.safe}")
        starts.append(start)
        position = start + shot_frames(caption.bars)
    if position < total:
        blank.save(folder / f"{len(starts):04d}.png")
        starts.append(position)
    elif position > total:
        raise SystemExit("render: a caption runs past the end")
    track = WORK / f"{folder.name}.ffconcat"
    write_ffconcat(track, starts, total)
    return track


def write_srt(target: Target, took: str) -> Path:
    def stamp(frames: int) -> str:
        ms = round(frames / FPS * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    blocks = []
    for n, caption in enumerate(sorted(target.captions, key=lambda c: c.bar), 1):
        start = shot_frames(caption.bar)
        end = start + shot_frames(caption.bars)
        text = "\n".join(line.format(took=took) for line in caption.lines)
        blocks.append(f"{n}\n{stamp(start)} --> {stamp(end)}\n{text}\n")
    path = BUILD / target.srt
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def filter_graph(target: Target, captions_input: int) -> tuple[str, int]:
    """Each segment trimmed to its frames, edge-held, and crossfaded on its bar line."""
    parts: list[str] = []
    last = len(target.segments) - 1
    boundary = 0
    for i, segment in enumerate(target.segments):
        frames = shot_frames(segment.bars)
        pads = []
        if i > 0:
            pads.append(f"start_mode=clone:start={HALF_FADE}")
        if i < last:
            pads.append(f"stop_mode=clone:stop={HALF_FADE}")
        parts.append(
            f"[{i}:v]fps={FPS},format=gbrp,tpad=stop_mode=clone:stop_duration=2,"
            f"trim=end_frame={frames},setpts=N/({FPS}*TB)"
            + (f",tpad={':'.join(pads)}" if pads else "")
            + f"[s{i}]"
        )
        if i > 0:
            previous = "s0" if i == 1 else f"x{i - 1}"
            offset = (boundary - HALF_FADE) / FPS
            parts.append(
                f"[{previous}][s{i}]xfade=transition=fade:"
                f"duration={2 * HALF_FADE / FPS:.6f}:offset={offset:.6f}[x{i}]"
            )
        boundary += frames
    total = boundary
    video = f"x{last}" if last > 0 else "s0"
    parts.append(
        f"[{captions_input}:v]fps={FPS},format=rgba,tpad=stop_mode=clone:stop_duration=2,"
        f"trim=end_frame={total},setpts=N/({FPS}*TB)[cap]"
    )
    # sRGB to BT.709 limited range, tagged below, so the dark greys survive.
    parts.append(
        f"[{video}][cap]overlay=format=gbrp,"
        "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p[out]"
    )
    return ";".join(parts), total


def encode(target: Target, inputs: list[Path], graph: str, frames: int) -> Path:
    output = BUILD / target.output
    rate = int(target.maxrate.rstrip("M"))
    sources: list[str] = []
    for path in inputs:
        sources += ["-f", "concat", "-i", str(path)]
    subprocess.run(
        [
            ffmpeg_tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *sources,
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-preset",
            "slow",
            # Quality, not bitrate: most frames are held still, so an average
            # bitrate target undershot to ~0.9 Mbps and meant nothing. YouTube
            # re-encodes the upload, so the master is near-lossless; the files
            # stay a few MB. The ceiling only bounds the scrolls.
            "-crf",
            "10",
            "-maxrate",
            target.maxrate,
            "-bufsize",
            f"{rate * 2}M",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
    )
    return output


def probe(path: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            ffmpeg_tool("ffprobe"),
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_read_frames,bit_rate:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    stream, fmt = data["streams"][0], data["format"]
    return {
        "size": f"{stream['width']}x{stream['height']}",
        "fps": stream["r_frame_rate"],
        "frames": int(stream["nb_read_frames"]),
        "seconds": round(float(fmt["duration"]), 3),
        "mbps": round(int(fmt["size"]) * 8 / float(fmt["duration"]) / 1e6, 1),
        "mb": round(int(fmt["size"]) / 1e6, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("target", nargs="?", default="all", choices=[*TARGETS, "all"])
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    took = run_took()
    targets = TARGETS.values() if args.target == "all" else [TARGETS[args.target]]
    failed = False
    with Chrome() as chrome:
        for target in targets:
            shots = [check_shot(segment) for segment in target.segments]
            graph, total = filter_graph(target, captions_input=len(shots))
            expected = round(sum(s.bars for s in target.segments) * BAR * FPS)
            track = caption_track(chrome, target, total, took)
            srt = write_srt(target, took)
            output = encode(target, [*shots, track], graph, total)
            info = probe(output)
            ok = info["frames"] == total == expected
            failed |= not ok
            print(f"render: {output.relative_to(ROOT)}: {json.dumps(info)}")
            print(
                f"render: {total} frames, {expected} in the storyboard: "
                f"{'ok' if ok else 'MISMATCH'}; {len(target.captions)} captions, "
                f"{len(shots) - 1} crossfades; {srt.relative_to(ROOT)}"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

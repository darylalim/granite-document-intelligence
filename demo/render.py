"""Step 5 of the demo videos: assemble the shots into the two videos with ffmpeg.

    .venv/bin/python demo/render.py [main|short|all]

docs/plans/demo-video.md has the storyboard; MAIN and SHORT below are its segments.
Each segment is a shot rendered by terminal.py, ui.py or cards.py (frames plus
an ffconcat timeline), or a gap: plain black for its exact length, kept for
roughing in a shot that is not built yet, so the cuts still match the storyboard.

For now the shots are joined end to end, silent, with no captions, crossfades
or music. It checks that the timelines encode to the
storyboard's lengths frame for frame, before any of that is layered on.

All segments go into one ffconcat, so ffmpeg reads each PNG once and encodes
once. Writes demo/build/gdi-demo-1440p.mp4 and demo/build/gdi-demo-short.mp4.
"""

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from timeline import BAR, FPS, shot_frames

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "demo" / "build"
WORK = BUILD / "render"


def ffmpeg_tool(name: str) -> str:
    """Homebrew's ffmpeg, which a fresh shell may not have on PATH yet."""
    found = shutil.which(name) or shutil.which(name, path="/opt/homebrew/bin")
    if found is None:
        raise SystemExit(f"render: {name} not found; brew install ffmpeg")
    return found


@dataclass(frozen=True)
class Segment:
    source: str  # "<renderer>/<layout>/<shot>" under demo/build, or "" for a gap
    bars: int
    note: str  # what fills a gap later


@dataclass(frozen=True)
class Target:
    name: str
    output: str
    width: int
    height: int
    maxrate: str  # a ceiling, not a target: plan step 5's 24 / 12 Mbps
    segments: tuple[Segment, ...]


MAIN = Target(
    "main",
    "gdi-demo-1440p.mp4",
    2560,
    1440,
    "24M",
    (
        Segment("cards/main/title", 2, ""),
        Segment("terminal/main/convert", 4, ""),
        Segment("terminal/main/ls", 2, ""),
        Segment("terminal/main/ui", 1, ""),
        Segment("ui/main/document", 3, ""),
        Segment("ui/main/tables", 2, ""),
        Segment("ui/main/charts", 5, ""),
        Segment("terminal/main/schema", 2, ""),
        Segment("ui/main/kvp", 1, ""),
        Segment("ui/main/chunks", 2, ""),
        Segment("ui/main/run", 1, ""),
        Segment("cards/main/end", 5, ""),
    ),
)
SHORT = Target(
    "short",
    "gdi-demo-short.mp4",
    1080,
    1920,
    "12M",
    (
        Segment("cards/short/open", 1, ""),
        Segment("terminal/short/convert", 3, ""),
        Segment("ui/short/charts", 5, ""),
        Segment("ui/short/kvp", 3, ""),
        Segment("ui/short/document", 1, ""),
        Segment("ui/short/tables", 1, ""),
        Segment("ui/short/chunks", 1, ""),
        Segment("cards/short/end", 1, ""),
    ),
)
TARGETS = {"main": MAIN, "short": SHORT}


def shot_entries(source: str, bars: int) -> list[tuple[str, int]]:
    """A shot's (PNG path relative to BUILD, frames shown), checked against its bars."""
    concat = BUILD / f"{source}.ffconcat"
    if not concat.is_file():
        raise SystemExit(f"render: {concat} missing; run terminal.py, ui.py, cards.py")
    entries: list[tuple[str, int]] = []
    lines = concat.read_text(encoding="utf-8").splitlines()[1:]
    # Pairs of `file` and `duration`; the final repeated `file` has no duration.
    for file_line, duration_line in zip(lines[0::2], lines[1::2], strict=False):
        name = file_line.split("'")[1]
        seconds = float(duration_line.split()[1])
        entries.append((f"{Path(source).parent}/{name}", round(seconds * FPS)))
    shown = sum(frames for _, frames in entries)
    if shown != shot_frames(bars):
        raise SystemExit(
            f"render: {source} lasts {shown} frames, the storyboard says "
            f"{shot_frames(bars)} ({bars} bars)"
        )
    return entries


def gap_frame(target: Target) -> str:
    path = WORK / f"gap-{target.name}.png"
    Image.new("RGB", (target.width, target.height), (0, 0, 0)).save(path)
    return str(path.relative_to(BUILD))


def build_concat(target: Target) -> tuple[Path, int, list[dict[str, object]]]:
    entries: list[tuple[str, int]] = []
    timeline: list[dict[str, object]] = []
    for segment in target.segments:
        start = sum(frames for _, frames in entries)
        if segment.source:
            entries += shot_entries(segment.source, segment.bars)
        else:
            entries.append((gap_frame(target), shot_frames(segment.bars)))
        timeline.append(
            {
                "from": start / FPS,
                "to": sum(frames for _, frames in entries) / FPS,
                "shot": segment.source or f"(gap: {segment.note})",
            }
        )
    lines = ["ffconcat version 1.0"]
    for name, frames in entries:
        lines += [f"file '{name}'", f"duration {frames / FPS:.9f}"]
    lines.append(f"file '{entries[-1][0]}'")
    # In BUILD, so every path in it is relative and inside it (ffconcat's safe mode).
    path = BUILD / f"render-{target.name}.ffconcat"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, sum(frames for _, frames in entries), timeline


def encode(target: Target, concat: Path, frames: int) -> Path:
    output = BUILD / target.output
    rate = int(target.maxrate.rstrip("M"))
    subprocess.run(
        [
            ffmpeg_tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-i",
            str(concat),
            # Constant 30 fps; sRGB PNGs to BT.709 limited range, tagged as such,
            # so the dark theme's greys survive YouTube's player.
            "-vf",
            f"fps={FPS},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
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
    failed = False
    for target in TARGETS.values() if args.target == "all" else [TARGETS[args.target]]:
        concat, frames, timeline = build_concat(target)
        expected = round(sum(s.bars for s in target.segments) * BAR * FPS)
        output = encode(target, concat, frames)
        info = probe(output)
        ok = info["frames"] == frames == expected
        failed |= not ok
        print(f"render: {output.relative_to(ROOT)}: {json.dumps(info)}")
        print(
            f"render: {frames} frames planned, {expected} in the storyboard: "
            f"{'ok' if ok else 'MISMATCH'}"
        )
        for row in timeline:
            print(f"  {row['from']:6.1f}–{row['to']:5.1f} s  {row['shot']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

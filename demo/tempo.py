"""Measure candidate music tracks for the demo videos, or make a test click track.

    .venv/bin/python demo/tempo.py demo/assets/*.mp3
    .venv/bin/python demo/tempo.py --click demo/build/click.wav

docs/plans/demo-video.md, *Music*: every cut sits on a 2.5 s bar, which is one
4/4 bar at 96 BPM, and listed BPMs are sometimes missing or wrong. So each track
is measured with aubio (`brew install aubio`): its tempo, its beat period from
the detected beats, and where the beats fall. aubio finds beats, not bar starts,
so the four beat phases of a bar are each offered as `render.py --offset`;
which one is the downbeat is a judgement by ear.

`--click` writes a click track at a known tempo and lead-in, a 1600 Hz click on
each downbeat and 1000 Hz on the other beats over a quiet drone, so render.py's music path can be
checked against exact beat times before a real track is chosen.
"""

import argparse
import math
import statistics
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

from render import ffmpeg_tool
from timeline import BAR

BPM = 4 * 60 / BAR  # 96: the storyboard's tempo
TOLERANCE = 2.0  # BPM either way (plan, Music)
STEADY = 0.1  # RMS miss of the fitted grid, in beats
# The fit's range: one octave, so a tempo and its half or double are never both
# candidates (a period always fits at least as well as twice it). 80-125 first
# pinned seven Audio Library tracks at its 125 BPM edge.
LOW_BPM, HIGH_BPM = 70.0, 139.9
NEEDS = 75.0  # seconds of music after the offset: the main video's length


def aubio(*args: str) -> str:
    tool = ffmpeg_tool("aubio")  # the same PATH fallback as ffmpeg's
    return subprocess.run(
        [tool, *args], capture_output=True, text=True, check=True
    ).stdout


def duration(path: Path) -> float:
    result = subprocess.run(
        [
            ffmpeg_tool("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout)


def fit_period(beats: list[float]) -> tuple[float, float]:
    """The beat period that best explains every interval as whole beats.

    aubio's beat tracker skips beats (on a clean click track its intervals fall
    at 1, 3 and 4 beats), so neither its tempo nor a median interval holds. Each
    candidate period from LOW_BPM to HIGH_BPM is scored by how far each interval
    lies from a whole number of periods; the range is one octave, so it excludes
    half and double tempo.
    Returns the period and its RMS miss, as a fraction of a beat.
    """
    intervals = [b - a for a, b in pairwise(beats)]
    best = (float("inf"), 0.0)
    for step in range(int(60 / HIGH_BPM / 0.0001) + 1, int(60 / LOW_BPM / 0.0001) + 1):
        period = step * 0.0001
        misses = [(i / period - max(1, round(i / period))) ** 2 for i in intervals]
        best = min(best, (math.sqrt(statistics.fmean(misses)), period))
    return best[1], best[0]


def phase(beats: list[float], period: float) -> float:
    """Where in the period the beats fall, as a circular mean, in seconds."""
    angles = [2 * math.pi * (b % period) / period for b in beats]
    mean = math.atan2(
        statistics.fmean(math.sin(a) for a in angles),
        statistics.fmean(math.cos(a) for a in angles),
    )
    return (mean % (2 * math.pi)) / (2 * math.pi) * period


def measure(path: Path) -> bool:
    """Print one track's tempo and offsets; return whether it suits the edit."""
    reported = float(aubio("tempo", str(path)).split()[0])
    beats = [float(x) for x in aubio("beat", str(path)).split()]
    if len(beats) < 8:
        print(f"{path.name}: only {len(beats)} beats found; not usable")
        return False
    period, miss = fit_period(beats)
    bpm = 60 / period
    first = phase(beats, period)
    length = duration(path)
    on_tempo = abs(bpm - BPM) <= TOLERANCE
    steady = miss < STEADY
    at_edge = min(abs(bpm - LOW_BPM), abs(bpm - HIGH_BPM)) < 0.5
    ok = on_tempo and steady and not at_edge
    print(
        f"{path.name}: {bpm:.2f} BPM fitted (beats every {period:.4f} s, "
        f"RMS miss {miss:.3f} beat; aubio's own estimate {reported:.2f})"
    )
    print(f"  length {length:.1f} s; offsets to try, one per beat of the bar:")
    for k in range(4):
        offset = first + k * period
        room = length - offset
        note = (
            ""
            if room >= NEEDS
            else f"  (only {room:.1f} s after it; needs {NEEDS:.0f})"
        )
        print(f"    --offset {offset:.3f}{note}")
    if at_edge:
        print(
            f"  the fit hit the edge of {LOW_BPM:g}-{HIGH_BPM:g} BPM: the tempo is outside it"
        )
    if not on_tempo:
        print(
            f"  not {BPM:.0f} ± {TOLERANCE:.0f} BPM: the cuts would drift off its bars"
        )
    if not steady:
        print(
            f"  beats miss the fitted grid by {miss:.2f} of a beat (limit {STEADY}): unsteady,"
        )
        print("  or aubio is not finding its beats; check the offsets by ear")
    return ok


def click(path: Path, bpm: float, lead: float, seconds: float) -> None:
    period = 60 / bpm
    phase = f"mod(t-{lead},{period})"
    beat = f"floor((t-{lead})/{period})"
    tick = (
        f"if(gte(t,{lead})*lt({phase},0.04),"
        f"0.5*exp(-{phase}*100)*sin(2*PI*if(eq(mod({beat},4),0),1600,1000)*t),0)"
    )
    # A quiet drone under the clicks, so the track's peaks stand over its
    # loudness about as music's do; clicks alone cannot reach -14 LUFS under a
    # -1 dBTP ceiling, so render.py's loudness check would fail on them.
    drone = "0.08*sin(2*PI*110*t)+0.06*sin(2*PI*165*t)+0.04*sin(2*PI*220*t)"
    expr = f"{tick}+{drone}"
    subprocess.run(
        [
            ffmpeg_tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=exprs='{expr}|{expr}':s=48000:d={seconds}",
            str(path),
        ],
        check=True,
    )
    print(
        f"tempo: wrote {path}: {bpm:g} BPM, first downbeat at {lead:g} s, {seconds:g} s"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("tracks", nargs="*", type=Path)
    parser.add_argument("--click", type=Path, help="write a test click track here")
    parser.add_argument("--bpm", type=float, default=BPM)
    parser.add_argument("--lead", type=float, default=0.3, help="seconds before beat 1")
    parser.add_argument("--seconds", type=float, default=90.0)
    args = parser.parse_args()
    if args.click:
        click(args.click, args.bpm, args.lead, args.seconds)
        return 0
    if not args.tracks:
        parser.error("give tracks to measure, or --click")
    results = [measure(track) for track in args.tracks]
    return 0 if any(results) else 1


if __name__ == "__main__":
    sys.exit(main())

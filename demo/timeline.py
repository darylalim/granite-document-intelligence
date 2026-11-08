"""Frame timing shared by the demo's shot renderers (docs/plans/demo-video.md).

Every shot lasts a whole number of 2.5 s bars (96 BPM, plan *Music*), and every
change on screen lands on a 30 fps frame. A shot is written as one PNG per
distinct frame plus an ffconcat timeline giving each PNG's duration, which
`render.sh` encodes.
"""

from pathlib import Path

from PIL import Image

FPS = 30
BAR = 2.5  # one 4/4 bar at 96 BPM


def shot_frames(bars: int) -> int:
    return round(bars * BAR * FPS)


def write_ffconcat(path: Path, starts: list[int], total: int) -> list[float]:
    """Write a timeline for PNGs 0000.png… shown from frames `starts` to `total`.

    Returns each PNG's duration in seconds; they sum to exactly total / FPS.
    """
    if not starts or starts[0] != 0 or starts != sorted(set(starts)):
        raise ValueError("starts must be increasing and begin at frame 0")
    if starts[-1] >= total:
        raise ValueError("a PNG starts at or after the shot's end")
    lines = ["ffconcat version 1.0"]
    durations: list[float] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else total
        durations.append((end - start) / FPS)
        lines += [f"file '{path.stem}/{i:04d}.png'", f"duration {durations[-1]:.6f}"]
    # ffconcat ignores the last entry's duration unless the file is listed again.
    lines.append(f"file '{path.stem}/{len(starts) - 1:04d}.png'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return durations


def preview(path: Path, shots: list[tuple[Path, list[float]]]) -> None:
    """A downscaled GIF of the shots in order, for a check without ffmpeg."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    for folder, shot_durations in shots:
        for i, seconds in enumerate(shot_durations):
            with Image.open(folder / f"{i:04d}.png") as image:
                small = image.convert("RGB")
                small.thumbnail((640, 640))
                frames.append(small)
            durations.append(max(20, round(seconds * 1000)))
    frames[0].save(
        path, save_all=True, append_images=frames[1:], duration=durations, loop=0
    )

"""Step 2 of the demo videos: the command-line shots, as frames and timelines.

    .venv/bin/python demo/terminal.py

docs/plans/demo-video.md has the storyboard. Each shot is a terminal window
drawn by `terminal.html` in headless Chrome, one screenshot per distinct
screen, plus an ffconcat timeline that `render.py` turns into video. Every
shot lasts a whole number of 2.5 s bars, and every change lands on a 30 fps frame.

What appears on screen is real output, never written by hand:
- `gdi convert`: stage.sh's recorded transcript, replayed with its timing
  compressed to fit the shot. Only lines in gdi's own progress format are kept;
  library noise (a torch warning, a weights progress bar, a transformers
  deprecation notice) is dropped, and each dropped line is listed on stderr.
- `ls -F` runs for real against the staged tree. `tree` is not on a stock Mac.
- `jq '.properties | map_values(.description)' schema.json` runs for real, with
  the jq macOS ships, to show the KVP schema before its results.
- `gdi ui --no-browser` runs for real on the default port, and its output up to
  the URL line is shown at its own timing.

Writes demo/build/terminal/<layout>/<shot>/NNNN.png and <shot>.ffconcat, plus
meta.json (each shot's length and the replay's speed-up) and a downscaled
preview.gif per layout, for checking without ffmpeg.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cdp import Chrome
from timeline import BAR, FPS, preview, shot_frames, write_ffconcat

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "demo" / "build"
HOME = BUILD / "home"
OUT = BUILD / "terminal"
PACKAGE = ROOT / "src" / "granite_document_intelligence" / "ui"
CACHE = Path(
    os.environ.get("GDI_CACHE_DIR")
    or Path.home() / ".cache" / "granite-document-intelligence"
)

PROMPT = "~ % "
TYPE_FRAMES = 1  # frames per typed character
CONVERT = "gdi convert gao-07-631.pdf --schema schema.json -o gdi-out"
LS = "ls -F gdi-out/gao-07-631"
UI = "gdi ui"
SCHEMA_FILTER = ".properties | map_values(.description)"
SCHEMA = f"jq '{SCHEMA_FILTER}' schema.json"

# gdi's own progress lines (batch.py, phase_a.py, charts.py, kvp.py); all else drops.
GDI_LINE = re.compile(
    r"^(\[\d+/\d+\] "
    r"|  (page|chart|key-values) \d+/\d+"
    r"|  done in "
    r"|\d+ done, \d+ skipped, \d+ failed"
    r"|  (done|skipped|failed) +\S)"
)


@dataclass(frozen=True)
class Layout:
    name: str
    width: int  # CSS px
    height: int
    scale: float  # device pixels per CSS px
    cols: int
    rows: int
    font_size: int
    line_height: int
    top: int  # the window's top edge; the bottom is left for captions
    padding: int
    bar_height: int


# 2560 x 1440. The window ends at 524 of 720 CSS px, clear of the captions.
MAIN = Layout("main", 1280, 720, 2, 90, 14, 20, 28, 40, 28, 36)
# 1080 x 1920. 360 x 640 at 3x is exact, where 390 px wide cannot reach 1080 x 1920
# with a whole height. The window ends at 363 of 640, above the middle-of-frame
# captions and clear of the bottom 20% that YouTube's buttons cover.
SHORT = Layout("short", 360, 640, 3, 40, 14, 13, 18, 56, 12, 28)

Line = tuple[str, bool]  # text, starts with the prompt


@dataclass
class Screen:
    lines: list[Line]
    cursor: bool


@dataclass
class Shot:
    name: str
    bars: int
    events: list[tuple[float, Screen]]
    note: dict[str, object]


class Session:
    """The terminal's scrollback, recorded as screens over time."""

    def __init__(self) -> None:
        self.lines: list[Line] = []

    def screen(self, cursor: bool) -> Screen:
        return Screen(list(self.lines), cursor)

    def prompt(self, events: list[tuple[float, Screen]], t: float) -> None:
        self.lines.append((PROMPT, True))
        events.append((t, self.screen(cursor=True)))

    def type(self, events: list[tuple[float, Screen]], t: float, text: str) -> float:
        """Type `text` at the prompt from time `t`; return the time of the Enter."""
        for i in range(1, len(text) + 1):
            t += TYPE_FRAMES / FPS
            self.lines[-1] = (PROMPT + text[:i], True)
            events.append((t, self.screen(cursor=True)))
        t += 4 / FPS
        events.append((t, self.screen(cursor=False)))
        return t

    def output(self, events: list[tuple[float, Screen]], t: float, line: str) -> None:
        self.lines.append((line, False))
        events.append((t, self.screen(cursor=False)))


def transcript() -> tuple[list[tuple[float, str]], float]:
    """gdi's own lines from stage.sh's record, and the run's wall time."""
    kept: list[tuple[float, str]] = []
    for raw in (BUILD / "transcript.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(raw)
        if GDI_LINE.match(record["line"]):
            kept.append((record["t"], record["line"]))
        else:
            print(f"terminal: dropped {record['line'][:100]!r}", file=sys.stderr)
    stage = json.loads((BUILD / "stage.json").read_text(encoding="utf-8"))
    return kept, float(stage["wall_seconds"])


def convert_shot(
    session: Session,
    bars: int,
    hold: float,
    lines: list[tuple[float, str]],
    wall: float,
) -> Shot:
    events: list[tuple[float, Screen]] = []
    session.prompt(events, 0.0)
    t = session.type(events, 0.3, CONVERT)
    start, end = t + 0.2, bars * BAR - hold
    first, last = lines[0][0], lines[-1][0]
    speed = (last - first) / (end - start)
    for when, line in lines:
        session.output(events, start + (when - first) / speed, line)
    return Shot(
        "convert",
        bars,
        events,
        {"speedup": round(speed, 1), "real_seconds": wall, "shown_seconds": end - t},
    )


def ls_lines(cols: int) -> list[str]:
    result = subprocess.run(
        ["ls", "-C", "-F", "gdi-out/gao-07-631"],
        cwd=HOME,
        env={**os.environ, "COLUMNS": str(cols)},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.rstrip("\n").split("\n")


def schema_lines() -> list[str]:
    """The KVP schema's fields and descriptions, by the real jq macOS ships."""
    result = subprocess.run(
        ["/usr/bin/jq", SCHEMA_FILTER, "schema.json"],
        cwd=HOME,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.rstrip("\n").split("\n")


def ui_lines() -> list[tuple[float, str]]:
    """`gdi ui`'s real startup output, timed, up to and including the URL line."""
    env = {**os.environ, "HOME": str(HOME), "GDI_CACHE_DIR": str(CACHE)}
    process = subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "gdi"), "ui", "--no-browser"],
        cwd=HOME,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    start = time.monotonic()
    lines: list[tuple[float, str]] = []
    try:
        for raw in process.stdout:
            lines.append((time.monotonic() - start, raw.rstrip("\n")))
            if "URL:" in raw:
                break
            if time.monotonic() - start > 30:
                raise RuntimeError("gdi ui printed no URL within 30 s")
    finally:
        process.terminate()
        process.wait(timeout=10)
    if not lines or "URL:" not in lines[-1][1]:
        raise RuntimeError(f"gdi ui exited without a URL: {lines}")
    return lines


def ls_shot(session: Session, bars: int, cols: int) -> Shot:
    events: list[tuple[float, Screen]] = []
    session.prompt(events, 0.0)
    t = session.type(events, 0.3, LS)
    for line in ls_lines(cols):
        session.output(events, t + 2 / FPS, line)
    return Shot("ls", bars, events, {})


def schema_shot(bars: int) -> Shot:
    """The schema before its results (storyboard, Key-values): a fresh window."""
    session = Session()
    events: list[tuple[float, Screen]] = []
    session.prompt(events, 0.0)
    t = session.type(events, 0.2, SCHEMA)
    for line in schema_lines():
        session.output(events, t + 2 / FPS, line)
    return Shot("schema", bars, events, {})


def ui_shot(session: Session, bars: int, lines: list[tuple[float, str]]) -> Shot:
    events: list[tuple[float, Screen]] = []
    session.prompt(events, 0.0)
    t = session.type(events, 0.2, UI)
    available = bars * BAR - 0.6 - t  # leave the URL on screen for 0.6 s
    span = lines[-1][0] or 1.0
    speed = max(1.0, span / available)
    for when, line in lines:
        session.output(events, t + 0.1 + when / speed, line)
    return Shot("ui", bars, events, {"speedup": round(speed, 2)})


def wrap(lines: list[Line], cols: int, rows: int) -> list[Line]:
    """Wrap at the terminal width and keep the last rows.

    A real terminal hard-wraps mid-word, which at the Short's 40 columns splits
    "3 tables" and "gao-07-631" across lines. Wrapping between words changes
    only where lines break, never the text.
    """
    out: list[Line] = []
    for text, is_prompt in lines:
        # A line that fits is kept whole: wrap() would strip the prompt's space.
        chunks = (
            [text]
            if len(text) <= cols
            else textwrap.wrap(text, cols, break_on_hyphens=False)
        )
        out.append((chunks[0], is_prompt))
        out.extend((chunk, False) for chunk in chunks[1:])
    return out[-rows:]


def font_faces() -> str:
    config = tomllib.loads((PACKAGE / ".streamlit" / "config.toml").read_text())
    css = []
    for face in config["theme"]["fontFaces"]:
        if face["family"] != "IBM Plex Mono" or face["weight"] != 400:
            continue
        path = PACKAGE / "static" / "fonts" / Path(face["url"]).name
        data = base64.b64encode(path.read_bytes()).decode()
        css.append(
            "@font-face { font-family: 'IBM Plex Mono'; font-weight: 400; "
            f"src: url(data:font/woff2;base64,{data}) format('woff2'); "
            f"unicode-range: {face['unicodeRange']}; }}"
        )
    return "\n".join(css)


def page(layout: Layout) -> str:
    html = (ROOT / "demo" / "terminal.html").read_text(encoding="utf-8")
    variables = (
        f":root {{ --font-size: {layout.font_size}px; "
        f"--line-height: {layout.line_height}px; --cols: {layout.cols}; "
        f"--rows: {layout.rows}; --top: {layout.top}px; "
        f"--padding: {layout.padding}px; --bar-height: {layout.bar_height}px; }}"
    )
    script = f"<script>const PROMPT = {json.dumps(PROMPT)};</script>"
    return html.replace("/*FONT_FACES*/", font_faces() + "\n" + variables).replace(
        "<script>", script + "\n<script>", 1
    )


def render(chrome: Chrome, layout: Layout, shot: Shot, folder: Path) -> list[float]:
    """Screenshot each distinct frame; write the ffconcat; return the durations."""
    total = shot_frames(shot.bars)
    by_frame: dict[int, Screen] = {}
    for t, screen in shot.events:
        frame = round(t * FPS)
        if not 0 <= frame < total:
            raise ValueError(f"{shot.name}: an event at {t:.2f} s is outside the shot")
        by_frame[frame] = screen  # the last state in a frame wins
    frames = sorted(by_frame)
    if frames[0] != 0:
        raise ValueError(f"{shot.name}: nothing on screen at 0 s")
    folder.mkdir(parents=True)
    for i, frame in enumerate(frames):
        screen = by_frame[frame]
        lines = wrap(screen.lines, layout.cols, layout.rows)
        chrome.evaluate(f"show({json.dumps(lines)}, {json.dumps(screen.cursor)})")
        chrome.screenshot(folder / f"{i:04d}.png")
    return write_ffconcat(folder.parent / f"{shot.name}.ffconcat", frames, total)


def main() -> int:
    if not (BUILD / "stage.json").is_file():
        print("terminal: run demo/stage.sh first", file=sys.stderr)
        return 1
    lines, wall = transcript()
    ui = ui_lines()
    shutil.rmtree(OUT, ignore_errors=True)
    plan = {
        # main: 4 + 2 + 1 bars (5-22.5 s); the scrollback carries across the cuts
        MAIN: lambda: _main_shots(lines, wall, ui),
        # Short: 3 bars (2.5-10 s)
        SHORT: lambda: [convert_shot(Session(), 3, 1.5, lines, wall)],
    }
    meta: dict[str, dict[str, object]] = {}
    with Chrome() as chrome:
        for layout, build in plan.items():
            chrome.viewport(layout.width, layout.height, layout.scale)
            source = BUILD / f"terminal-{layout.name}.html"
            source.write_text(page(layout), encoding="utf-8")
            chrome.navigate(source.as_uri())
            rendered = []
            for shot in build():
                folder = OUT / layout.name / shot.name
                durations = render(chrome, layout, shot, folder)
                rendered.append((folder, durations))
                meta[f"{layout.name}/{shot.name}"] = {
                    "seconds": shot.bars * BAR,
                    "frames": len(durations),
                    **shot.note,
                }
            preview(OUT / layout.name / "preview.gif", rendered)
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


def _main_shots(
    lines: list[tuple[float, str]], wall: float, ui: list[tuple[float, str]]
) -> list[Shot]:
    session = Session()
    return [
        convert_shot(session, 4, 2.0, lines, wall),
        ls_shot(session, 2, MAIN.cols),
        ui_shot(session, 1, ui),
        # Cut to after the UI's Charts shot, so a window of its own.
        schema_shot(2),
    ]


if __name__ == "__main__":
    sys.exit(main())

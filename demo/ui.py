"""Step 3 of the demo videos: the UI shots, as frames and timelines.

    .venv/bin/python demo/ui.py

docs/plans/demo-video.md has the storyboard. This serves the staged tree with
the real `gdi ui --no-browser` under stage.sh's scratch HOME, so the sidebar
reads ~/gdi-out, and drives headless Chrome in dark mode through `cdp.py`.

Each shot opens one view by its link (`?view=Charts`), waits for the README
screenshots' readiness signals (four count metrics and every /media/ image
loaded), and then scrolls the page's main column along a list of keyframes. A
PNG is taken only when the scroll position changes, and the timeline holds
each one for as long as it stays on screen. Nothing on the page is altered: the
script only scrolls.

Writes demo/build/ui/<main|short>/<shot>/NNNN.png and <shot>.ffconcat, plus
meta.json and a downscaled preview.gif per layout.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from cdp import Chrome
from timeline import BAR, FPS, preview, shot_frames, write_ffconcat

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "demo" / "build"
HOME = BUILD / "home"
OUT = BUILD / "ui"
CACHE = Path(
    os.environ.get("GDI_CACHE_DIR")
    or Path.home() / ".cache" / "granite-document-intelligence"
)

# Where a scroll keyframe points: the top of the page, or an element's top (a
# CSS selector), plus an offset in CSS px.
TOP = ""
MARGIN = 16
PILLS = ".st-key-view"  # the six view pills (app.py's `key="view"`)


@dataclass(frozen=True)
class Key:
    t: float  # seconds into the shot
    anchor: str  # TOP, or a selector for the element to show
    offset: float = 0.0  # CSS px further down the page than the resting position
    # "top": the element's top rests MARGIN below Streamlit's fixed header, which
    # covers the top of the page. "bottom": its bottom rests at the frame's
    # bottom edge, so a positive offset lifts it clear of the captions.
    edge: str = "top"


@dataclass(frozen=True)
class Shot:
    name: str
    view: str  # the pill's label, as the link carries it
    bars: int
    keys: tuple[Key, ...]  # eased between; held before the first and after the last


@dataclass(frozen=True)
class Layout:
    name: str
    width: int
    height: int
    scale: float
    shots: tuple[Shot, ...]


# Main video, 22.5-62.5 s (plan, storyboard). The bottom ~27% of the frame
# carries the captions, so each resting position keeps the subject above it.
MAIN = Layout(
    "main",
    1280,
    720,
    2,
    (
        # Opens at the top, as the browser does, then scrolls into the document.
        Shot(
            "document",
            "Document",
            3,
            (
                Key(0.8, TOP),
                Key(2.4, PILLS),
                Key(7.2, PILLS, 1100),
            ),
        ),
        Shot("tables", "Tables", 2, (Key(0.6, PILLS), Key(4.4, PILLS, 520))),
        Shot(
            "charts",
            "Charts",
            5,
            (
                Key(1.5, ".st-key-card_chart-001"),
                Key(11.5, ".st-key-card_chart-001", 200, "bottom"),
            ),
        ),
        # After terminal.py's 2-bar schema shot, the same four fields filled in.
        Shot("kvp", "Key-values", 1, (Key(0.0, PILLS),)),
        Shot("chunks", "Chunks", 2, (Key(0.6, PILLS), Key(4.4, PILLS, 260))),
        Shot("run", "Run", 1, (Key(0.0, PILLS),)),
    ),
)

# The Short, 10-37.5 s, at 360 x 640 CSS px x 3 = 1080 x 1920 (as terminal.py).
# Captions sit mid-frame, and YouTube's buttons cover the bottom 20%.
SHORT = Layout(
    "short",
    360,
    640,
    3,
    (
        Shot(
            "charts",
            "Charts",
            5,
            (
                Key(1.5, ".st-key-card_chart-001"),
                Key(11.5, ".st-key-card_chart-001", 140, "bottom"),
            ),
        ),
        Shot("kvp", "Key-values", 3, (Key(0.6, PILLS), Key(5.5, PILLS, 360))),
        Shot("document", "Document", 1, (Key(0.0, PILLS), Key(2.3, PILLS, 300))),
        Shot("tables", "Tables", 1, (Key(0.0, PILLS), Key(2.3, PILLS, 200))),
        Shot("chunks", "Chunks", 1, (Key(0.0, PILLS), Key(2.3, PILLS, 200))),
    ),
)

# The page's scroll container (Streamlit 1.66), and the readiness check the
# README screenshots used: four count metrics, every /media/ image decoded.
MAIN_COLUMN = "document.querySelector('[data-testid=stMain]')"
READY = """(() => {
  const media = [...document.images].filter(i => i.src.includes('/media/'));
  return document.querySelectorAll('[data-testid=stMetricValue]').length === 4
    && media.every(i => i.complete && i.naturalWidth > 0);
})()"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def ui_server() -> Generator[int]:
    """The real `gdi ui` over the staged tree, under stage.sh's scratch HOME."""
    port = free_port()
    env = {**os.environ, "HOME": str(HOME), "GDI_CACHE_DIR": str(CACHE)}
    process = subprocess.Popen(
        [
            str(ROOT / ".venv" / "bin" / "gdi"),
            "ui",
            "--no-browser",
            "--port",
            str(port),
        ],
        cwd=HOME,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health"):
                    break
            except OSError:
                if time.monotonic() > deadline or process.poll() is not None:
                    raise RuntimeError("gdi ui did not start") from None
                time.sleep(0.2)
        yield port
    finally:
        process.terminate()
        process.wait(timeout=10)


def open_view(chrome: Chrome, port: int, view: str) -> None:
    chrome.navigate(f"http://localhost:{port}/?view={view}")
    deadline = time.monotonic() + 30
    while not chrome.evaluate(READY):
        if time.monotonic() > deadline:
            raise RuntimeError(f"the {view} view did not finish loading")
        time.sleep(0.2)
    chrome.evaluate("document.fonts.ready.then(() => true)")
    time.sleep(1.5)  # dataframes draw on a canvas after their data arrives


def resolve(chrome: Chrome, key: Key) -> float:
    """A keyframe's scrollTop in CSS px, clamped to what the page can scroll."""
    script = f"""(() => {{
      const main = {MAIN_COLUMN};
      let y = 0;
      const anchor = {json.dumps(key.anchor)};
      if (anchor) {{
        const el = document.querySelector(anchor);
        if (!el) return null;
        const box = el.getBoundingClientRect();
        const origin = main.getBoundingClientRect().top - main.scrollTop;
        if ({json.dumps(key.edge)} === "bottom") {{
          y = box.bottom - origin - main.clientHeight;
        }} else {{
          const head = document.querySelector("[data-testid=stHeader]");
          const covered = head ? head.getBoundingClientRect().bottom
            - main.getBoundingClientRect().top : 0;
          y = box.top - origin - Math.max(0, covered) - {MARGIN};
        }}
      }}
      return Math.max(0, Math.min(y + {key.offset}, main.scrollHeight - main.clientHeight));
    }})()"""
    y = chrome.evaluate(script)
    if y is None:
        raise RuntimeError(f"nothing matches {key.anchor!r}")
    return float(y)


def ease(u: float) -> float:
    return u * u * (3 - 2 * u)  # smoothstep: no jolt at either end


def positions(keys: list[tuple[float, float]], total: int) -> list[float]:
    """scrollTop for every frame of the shot."""
    out: list[float] = []
    for frame in range(total):
        t = frame / FPS
        if t <= keys[0][0]:
            out.append(keys[0][1])
            continue
        for (t0, y0), (t1, y1) in pairwise(keys):
            if t0 <= t <= t1:
                out.append(y0 + (y1 - y0) * ease((t - t0) / (t1 - t0)))
                break
        else:
            out.append(keys[-1][1])
    return out


def render(
    chrome: Chrome, port: int, layout: Layout, shot: Shot
) -> tuple[Path, list[float]]:
    open_view(chrome, port, shot.view)
    total = shot_frames(shot.bars)
    if any(not 0 <= k.t <= total / FPS for k in shot.keys):
        raise ValueError(f"{shot.name}: a keyframe is outside the shot")
    keys = [(k.t, resolve(chrome, k)) for k in shot.keys]
    folder = OUT / layout.name / shot.name
    folder.mkdir(parents=True)
    starts: list[int] = []
    last: float | None = None
    for frame, y in enumerate(positions(keys, total)):
        # Whole device pixels: a sub-pixel change is not a new picture.
        y = round(y * layout.scale) / layout.scale
        if y == last:
            continue
        chrome.evaluate(
            f"{MAIN_COLUMN}.scrollTop = {y};"
            "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
            ".then(() => true)"
        )
        chrome.screenshot(folder / f"{len(starts):04d}.png")
        starts.append(frame)
        last = y
    durations = write_ffconcat(
        OUT / layout.name / f"{shot.name}.ffconcat", starts, total
    )
    return folder, durations


def main() -> int:
    if not (HOME / "gdi-out" / "gao-07-631" / "run.json").is_file():
        print("ui: run demo/stage.sh first", file=sys.stderr)
        return 1
    shutil.rmtree(OUT, ignore_errors=True)
    meta: dict[str, dict[str, object]] = {}
    with ui_server() as port, Chrome() as chrome:
        chrome.call(
            "Emulation.setEmulatedMedia",
            features=[{"name": "prefers-color-scheme", "value": "dark"}],
        )
        for layout in (MAIN, SHORT):
            chrome.viewport(layout.width, layout.height, layout.scale)
            rendered = []
            for shot in layout.shots:
                folder, durations = render(chrome, port, layout, shot)
                rendered.append((folder, durations))
                meta[f"{layout.name}/{shot.name}"] = {
                    "seconds": shot.bars * BAR,
                    "pngs": len(durations),
                }
                print(f"ui: {layout.name}/{shot.name}: {len(durations)} PNGs")
            preview(OUT / layout.name / "preview.gif", rendered)
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Step 4 of the demo videos: the still cards and the thumbnail.

    .venv/bin/python demo/cards.py

docs/plans/demo-video.md has the storyboard. Each card is a section of
`cards.html`, drawn in the UI's IBM Plex and Carbon g100 colours and
screenshotted through `page.py`. Every picture on a card comes from the staged
run: page 12 of the PDF and its chart-001, rendered from the PDF as `charts.py`
crops a chart (at a higher scale, so it stays sharp at phone width, and padded
by CROP_PAD so the chart's source line is not clipped), and the
first rows of chart-001.csv as Granite Vision wrote them.

Writes, under demo/build/cards/, each card as a shot that render.py reads like
terminal.py's and ui.py's (PNG frames plus an ffconcat):
  main/title    2 bars   PDF page -> its outputs
  main/end      5 bars   repo, models, channel; the right half left for
                         YouTube's end-screen elements
  short/open    1 bar    chart-001, large
  short/end     1 bar    the repo, then chart-001 again, so the loop's last
                         frame is its first
and demo/build/thumbnail.png (1280 x 720, under 2 MB).
"""

import csv
import json
import shutil
import sys
from pathlib import Path

import pypdfium2
from cdp import Chrome
from page import data_uri, fill, font_faces, snap
from PIL import Image
from timeline import FPS, shot_frames, write_ffconcat

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "demo" / "build"
TREE = BUILD / "home" / "gdi-out" / "gao-07-631"
PDF = ROOT / "eval" / "corpus" / "gao-07-631.pdf"
OUT = BUILD / "cards"
TEMPLATE = ROOT / "demo" / "cards.html"

# Settled with the user on 2026-11-08 (plan, Open questions).
REPO = "github.com/darylalim/granite-document-intelligence"
HANDLE = "@darylalim-hnl"
CHART = "chart-001"
CHART_SCALE = 4.0  # 360 pt wide -> 1440 px, above the Short's 1008 px
CSV_ROWS = 6
# Docling's box clips the chart's source line; the cards show a little more of the
# same page around it. The pipeline's own crop (charts.py) is unchanged.
CROP_PAD = 10.0  # points
SHORT_END_REPO_FRAMES = 45  # 1.5 s of the repo, then 1 s of the opening chart

# The tree's outputs on the title card, each checked to exist in the staged run.
OUTPUTS = [
    ("MD", "document.md", "Markdown"),
    ("CSV", f"charts/{CHART}.csv", "Chart data"),
    ("JSON", "kvp.json", "Key-values"),
    ("JSONL", "chunks.jsonl", "RAG chunks"),
]

MAIN_SIZE, MAIN_SCALE = (1280, 720), 2  # 2560 x 1440
SHORT_SIZE, SHORT_SCALE = (360, 640), 3  # 1080 x 1920


def chart_box() -> tuple[int, tuple[float, float, float, float]]:
    """chart-001's page and top-left box in points, from the run's document.json."""
    document = json.loads((TREE / "document.json").read_text(encoding="utf-8"))
    for picture in document["pictures"]:
        if picture.get("image", {}).get("uri") == f"charts/{CHART}.png":
            prov = picture["prov"][0]
            box = prov["bbox"]
            if box.get("coord_origin") != "TOPLEFT":
                raise RuntimeError(f"{CHART}'s box is not top-left: {box}")
            return prov["page_no"], (box["l"], box["t"], box["r"], box["b"])
    raise RuntimeError(f"no picture for {CHART} in document.json")


def render_pdf(
    page_no: int, box: tuple[float, float, float, float]
) -> tuple[Image.Image, Image.Image]:
    """The whole page, and the chart cropped as charts.render_crop does, padded."""
    pdf = pypdfium2.PdfDocument(PDF)
    try:
        page = pdf[page_no - 1]
        width, height = page.get_size()
        whole = page.render(scale=2.0).to_pil()
        left, top, right, bottom = box
        left, top = max(0.0, left - CROP_PAD), max(0.0, top - CROP_PAD)
        right, bottom = min(width, right + CROP_PAD), min(height, bottom + CROP_PAD)
        crop = (left, height - bottom, width - right, top)
        chart = page.render(scale=CHART_SCALE, crop=crop).to_pil()
        return whole.convert("RGB"), chart.convert("RGB")
    finally:
        pdf.close()


def csv_preview() -> dict[str, object]:
    with (TREE / "charts" / f"{CHART}.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return {"header": rows[0], "rows": rows[1 : 1 + CSV_ROWS]}


def still(path: Path, frames: int) -> None:
    """A one-PNG shot: `path`'s folder holds 0000.png; write its ffconcat."""
    write_ffconcat(path.parent.with_suffix(".ffconcat"), [0], frames)


def main() -> int:
    if not (TREE / "run.json").is_file():
        print("cards: run demo/stage.sh first", file=sys.stderr)
        return 1
    missing = [name for _, name, _ in OUTPUTS if not (TREE / name).is_file()]
    if missing:
        print(f"cards: the staged tree lacks {missing}", file=sys.stderr)
        return 1
    page_no, box = chart_box()
    page_image, chart_image = render_pdf(page_no, box)
    shared = {
        "page_image": data_uri(page_image),
        "chart_image": data_uri(chart_image),
        "source": f"{PDF.name} · page {page_no}",
        "repo": REPO,
        "repo_wrapped": REPO.replace("/", "/\n", 2).replace("\n", "", 1),
        "handle": HANDLE,
        "outputs": OUTPUTS,
        "csv": csv_preview(),
    }
    fonts = font_faces(
        ("IBM Plex Sans", None), ("IBM Plex Mono", 400), ("IBM Plex Mono", 600)
    )
    shutil.rmtree(OUT, ignore_errors=True)
    scratch = BUILD / "cards.html"

    def card(name: str, out: Path, size: tuple[int, int], scale: float) -> Path:
        snap(
            chrome,
            fill(TEMPLATE, fonts, {**shared, "card": name}),
            scratch,
            out,
            size,
            scale,
        )
        return out

    with Chrome() as chrome:
        still(
            card("title", OUT / "main/title/0000.png", MAIN_SIZE, MAIN_SCALE),
            shot_frames(2),
        )
        still(
            card("end", OUT / "main/end/0000.png", MAIN_SIZE, MAIN_SCALE),
            shot_frames(5),
        )
        opening = card(
            "short-open", OUT / "short/open/0000.png", SHORT_SIZE, SHORT_SCALE
        )
        still(opening, shot_frames(1))
        card("short-end", OUT / "short/end/0000.png", SHORT_SIZE, SHORT_SCALE)
        # The loop's seam: the Short ends on the frame it opens with.
        shutil.copyfile(opening, OUT / "short/end/0001.png")
        write_ffconcat(
            OUT / "short/end.ffconcat", [0, SHORT_END_REPO_FRAMES], shot_frames(1)
        )
        thumbnail = card("thumbnail", BUILD / "thumbnail.png", (1280, 720), 1)
    size = thumbnail.stat().st_size
    if size >= 2_000_000:
        print(
            f"cards: thumbnail.png is {size} bytes, over YouTube's 2 MB",
            file=sys.stderr,
        )
        return 1
    print(f"cards: 4 cards under {OUT.relative_to(ROOT)}, thumbnail {size // 1000} KB")
    print(
        f"cards: the Short's last {(shot_frames(1) - SHORT_END_REPO_FRAMES) / FPS:.1f} s "
        "repeat its opening frame"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

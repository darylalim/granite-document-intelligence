"""HTML pages rendered to PNG in headless Chrome, for the demo's still images.

The cards (`cards.py`), and in step 5 the captions, are small HTML pages in the
UI's own IBM Plex fonts, screenshotted at an exact size (docs/plans/demo-video.md).
Fonts and images are inlined as data URIs, because a file:// page cannot load
fonts from another file:// path. A page's data goes in as JSON and the page's
script places it with textContent only.
"""

import base64
import io
import json
import tomllib
from pathlib import Path

from cdp import Chrome
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "granite_document_intelligence" / "ui"

READY = """Promise.all([
  document.fonts.ready,
  ...[...document.images].filter(i => i.src).map(i => i.decode()),
]).then(() => true)"""


def font_faces(*faces: tuple[str, int | None]) -> str:
    """@font-face rules for (family, weight) pairs, from the UI's theme config.

    The weight is None for a variable face (Plex Sans's "100 700").
    """
    config = tomllib.loads((PACKAGE / ".streamlit" / "config.toml").read_text())
    css = []
    for face in config["theme"]["fontFaces"]:
        weight = face["weight"] if isinstance(face["weight"], int) else None
        if (face["family"], weight) not in faces:
            continue
        path = PACKAGE / "static" / "fonts" / Path(face["url"]).name
        data = base64.b64encode(path.read_bytes()).decode()
        css.append(
            f"@font-face {{ font-family: '{face['family']}'; "
            f"font-weight: {face['weight']}; "
            f"src: url(data:font/woff2;base64,{data}) format('woff2'); "
            f"unicode-range: {face['unicodeRange']}; }}"
        )
    if not css:
        raise ValueError(f"no font faces match {faces}")
    return "\n".join(css)


def data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def fill(template: Path, fonts: str, data: object) -> str:
    """The template with its /*FONT_FACES*/ and /*DATA*/ placeholders filled."""
    html = template.read_text(encoding="utf-8")
    for marker in ("/*FONT_FACES*/", "/*DATA*/"):
        if marker not in html:
            raise ValueError(f"{template.name} has no {marker}")
    # </ in JSON would end the script element; JSON allows the escape.
    payload = json.dumps(data).replace("</", "<\\/")
    return html.replace("/*FONT_FACES*/", fonts).replace("/*DATA*/", payload)


def snap(
    chrome: Chrome,
    html: str,
    scratch: Path,
    out: Path,
    size: tuple[int, int],
    scale: float,
    *,
    transparent: bool = False,
) -> None:
    """Load `html` (written to `scratch`) at `size` CSS px and save a PNG.

    `transparent` keeps the PNG's alpha where the page paints nothing, for an
    overlay such as a caption; the page must leave its own background unset.
    """
    scratch.write_text(html, encoding="utf-8")
    chrome.viewport(size[0], size[1], scale)
    if transparent:
        chrome.call(
            "Emulation.setDefaultBackgroundColorOverride",
            color={"r": 0, "g": 0, "b": 0, "a": 0},
        )
    chrome.navigate(scratch.as_uri())
    chrome.evaluate(READY)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        chrome.screenshot(out)
    finally:
        if transparent:
            chrome.call("Emulation.setDefaultBackgroundColorOverride")
    with Image.open(out) as image:
        expected = (round(size[0] * scale), round(size[1] * scale))
        if image.size != expected:
            raise RuntimeError(f"{out.name} is {image.size}, not {expected}")

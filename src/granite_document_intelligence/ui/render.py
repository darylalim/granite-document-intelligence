"""Model-written Markdown made safe for `st.markdown` (plan §18.4 rule 2).

`document.md` is model output, and a rendered remote image is a request the PDF's
author chose (U2: eight image forms fetched, raw HTML none). So nothing here tries
to recognise a dangerous construct. It fails closed instead: every character that
can start an image, a link, a directive, an emoji or LaTeX is backslash-escaped, so
whatever the model wrote reaches the browser as text. Only two things are lifted
out first:

- an inline image whose target is a file inside the PDF's own output folder, which
  `app.py` shows with `st.image`. Any other image keeps its alt text, as text.
- a fenced code block, which `app.py` shows with `st.code`. Treating too much as a
  fence is safe (it becomes plain text), and so is treating too little (it gets
  escaped).

Inline code spans are escaped too, so they show as plain text. That is the price
of not having to find where a span ends.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


@dataclass(frozen=True)
class Markdown:
    """Escaped text, safe for `st.markdown` with HTML off."""

    text: str


@dataclass(frozen=True)
class Image:
    """A picture contained in the output folder, for `st.image` (no caption: rule 3)."""

    path: Path


@dataclass(frozen=True)
class Code:
    """A fenced block's contents, for `st.code` with no language."""

    text: str


Segment = Markdown | Image | Code

# Each one starts a construct that can fetch or link: `[` `]` `!` images, links,
# footnotes and directives; `<` autolinks and HTML; `$` LaTeX; `:` emoji, Material
# icons, directives and URL schemes; `@` email autolinks; a backtick code spans.
ACTIVE = frozenset("[]!<$:@`")
PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# Docling writes `![Image](charts/chart-001.png)`. Anything fancier is not matched,
# so it is escaped, which is the safe failure.
_IMAGE = re.compile(
    r"!\[([^\[\]\n]*)\]\(\s*([^\s()<>]+)(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)"
)
# A table's delimiter row (`| :--- | ---: |`) holds no letters, so it cannot form
# anything above; escaping its colons would break the table.
_DELIMITER_ROW = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
# GFM finds bare `http://`, `www.` and `a@b.c` links, and Streamlit swaps
# `:streamlit:` and `:material/…:` for a logo and an icon, with regexes over text,
# after escapes are resolved, so a backslash cannot stop them (U2). An invisible
# U+2060 WORD JOINER where each pattern needs adjacency does: after a colon that
# touches the next character, before `@`, and between `www` and its dot.
JOINER = "\u2060"
_WWW = re.compile(r"(?i)\b(www)(?=\\?\.)")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def escape(text: str) -> str:
    """Backslash-escape every active character outside an existing escape pair,
    and break every bare-link pattern with `JOINER`.

    Docling's export already escapes (`\\_`), so a backslash followed by
    punctuation is kept as the pair it is. Doubling it would turn `\\[` into a
    literal backslash before a live bracket.
    """
    lines = []
    for line in text.split("\n"):
        if _DELIMITER_ROW.match(line):
            lines.append(line)
            continue
        out: list[str] = []
        i = 0
        while i < len(line):
            char = line[i]
            if char == "\\" and i + 1 < len(line) and line[i + 1] in PUNCTUATION:
                token, i = line[i : i + 2], i + 2
            else:
                token, i = ("\\" + char if char in ACTIVE else char), i + 1
            if token[-1] == "@":
                token = JOINER + token
            elif token[-1] == ":" and i < len(line) and not line[i].isspace():
                token += JOINER
            out.append(token)
        lines.append(_WWW.sub(rf"\1{JOINER}", "".join(out)))
    return "\n".join(lines)


def contained_image(target: str, folder: Path) -> Path | None:
    """The file `target` names, if it is an image inside `folder`; else None."""
    if _SCHEME.match(target) or target.startswith(("/", "\\")):
        return None
    root = folder.resolve()
    path = (root / unquote(target)).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    return path if path.is_file() else None


def segments(markdown: str, folder: Path) -> list[Segment]:
    """Split `document.md` into escaped text, contained images and code blocks."""
    out: list[Segment] = []
    text: list[str] = []

    def flush() -> None:
        if text:
            out.extend(_split_images("\n".join(text), folder))
            text.clear()

    lines = markdown.split("\n")
    i = 0
    while i < len(lines):
        fence = _FENCE.match(lines[i])
        # A backtick fence's info string may not hold a backtick (CommonMark).
        if fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2)):
            flush()
            marker = fence.group(1)
            closing = re.compile(
                rf"^ {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}\s*$"
            )
            body: list[str] = []
            i += 1
            while i < len(lines) and not closing.match(lines[i]):
                body.append(lines[i])
                i += 1
            out.append(Code("\n".join(body)))
            i += 1  # the closing fence; an unclosed one runs to the end
            continue
        text.append(lines[i])
        i += 1
    flush()
    return out


def _split_images(text: str, folder: Path) -> list[Segment]:
    out: list[Segment] = []
    pending: list[str] = []  # raw text not yet emitted, a dropped image's alt included
    start = 0
    for match in _IMAGE.finditer(text):
        before = text[start : match.start()]
        # `\![…](…)` is escaped already: leave it to `escape`.
        if (len(before) - len(before.rstrip("\\"))) % 2:
            continue
        pending.append(before)
        start = match.end()
        path = contained_image(match.group(2), folder)
        if path is None:
            pending.append(match.group(1))  # the alt text, as text
            continue
        _emit(out, pending)
        out.append(Image(path))
    pending.append(text[start:])
    _emit(out, pending)
    return out


def _emit(out: list[Segment], pending: list[str]) -> None:
    raw = "".join(pending)
    pending.clear()
    if raw.strip():
        out.append(Markdown(escape(_tidy(raw))))


# Best-effort tidying before the escape: an inline link or an autolink becomes its
# text, so the reader does not see raw syntax. Reference links and definitions are
# left alone, because `[1]` and a bibliography's `[1]: …` look exactly like them.
# Whatever is left is escaped, so a miss looks untidy and fetches nothing.
_INLINE_LINK = re.compile(r"\[([^\[\]\n]*)\]\([^()\n]*\)")
_AUTOLINK = re.compile(
    r"<([a-z][a-z0-9+.-]*:[^<>\s]*|[^<>\s@]+@[^<>\s]+)>", re.IGNORECASE
)


def _tidy(text: str) -> str:
    return _AUTOLINK.sub(r"\1", _INLINE_LINK.sub(r"\1", text))

"""The Markdown neutraliser (plan §18.4 rule 2; U2 in the spike report).

U2 found what reaches the browser: eight Markdown image forms fetched, a
backslash does not stop GFM's bare links or Streamlit's `:streamlit:` and
`:material/…:` swaps, and raw HTML and KaTeX fetched nothing. These tests pin the
neutraliser's output as text; U2's browser run is the proof that the text is inert.
"""

import re
from pathlib import Path

import pytest

from granite_document_intelligence.ui import render
from granite_document_intelligence.ui.render import JOINER, Code, Image, Markdown

HOSTILE = """\
# Title ![h](http://evil.example/heading.png)

Inline ![a](http://evil.example/inline.png) titled ![c](http://evil.example/t.png "t")
reference ![b][r1] and protocol-relative ![p](//evil.example/p.png)

[r1]: http://evil.example/ref.png

[link](http://evil.example/link) [js](javascript:alert(1)) <http://evil.example/auto>
bare http://evil.example/bare www.evil.example user@evil.example <user@evil.example>
[![il](http://evil.example/in-link.png)](http://evil.example/around)

| col | img |
|:---|---:|
| 1 | ![t](http://evil.example/table.png) $5 [x] http://evil.example/cell |

<img src="http://evil.example/raw.png"> <a href="http://evil.example/a">a</a>

$\\href{http://evil.example/k}{x}$ and it cost $5 and $3.

:red[red] :material/home: :smile: :streamlit: footnote[^1]

[^1]: ![fn](http://evil.example/fn.png)
"""


def _text(segments: list[render.Segment]) -> str:
    """The Markdown segments' text, less table delimiter rows (exempt: no letters)."""
    text = "\n".join(s.text for s in segments if isinstance(s, Markdown))
    lines = text.split("\n")
    return "\n".join(line for line in lines if not render._DELIMITER_ROW.match(line))


def _unescaped(text: str, char: str) -> list[int]:
    """Positions of `char` not preceded by an odd run of backslashes."""
    found = []
    for match in re.finditer(re.escape(char), text):
        run = len(text[: match.start()]) - len(text[: match.start()].rstrip("\\"))
        if run % 2 == 0:
            found.append(match.start())
    return found


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    out = tmp_path / "report"
    (out / "charts").mkdir(parents=True)
    (out / "charts" / "chart-001.png").write_bytes(b"\x89PNG")
    (out / "notes.txt").write_text("x")
    (tmp_path / "outside.png").write_bytes(b"\x89PNG")
    (out / "charts" / "link-out.png").symlink_to(tmp_path / "outside.png")
    return out


class TestHostileDocument:
    def test_no_active_character_survives_unescaped(self, folder: Path) -> None:
        text = _text(render.segments(HOSTILE, folder))
        # Spelled out, not `render.ACTIVE`, so narrowing that set fails here.
        survivors = {c: _unescaped(text, c) for c in "[]!<$:@`"}
        assert not any(survivors.values()), survivors

    def test_every_bare_link_pattern_is_broken(self, folder: Path) -> None:
        text = _text(render.segments(HOSTILE, folder))
        assert not re.search(r"https?:\\?:?//|\bwww\\?\.|[\w.+-]\\?@", text)
        assert not re.search(r":(?!\s|$|" + JOINER + ")", text.replace("\\:", ":"))

    def test_no_remote_url_reaches_an_image(self, folder: Path) -> None:
        assert not [s for s in render.segments(HOSTILE, folder) if isinstance(s, Image)]

    def test_links_and_dropped_images_leave_their_text(self, folder: Path) -> None:
        plain = _text(render.segments(HOSTILE, folder)).replace(JOINER, "")
        plain = re.sub(r"\\(.)", r"\1", plain)
        assert "Inline a titled c" in plain
        assert "[link]" not in plain and "link " in plain
        assert "it cost $5 and $3." in plain
        assert ":red[red] :material/home: :smile: :streamlit:" in plain


class TestEscape:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(r"a \_b\_", r"a \_b\_", id="docling-escape-kept"),
            pytest.param(r"\[x]", r"\[x\]", id="escaped-bracket-not-doubled"),
            pytest.param(r"\\[x]", r"\\\[x\]", id="literal-backslash-then-bracket"),
            pytest.param("C:\\temp", "C\\:\u2060\\temp", id="lone-backslash-kept"),
            pytest.param("$5", r"\$5", id="dollar"),
            pytest.param("`code`", r"\`code\`", id="code-span-flattened"),
            pytest.param("Note: x", r"Note\: x", id="prose-colon-no-joiner"),
            pytest.param("3:1", "3\\:\u20601", id="touching-colon-joined"),
            pytest.param("a@b.c", "a\u2060\\@b.c", id="email"),
            pytest.param(r"a\@b.c", "a\u2060\\@b.c", id="escaped-email"),
            pytest.param("WWW.x.org", "WWW\u2060.x.org", id="www"),
            pytest.param("|:---|---:|", "|:---|---:|", id="delimiter-row"),
            pytest.param("> quote *em* # h | t", "> quote *em* # h | t", id="kept"),
        ],
    )
    def test_escape(self, source: str, expected: str) -> None:
        assert render.escape(source) == expected


class TestImages:
    @pytest.mark.parametrize(
        ("target", "contained"),
        [
            pytest.param("charts/chart-001.png", True, id="contained"),
            pytest.param("charts/chart%2D001.png", True, id="url-encoded"),
            pytest.param("charts/link-out.png", False, id="symlink-out"),
            pytest.param("../outside.png", False, id="dot-dot"),
            pytest.param("/etc/hosts", False, id="absolute"),
            pytest.param("//evil.example/x.png", False, id="protocol-relative"),
            pytest.param("file:charts/chart-001.png", False, id="scheme"),
            pytest.param("charts/missing.png", False, id="missing"),
            pytest.param("notes.txt", False, id="not-an-image"),
        ],
    )
    def test_containment(self, folder: Path, target: str, contained: bool) -> None:
        found = render.contained_image(target, folder)
        assert (found is not None) is contained
        if found:
            assert found == folder.resolve() / "charts" / "chart-001.png"

    def test_a_contained_image_splits_the_text(self, folder: Path) -> None:
        segments = render.segments(
            "Before $1.\n\n![Image](charts/chart-001.png)\n\nAfter.", folder
        )
        assert segments == [
            Markdown("Before \\$1.\n\n"),
            Image(folder.resolve() / "charts" / "chart-001.png"),
            Markdown("\n\nAfter."),
        ]

    def test_an_escaped_image_is_a_bang_and_a_link(self, folder: Path) -> None:
        # CommonMark reads `\![a](b)` as a literal `!` followed by a link.
        segments = render.segments(r"\![Image](charts/chart-001.png)", folder)
        assert segments == [Markdown(r"\!Image")]


class TestFences:
    def test_a_fence_becomes_code_verbatim(self, folder: Path) -> None:
        source = 'a\n```html\n<img src="http://evil.example/x">\n```\nb'
        assert render.segments(source, folder) == [
            Markdown("a"),
            Code('<img src="http://evil.example/x">'),
            Markdown("b"),
        ]

    def test_an_unclosed_fence_runs_to_the_end(self, folder: Path) -> None:
        assert render.segments("~~~\n![x](y)", folder) == [Code("![x](y)")]

    def test_a_backtick_in_the_info_string_is_not_a_fence(self, folder: Path) -> None:
        assert render.segments("``` a`b", folder) == [Markdown(r"\`\`\` a\`b")]

"""The v1.x UI's machinery: the theme, the launcher and the Streamlit boundary (§18).

The views have their own file (`test_ui_app.py`). Everything here is plain Python
plus Streamlit's config module, so it stays in the Stop-hook gate (§18.5).
"""

import colorsys
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from typing import Any

import pytest

from granite_document_intelligence import cli
from granite_document_intelligence.ui import launch

UI = Path(launch.__file__).parent
CONFIG = UI / ".streamlit" / "config.toml"


def _config() -> dict[str, Any]:
    with CONFIG.open("rb") as handle:
        return tomllib.load(handle)


def _flatten(section: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    """A config table as Streamlit's dotted option keys."""
    items: list[tuple[str, Any]] = []
    for key, value in section.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            items.extend(_flatten(value, path))
        else:
            items.append((path, value))
    return items


def _luminance(hex_color: str) -> float:
    channels = []
    for pair in (hex_color[1:3], hex_color[3:5], hex_color[5:7]):
        c = int(pair, 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(one: str, two: str) -> float:
    a, b = _luminance(one), _luminance(two)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _hue(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    return colorsys.rgb_to_hls(*rgb)[0] * 360


MODES = ("light", "dark")
# Every hue Streamlit derives a text colour for when it is left unset (§14).
HUES = ("red", "orange", "yellow", "green", "blue", "violet", "gray")


class TestThemeConfig:
    """`ui/.streamlit/config.toml` (plan §14). Streamlit only *warns* on a bad key
    or face, so each silent failure is pinned here; none has another guard."""

    def test_holds_the_theme_and_the_toolbar_only(self) -> None:
        # Server and browser keys are the launcher's flags (§18.4 rule 1). One in
        # this file would read as a setting while a flag overrides it.
        assert set(_config()) == {"theme", "client"}

    def test_defines_both_light_and_dark(self) -> None:
        # A lone [theme] locks one mode and drops the toggle, with no warning.
        theme = _config()["theme"]
        assert all(isinstance(theme.get(mode), dict) for mode in MODES)

    def test_every_key_is_one_streamlit_registers(self) -> None:
        # A mis-cased or misplaced key (`theme.dark.baseFontSize`) is ignored with
        # only a warning; checking Streamlit's own registry catches it, and catches
        # an option an upgrade removed.
        from streamlit.config import get_config_options

        registered = set(get_config_options())
        unknown = [k for k, _ in _flatten(_config()) if k not in registered]
        assert not unknown, f"not options Streamlit registers: {unknown}"

    @pytest.mark.parametrize("mode", MODES)
    def test_every_text_colour_is_pinned_per_mode(self, mode: str) -> None:
        # Unset, Streamlit derives <hue>TextColor from <hue>Color, which turned a
        # Carbon red into salmon in the old app and yellow text into 2.59:1.
        section = _config()["theme"][mode]
        missing = [f"{h}TextColor" for h in HUES if f"{h}TextColor" not in section]
        assert not missing, f"[theme.{mode}] leaves {missing} to derivation"

    def test_the_fonts_are_served_from_the_apps_static_folder(self) -> None:
        # A missing .woff2 does not raise: the browser falls back to Source Sans.
        # Static serving is the launcher's flag; without it every face 404s.
        assert launch.FLAGS["server.enableStaticServing"] is True
        faces = _config()["theme"]["fontFaces"]
        assert faces
        missing = [
            f["url"]
            for f in faces
            if not f["url"].startswith("app/static/")
            or not (UI / "static" / f["url"].removeprefix("app/static/")).is_file()
        ]
        assert not missing, f"font faces with no file behind them: {missing}"

    def test_every_font_face_declares_a_unicode_range(self) -> None:
        # Two files share each family and weight; without a range the browser
        # never fetches latin-ext.
        faces = _config()["theme"]["fontFaces"]
        assert all(face.get("unicodeRange") for face in faces)

    def test_font_face_keys_are_ones_streamlit_parses(self) -> None:
        # One mis-cased key makes Streamlit drop the whole face, logging a warning.
        from streamlit.proto.NewSession_pb2 import FontFace

        fields = FontFace.DESCRIPTOR.fields
        accepted = {f.name for f in fields} | {f.json_name for f in fields} | {"weight"}
        keys = {key for face in _config()["theme"]["fontFaces"] for key in face}
        assert keys <= accepted, f"face keys Streamlit drops: {keys - accepted}"

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("scale", ["chartSequentialColors", "chartDivergingColors"])
    def test_scales_have_exactly_ten_steps(self, mode: str, scale: str) -> None:
        # Any other length and Streamlit silently uses its own palette (§14).
        assert len(_config()["theme"][mode][scale]) == 10

    @pytest.mark.parametrize("mode", MODES)
    def test_categorical_slots_read_against_the_page_and_the_text(
        self, mode: str
    ) -> None:
        # Each series must clear 3:1 against the page, and not sit within 1.5:1 of
        # the text colour, where its bars read as text-coloured (§14).
        section = _config()["theme"][mode]
        page, text = section["backgroundColor"], section["textColor"]
        faint = [c for c in section["chartCategoricalColors"] if _contrast(c, page) < 3]
        textlike = [
            c for c in section["chartCategoricalColors"] if _contrast(c, text) < 1.5
        ]
        assert not faint and not textlike, (faint, textlike)

    @pytest.mark.parametrize("mode", MODES)
    def test_primary_stays_clear_of_the_error_red(self, mode: str) -> None:
        # The reason the theme exists: Streamlit's default primary is red, the
        # colour of every error. White on primary must also pass AA.
        theme = _config()["theme"]
        primary = theme[mode].get("primaryColor", theme["primaryColor"])
        gap = abs(_hue(primary) - _hue(theme[mode]["redTextColor"])) % 360
        assert min(gap, 360 - gap) >= 60
        assert _contrast("#ffffff", primary) >= 4.5

    def test_base_font_size_is_the_one_the_layout_was_measured_at(self) -> None:
        # §15's widths (130 px tiles, 344 px cards, one row of pills from 768 px)
        # were measured at this root; changing it re-runs that check.
        assert _config()["theme"]["baseFontSize"] == 15

    def test_the_toolbar_hides_developer_actions(self) -> None:
        assert _config()["client"]["toolbarMode"] == "viewer"


# §18.4 rule 1, spelled out so that dropping or weakening a row fails here.
EXPECTED_FLAGS = {
    "server.address": "127.0.0.1",
    "server.allowedHosts": ["localhost", "127.0.0.1"],
    "server.enableXsrfProtection": True,
    "server.enableCORS": True,
    "server.headless": True,
    "server.fileWatcherType": "none",
    "server.enableStaticServing": True,
    "server.maxUploadSize": 200,
    "browser.gatherUsageStats": False,
    "browser.serverAddress": "localhost",
    "server.corsAllowedOrigins": ["http://localhost"],
    "client.allowedOrigins": ["http://localhost"],
    "server.xsrfCookieSameSite": "lax",
    "global.developmentMode": False,
}


class TestLauncher:
    def test_every_security_key_is_forced(self) -> None:
        assert launch.FLAGS == EXPECTED_FLAGS
        assert launch.flags(8600) == EXPECTED_FLAGS | {"server.port": 8600}

    def test_every_forced_key_is_one_streamlit_registers(self) -> None:
        from streamlit.config import get_config_options

        assert set(launch.flags(8600)) <= set(get_config_options())

    def test_the_app_ships_beside_its_config(self) -> None:
        # Streamlit reads the script-level config only beside the main script (U1).
        assert launch.app_path().is_file()
        assert (launch.app_path().parent / ".streamlit" / "config.toml") == CONFIG

    @pytest.fixture
    def started(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        """`launch.run` with Streamlit's server replaced; Streamlit's own globals
        are restored afterwards."""
        from streamlit import config, net_util
        from streamlit.web import bootstrap

        seen: dict[str, Any] = {}
        monkeypatch.setattr(net_util, "get_external_ip", net_util.get_external_ip)
        monkeypatch.setattr(config, "_main_script_path", config._main_script_path)

        def load(options: dict[str, Any]) -> None:
            seen["script_at_load"] = config._main_script_path
            seen["loaded"] = options

        def serve(app: str, is_hello: bool, args: list[str], options: dict) -> None:
            seen["run"] = (app, is_hello, args, options)

        monkeypatch.setattr(bootstrap, "load_config_options", load)
        monkeypatch.setattr(bootstrap, "run", serve)
        launch.run(8600, open_browser=False)
        return seen

    def test_run_applies_the_flags_as_streamlit_run_does(
        self, started: dict[str, Any]
    ) -> None:
        # U1: bootstrap.run alone applies no flag and never finds the script's
        # config; both need the script path set before load_config_options.
        app = str(launch.app_path())
        assert started["script_at_load"] == app
        assert started["loaded"] == launch.flags(8600)
        assert started["run"] == (app, False, [], launch.flags(8600))

    def test_a_foreign_origin_is_refused_without_a_fetch(
        self, started: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # U1: Streamlit's origin check fetched checkip.amazonaws.com before it
        # refused a foreign origin. With the launcher's patch it fetches nothing.
        from streamlit import net_util
        from streamlit.web.server import server_util

        fetched: list[str] = []
        monkeypatch.setattr(net_util, "_external_ip", None)
        monkeypatch.setattr(net_util, "get_internal_ip", lambda: None)
        monkeypatch.setattr(
            net_util,
            "_make_blocking_http_get",
            lambda url, timeout=0: fetched.append(url),
        )
        assert not server_util.is_url_from_allowed_origins("http://evil.example")
        assert fetched == []

    def test_without_the_patch_the_same_check_fetches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The positive control: the test above can see a fetch when one happens,
        # so a Streamlit release that moves the lookup fails it.
        from streamlit import net_util
        from streamlit.web.server import server_util

        fetched: list[str] = []
        monkeypatch.setattr(net_util, "_external_ip", None)
        monkeypatch.setattr(net_util, "get_internal_ip", lambda: None)
        monkeypatch.setattr(
            net_util,
            "_make_blocking_http_get",
            lambda url, timeout=0: fetched.append(url),
        )
        assert not server_util.is_url_from_allowed_origins("http://evil.example")
        assert fetched and "checkip" in fetched[0]


def _without_streamlit(code: str) -> subprocess.CompletedProcess[str]:
    """Run `code` in a fresh interpreter where `import streamlit` fails."""
    prelude = "import sys\nsys.modules['streamlit'] = None\n"
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


class TestWithoutStreamlit:
    """`default-groups` installs Streamlit for the gate, so "works without it" is
    tested by blocking the import (§18.5)."""

    def test_gdi_ui_names_the_extra_and_exits_1(self) -> None:
        done = _without_streamlit(
            "from granite_document_intelligence import cli\n"
            "sys.exit(cli.main(['ui', '--no-browser']))\n"
        )
        assert done.returncode == 1
        assert done.stderr.strip() == cli.UI_MISSING

    def test_the_library_and_gdi_convert_run(self, tmp_path: Path) -> None:
        not_a_pdf = tmp_path / "notes.pdf"
        not_a_pdf.write_text("not a PDF")
        done = _without_streamlit(
            f"""
            import os, pkgutil, importlib
            os.environ["GDI_CACHE_DIR"] = {str(tmp_path / "cache")!r}
            import granite_document_intelligence as pkg
            for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
                if info.name != "granite_document_intelligence.ui.app":
                    importlib.import_module(info.name)
            from granite_document_intelligence import cli
            sys.exit(cli.main(["convert", {str(not_a_pdf)!r}, "-o", {str(tmp_path / "out")!r}]))
            """
        )
        assert "Traceback" not in done.stderr, done.stderr
        assert done.returncode == 1, done.stderr

"""`gdi ui`: the Streamlit server, in this process, with every security key forced.

Streamlit resolves config per key, and a command-line flag is the one source a
user's own `~/.streamlit/config.toml` cannot override (plan §18.4 rule 1). U1
showed that a global config's `corsAllowedOrigins` alone let a web page drive the
app, so every key below is passed as a flag.

`bootstrap.run` alone applies none of them: called that way it served on a
hostile `0.0.0.0` with XSRF off (U1). So `run` does what `streamlit run` does
first, setting `config._main_script_path` and calling `load_config_options`. It
also disables Streamlit's external-IP lookup, which fetches
`checkip.amazonaws.com` before refusing a foreign origin (U1; decided with the
user). Both are private names; `tests/test_ui.py` pins them.

Streamlit is imported inside `run`, so the library and `gdi convert` never import
it (§18.5).
"""

from __future__ import annotations

import threading
import time
import urllib.request
import webbrowser
from importlib.resources import files
from pathlib import Path

DEFAULT_PORT = 8501

# §18.4 rule 1's table. The value types are what `load_config_options` accepts.
FLAGS: dict[str, object] = {
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
    # Added by U1. An empty list cannot be forced (a flag of `()` reads as "not
    # given"), and localhost is always allowed, so these values add nothing.
    "server.corsAllowedOrigins": ["http://localhost"],
    "client.allowedOrigins": ["http://localhost"],
    "server.xsrfCookieSameSite": "lax",
    "global.developmentMode": False,
}


def app_path() -> Path:
    """The installed `app.py`, beside the `.streamlit/config.toml` it is themed by."""
    return Path(str(files("granite_document_intelligence.ui") / "app.py"))


def flags(port: int) -> dict[str, object]:
    """Every forced key, with the port. A set port is never moved on a clash, so
    the URL opened is the one served."""
    return FLAGS | {"server.port": port}


def url(port: int) -> str:
    return f"http://localhost:{port}/"


def _no_external_ip() -> str | None:
    return None


def run(port: int = DEFAULT_PORT, *, open_browser: bool = True) -> None:
    """Serve the UI until Ctrl-C."""
    from streamlit import config, net_util
    from streamlit.web import bootstrap

    options = flags(port)
    # §18.4 rule 1 (U1). ty reads a module function as unassignable; the patch is
    # the point, and tests/test_ui.py pins that it reaches the origin check.
    net_util.get_external_ip = _no_external_ip  # ty: ignore[invalid-assignment]
    app = str(app_path())
    config._main_script_path = app  # read by load_config_options (U1)
    bootstrap.load_config_options(options)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
    bootstrap.run(app, False, [], options)


def _open_when_ready(port: int, timeout: float = 30.0) -> None:
    # `server.headless` keeps Streamlit from opening a browser itself; open one,
    # once, when the server answers.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health"):
                webbrowser.open(url(port))
                return
        except OSError:
            time.sleep(0.2)

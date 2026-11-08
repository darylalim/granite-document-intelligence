"""Headless Chrome over the DevTools protocol, with the standard library only.

The demo's terminal frames (`terminal.py`) and UI shots (`ui.py`) are both
screenshots of headless Chrome (docs/plans/demo-video.md, steps 2 and 3). This is
the smallest client that does that: launch Chrome on a throwaway profile, attach
to its first page over a WebSocket, send commands and wait for their results.
No third-party client, so nothing joins the project's dependencies.
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Self

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


class CdpError(RuntimeError):
    pass


class _WebSocket:
    """A client WebSocket (RFC 6455): text frames out, whole messages in."""

    def __init__(self, url: str) -> None:
        if not url.startswith("ws://"):
            raise CdpError(f"not a ws:// URL: {url}")
        hostport, _, path = url[len("ws://") :].partition("/")
        host, _, port = hostport.partition(":")
        self._sock = socket.create_connection((host, int(port)), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        # No Origin header: Chrome refuses WebSocket origins it was not told to
        # allow, and a client without one is not a browser page.
        request = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CdpError("connection closed during the WebSocket handshake")
            response += chunk
        head, _, self._buffer = response.partition(b"\r\n\r\n")
        if not head.startswith(b"HTTP/1.1 101"):
            raise CdpError(f"WebSocket handshake refused: {head.splitlines()[0]!r}")

    def _read(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self._sock.recv(max(65536, n - len(self._buffer)))
            if not chunk:
                raise CdpError("connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def send(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])  # FIN, text
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        mask = os.urandom(4)  # a client must mask every frame
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + mask + masked)

    def receive(self) -> str:
        message = b""
        while True:
            first, second = self._read(2)
            opcode, n = first & 0x0F, second & 0x7F
            if n == 126:
                (n,) = struct.unpack("!H", self._read(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", self._read(8))
            if second & 0x80:
                mask = self._read(4)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
            else:
                data = self._read(n)
            if opcode == 0x8:
                raise CdpError("Chrome closed the connection")
            if opcode == 0x9:  # ping: answer, keep reading
                self._sock.sendall(bytes([0x8A, 0x80 | len(data)]) + b"\0" * 4 + data)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += data
                if first & 0x80:
                    return message.decode()

    def close(self) -> None:
        self._sock.close()


class Chrome:
    """One headless Chrome with one page; use as a context manager."""

    def __init__(self) -> None:
        if not CHROME.exists():
            raise CdpError(f"Chrome not found at {CHROME}")
        self._profile = Path(tempfile.mkdtemp(prefix="gdi-demo-chrome-"))
        self._process = subprocess.Popen(
            [
                str(CHROME),
                "--headless=new",
                "--remote-debugging-port=0",
                f"--user-data-dir={self._profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--hide-scrollbars",
                "--force-color-profile=srgb",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        port = self._port()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as r:
            targets = json.load(r)
        page = next(t for t in targets if t.get("type") == "page")
        self._ws = _WebSocket(page["webSocketDebuggerUrl"])
        self._next_id = 0
        self.events: list[dict[str, Any]] = []

    def _port(self, timeout: float = 20.0) -> int:
        # --remote-debugging-port=0 picks a free port and writes it here.
        active = self._profile / "DevToolsActivePort"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if active.is_file():
                first = active.read_text().split("\n", 1)[0].strip()
                if first.isdigit():
                    return int(first)
            if self._process.poll() is not None:
                raise CdpError(f"Chrome exited with {self._process.returncode}")
            time.sleep(0.05)
        raise CdpError("Chrome did not open its debugging port")

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self._next_id += 1
        ident = self._next_id
        self._ws.send(json.dumps({"id": ident, "method": method, "params": params}))
        while True:
            message = json.loads(self._ws.receive())
            if message.get("id") == ident:
                if "error" in message:
                    raise CdpError(f"{method}: {message['error']}")
                return message.get("result", {})
            if "method" in message:
                self.events.append(message)

    def wait_event(self, method: str, timeout: float = 30.0) -> dict[str, Any]:
        """Wait for an event; one already received since the last wait counts."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for i, event in enumerate(self.events):
                if event["method"] == method:
                    del self.events[: i + 1]
                    return event
            message = json.loads(self._ws.receive())
            if "method" in message:
                self.events.append(message)
        raise CdpError(f"timed out waiting for {method}")

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            expression=expression,
            awaitPromise=True,
            returnByValue=True,
        )
        if "exceptionDetails" in result:
            raise CdpError(f"page script failed: {result['exceptionDetails']}")
        return result["result"].get("value")

    def viewport(self, width: int, height: int, scale: float) -> None:
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=scale,
            mobile=False,
        )

    def navigate(self, url: str) -> None:
        self.call("Page.enable")
        self.events.clear()
        self.call("Page.navigate", url=url)
        self.wait_event("Page.loadEventFired")

    def screenshot(self, path: Path) -> None:
        result = self.call("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(result["data"]))

    def close(self) -> None:
        try:
            self._ws.close()
        finally:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            shutil.rmtree(self._profile, ignore_errors=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

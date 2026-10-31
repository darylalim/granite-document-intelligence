"""`gdi convert --events PATH`: progress as one JSON object a line (plan §18.2).

The UI runs `gdi convert` as a child process and follows this file rather than
parse stderr's prose. It is a file, not stdout: a pipe whose reader goes away
either blocks the child while it holds the run lock or raises `BrokenPipeError`
inside a PDF's conversion, where it would fail that PDF and every one after it.

Three rules (§18.2):

- **A failed write never fails a PDF.** The writer catches its own `OSError`,
  logs one warning and turns itself off; the conversion carries on without it.
- **Every line carries `"v": VERSION`**, because the format is a public CLI
  contract: a reader skips a line it cannot parse or whose `v` it does not know.
- **Counts, our own paths and our own error classes only**, never PDF text or
  model output (§3.13 rule 5). Callers pass ints and strings they made
  themselves; a `failed` result names no error, since `str()` of an arbitrary
  exception can quote the PDF. The error is in that PDF's `run.json`.

The library emits through `emit`, which does nothing unless the CLI has opened
a writer with `writing`; so `convert` and `convert_many` behave as before for
every other caller.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Literal

VERSION = 1

Event = Literal[
    "waiting",
    "batch",
    "pdf",
    "page",
    "chart",
    "kvp",
    "result",
    "error",
    "interrupted",
    "summary",
]
# `error.kind` follows the exit codes (§3.8): usage is 2, models and locked are 1.
ErrorKind = Literal["usage", "models", "locked"]

_log = logging.getLogger(__name__)


class EventWriter:
    """Appends events to `path`, flushed line by line, until a write fails.

    Thread-safe: Docling reports pages from the thread that converts the PDF.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._file: IO[str] | None = None
        try:
            self._file = path.open("a", encoding="utf-8")
        except OSError as exc:
            self._give_up(exc)

    def _give_up(self, exc: OSError) -> None:
        _log.warning(
            "could not write progress events to %s (%s); continuing without them",
            self.path,
            exc.strerror or exc,
        )
        file, self._file = self._file, None
        if file is not None:
            try:
                file.close()
            except OSError:
                pass  # the buffered line that just failed fails again

    def emit(self, event: Event, fields: dict[str, int | str | None]) -> None:
        # ensure_ascii keeps a filename that is not valid UTF-8 writable.
        line = json.dumps({"v": VERSION, "event": event, **fields}) + "\n"
        with self._lock:
            if self._file is None:
                return
            try:
                self._file.write(line)
                self._file.flush()
            except OSError as exc:
                self._give_up(exc)

    def close(self) -> None:
        with self._lock:
            file, self._file = self._file, None
            if file is not None:
                try:
                    file.close()
                except OSError as exc:
                    _log.warning(
                        "could not finish writing progress events to %s (%s)",
                        self.path,
                        exc.strerror or exc,
                    )


_writer: EventWriter | None = None


def emit(event: Event, **fields: int | str | None) -> None:
    """Record `event` if a writer is open; never raises for a failed write."""
    writer = _writer
    if writer is not None:
        writer.emit(event, fields)


def error(kind: ErrorKind, message: str) -> None:
    """Record why the run stopped as a whole: our own message for one of our own
    batch-level errors, never `str()` of an arbitrary exception (§18.2)."""
    emit("error", kind=kind, message=message)


@contextmanager
def writing(path: Path | None) -> Generator[None]:
    """Send `emit`'s events to `path` for the `with` block; with None, nowhere."""
    global _writer
    if path is None:
        yield
        return
    writer = EventWriter(path)
    saved, _writer = _writer, writer
    try:
        yield
    finally:
        _writer = saved
        writer.close()

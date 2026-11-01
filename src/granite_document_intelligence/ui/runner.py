"""The UI's conversions: `gdi convert` as a child process (plan §18.2, decided).

The UI never converts in its own process. It starts `gdi convert --events …` with
`start_new_session=True`, sends the child's stdout and stderr to files, and
follows the events file, so memory goes back when the child exits, a crash stays
in the child, and cancel is Ctrl-C's SIGINT (§18.2).

Each run gets `$GDI_CACHE_DIR/ui/runs/<run-id>/` holding `argv.json`, `pid`,
`started` (the child's start time), `events.jsonl`, `stdout.txt` and
`stderr.txt`. Uploads are written to `$GDI_CACHE_DIR/ui/inputs/<sha256>/<name>`
(§18.3). Both trees are the UI's own copies, and `cleanup` deletes what is older
than 30 days in them and nowhere else: it never touches the output root
(decided; the lesson of PR 11).

**Liveness (U3).** A finished child stays a zombie until its parent waits on it,
a zombie answers `kill(pid, 0)`, and once reaped its pid can belong to anyone.
Only the parent gets the exit status. So every `Popen` goes into a `Registry`
that the server keeps for its lifetime and polls on each rerun, and a run from
an earlier server counts as running only if its pid is alive, is not a zombie
and has the start time recorded beside it.

Every string read back from a run folder is untrusted: the events file carries
PDF paths, and `stderr.txt` carries library output. This module parses them;
`app.py` shows them under §18.4 rule 3. Nothing branches on a model's output:
the state comes from `event` and `kind`, which our own code wrote.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from granite_document_intelligence import config, events, safety

# The seam tests replace with a `$STUB_*`-driven stub (§18.5): the same
# interpreter and install as the server.
CHILD: tuple[str, ...] = (sys.executable, "-m", "granite_document_intelligence.cli")
RETENTION_SECONDS = 30 * 24 * 3600  # §18.2 (decided)
EVENTS = "events.jsonl"
ARGV = "argv.json"
PID = "pid"
STARTED = "started"
STDOUT = "stdout.txt"
STDERR = "stderr.txt"
# What `start` writes before the child exists: a folder this young without a
# pid is starting, not dead.
_START_GRACE_SECONDS = 10.0

State = Literal[
    "starting",  # the folder exists; the child has written nothing yet
    "waiting",  # the run lock is held by another run
    "running",
    "done",  # summary, nothing failed
    "failed",  # summary, at least one PDF failed
    "interrupted",  # cancelled: finished PDFs are kept
    "refused",  # an `error` event: the batch never started
    "crashed",  # a negative return code with a PDF left without a result
    "ended",  # gone without a summary: killed, crashed or orphaned
]
ACTIVE: frozenset[State] = frozenset({"starting", "waiting", "running"})


def ui_dir() -> Path:
    return config.cache_dir() / "ui"


def runs_dir() -> Path:
    return ui_dir() / "runs"


def inputs_dir() -> Path:
    return ui_dir() / "inputs"


# --- The command line ------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """The sidebar, one field per `gdi convert` flag (§18.3)."""

    charts: bool = True
    chart_code: bool = False
    chunks: bool = True
    chunk_tokens: int = config.CHUNK_TOKENS_DEFAULT
    text_source: Literal["vlm", "pdf"] = "vlm"
    schema: Path | None = None
    pages: str = "1"
    save_page_images: bool = False
    force: bool = False


def convert_argv(
    inputs: Sequence[Path], out_dir: Path, settings: Settings, events_path: Path
) -> list[str]:
    """`gdi convert`'s arguments, after `convert`, for these inputs and settings.

    Every path is absolute, and the inputs follow `--`, so no name can read as
    a flag.
    """
    argv = ["-o", str(_absolute(out_dir))]
    if settings.text_source != "vlm":
        argv += ["--text-source", settings.text_source]
    if not settings.charts:
        argv.append("--no-charts")
    elif settings.chart_code:
        argv.append("--chart-code")
    if not settings.chunks:
        argv.append("--no-chunks")
    elif settings.chunk_tokens != config.CHUNK_TOKENS_DEFAULT:
        argv += ["--chunk-tokens", str(settings.chunk_tokens)]
    if settings.save_page_images:
        argv.append("--save-page-images")
    if settings.schema is not None:
        argv += ["--schema", str(_absolute(settings.schema)), "--pages", settings.pages]
    if settings.force:
        argv.append("--force")
    argv += ["--events", str(events_path), "--", *(str(_absolute(p)) for p in inputs)]
    return argv


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


# --- Starting, polling and cancelling ----------------------------------------------


class Registry:
    """Every child this server started, reaped on `poll` (U3).

    One per server process (`st.cache_resource`), shared by every session, and
    so locked: sessions run on their own threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: dict[str, subprocess.Popen[bytes]] = {}
        self._codes: dict[str, int] = {}

    def add(self, run_id: str, child: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._children[run_id] = child

    def poll(self) -> None:
        """Reap finished children and keep their return codes."""
        with self._lock:
            for run_id, child in list(self._children.items()):
                code = child.poll()
                if code is not None:
                    self._codes[run_id] = code
                    del self._children[run_id]

    def running(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._children

    def returncode(self, run_id: str) -> int | None:
        with self._lock:
            return self._codes.get(run_id)

    def pid(self, run_id: str) -> int | None:
        with self._lock:
            child = self._children.get(run_id)
            return child.pid if child is not None else None


def new_run_id() -> str:
    """Sortable by start time, and unique within the second."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def start(
    inputs: Sequence[Path],
    out_dir: Path,
    settings: Settings,
    registry: Registry,
) -> str:
    """Start `gdi convert` on `inputs` and return the run's id."""
    run_id = new_run_id()
    folder = runs_dir() / run_id
    folder.mkdir(parents=True, mode=0o700)
    argv = [
        *CHILD,
        "convert",
        *convert_argv(inputs, out_dir, settings, folder / EVENTS),
    ]
    (folder / ARGV).write_text(json.dumps(argv), encoding="utf-8")
    (folder / EVENTS).touch()
    with (
        (folder / STDOUT).open("wb") as stdout,
        (folder / STDERR).open("wb") as stderr,
    ):
        # Files, never a pipe (§18.2): a pipe with no reader blocks the child
        # while it holds the run lock. A new session, so Ctrl-C in the server's
        # terminal does not reach it (U3).
        child = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    registry.add(run_id, child)
    (folder / PID).write_text(f"{child.pid}\n", encoding="ascii")
    started = _start_time(child.pid)
    if started is not None:
        (folder / STARTED).write_text(started + "\n", encoding="ascii")
    return run_id


def cancel(run_id: str, registry: Registry) -> bool:
    """Ctrl-C to the run's child (U3: exit 130, a free lock, no residue).

    Only a child known to be this run's is signalled, never a pid that may
    have been reused. Returns whether a signal was sent.
    """
    pid = registry.pid(run_id)
    if pid is None:
        folder = runs_dir() / run_id
        pid = _alive_pid(folder)
    if pid is None:
        return False
    try:
        # The child leads its own group (start_new_session), so this is the
        # child and anything it starts: Ctrl-C's semantics (U3 finding 4).
        os.killpg(pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _start_time(pid: int) -> str | None:
    """`ps -o lstart` of `pid`, or None when it is gone."""
    status = _ps(pid)
    return status[1] if status else None


def _ps(pid: int) -> tuple[str, str] | None:
    """The process's state letters and start time, or None when there is none."""
    try:
        done = subprocess.run(
            ["ps", "-o", "stat=,lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    parts = done.stdout.split()
    if done.returncode != 0 or len(parts) < 2:
        return None
    return parts[0], " ".join(parts[1:])


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() and int(text) > 1 else None


def _alive_pid(folder: Path) -> int | None:
    """The run's pid if it is still that run's process (U3): alive, not a
    zombie, and started when the run folder says it did."""
    pid = _read_int(folder / PID)
    if pid is None:
        return None
    try:
        recorded = (folder / STARTED).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    status = _ps(pid)
    if status is None or status[0].startswith("Z") or status[1] != recorded:
        return None
    return pid


# --- Reading a run ---------------------------------------------------------------


@dataclass
class Progress:
    """What the events file says, as far as it goes."""

    waiting: bool = False
    holder_pid: int | None = None
    batch_total: int | None = None
    position: int | None = None  # of the current PDF
    pdf: str | None = None  # the current PDF's path, untrusted
    out_dir: str | None = None
    page: tuple[int, int] | None = None  # completed, total
    chart: tuple[int, int] | None = None  # position, total
    kvp: tuple[int, int] | None = None
    out_dirs: list[str] = field(default_factory=list)  # every PDF's, in order
    results: dict[str, str] = field(default_factory=dict)  # pdf -> status
    summary: dict[str, int] | None = None
    error_kind: str | None = None
    error_message: str | None = None
    interrupted: bool = False


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _pair(event: dict[str, Any], first: str) -> tuple[int, int] | None:
    a, b = _int(event.get(first)), _int(event.get("total"))
    return (a, b) if a is not None and b is not None else None


def read_events(lines: Iterable[str]) -> Progress:
    """Fold an events file into a `Progress`.

    A line that does not parse, is not an object, or carries a `v` this code
    does not know is skipped, as the format's contract says (§18.2).
    """
    progress = Progress()
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("v") != events.VERSION:
            continue
        kind = event.get("event")
        if kind == "waiting":
            progress.waiting = True
            progress.holder_pid = _int(event.get("holder_pid"))
        elif kind == "batch":
            progress.waiting = False
            progress.batch_total = _int(event.get("total"))
        elif kind == "pdf":
            progress.waiting = False
            progress.position = _int(event.get("position"))
            progress.batch_total = _int(event.get("total")) or progress.batch_total
            pdf, out_dir = event.get("pdf"), event.get("out_dir")
            progress.pdf = pdf if isinstance(pdf, str) else None
            progress.out_dir = out_dir if isinstance(out_dir, str) else None
            if progress.out_dir is not None:
                progress.out_dirs.append(progress.out_dir)
            progress.page = progress.chart = progress.kvp = None
        elif kind == "page":
            progress.page = _pair(event, "completed")
        elif kind == "chart":
            progress.chart = _pair(event, "position")
        elif kind == "kvp":
            progress.kvp = _pair(event, "position")
        elif kind == "result":
            pdf, status = event.get("pdf"), event.get("status")
            if isinstance(pdf, str) and status in ("done", "skipped", "failed"):
                progress.results[pdf] = status
        elif kind == "error":
            kind_, message = event.get("kind"), event.get("message")
            progress.error_kind = kind_ if isinstance(kind_, str) else "unknown"
            progress.error_message = message if isinstance(message, str) else None
        elif kind == "interrupted":
            progress.interrupted = True
        elif kind == "summary":
            progress.summary = {
                key: _int(event.get(key)) or 0 for key in ("done", "skipped", "failed")
            }
    return progress


@dataclass
class Run:
    run_id: str
    folder: Path
    state: State
    progress: Progress
    returncode: int | None  # None when unknown: still running, or not ours

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    @property
    def crashed_pdf(self) -> str | None:
        """The PDF a crash left without a result, if the events say which."""
        pdf = self.progress.pdf
        return pdf if pdf is not None and pdf not in self.progress.results else None


def read_run(run_id: str, registry: Registry) -> Run:
    """The run's state now (§18.2's rules, amended by U3)."""
    folder = runs_dir() / run_id
    try:
        with (folder / EVENTS).open(encoding="utf-8", errors="replace") as handle:
            progress = read_events(handle)
    except OSError:
        progress = Progress()
    code = registry.returncode(run_id)
    state: State
    if progress.summary is not None:
        state = "failed" if progress.summary["failed"] else "done"
    elif progress.interrupted:
        state = "interrupted"
    elif progress.error_kind is not None:
        state = "refused"
    elif registry.running(run_id) or _alive_pid(folder) is not None:
        if progress.waiting:
            state = "waiting"
        elif progress.batch_total is None:
            state = "starting"
        else:
            state = "running"
    elif code is None and _young_without_pid(folder):
        state = "starting"
    elif code is not None and code < 0 and progress.pdf is not None:
        state = "crashed" if progress.pdf not in progress.results else "ended"
    else:
        state = "ended"
    return Run(run_id, folder, state, progress, code)


def _young_without_pid(folder: Path) -> bool:
    try:
        age = time.time() - folder.stat().st_mtime
    except OSError:
        return False
    return not (folder / PID).exists() and age < _START_GRACE_SECONDS


def run_ids() -> list[str]:
    """Every run folder's id, newest first."""
    try:
        names = [
            p.name
            for p in runs_dir().iterdir()
            if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
        ]
    except OSError:
        return []
    return sorted(names, reverse=True)


def active_run(registry: Registry) -> Run | None:
    """The newest run still starting, waiting or running, if any: how a reopened
    tab finds a conversion it did not start (U3)."""
    for run_id in run_ids():
        run = read_run(run_id, registry)
        if run.active:
            return run
    return None


def run_for(out_dir: Path, registry: Registry) -> Run | None:
    """The newest UI run that wrote `out_dir`, for the Run view's command line."""
    target = _absolute(out_dir)
    for run_id in run_ids():
        run = read_run(run_id, registry)
        if any(_absolute(Path(d)) == target for d in run.progress.out_dirs):
            return run
    return None


def argv_of(run: Run) -> list[str] | None:
    try:
        argv = json.loads((run.folder / ARGV).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(argv, list) and all(isinstance(a, str) for a in argv):
        return argv
    return None


def tail(path: Path, max_bytes: int = 64 * 1024) -> str:
    """The end of a log file, as text; empty when there is none."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


# --- Uploads ---------------------------------------------------------------------

_DEFAULT_UPLOAD_NAME = "upload.pdf"
_MAX_NAME_BYTES = 200


def upload_name(raw: str, default: str = _DEFAULT_UPLOAD_NAME) -> str:
    """The raw multipart filename made a single safe path component (§18.4 rule 5).

    The browser's name can be empty, hold separators, `..`, control characters
    or a name too long for the file system.
    """
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        c for c in name if unicodedata.category(c)[0] != "C"
    ).strip()  # control and format characters, NUL included
    # No hidden files, and never `.` or `..`.
    name = name.lstrip(".") or default
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    while len(name.encode("utf-8")) > _MAX_NAME_BYTES and stem:
        stem = stem[:-1]
        name = f"{stem}.{suffix}" if dot else stem
    return name or default


def save_upload(
    raw_name: str, data: bytes, default: str = _DEFAULT_UPLOAD_NAME
) -> Path:
    """Write an upload to `inputs/<sha256>/<name>` and return its path.

    The same content under the same name is written once; using it again marks
    the folder as used, for cleanup.
    """
    digest = hashlib.sha256(data).hexdigest()
    folder = inputs_dir() / digest
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = safety.contained(folder / upload_name(raw_name, default), folder)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != len(data):
        partial = folder / f".{secrets.token_hex(4)}.partial"
        partial.write_bytes(data)
        partial.replace(path)
    os.utime(folder)
    return path


def stem_clashes(names: Sequence[str]) -> list[tuple[str, str]]:
    """Pairs of uploads that would write the same output folder.

    Uploads are passed as files, and a file writes to `<out>/<stem>` (§3.8),
    compared case-folded as the CLI's overlap check does. The UI refuses these
    before starting rather than let the CLI exit 2 (§18.3).
    """
    seen: dict[str, str] = {}
    clashes = []
    for name in names:
        key = Path(upload_name(name)).stem.casefold()
        if key in seen:
            clashes.append((seen[key], name))
        else:
            seen[key] = name
    return clashes


def replaces(out_root: Path, raw_name: str, sha256: str) -> bool:
    """Whether converting this upload replaces an earlier result for a different
    file under the same name (§18.3: said before it starts)."""
    record = out_root / Path(upload_name(raw_name)).stem / "run.json"
    try:
        run = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(run, dict) and run.get("input_sha256") not in (None, sha256)


# --- Cleanup ---------------------------------------------------------------------


def cleanup(registry: Registry, now: float | None = None) -> list[Path]:
    """Delete run and input folders unused for 30 days; return what went.

    Only `ui/runs/` and `ui/inputs/`, both the UI's own copies. The output root
    is the user's, and nothing here can reach it (§18.2, decided). A symlink is
    never followed: one in either folder is unlinked as the link alone.
    """
    now = time.time() if now is None else now
    removed: list[Path] = []
    for parent in (runs_dir(), inputs_dir()):
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children:
            if not _stale(child, now):
                continue
            if parent == runs_dir() and read_run(child.name, registry).active:
                continue
            try:
                if child.is_symlink() or not child.is_dir():
                    child.unlink()
                else:
                    shutil.rmtree(child)
            except OSError:
                continue
            removed.append(child)
    return removed


def _stale(path: Path, now: float) -> bool:
    try:
        newest = path.lstat().st_mtime
        if path.is_dir() and not path.is_symlink():
            for inner in path.iterdir():
                newest = max(newest, inner.lstat().st_mtime)
    except OSError:
        return False
    return now - newest > RETENTION_SECONDS

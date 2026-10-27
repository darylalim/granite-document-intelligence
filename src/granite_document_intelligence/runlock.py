"""One run at a time per Mac (plan §3.14 rule 4).

Two `gdi` processes would each load the models and push a 16 GB Mac into swap,
so every run holds an exclusive `flock` on `$GDI_CACHE_DIR/run.lock` from start
to finish. The OS drops the lock when the process exits, however it exits, so a
crash cannot leave it stuck — which is why this is `flock` and not a pid file.
The pid written into the file is only for the waiting message.
"""

from __future__ import annotations

import fcntl
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager

from granite_document_intelligence import config

LOCK_NAME = "run.lock"

_log = logging.getLogger(__name__)


class RunLockedError(RuntimeError):
    """Another run holds the lock and the caller chose not to wait."""


def _holder(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    pid = os.read(fd, 32).decode("ascii", "replace").strip()
    return f"pid {pid}" if pid.isdigit() else "pid unknown"


@contextmanager
def run_lock(*, wait: bool = True) -> Generator[None]:
    """Hold the run lock for the `with` block, waiting for it unless `wait` is False.

    The lock belongs to one open file, so a process that takes it twice
    deadlocks itself: `convert_many` takes it once for the whole batch and runs
    each PDF beneath it, never through `convert`.
    """
    path = config.cache_dir() / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = _holder(fd)
            if not wait:
                raise RunLockedError(
                    f"another gdi run ({holder}) is in progress; not waiting (--no-wait)"
                ) from None
            _log.warning("waiting for another gdi run (%s) to finish", holder)
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        yield
    finally:
        # Closing the file releases the lock.
        os.close(fd)

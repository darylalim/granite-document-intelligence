"""Many PDFs into one output directory, with resume (plan §3.8, §3.12).

Implemented once, here, so a CLI and a UI cannot diverge on it. Each PDF goes
through `pipeline.run` under one run lock for the whole batch. Phase A's
converter is built and released per PDF (§3.1, decided with the user in PR 6):
that costs ~2.6 s a PDF, and is what keeps no Docling model resident beside
Granite Vision.

"Done" is decided only by a PDF's own `run.json`, and every uncertainty runs it
again — the rule `scripts/gate.sh`'s memo follows.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from granite_document_intelligence import (
    config,
    export,
    kvp,
    models,
    pipeline,
    runlock,
    safety,
)
from granite_document_intelligence.api import BatchItem, ConvertOptions

_log = logging.getLogger(__name__)


class BatchError(ValueError):
    """A problem with the batch as a whole, raised before any PDF runs."""


@dataclass(frozen=True)
class Job:
    pdf: Path
    out_dir: Path


# --- Expanding the inputs -----------------------------------------------------


def _is_pdf_name(name: str) -> bool:
    return name.lower().endswith(".pdf")


def _pdfs_in(directory: Path, recursive: bool, warnings: list[str]) -> list[Path]:
    """`directory`'s `*.pdf` files (any case), sorted; below it too if `recursive`.

    A symlink that resolves outside `directory` is skipped with a warning
    (§3.10). Symlinked directories are never descended: one that points inside
    is reached by its real path, and one that points outside is out of bounds.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        here = Path(dirpath)
        for name in sorted(dirnames):
            path = here / name
            if path.is_symlink() and not safety.is_within(path, directory):
                warnings.append(f"skipped {path}: a symlink out of {directory}")
        if not recursive:
            dirnames.clear()
        for name in filenames:
            if not _is_pdf_name(name):
                continue
            path = here / name
            if path.is_symlink() and not safety.is_within(path, directory):
                warnings.append(f"skipped {path}: a symlink out of {directory}")
            elif not path.is_file():
                warnings.append(f"skipped {path}: not a regular file")
            else:
                found.append(path)
    return sorted(found, key=lambda p: p.relative_to(directory).parts)


def _check_collisions(jobs: list[Job], out_root: Path) -> None:
    """Refuse two PDFs whose outputs would overlap, before anything runs.

    Compared case-folded, since APFS is case-insensitive by default. Overlap is
    the same folder, one folder inside another (its swap would delete the
    other's tree), or one folder named as another's `.partial`/`.old` sibling
    (startup recovery would delete or move it).
    """
    owners: dict[str, Job] = {}
    for job in jobs:
        key = job.out_dir.relative_to(out_root).as_posix().casefold()
        for name in (
            key,
            key + export.PARTIAL_SUFFIX,
            key + export.OLD_SUFFIX,
        ):
            other = owners.get(name)
            if other is not None:
                raise BatchError(
                    f"{job.pdf} and {other.pdf} would both write to "
                    f"{job.out_dir if name == key else other.out_dir}; "
                    "convert them into separate output directories"
                )
        owners[key] = job
        owners.setdefault(key + export.PARTIAL_SUFFIX, job)
        owners.setdefault(key + export.OLD_SUFFIX, job)
    mains = {
        job.out_dir.relative_to(out_root).as_posix().casefold(): job for job in jobs
    }
    for key, job in mains.items():
        parts = key.split("/")
        for depth in range(1, len(parts)):
            parent = mains.get("/".join(parts[:depth]))
            if parent is not None:
                raise BatchError(
                    f"{job.pdf}'s output folder {job.out_dir} would sit inside "
                    f"{parent.pdf}'s; convert them into separate output directories"
                )


def expand(
    inputs: list[Path], out_root: Path, recursive: bool
) -> tuple[list[Job], list[str]]:
    """The batch's jobs in order, and warnings for anything skipped.

    A directory contributes its PDFs, each writing to its path relative to the
    directory without the suffix, so two `report.pdf` in different subfolders
    do not collide (§3.8). A file named on its own is taken whatever its
    extension — it is judged by content, not name (§3.13 rule 7) — and writes
    to its stem.
    """
    jobs: list[Job] = []
    warnings: list[str] = []
    for item in inputs:
        if item.is_dir():
            for pdf in _pdfs_in(item, recursive, warnings):
                rel = pdf.relative_to(item).with_suffix("")
                jobs.append(Job(pdf, out_root / rel))
        elif item.is_file():
            jobs.append(Job(item, out_root / item.stem))
        else:
            raise BatchError(f"{item}: no such file or directory")
    _check_collisions(jobs, out_root)
    return jobs, warnings


# --- Resume and recovery ------------------------------------------------------


def read_run_json(out_dir: Path) -> dict[str, Any] | None:
    try:
        record = json.loads((out_dir / export.RUN_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def is_done(
    record: dict[str, Any] | None, input_sha256: str, options: dict[str, Any]
) -> bool:
    """Done = success, for this exact file, with these exact options (§3.8)."""
    return (
        record is not None
        and record.get("status") == "ok"
        and record.get("input_sha256") == input_sha256
        and record.get("options") == options
    )


def recover(out_dir: Path) -> None:
    """Undo a crash's residue around `out_dir` (§3.12 rule 1).

    A `.partial/` is an unfinished tree: deleted. An `.old/` beside no folder
    is a crash between the swap's two renames: renamed back. An `.old/` beside
    a folder is a crash after both: deleted. A sibling gdi did not write is
    left alone (PR 11); `_one` then refuses the PDF rather than touch it.
    """
    partial, old = export.siblings(out_dir)
    if export.is_ours(partial):
        shutil.rmtree(partial)
    if export.is_ours(old):
        if out_dir.exists() or out_dir.is_symlink():
            shutil.rmtree(old)
        else:
            os.rename(old, out_dir)
            _log.warning("restored %s from an interrupted run", out_dir)


# --- Running ------------------------------------------------------------------


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _one(
    job: Job,
    out_root: Path,
    options: ConvertOptions,
    recorded: dict[str, Any],
    force: bool,
) -> BatchItem:
    try:
        safety.contained(job.out_dir, out_root)
    except safety.OutsideOutputError as exc:
        # Nowhere safe to write even an error record.
        return BatchItem(job.pdf, job.out_dir, "failed", error=_describe(exc))

    try:
        input_sha256: str | None = pipeline.sha256_of(job.pdf)
    except OSError:
        input_sha256 = None
    record = read_run_json(job.out_dir)
    if (
        not force
        and input_sha256 is not None
        and is_done(record, input_sha256, recorded)
    ):
        assert record is not None
        return BatchItem(
            job.pdf,
            job.out_dir,
            "skipped",
            output_bytes=int(record.get("output_bytes") or 0),
        )

    try:
        export.check_replaceable(job.out_dir)
    except export.ForeignFolderError as exc:
        # Refused before any model loads, and with nothing written: an error
        # run.json would need the same swap (PR 11).
        return BatchItem(job.pdf, job.out_dir, "failed", error=_describe(exc))

    started = time.perf_counter()
    try:
        result = pipeline.run(job.pdf, options)
        result.save(job.out_dir)
    except models.ModelsNotReadyError:
        raise  # the same for every PDF: the batch stops with the fix
    except Exception as exc:  # noqa: BLE001 — one PDF's failure must not stop the batch (§3.8)
        try:
            export.save_error(
                job.out_dir, job.pdf, safety.error_record(exc), input_sha256, recorded
            )
        except OSError as write_exc:
            _log.warning(
                "could not record the failure in %s: %s", job.out_dir, write_exc
            )
        return BatchItem(job.pdf, job.out_dir, "failed", error=_describe(exc))

    written = read_run_json(job.out_dir) or {}
    _log.info(
        "  done in %.0f s: %d pages, %d charts, %d tables",
        time.perf_counter() - started,
        result.run.page_count,
        len(result.charts),
        len(result.tables),
    )
    return BatchItem(
        job.pdf,
        job.out_dir,
        "done",
        result=result,
        output_bytes=int(written.get("output_bytes") or 0),
    )


def run(
    inputs: list[Path],
    out_root: Path,
    options: ConvertOptions,
    *,
    force: bool = False,
    recursive: bool = False,
    wait: bool = True,
) -> Iterator[BatchItem]:
    jobs, warnings = expand(inputs, out_root, recursive)
    if not jobs:
        raise BatchError("no PDFs found in " + ", ".join(str(p) for p in inputs))
    for warning in warnings:
        _log.warning(warning)
    # The same for every PDF, so refused once, before the lock and any model.
    models.verify_models_dir(config.models_dir())
    if options.schema is not None:
        kvp.load_schema(options.schema)
    recorded = pipeline.recorded_options(options)

    with runlock.run_lock(wait=wait):
        out_root.mkdir(parents=True, exist_ok=True)
        for job in jobs:
            if safety.is_within(job.out_dir, out_root):
                recover(job.out_dir)
        for position, job in enumerate(jobs, start=1):
            _log.info("[%d/%d] %s", position, len(jobs), job.pdf)
            item = _one(job, out_root, options, recorded, force)
            if item.status == "skipped":
                _log.info("  skipped: already converted with these options")
            elif item.status == "failed":
                _log.warning("  failed: %s", item.error)
            yield item


# --- Reporting ----------------------------------------------------------------


def _size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def summary(items: list[BatchItem], out_root: Path) -> str:
    """The end-of-batch report (§3.8): counts, the output size, and every path."""
    counts = {status: 0 for status in ("done", "skipped", "failed")}
    for item in items:
        counts[item.status] += 1
    total = sum(item.output_bytes for item in items)
    lines = [
        (
            f"{counts['done']} done, {counts['skipped']} skipped, "
            f"{counts['failed']} failed — {_size(total)} in {out_root}"
        )
    ]
    for status in ("done", "skipped", "failed"):
        for item in items:
            if item.status == status:
                line = f"  {status:7} {item.pdf} → {item.out_dir}"
                if item.error:
                    line += f" ({item.error})"
                lines.append(line)
    return "\n".join(lines)

"""Untrusted input (plan §3.10, §3.13): limits, content checks, paths and error records.

Every PDF is untrusted. It is recognised by its first bytes rather than by its
name (§3.13 rule 7) and held to the size and page limits before any parser
converts it. Its output may only land under the output directory, and what a
failure records names our own code, never the document's content or the
environment (§3.13 rule 5).
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from granite_document_intelligence.api import ConvertOptions

PDF_MAGIC = b"%PDF-"
# PDF 1.7 §7.5.2 lets the header sit anywhere in the first 1024 bytes; pdfium
# and every PDF in the corpus start with it, so v1 accepts it at byte 0 only.
_PACKAGE_DIR = Path(__file__).resolve().parent


class InputRejected(ValueError):
    """A PDF refused before conversion: a per-PDF error, never a batch stop."""


class OutsideOutputError(ValueError):
    """A write that would land outside the output directory (§3.10)."""


def check_pdf(pdf: Path, options: ConvertOptions) -> int:
    """Refuse `pdf` unless it is a PDF within the limits; return its page count.

    The cheap checks go first, so a file that is not a PDF, or is too large,
    never reaches pdfium (§3.13 rule 4).
    """
    try:
        with pdf.open("rb") as f:
            head = f.read(len(PDF_MAGIC))
    except OSError as exc:
        raise InputRejected(f"cannot read {pdf.name}: {exc.strerror}") from exc
    if head != PDF_MAGIC:
        raise InputRejected(f"{pdf.name} is not a PDF (it does not start with %PDF-)")
    size_mb = pdf.stat().st_size / (1024 * 1024)
    if size_mb > options.max_file_mb:
        raise InputRejected(
            f"{pdf.name} is {size_mb:.1f} MB, over the {options.max_file_mb} MB limit "
            "(--max-file-mb)"
        )
    pages = page_count(pdf)
    if pages > options.max_pages:
        raise InputRejected(
            f"{pdf.name} has {pages} pages, over the {options.max_pages}-page limit "
            "(--max-pages)"
        )
    return pages


def page_count(pdf: Path) -> int:
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(pdf)
    except pypdfium2.PdfiumError as exc:
        raise InputRejected(f"{pdf.name} could not be opened as a PDF: {exc}") from exc
    try:
        return len(document)
    finally:
        document.close()


def is_within(path: Path, root: Path) -> bool:
    """Whether `path`, symlinks resolved, lies under `root` (or is it)."""
    return path.resolve().is_relative_to(root.resolve())


def contained(path: Path, root: Path) -> Path:
    """`path`, refused unless it resolves under `root` (§3.10)."""
    if not is_within(path, root):
        raise OutsideOutputError(
            f"{path} resolves to {path.resolve()}, outside the output directory {root}"
        )
    return path


def error_record(exc: BaseException) -> dict[str, Any]:
    """What `run.json` keeps of a failure: type, message and our own frames.

    Frames outside this package are dropped, and no frame's local variables or
    source line is kept (§3.13 rule 5): a Docling or mlx-vlm frame can hold page
    text, and a local can hold anything. The message is the exception's own.
    """
    frames = [
        f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
        if Path(frame.filename).resolve().is_relative_to(_PACKAGE_DIR)
    ]
    return {"type": type(exc).__name__, "message": str(exc), "frames": frames}

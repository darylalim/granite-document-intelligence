"""The `gdi` console script — a thin wrapper over the library (plan §3.7).

`gdi convert` parses arguments, hands everything to `convert_many` and prints;
resume, expansion and failure handling live in the library (§7), so a UI gets
the same behaviour. Exit status: 0 when every PDF was converted or skipped, 1
when any failed or the run could not start (models not ready, another run
holding the lock under `--no-wait`), 2 for a usage error, 130 on Ctrl-C.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from granite_document_intelligence import config

_PACKAGE_LOGGER = "granite_document_intelligence"


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gdi",
        description="Local PDF document intelligence with IBM Granite models on MLX.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    convert = commands.add_parser(
        "convert",
        help="convert PDFs, or directories of them, into Markdown, JSON, tables, "
        "chart data, key-values and chunks",
        description=(
            "Convert each PDF into OUT_DIR/<name>/. A directory contributes its *.pdf "
            "files, each written to its path relative to the directory. A PDF whose "
            "run.json records success for the same file and options is skipped; "
            "--force converts it again. A failed PDF records the error in its run.json "
            "and the batch continues."
        ),
    )
    convert.add_argument(
        "inputs", nargs="+", type=Path, metavar="INPUT", help="a PDF or a directory"
    )
    convert.add_argument(
        "-o", "--output", required=True, type=Path, metavar="OUT_DIR", dest="out_dir"
    )
    convert.add_argument(
        "--schema",
        type=Path,
        metavar="FIELDS_JSON",
        help="a JSON Schema: extract its fields into kvp.json",
    )
    convert.add_argument(
        "--pages",
        default="1",
        help="pages to extract key-values from: 1 (default), 1,3-4 or all; needs --schema",
    )
    convert.add_argument(
        "--text-source",
        choices=("vlm", "pdf"),
        default="vlm",
        help="pdf: take text from the PDF's own text layer (born-digital PDFs you trust)",
    )
    convert.add_argument(
        "--no-charts",
        dest="charts",
        action="store_false",
        help="skip chart data and summaries",
    )
    convert.add_argument(
        "--chart-code",
        action="store_true",
        help="also write each chart as Python (untrusted model output; never run)",
    )
    convert.add_argument(
        "--no-chunks", dest="chunks", action="store_false", help="skip chunks.jsonl"
    )
    convert.add_argument(
        "--chunk-tokens",
        type=int,
        default=config.CHUNK_TOKENS_DEFAULT,
        metavar="N",
        help=f"chunk size in tokens, {config.CHUNK_TOKENS_MIN}–{config.CHUNK_TOKENS_MAX} "
        f"(default {config.CHUNK_TOKENS_DEFAULT})",
    )
    convert.add_argument(
        "--save-page-images", action="store_true", help="also write pages/page-NNN.png"
    )
    convert.add_argument(
        "--recursive", action="store_true", help="descend into subdirectories"
    )
    convert.add_argument(
        "--force", action="store_true", help="convert even PDFs already done"
    )
    convert.add_argument(
        "--max-pages",
        type=_positive,
        default=config.MAX_PAGES_DEFAULT,
        metavar="N",
        help=f"refuse a PDF with more pages (default {config.MAX_PAGES_DEFAULT})",
    )
    convert.add_argument(
        "--max-file-mb",
        type=_positive,
        default=config.MAX_FILE_MB_DEFAULT,
        metavar="MB",
        help=f"refuse a larger PDF (default {config.MAX_FILE_MB_DEFAULT})",
    )
    convert.add_argument(
        "--quiet",
        action="store_true",
        help="no progress, only warnings and the summary",
    )
    convert.add_argument(
        "--no-wait",
        dest="wait",
        action="store_false",
        help="exit instead of waiting when another gdi run is in progress",
    )

    convert_model = commands.add_parser(
        "convert-model",
        help="fetch the pinned models and build the 8-bit Granite Vision (one-time setup)",
        description=(
            "Fill $GDI_CACHE_DIR/models (default ~/.cache/granite-document-intelligence/models): "
            "fetch granite-docling and the figure classifier at their pinned revisions, "
            "convert Granite Vision 4.1 to 8-bit MLX, strip the repo's code and write the "
            "manifest. Downloads about 9 GB the first time."
        ),
    )
    convert_model.add_argument(
        "--delete-source",
        action="store_true",
        help="afterwards, delete the 8 GB bf16 source from the Hugging Face cache "
        "(only once the 8-bit model has loaded)",
    )
    return parser


def _configure_logging(quiet: bool) -> None:
    """Progress to stderr (§7): the package's own messages, which carry counts,
    paths and errors but never page text (§3.13 rule 5)."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(_PACKAGE_LOGGER)
    logger.handlers[:] = [handler]
    logger.setLevel(logging.WARNING if quiet else logging.INFO)
    logger.propagate = False


def _convert(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from granite_document_intelligence import api, batch, kvp, models, runlock

    try:
        options = api.ConvertOptions(
            text_source=args.text_source,
            charts=args.charts,
            chart_code=args.chart_code,
            save_page_images=args.save_page_images,
            schema=args.schema,
            pages=args.pages,
            chunks=args.chunks,
            chunk_tokens=args.chunk_tokens,
            max_pages=args.max_pages,
            max_file_mb=args.max_file_mb,
        )
    except ValueError as exc:
        parser.error(str(exc))
    _configure_logging(args.quiet)

    items: list[api.BatchItem] = []
    try:
        for item in api.convert_many(
            args.inputs,
            args.out_dir,
            options,
            force=args.force,
            recursive=args.recursive,
            wait=args.wait,
        ):
            # Keep the outcome, not the in-memory result: a long batch would
            # otherwise hold every PDF's document and images until the end.
            item.result = None
            items.append(item)
    except (batch.BatchError, kvp.SchemaError) as exc:
        print(f"gdi convert: error: {exc}", file=sys.stderr)
        return 2
    except (models.ModelsNotReadyError, runlock.RunLockedError) as exc:
        print(f"gdi convert: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("gdi convert: interrupted; finished PDFs are kept", file=sys.stderr)
        if items:
            print(batch.summary(items, args.out_dir), file=sys.stderr)
        return 130
    print(batch.summary(items, args.out_dir), file=sys.stderr)
    return 1 if any(item.status == "failed" for item in items) else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    from dotenv import load_dotenv

    # HF_TOKEN from .env only lifts Hub rate limits; the models are public.
    load_dotenv()
    if args.command == "convert":
        return _convert(args, parser)
    if args.command == "convert-model":
        from granite_document_intelligence.models import convert_model

        convert_model(delete_source=args.delete_source)
        return 0
    return 2  # unreachable: argparse rejects an unknown command


if __name__ == "__main__":
    sys.exit(main())

"""The `gdi` console script — a thin wrapper over the library (plan §3.7).

PR 4 ships `gdi convert-model`; `gdi convert` arrives with batching in PR 6.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gdi",
        description="Local PDF document intelligence with IBM Granite models on MLX.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from dotenv import load_dotenv

    # HF_TOKEN from .env only lifts Hub rate limits; the models are public.
    load_dotenv()
    if args.command == "convert-model":
        from granite_document_intelligence.models import convert_model

        convert_model(delete_source=args.delete_source)
        return 0
    return 2  # unreachable: argparse rejects an unknown command


if __name__ == "__main__":
    sys.exit(main())

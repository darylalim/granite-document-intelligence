"""MLX memory: the cache limit, the release between phases, and peak reporting.

The 16 GB Mac's budget is a hard 10 GB peak per phase (§3.11). S4 measured that
releasing a phase needs nothing more than dropping every reference, a garbage
collection and `mx.clear_cache()` — active memory returns to 0.
"""

from __future__ import annotations

import gc
import sys

from granite_document_intelligence.config import MLX_CACHE_LIMIT_BYTES


def configure() -> None:
    """Bound MLX's buffer cache (§3.14 rule 5); its default bounds nothing here."""
    import mlx.core as mx

    mx.set_cache_limit(MLX_CACHE_LIMIT_BYTES)


def reset_peak() -> None:
    import mlx.core as mx

    mx.reset_peak_memory()


def peak_bytes() -> int:
    import mlx.core as mx

    return int(mx.get_peak_memory())


def release() -> None:
    """Free every model the caller has already dropped its references to.

    The caller drops them first; this only collects and empties the caches. The
    figure classifier runs on torch's MPS backend, whose small cache is emptied
    too, but torch is never imported just to do that.
    """
    import mlx.core as mx

    gc.collect()
    mx.clear_cache()
    torch = sys.modules.get("torch")
    if torch is not None and torch.backends.mps.is_available():
        torch.mps.empty_cache()

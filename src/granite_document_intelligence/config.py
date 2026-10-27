"""Model ids, pinned revisions, paths and the fixed tuning values (plan §3.6).

Every value here was settled by a Phase 0 spike or a plan decision; the comment
beside each names where, so a change can be judged against its reason.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# --- Models, each pinned to a commit SHA (§3.13 rule 1) ---------------------


@dataclass(frozen=True)
class PinnedModel:
    repo_id: str
    revision: str

    @property
    def folder(self) -> str:
        """Docling's folder name for this repo under `artifacts_path` (S2)."""
        return self.repo_id.replace("/", "--")


# granite-docling, page → DocTags. The only revision that exists; the one S3 ran.
GRANITE_DOCLING = PinnedModel(
    "ibm-granite/granite-docling-258M-mlx", "e9939db25d2f296c8678d0491c4609a8c596c50a"
)
# Docling's figure classifier. Docling itself loads it at `main`; this pins it.
FIGURE_CLASSIFIER = PinnedModel(
    "docling-project/DocumentFigureClassifier-v2.5",
    "f859dfbff5c9916cd996942d4b0db7fa25808220",
)
# Granite Vision 4.1 bf16 source, at the revision Docling's chart preset pins. It
# is converted to 8-bit locally; IBM publishes no MLX build of 4.1 (§2).
GRANITE_VISION_SOURCE = PinnedModel(
    "ibm-granite/granite-vision-4.1-4b", "dd48e97503de471803850df70843cf9eb5da8712"
)
# The chunking tokenizer (§3.9), loaded from the HF cache at this revision (S10).
EMBEDDING_TOKENIZER = PinnedModel(
    "ibm-granite/granite-embedding-english-r2",
    "47ea694b257b703fee9253d75c2b1f2985180498",
)

# The two Docling models `gdi convert-model` fetches as-is, in manifest order.
FETCHED_MODELS = (GRANITE_DOCLING, FIGURE_CLASSIFIER)

# The local 8-bit conversion. The `local/` namespace is how it is named to Docling-
# style folder lookup (`local--…`); there is no such Hub repo (S2).
GRANITE_VISION_LOCAL_ID = "local/granite-vision-4.1-4b-8bit"
GRANITE_VISION_FOLDER = GRANITE_VISION_LOCAL_ID.replace("/", "--")
# 8-bit, group 64: no measurable regression against bf16 (S6), 4.92 GB (S2).
GRANITE_VISION_Q_BITS = 8
GRANITE_VISION_Q_GROUP_SIZE = 64

MANIFEST_NAME = "manifest.json"

# --- Tuning values, each shipped as one tested value (§3.14 rule 2) ---------

# Any other value maps every DocTags box into image pixels and crops every chart
# from the wrong region, still with status=success (S7).
IMAGES_SCALE = 1.0
# Our own render of a chart's box for Granite Vision: +6 of 69 cells over
# Docling's 72 dpi crop for +2.3 s per chart (S11, approved 2026-10-25).
CHART_CROP_SCALE = 1.5
# Docling's chart preset generation settings, reproduced in our chart stage.
CHART_MAX_NEW_TOKENS = 4096
# Key-value extraction (§3.3) reads the whole page at the scale and token cap of
# Docling's `GRANITE_VISION_4_1_TRANSFORMERS` extraction spec (S6 rendered at 2.0).
KVP_PAGE_SCALE = 2.0
KVP_MAX_NEW_TOKENS = 4096
# A KVP schema is local and bounded (§3.13 rule 8).
KVP_MAX_SCHEMA_BYTES = 256 * 1024
# The chunk-size range `chunk_tokens` must fall in (§3.9); 512 is the default.
CHUNK_TOKENS_DEFAULT = 512
CHUNK_TOKENS_MIN = 64
CHUNK_TOKENS_MAX = 8192
# MLX's default cache limit (16.32 GB) bounds nothing on 16 GB; 1 GiB was as
# fast as the default and bounds the cache well inside the ceiling (S4).
MLX_CACHE_LIMIT_BYTES = 1 << 30

# --- Paths ------------------------------------------------------------------

DEFAULT_CACHE_DIR = Path("~/.cache/granite-document-intelligence")


def cache_dir() -> Path:
    """`$GDI_CACHE_DIR`, or the default; tests point it at `tmp_path` (§3.6)."""
    value = os.environ.get("GDI_CACHE_DIR", "").strip()
    return Path(value).expanduser() if value else DEFAULT_CACHE_DIR.expanduser()


def models_dir() -> Path:
    """The single `artifacts_path` every Docling stage loads from (§3.2, S2)."""
    return cache_dir() / "models"

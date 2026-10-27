"""Phase A: granite-docling on every page and the figure classifier, through Docling.

Docling builds the document; it does **not** run chart extraction here. Granite
Vision runs afterwards in our own chart stage (`charts`), on our 1.5× crop and
with its raw output kept, neither of which Docling's stage allows through public
API (decided with the user, 2026-10-27). The converter is built, run and released
inside `run`, so no Docling model outlives the call (§3.1).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from granite_document_intelligence import config, memory
from granite_document_intelligence.api import ConvertOptions, PageStats

if TYPE_CHECKING:
    from docling.datamodel.pipeline_options import VlmPipelineOptions
    from docling_core.types.doc import DoclingDocument

# The post-hoc loop check slides Docling's DocTags stopper over the final text in
# windows this long (S3, S11). A 3-block loop spans well under half a window, so
# a half-window stride never misses one.
LOOP_WINDOW_CHARS = 640
LOOP_STRIDE_CHARS = LOOP_WINDOW_CHARS // 2
DOCTAG_END = "</doctag>"


def pipeline_options(options: ConvertOptions, models_dir: Path) -> VlmPipelineOptions:
    """The `VlmPipelineOptions` Phase A runs with.

    Each choice is pinned by `test_phase_a.py`, since a wrong one still produces
    output, just worse: the preset path (not the deprecated `GRANITEDOCLING_MLX`),
    `force_backend_text` on the *convert* options (on the pipeline options it is a
    silent no-op, S5), `images_scale` exactly 1.0 (S7), every model from
    `models_dir` (S2), no remote services, and chart extraction left off.
    """
    from docling.datamodel.pipeline_options import VlmConvertOptions, VlmPipelineOptions
    from docling.datamodel.vlm_engine_options import MlxVlmEngineOptions

    vlm_options = VlmConvertOptions.from_preset(
        "granite_docling",
        engine_options=MlxVlmEngineOptions(),
        force_backend_text=options.text_source == "pdf",
    )
    return VlmPipelineOptions(
        vlm_options=vlm_options,
        artifacts_path=models_dir,
        enable_remote_services=False,
        images_scale=config.IMAGES_SCALE,
        generate_page_images=True,
        generate_picture_images=True,
        do_picture_classification=True,
        do_chart_extraction=False,
    )


def is_capped(text: str, tokens: int, max_new_tokens: int) -> bool:
    """A page that ran into the token cap (S3: Docling reports success regardless)."""
    return tokens >= max_new_tokens or not text.rstrip().endswith(DOCTAG_END)


def is_looped(text: str) -> bool:
    """A repetition loop, including one that closed itself under the cap (S3, S11)."""
    from docling.models.utils.generation_utils import DocTagsRepetitionStopper

    stopper = DocTagsRepetitionStopper()
    last = max(len(text) - LOOP_WINDOW_CHARS, 0)
    starts = list(range(0, last + 1, LOOP_STRIDE_CHARS))
    if starts[-1] != last:
        starts.append(last)
    return any(stopper.should_stop(text[s : s + LOOP_WINDOW_CHARS]) for s in starts)


@contextmanager
def _profiled() -> Generator[None]:
    """Docling's per-stage timings at one page per batch (S11: inert on MLX).

    Both are process-wide settings, restored afterwards.
    """
    from docling.datamodel.settings import settings

    saved = settings.debug.profile_pipeline_timings, settings.perf.page_batch_size
    settings.debug.profile_pipeline_timings = True
    settings.perf.page_batch_size = 1
    try:
        yield
    finally:
        settings.debug.profile_pipeline_timings, settings.perf.page_batch_size = saved


@dataclass
class PhaseAResult:
    document: DoclingDocument
    pages: list[PageStats]
    timings_ms: dict[str, float]
    warnings: list[str]


def run(pdf: Path, options: ConvertOptions, models_dir: Path) -> PhaseAResult:
    """Convert `pdf` with granite-docling and the classifier, then release both."""
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import VlmConvertOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.vlm_pipeline import VlmPipeline

    pipe = pipeline_options(options, models_dir)
    assert isinstance(pipe.vlm_options, VlmConvertOptions)
    max_new_tokens = pipe.vlm_options.model_spec.max_new_tokens
    with _profiled():
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_cls=VlmPipeline, pipeline_options=pipe
                )
            }
        )
        result = converter.convert(pdf, raises_on_error=True)

    if result.status not in (
        ConversionStatus.SUCCESS,
        ConversionStatus.PARTIAL_SUCCESS,
    ):
        raise RuntimeError(f"Docling could not convert {pdf.name}: {result.status}")

    warnings = [f"Docling: {e.error_message}" for e in result.errors]
    render_ms = (
        [t * 1000 for t in result.timings["vlm_convert"].times]
        if "vlm_convert" in result.timings
        else []
    )
    pages: list[PageStats] = []
    for i, page in enumerate(result.pages):
        response = page.predictions.vlm_response
        if response is None:
            warnings.append(f"page {page.page_no}: no model output")
            continue
        tokens = response.num_tokens or 0
        generation_ms = (response.generation_time or 0.0) * 1000
        stats = PageStats(
            page_no=page.page_no,
            tokens=tokens,
            generation_ms=generation_ms,
            # One page per batch, so the batch's time less generation is the render.
            render_ms=render_ms[i] - generation_ms if i < len(render_ms) else None,
            capped=is_capped(response.text, tokens, max_new_tokens),
            looped=is_looped(response.text),
        )
        if stats.capped:
            warnings.append(
                f"page {page.page_no}: hit the {max_new_tokens}-token cap; its content is likely lost"
            )
        elif stats.looped:
            warnings.append(
                f"page {page.page_no}: repetition loop in the page's output"
            )
        pages.append(stats)

    timings_ms = {
        f"docling_{key}": sum(item.times) * 1000
        for key, item in result.timings.items()
        if key in ("pipeline_total", "vlm_convert", "doc_enrich")
    }
    document = result.document
    # Drop the converter and the result (with their page images and backends)
    # before the next phase loads anything (§3.1, S4).
    del converter, result
    memory.release()
    return PhaseAResult(
        document=document, pages=pages, timings_ms=timings_ms, warnings=warnings
    )

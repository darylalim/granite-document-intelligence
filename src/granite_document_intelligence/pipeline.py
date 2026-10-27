"""One PDF through every phase, in memory: what `api.convert` runs.

Phases run one after another with a release between them, so no two models'
weights are resident at once beyond what a phase itself needs (§3.1): Phase A
holds granite-docling and the classifier, the chart stage holds Granite Vision.
With a schema, Phase B runs on the chart stage's Granite Vision instance rather
than loading its own (§3.8, decided in PR 5): the same weights, so the peak is
the larger of the two stages', and the model loads once per PDF instead of twice.
"""

from __future__ import annotations

import hashlib
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from granite_document_intelligence import (
    charts,
    chunk,
    config,
    kvp,
    memory,
    models,
    phase_a,
    safety,
)
from granite_document_intelligence.api import (
    Chunk,
    ConvertOptions,
    ConvertResult,
    KvpResult,
    Picture,
    RunInfo,
    Table,
)

# The packages whose versions decide the output (§3.5, §2.2).
RECORDED_PACKAGES = (
    "granite-document-intelligence",
    "docling",
    "docling-core",
    "docling-ibm-models",
    "docling-parse",
    "mlx",
    "mlx-vlm",
    "transformers",
    "tokenizers",
    "torch",
    "huggingface-hub",
    "pypdfium2",
    # Pillow resizes every image Granite Vision reads: moving 12.3.0 → 13.0.0
    # changed one chart summary on an identical crop (PR 4).
    "pillow",
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in RECORDED_PACKAGES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def recorded_options(options: ConvertOptions) -> dict[str, Any]:
    """The options `run.json` records and resume compares (§3.8).

    The schema's content hash goes in beside its path, so an edited schema at
    the same path re-runs. A batch computes this once and compares it with
    every finished PDF's, so it must match what `run` records exactly.
    """
    recorded = options.as_dict()
    if options.schema is not None:
        recorded["schema_sha256"] = sha256_of(options.schema)
    return recorded


def run(pdf: Path, options: ConvertOptions) -> ConvertResult:
    models_dir = config.models_dir()
    # Before any model loads: a wrong or unstripped directory is refused here,
    # and so are a bad schema and pages the PDF does not have.
    manifest = models.verify_models_dir(models_dir)
    schema = kvp.load_schema(options.schema) if options.schema is not None else None
    # Not a PDF, too large or too long: refused before any parser converts it (§3.10).
    page_count = safety.check_pdf(pdf, options)
    kvp_pages = (
        kvp.resolve_pages(options.pages, page_count) if schema is not None else []
    )
    input_sha256 = sha256_of(pdf)
    options_record = recorded_options(options)
    memory.configure()

    timings_ms: dict[str, float] = {}
    peaks: dict[str, int] = {}
    started = time.perf_counter()

    memory.reset_peak()
    t = time.perf_counter()
    a = phase_a.run(pdf, options, models_dir)
    timings_ms["phase_a"] = (time.perf_counter() - t) * 1000
    # Docling's own per-stage profile (§3.14 rule 3), dropped before PR 5.
    timings_ms.update(a.timings_ms)
    peaks["phase_a"] = memory.peak_bytes()
    document = a.document
    warnings = list(a.warnings)

    chart_list = []
    chart_ms: list[float] = []
    kvp_result: KvpResult | None = None
    kvp_ms: list[float] = []
    # One Granite Vision for the chart stage and Phase B. Without a schema the
    # chart stage loads its own, and only if the document has a chart.
    vision: charts.GraniteVision | None = None
    try:
        if schema is not None:
            memory.reset_peak()
            t = time.perf_counter()
            vision = charts.GraniteVision(models_dir)
            timings_ms["granite_vision_load"] = (time.perf_counter() - t) * 1000
        if options.charts:
            if vision is None:
                memory.reset_peak()
            t = time.perf_counter()
            c = charts.run(
                pdf, document, models_dir, code=options.chart_code, vision=vision
            )
            timings_ms["charts"] = (time.perf_counter() - t) * 1000
            peaks["charts"] = memory.peak_bytes()
            chart_list, chart_ms = c.charts, c.chart_ms
            warnings.extend(c.warnings)
        if schema is not None:
            memory.reset_peak()
            t = time.perf_counter()
            k = kvp.run(pdf, schema, kvp_pages, models_dir, vision=vision)
            timings_ms["kvp"] = (time.perf_counter() - t) * 1000
            peaks["kvp"] = memory.peak_bytes()
            kvp_result, kvp_ms = k.result, k.page_ms
            warnings.extend(k.warnings)
    finally:
        if vision is not None:
            vision.close()
            del vision
            memory.release()

    chart_refs = {chart.ref for chart in chart_list}
    pictures = [
        Picture(
            index=index,
            page_no=p.prov[0].page_no if p.prov else 0,
            kind=(
                p.meta.classification.get_main_prediction().class_name
                if p.meta and p.meta.classification
                else None
            ),
            image=p.image.pil_image if p.image else None,
            ref=p.self_ref,
        )
        for index, p in enumerate(
            (p for p in document.pictures if p.self_ref not in chart_refs), start=1
        )
    ]

    t = time.perf_counter()
    tables = [
        Table(
            index=index,
            page_no=table.prov[0].page_no if table.prov else 0,
            dataframe=table.export_to_dataframe(doc=document),
            html=table.export_to_html(doc=document),
            ref=table.self_ref,
        )
        for index, table in enumerate(document.tables, start=1)
    ]
    timings_ms["tables"] = (time.perf_counter() - t) * 1000

    # After the chart stage, so summaries and CSVs are on the pictures (S7).
    chunk_list: list[Chunk] = []
    if options.chunks:
        t = time.perf_counter()
        chunk_list = chunk.run(document, options.chunk_tokens)
        timings_ms["chunks"] = (time.perf_counter() - t) * 1000
    timings_ms["total"] = (time.perf_counter() - started) * 1000

    run_info = RunInfo(
        input_sha256=input_sha256,
        page_count=len(document.pages),
        options=options_record,
        models={
            "manifest": manifest,
            "granite_vision": config.GRANITE_VISION_LOCAL_ID,
            "embedding_tokenizer": {
                "repo_id": config.EMBEDDING_TOKENIZER.repo_id,
                "revision": config.EMBEDDING_TOKENIZER.revision,
            },
        },
        versions=package_versions(),
        timings_ms=timings_ms,
        pages=a.pages,
        chart_ms=chart_ms,
        peak_memory_bytes=peaks,
        warnings=warnings,
        kvp_ms=kvp_ms,
    )
    return ConvertResult(
        source=pdf,
        document=document,
        charts=chart_list,
        pictures=pictures,
        tables=tables,
        kvp=kvp_result,
        chunks=chunk_list,
        run=run_info,
    )

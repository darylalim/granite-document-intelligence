"""The phase sequence with every stage faked: what loads, when, and what is shared."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import build_document

from granite_document_intelligence import (
    ConvertOptions,
    charts,
    chunk,
    kvp,
    memory,
    models,
    phase_a,
    pipeline,
)
from granite_document_intelligence.api import KvpResult
from granite_document_intelligence.charts import ChartsResult
from granite_document_intelligence.kvp import KvpRun
from granite_document_intelligence.phase_a import PhaseAResult


class Stages:
    """Fakes for each stage, recording the order of events."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[str] = []
        self.chart_vision: object = "unset"
        self.kvp_vision: object = "unset"
        self.kvp_pages: list[int] = []
        monkeypatch.setattr(models, "verify_models_dir", lambda _: {})
        for name in ("configure", "reset_peak", "release"):
            monkeypatch.setattr(memory, name, lambda: None)
        monkeypatch.setattr(memory, "peak_bytes", lambda: 1)
        monkeypatch.setattr(phase_a, "run", self.phase_a)
        monkeypatch.setattr(charts, "GraniteVision", self.load_vision)
        monkeypatch.setattr(charts, "run", self.charts)
        monkeypatch.setattr(kvp, "run", self.kvp)
        monkeypatch.setattr(chunk, "run", self.chunk)

    def phase_a(self, *_: Any) -> PhaseAResult:
        self.events.append("phase-a")
        return PhaseAResult(
            document=build_document(),
            pages=[],
            warnings=[],
            timings_ms={"docling_doc_enrich": 2.0},
        )

    def load_vision(self, _: Path) -> Any:
        self.events.append("load-vision")
        stages = self

        class Vision:
            def close(self) -> None:
                stages.events.append("close-vision")

        return Vision()

    def charts(self, *_: Any, vision: Any = None, **__: Any) -> ChartsResult:
        self.events.append("charts")
        self.chart_vision = vision
        return ChartsResult([], [], [])

    def kvp(
        self, _pdf: Path, _schema: Any, pages: list[int], _dir: Path, *, vision: Any
    ) -> KvpRun:
        self.events.append("kvp")
        self.kvp_vision = vision
        self.kvp_pages = pages
        result = KvpResult({}, {}, [], True, [], {})
        return KvpRun(result, [1.0], [])

    def chunk(self, *_: Any) -> list:
        self.events.append("chunk")
        return []


@pytest.fixture
def schema(tmp_path: Path) -> Path:
    path = tmp_path / "fields.json"
    path.write_text(json.dumps({"type": "object"}))
    return path


def test_with_a_schema_charts_and_kvp_share_one_granite_vision(
    monkeypatch: pytest.MonkeyPatch, blank_pdf: Path, schema: Path
) -> None:
    # Decided in PR 5 (§3.8): no reload between the chart stage and Phase B.
    stages = Stages(monkeypatch)
    result = pipeline.run(blank_pdf, ConvertOptions(schema=schema))
    assert stages.events == [
        "phase-a",
        "load-vision",
        "charts",
        "kvp",
        "close-vision",
        "chunk",
    ]
    assert stages.chart_vision is stages.kvp_vision is not None
    assert result.kvp is not None
    assert result.run.kvp_ms == [1.0]
    assert result.run.options["schema_sha256"] == pipeline.sha256_of(schema)


def test_without_a_schema_the_chart_stage_owns_its_model(
    monkeypatch: pytest.MonkeyPatch, blank_pdf: Path
) -> None:
    stages = Stages(monkeypatch)
    result = pipeline.run(blank_pdf, ConvertOptions())
    assert stages.events == ["phase-a", "charts", "chunk"]
    assert stages.chart_vision is None
    assert result.kvp is None
    assert result.run.timings_ms["docling_doc_enrich"] == 2.0


def test_kvp_without_charts_still_loads_once(
    monkeypatch: pytest.MonkeyPatch, blank_pdf: Path, schema: Path
) -> None:
    stages = Stages(monkeypatch)
    pipeline.run(blank_pdf, ConvertOptions(schema=schema, charts=False))
    assert stages.events == ["phase-a", "load-vision", "kvp", "close-vision", "chunk"]
    assert stages.kvp_pages == [1]


def test_a_failing_kvp_still_releases_the_shared_model(
    monkeypatch: pytest.MonkeyPatch, blank_pdf: Path, schema: Path
) -> None:
    stages = Stages(monkeypatch)

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("generation failed")

    monkeypatch.setattr(kvp, "run", boom)
    with pytest.raises(RuntimeError):
        pipeline.run(blank_pdf, ConvertOptions(schema=schema))
    assert stages.events[-1] == "close-vision"


def test_chunks_off_skips_the_chunker(
    monkeypatch: pytest.MonkeyPatch, blank_pdf: Path
) -> None:
    stages = Stages(monkeypatch)
    pipeline.run(blank_pdf, ConvertOptions(chunks=False))
    assert "chunk" not in stages.events


@pytest.mark.parametrize(
    "content, pages, match",
    [
        pytest.param("[]", "1", "JSON object", id="bad-schema"),
        pytest.param('{"type": "object"}', "2", "but the PDF has 1", id="bad-pages"),
    ],
)
def test_a_bad_schema_or_page_is_refused_before_phase_a(
    monkeypatch: pytest.MonkeyPatch,
    blank_pdf: Path,
    tmp_path: Path,
    content: str,
    pages: str,
    match: str,
) -> None:
    stages = Stages(monkeypatch)
    path = tmp_path / "fields.json"
    path.write_text(content)
    with pytest.raises(ValueError, match=match):
        pipeline.run(blank_pdf, ConvertOptions(schema=path, pages=pages))
    assert stages.events == []

"""The viewer's reader (plan §18.3): the §3.5 layout as a contract.

A result goes through the real `export.save()` and comes back through
`reader.read()`, so a layout change that would empty a view fails here.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from test_export import KVP, make_result

from granite_document_intelligence import export
from granite_document_intelligence.api import Chunk
from granite_document_intelligence.ui import reader


def _mark_charts(result: Any) -> None:
    """What the chart stage writes onto each chart's picture (charts.py)."""
    from docling_core.types.doc import DescriptionMetaField, PictureMeta

    for chart in result.charts:
        picture = next(p for p in result.document.pictures if p.self_ref == chart.ref)
        picture.meta = picture.meta or PictureMeta()
        picture.meta.description = DescriptionMetaField(
            text=chart.summary, created_by="test"
        )


def _chunks(n: int) -> list[Chunk]:
    return [
        Chunk(
            id=f"chunk-{i:04d}",
            text=f"text {i}",
            contextualized=f"text {i}",
            headings=["Report"],
            page_nos=[1],
            refs=[],
        )
        for i in range(1, n + 1)
    ]


@pytest.fixture
def saved(tmp_path: Path) -> Path:
    result = make_result(charts=2, kvp=KVP, chunks=_chunks(3), csv=None)
    result.charts[0].csv = "Region,Revenue\nNorth,120\n"
    result.run.warnings = [
        "page 1: hit the 8192-token cap; its content is likely lost",
        "chart-002 (page 1): no table in the model's output",
    ]
    _mark_charts(result)
    out = tmp_path / "out" / "report"
    export.save(result, out)
    return out


class TestRoundTrip:
    def test_every_view_finds_its_files(self, saved: Path) -> None:
        got = reader.read(saved)
        assert got.ok and got.problems == []
        assert got.document_md == saved / "document.md"
        assert [(t.name, t.page_no) for t in got.tables] == [("table-001", 1)]
        assert got.kvp is not None and got.kvp["merged"] == KVP.merged
        assert got.chunks_jsonl == saved / "chunks.jsonl"
        assert got.counts() == {"pages": 1, "tables": 1, "charts": 2, "chunks": 3}

    def test_chart_cards_hold_their_files_page_and_warnings(self, saved: Path) -> None:
        first, second = reader.read(saved).charts
        assert (first.name, first.page_no, first.raw) == ("chart-001", 1, None)
        assert first.csv == saved / "charts" / "chart-001.csv"
        assert first.summary == "Revenue is highest in the North."
        assert first.warnings == []
        # No CSV came back for chart-002, so its raw text is kept and shown.
        assert (second.csv, second.raw) == (
            None,
            saved / "charts" / "chart-002.raw.txt",
        )
        assert second.warnings == ["chart-002 (page 1): no table in the model's output"]

    def test_unprefixed_warnings_stay_on_the_run_view(self, saved: Path) -> None:
        assert reader.read(saved).run_warnings == [
            "page 1: hit the 8192-token cap; its content is likely lost"
        ]

    def test_an_error_tree_reads_as_failed(self, tmp_path: Path) -> None:
        out = tmp_path / "out" / "broken"
        error = {"type": "RuntimeError", "message": "boom", "frames": []}
        export.save_error(out, Path("broken.pdf"), error, None, {})
        got = reader.read(out)
        assert not got.ok
        assert got.run["error"] == error
        assert (got.tables, got.charts, got.document_md) == ([], [], None)


class TestChartPages:
    def test_unknown_rather_than_wrong_when_the_marks_do_not_match(
        self, tmp_path: Path
    ) -> None:
        # A chart that produced neither a table nor a summary leaves no mark on
        # its picture; then no page is guessed.
        result = make_result(charts=2)
        out = tmp_path / "report"
        export.save(result, out)
        assert [c.page_no for c in reader.read(out).charts] == [None, None]

    def test_a_prefixed_warning_without_files_goes_to_the_run_view(
        self, saved: Path
    ) -> None:
        run = json.loads((saved / "run.json").read_text())
        run["warnings"].append(
            "chart-009 (page 3): used Docling's crop, not our render"
        )
        (saved / "run.json").write_text(json.dumps(run))
        assert "chart-009 (page 3): used Docling's crop, not our render" in (
            reader.read(saved).run_warnings
        )


class TestDamage:
    def test_an_unreadable_file_is_named_not_raised(self, saved: Path) -> None:
        (saved / "kvp.json").write_text("{not json")
        got = reader.read(saved)
        assert got.kvp is None
        assert got.problems == ["kvp.json"]

    def test_odd_run_json_values_do_not_crash_the_counts(self, saved: Path) -> None:
        (saved / "run.json").write_text(
            json.dumps({"status": "ok", "page_count": "x", "counts": [], "warnings": 7})
        )
        got = reader.read(saved)
        assert got.counts() == {"pages": None, "tables": 1, "charts": 2, "chunks": None}
        assert got.run_warnings == []


class TestChunks:
    def test_pages_are_read_one_at_a_time(self, saved: Path) -> None:
        rows, total = reader.read_chunks(saved / "chunks.jsonl", 1, 1)
        assert total == 3
        assert [r["id"] for r in rows] == ["chunk-0002"]

    def test_a_bad_line_is_an_empty_row(self, tmp_path: Path) -> None:
        path = tmp_path / "chunks.jsonl"
        path.write_text('{"id": "a"}\nnot json\n[1]\n')
        rows, total = reader.read_chunks(path, 0, 10)
        assert (rows, total) == ([{"id": "a"}, {}, {}], 3)


def _run_json(folder: Path, mtime: float) -> None:
    folder.mkdir(parents=True)
    (folder / "run.json").write_text('{"status": "ok"}')
    os.utime(folder / "run.json", (mtime, mtime))


class TestListRuns:
    def test_newest_first_by_run_json_time(self, tmp_path: Path) -> None:
        _run_json(tmp_path / "old", 1_000)
        _run_json(tmp_path / "a" / "nested", 3_000)
        _run_json(tmp_path / "new", 2_000)
        assert [e.name for e in reader.list_runs(tmp_path)] == [
            "a/nested",
            "new",
            "old",
        ]

    def test_residue_and_inner_folders_are_skipped(self, tmp_path: Path) -> None:
        _run_json(tmp_path / "report", 1_000)
        _run_json(tmp_path / "report" / "charts" / "inner", 2_000)
        _run_json(tmp_path / "other.partial", 3_000)
        _run_json(tmp_path / "other.old", 4_000)
        assert [e.name for e in reader.list_runs(tmp_path)] == ["report"]

    def test_symlinks_never_take_the_listing_outside_the_root(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        _run_json(outside / "secret", 1_000)
        root = tmp_path / "root"
        root.mkdir()
        (root / "link").symlink_to(outside, target_is_directory=True)
        (root / "fake").mkdir()
        (root / "fake" / "run.json").symlink_to(outside / "secret" / "run.json")
        assert reader.list_runs(root) == []

    def test_a_missing_root_lists_nothing(self, tmp_path: Path) -> None:
        assert reader.list_runs(tmp_path / "absent") == []

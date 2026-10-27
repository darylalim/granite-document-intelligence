"""Our chart stage: lenient CSV parsing, the 1.5× crop, and wiring with a fake model."""

from pathlib import Path
from typing import Any

import pytest
from conftest import CHART_BOX, PAGE_H, build_document

from granite_document_intelligence import charts, config


class TestParseChartCsv:
    def test_reads_the_fenced_block(self) -> None:
        raw = "Here it is:\n```csv\nYear,Sales\n2020,1\n2021,2\n```\nDone."
        rows, repaired = charts.parse_chart_csv(raw)
        assert rows == [["Year", "Sales"], ["2020", "1"], ["2021", "2"]]
        assert repaired == 0

    def test_reads_bare_csv(self) -> None:
        rows, _ = charts.parse_chart_csv("Year,Sales\n2020,1\n")
        assert rows == [["Year", "Sales"], ["2020", "1"]]

    def test_rejoins_a_label_split_by_an_unquoted_comma(self) -> None:
        # S2: Docling's pandas parse dropped this whole CSV, keeping nothing.
        raw = "Group,Sex,Earnings\nAll workers,Male,61440\nFull-Time, Year-Round Workers,Male,1\n"
        rows, repaired = charts.parse_chart_csv(raw)
        assert rows is not None
        assert rows[2] == ["Full-Time, Year-Round Workers", "Male", "1"]
        assert repaired == 1

    def test_rejoins_a_split_header(self) -> None:
        # S6: a comma in the header line, which pandas takes as the table's width.
        raw = "Fiscal year, constant dollars,Total\n2001,10\n2002,12\n"
        rows, repaired = charts.parse_chart_csv(raw)
        assert rows is not None
        assert rows[0] == ["Fiscal year, constant dollars", "Total"]
        assert repaired == 1

    def test_pads_a_short_row(self) -> None:
        rows, repaired = charts.parse_chart_csv("a,b,c\n1,2,3\n4,5\n6,7,8\n")
        assert rows is not None
        assert rows[2] == ["4", "5", ""]
        assert repaired == 1

    def test_quoted_commas_are_kept(self) -> None:
        rows, repaired = charts.parse_chart_csv('Label,Value\n"North, East",3\n')
        assert rows == [["Label", "Value"], ["North, East", "3"]]
        assert repaired == 0

    @pytest.mark.parametrize(
        "raw", ["", "```csv\n```", "\n\n"], ids=["empty", "empty-fence", "blank"]
    )
    def test_nothing_parseable_is_none(self, raw: str) -> None:
        assert charts.parse_chart_csv(raw) == (None, 0)

    def test_rows_round_trip_through_csv_with_quoting(self) -> None:
        rows = [["Label", "Value"], ["North, East", "3"]]
        assert charts.rows_to_csv(rows) == 'Label,Value\n"North, East",3\n'


class TestTableData:
    def test_a_text_first_row_is_the_header(self) -> None:
        data = charts.rows_to_table_data([["Year", "Sales"], ["2020", "1"]])
        header = [c for c in data.table_cells if c.column_header]
        assert [c.text for c in header] == ["Year", "Sales"]
        assert (data.num_rows, data.num_cols) == (2, 2)

    def test_an_all_numeric_first_row_is_not(self) -> None:
        data = charts.rows_to_table_data([["1", "2"], ["3", "4"]])
        assert not any(c.column_header for c in data.table_cells)


class TestChartKind:
    def test_only_chart_classes_are_charts(self) -> None:
        doc = build_document()
        kinds = [charts.chart_kind(p) for p in doc.pictures]
        assert kinds == [("bar_chart", 0.97), None]

    def test_the_set_matches_doclings_own_chart_stage(self) -> None:
        # The stage we replace sends exactly these classes to Granite Vision.
        from docling.models.stages.chart_extraction.granite_vision import (
            SUPPORTED_CHART_TYPES,
        )

        assert charts.CHART_KINDS == frozenset(SUPPORTED_CHART_TYPES)


class TestRenderCrop:
    def test_renders_the_box_at_the_crop_scale(self, blank_pdf: Path) -> None:
        import pypdfium2

        picture = build_document().pictures[0]
        pdf = pypdfium2.PdfDocument(blank_pdf)
        try:
            image = charts.render_crop(pdf, picture, PAGE_H)
        finally:
            pdf.close()
        assert image is not None
        l, t, r, b = CHART_BOX
        scale = config.CHART_CROP_SCALE
        assert image.size == (round((r - l) * scale), round((b - t) * scale))
        assert image.mode == "RGB"

    def test_a_rotated_page_falls_back(self, blank_pdf: Path) -> None:
        import pypdfium2

        pdf = pypdfium2.PdfDocument(blank_pdf)
        try:
            pdf[0].set_rotation(90)
            assert charts.render_crop(pdf, build_document().pictures[0], PAGE_H) is None
        finally:
            pdf.close()

    def test_the_crop_scale_is_s11s(self) -> None:
        assert config.CHART_CROP_SCALE == 1.5


class FakeVision:
    """Answers each prompt with a canned response, recording what it was shown."""

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[int, int]]] = []

    def generate(self, image: Any, prompt: str) -> str:
        self.calls.append((prompt, image.size))
        return self.responses[prompt]

    def close(self) -> None:
        pass


GOOD = {
    charts.CSV_PROMPT: "```csv\nRegion,Revenue\nNorth,120\n```",
    charts.SUMMARY_PROMPT: "Revenue is highest in the North.",
    charts.CODE_PROMPT: "```python\nimport matplotlib\n```",
}


class TestRun:
    def _run(
        self, blank_pdf: Path, vision: FakeVision, **kwargs: Any
    ) -> tuple[Any, Any]:
        doc = build_document(**{k: v for k, v in kwargs.items() if k == "charts"})
        code = bool(kwargs.get("code"))
        return doc, charts.run(blank_pdf, doc, Path("unused"), code=code, vision=vision)  # ty: ignore[invalid-argument-type]

    def test_reads_csv_and_summary_from_our_crop(self, blank_pdf: Path) -> None:
        vision = FakeVision(GOOD)
        _, result = self._run(blank_pdf, vision)
        assert [prompt for prompt, _ in vision.calls] == [
            charts.CSV_PROMPT,
            charts.SUMMARY_PROMPT,
        ]
        l, t, r, b = CHART_BOX
        expected = (round((r - l) * 1.5), round((b - t) * 1.5))
        assert {size for _, size in vision.calls} == {expected}
        (chart,) = result.charts
        assert chart.csv == "Region,Revenue\nNorth,120\n"
        assert chart.raw_csv == GOOD[charts.CSV_PROMPT]
        assert chart.summary == "Revenue is highest in the North."
        assert chart.code is None
        assert (chart.index, chart.page_no, chart.kind) == (1, 1, "bar_chart")

    def test_chart_code_only_on_request(self, blank_pdf: Path) -> None:
        vision = FakeVision(GOOD)
        _, result = self._run(blank_pdf, vision, code=True)
        assert vision.calls[-1][0] == charts.CODE_PROMPT
        assert result.charts[0].code == "import matplotlib"

    def test_results_land_on_the_picture_meta(self, blank_pdf: Path) -> None:
        doc, _ = self._run(blank_pdf, FakeVision(GOOD))
        meta = doc.pictures[0].meta
        assert meta.description.text == "Revenue is highest in the North."
        cells = [c.text for c in meta.tabular_chart.chart_data.table_cells]
        assert cells == ["Region", "Revenue", "North", "120"]

    def test_an_unparseable_csv_keeps_the_raw_text_and_warns(
        self, blank_pdf: Path
    ) -> None:
        vision = FakeVision({**GOOD, charts.CSV_PROMPT: ""})
        doc, result = self._run(blank_pdf, vision)
        (chart,) = result.charts
        assert chart.csv is None
        assert chart.raw_csv == ""
        assert doc.pictures[0].meta.tabular_chart is None
        assert any("no table" in w for w in result.warnings)

    def test_a_repaired_row_is_a_warning(self, blank_pdf: Path) -> None:
        vision = FakeVision({**GOOD, charts.CSV_PROMPT: "a,b\nx, y,1\n"})
        _, result = self._run(blank_pdf, vision)
        assert any("chart-001" in w and "comma-split" in w for w in result.warnings)

    def test_charts_are_numbered_in_document_order(self, blank_pdf: Path) -> None:
        _, result = self._run(blank_pdf, FakeVision(GOOD), charts=3)
        assert [c.index for c in result.charts] == [1, 2, 3]

    def test_no_chart_loads_no_model(
        self, blank_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(_: Path) -> None:
            raise AssertionError("loaded Granite Vision for a document with no chart")

        monkeypatch.setattr(charts, "GraniteVision", boom)
        doc = build_document(charts=0)
        result = charts.run(blank_pdf, doc, Path("unused"))
        assert result.charts == []


class TestGraniteVisionLoad:
    def test_never_trusts_remote_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The second guard of §3.13 rule 2; the first is the stripped folder.
        import sys
        import types

        seen: dict[str, Any] = {}

        def load(path: str, **kwargs: Any) -> tuple[object, object]:
            seen.update(kwargs, path=path)
            return object(), object()

        package = types.ModuleType("mlx_vlm")
        package.load = load  # ty: ignore[unresolved-attribute]
        utils = types.ModuleType("mlx_vlm.utils")
        utils.load_config = lambda _: {}  # ty: ignore[unresolved-attribute]
        monkeypatch.setitem(sys.modules, "mlx_vlm", package)
        monkeypatch.setitem(sys.modules, "mlx_vlm.utils", utils)
        charts.GraniteVision(Path("/models"))
        assert seen["trust_remote_code"] is False
        assert seen["path"] == f"/models/{config.GRANITE_VISION_FOLDER}"

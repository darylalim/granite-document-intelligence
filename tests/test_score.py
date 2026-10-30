"""The eval scorer and its matching rules on synthetic pairs (plan §8.2 rule 9).

No model, no corpus PDF: every case is a hand-made expectation and prediction.
Each **bad** prediction must lower its score — a wrong cell, swapped rows, a
missing chart, an extra chart, a hallucinated summary number, an oversized
chunk — and a committed results file naming the private tier is refused. Each
of those was mutation-checked: a scorer that passes everything fails here.

`eval/` is not a package, so it is put on the path the way `python
eval/score.py` puts it there.
"""

import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from conftest import make_pdf_bytes

EVAL = Path(__file__).resolve().parent.parent / "eval"
sys.path.insert(0, str(EVAL))

import matching
import score
from matching import Item

PIE = [
    ["Transport mode", "Percent"],
    ["Rail", "4"],
    ["Motor carriers", "6"],
    ["Maritime", "30"],
    ["Pipelines", "60"],
]


def cells(rows: matching.Rows) -> matching.ChartScore:
    return matching.score_chart(PIE, rows)


# --- Values (rule 3) ------------------------------------------------------------


class TestParseNumber:
    @pytest.mark.parametrize(
        ("text", "value"),
        [
            pytest.param("31,481", 31481, id="thousands"),
            pytest.param("$14.6", 14.6, id="currency"),
            pytest.param("12%", 12, id="percent"),
            pytest.param("–0.7", -0.7, id="en-dash-minus"),
            pytest.param("−0.7", -0.7, id="minus-sign"),
            pytest.param("*1.3", 1.3, id="significance-asterisk"),
            pytest.param("*–1.7", -1.7, id="asterisk-and-minus"),
            pytest.param("(0.2)", -0.2, id="parenthesized-negative"),
            pytest.param("-$5", -5, id="sign-before-currency"),
            pytest.param(" 7 ", 7, id="padded"),
        ],
    )
    def test_numbers(self, text: str, value: float) -> None:
        assert matching.parse_number(text) == pytest.approx(value)

    @pytest.mark.parametrize("text", ["X", "Z", "", "-", "n/a", "1,2,3x", "Rail"])
    def test_codes_words_and_blanks_are_not_numbers(self, text: str) -> None:
        assert matching.parse_number(text) is None


class TestCellsMatch:
    def test_numbers_within_one_percent(self) -> None:
        assert matching.cells_match("100", "100.9")
        assert not matching.cells_match("100", "101.5")

    def test_zero_needs_zero(self) -> None:
        assert matching.cells_match("0", "0.0")
        assert not matching.cells_match("0", "0.001")

    def test_codes_compare_as_strings(self) -> None:
        assert matching.cells_match("X", "x")
        assert not matching.cells_match("X", "Z")

    def test_a_number_never_matches_a_word(self) -> None:
        assert not matching.cells_match("4", "four")

    def test_labels_ignore_case_whitespace_and_footnote_marks(self) -> None:
        assert matching.normalize_label("  Motor   CARRIERS ") == "motor carriers"
        assert matching.normalize_label("2017⁴") == "2017"


# --- Charts ---------------------------------------------------------------------


class TestScoreChart:
    def test_a_perfect_prediction_scores_every_cell(self) -> None:
        s = cells(PIE)
        assert (s.matched, s.cells, s.headers_matched, s.headers) == (4, 4, 2, 2)

    def test_a_wrong_cell_lowers_the_score(self) -> None:
        bad = [row[:] for row in PIE]
        bad[3][1] = "35"
        assert cells(bad).matched == 3

    def test_swapped_values_lower_the_score(self) -> None:
        """The GAO-08-14 pie's failure mode: each label beside another's value."""
        bad = [
            PIE[0],
            ["Rail", "6"],
            ["Motor carriers", "4"],
            ["Maritime", "60"],
            ["Pipelines", "30"],
        ]
        assert cells(bad).matched == 0

    def test_row_order_does_not_matter(self) -> None:
        """Chart rows pair by label: a pie's slice order is arbitrary."""
        assert cells([PIE[0], *reversed(PIE[1:])]).matched == 4

    def test_a_generic_header_costs_only_the_header(self) -> None:
        s = cells([["Category", "Value"], *PIE[1:]])
        assert (s.matched, s.headers_matched) == (4, 0)

    def test_a_transposed_prediction_is_read_both_ways(self) -> None:
        s = cells(matching.transpose(PIE))
        assert s.matched == 4 and s.transposed

    def test_values_within_tolerance_and_with_units(self) -> None:
        assert (
            cells(
                [
                    PIE[0],
                    ["Rail", "4%"],
                    ["Motor carriers", "6.04"],
                    ["Maritime", "30"],
                    ["Pipelines", "60"],
                ]
            ).matched
            == 4
        )

    def test_an_empty_prediction_scores_nothing(self) -> None:
        assert cells([]).matched == 0
        assert cells([[]]).matched == 0

    def test_a_merged_panel_picture_credits_each_panel_column(self) -> None:
        """S7: a picture holding two panels writes one column per panel."""
        panel_1 = [["Category", "A"], ["x", "1"], ["y", "2"]]
        panel_2 = [["Category", "B"], ["x", "3"], ["y", "4"]]
        merged = [["Category", "A", "B"], ["x", "1", "3"], ["y", "2", "4"]]
        assert matching.score_chart(panel_1, merged).matched == 2
        assert matching.score_chart(panel_2, merged).matched == 2

    def test_one_predicted_column_serves_one_expected_series(self) -> None:
        two = [["k", "a", "b"], ["x", "1", "1"], ["y", "2", "2"]]
        one = [["k", "a"], ["x", "1"], ["y", "2"]]
        assert matching.score_chart(two, one).matched == 2


# --- Tables ---------------------------------------------------------------------

TABLE = [
    ["Year", "Number", "Median"],
    ["2017", "100", "61,000"],
    ["2017", "120", "75,500"],
    ["2016", "99", "*60,100"],
]


class TestScoreTable:
    def test_a_perfect_prediction(self) -> None:
        s = matching.score_table(TABLE, TABLE)
        assert (s.matched, s.cells, s.found, s.all_cells) == (9, 9, 12, 12)

    def test_a_wrong_cell_lowers_both_counts(self) -> None:
        bad = [row[:] for row in TABLE]
        bad[2][2] = "91,500"
        s = matching.score_table(TABLE, bad)
        assert (s.matched, s.found) == (8, 11)

    def test_swapped_rows_lower_the_positional_count(self) -> None:
        """Rows pair by position: the two `2017` rows cannot trade places."""
        swapped = [TABLE[0], TABLE[2], TABLE[1], TABLE[3]]
        s = matching.score_table(TABLE, swapped)
        assert s.matched < 9
        assert s.found == 12  # the multiset count cannot see order

    def test_a_split_header_and_a_dropped_row_keep_the_rest_aligned(self) -> None:
        """Census Table A-6: a header over several rows, the first body row missing."""
        predicted = [
            ["0", "1", "2"],
            ["", "Number", ""],
            ["Year", "", "Median"],
            TABLE[2],
            TABLE[3],
        ]
        assert matching.score_table(TABLE, predicted).matched == 6

    def test_an_added_index_column_is_forgiven(self) -> None:
        shifted = [["", *row] for row in TABLE]
        assert matching.score_table(TABLE, shifted).matched == 9

    def test_normalization_reaches_table_cells(self) -> None:
        predicted = [row[:] for row in TABLE]
        predicted[3][2] = "60100"  # no asterisk, no separator
        assert matching.score_table(TABLE, predicted).matched == 9

    def test_each_predicted_cell_is_found_once(self) -> None:
        assert matching.score_table([["a", "a"], ["5", "5"]], [["a"], ["5"]]).found == 2


# --- Pairing --------------------------------------------------------------------


def item(key: str, page: int, order: int, rows: matching.Rows) -> Item:
    return Item(key, page, order, rows)


def chart_score(e: matching.Rows, p: matching.Rows) -> int:
    return matching.score_chart(e, p).matched


class TestPairItems:
    A: ClassVar[matching.Rows] = [["k", "v"], ["a", "1"]]
    B: ClassVar[matching.Rows] = [["k", "v"], ["b", "2"]]

    def test_same_count_pairs_by_reading_order(self) -> None:
        exp = [item("p1-1", 1, 1, self.A), item("p1-2", 1, 2, self.B)]
        pred = [item("c1", 1, 1, self.B), item("c2", 1, 2, self.A)]
        pairing = matching.pair_items(exp, pred, chart_score)
        assert [(e.key, p.key) for e, p in pairing.pairs] == [
            ("p1-1", "c1"),
            ("p1-2", "c2"),
        ]

    def test_a_missing_chart_is_a_miss(self) -> None:
        exp = [item("p1-1", 1, 1, self.A), item("p1-2", 1, 2, self.B)]
        pairing = matching.pair_items(exp, [item("c1", 1, 1, self.B)], chart_score)
        assert [(e.key, p.key) for e, p in pairing.pairs] == [("p1-2", "c1")]
        assert [e.key for e in pairing.missed] == ["p1-1"]

    def test_an_extra_chart_is_a_false_positive(self) -> None:
        exp = [item("p1-1", 1, 1, self.A)]
        pred = [item("c1", 1, 1, self.A), item("c2", 1, 2, self.B)]
        pairing = matching.pair_items(exp, pred, chart_score)
        assert [p.key for p in pairing.false_positives] == ["c2"]

    def test_a_chart_on_a_page_with_none_expected_is_a_false_positive(self) -> None:
        pairing = matching.pair_items([], [item("c1", 3, 1, self.A)], chart_score)
        assert [p.key for p in pairing.false_positives] == ["c1"]

    def test_pages_never_mix(self) -> None:
        pairing = matching.pair_items(
            [item("p1-1", 1, 1, self.A)], [item("c1", 2, 1, self.A)], chart_score
        )
        assert not pairing.pairs and pairing.missed and pairing.false_positives

    def test_shared_lets_one_picture_cover_two_panels(self) -> None:
        merged = [["k", "v", "w"], ["a", "1", "9"], ["b", "8", "2"]]
        exp = [item("p1-1", 1, 1, self.A), item("p1-2", 1, 2, self.B)]
        pred = [item("c1", 1, 1, merged)]
        assert matching.pair_items(exp, pred, chart_score).missed
        shared = matching.pair_items(exp, pred, chart_score, shared=True)
        assert [(e.key, p.key) for e, p in shared.pairs] == [
            ("p1-1", "c1"),
            ("p1-2", "c1"),
        ]
        assert not shared.missed and not shared.false_positives


# --- Document text --------------------------------------------------------------


def brute_levenshtein(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


class TestText:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("kitten", "sitting"),
            ("", "abc"),
            ("abc", ""),
            ("flaw", "lawn"),
            ("intention", "execution"),
            ("aaaa", "aa"),
            ("ab", "ba"),
        ],
    )
    def test_levenshtein_agrees_with_the_textbook_one(self, a: str, b: str) -> None:
        assert matching.levenshtein(a, b) == brute_levenshtein(a, b)

    def test_normalization_undoes_layout_and_markup(self) -> None:
        assert (
            matching.normalize_text("opera-\ntion  of the\nﬁrm’s O&amp;M $^{3}$Office")
            == "operation of the firm's O&M 3Office"
        )

    def test_similarity_drops_with_a_wrong_number(self) -> None:
        ref = "Costs rose by 7.3 percent in fiscal year 2005."
        assert matching.text_similarity(ref, ref) == 1.0
        assert matching.text_similarity(ref.replace("7.3", "73"), ref) < 1.0

    def test_word_f1_drops_with_an_invented_paragraph(self) -> None:
        ref = "The Army spent more on maintenance."
        assert matching.word_f1(ref, ref) == 1.0
        assert (
            matching.word_f1(
                ref + " The Army's 2015-2016 budget is $1,000 million.", ref
            )
            < 0.8
        )

    def test_reading_order_drops_when_paragraphs_swap(self) -> None:
        first = "The first paragraph of the page says something."
        second = "The second paragraph follows it and says more."
        ref = first + " " + second
        assert matching.reading_order([first, second], ref) == 1.0
        assert matching.reading_order([second, first], ref) == -1.0
        assert matching.reading_order(["short"], ref) is None

    def test_headings_as_heading_lines_and_as_text(self) -> None:
        md = "## Results in Brief\n\nBackground is discussed here.\n\n# O&amp;M Costs"
        expected = ["RESULTS IN BRIEF", "Background", "O&M Costs", "Agency Comments"]
        assert matching.headings_found(expected, md) == [True, False, True, False]
        assert matching.headings_in_text(expected, md) == [True, True, True, False]


class TestReferenceText:
    LAYERS: ClassVar[dict[str, dict[int, str]]] = {
        "orig.pdf": {1: "cover", 2: "two", 3: "three", 4: "four"},
        "scan.pdf": {1: "", 2: ""},
    }

    @pytest.mark.parametrize(
        ("manifest", "expected"),
        [
            pytest.param(
                {"text_layer": True},
                {1: "", 2: ""},
                id="born-digital-reads-its-own-layer",
            ),
            pytest.param(
                {"text_layer": False, "scan_of": {"pdf": "orig.pdf", "pages": [2, 3]}},
                {1: "two", 2: "three"},
                id="scan-reads-its-original-renumbered",
            ),
            pytest.param({"text_layer": False}, {}, id="scan-without-original"),
        ],
    )
    def test_reference(
        self,
        manifest: dict[str, Any],
        expected: dict[int, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(score, "text_layer", lambda pdf, _: self.LAYERS[pdf.name])
        entry = score.Entry("scan", "public", tmp_path / "scan.pdf", tmp_path, manifest)
        assert score.reference_text(entry) == expected


# --- Summaries ------------------------------------------------------------------


class TestScoreSummary:
    def test_supported_numbers_years_and_derivatives(self) -> None:
        s = matching.score_summary(
            "In 2004 pipelines carried 60% and rail 4%, a gap of 56 points.",
            PIE,
        )
        assert (s.numbers, s.unsupported) == (4, 0)

    def test_a_hallucinated_number_is_unsupported(self) -> None:
        s = matching.score_summary("Rail carried 4% and air freight 17%.", PIE)
        assert s.unsupported == 1 and s.unsupported_values == ["17%"]

    def test_axis_ranges_and_list_numbering_are_counted_apart(self) -> None:
        s = matching.score_summary(
            "The y-axis ranges from 0 to 250.\n1. Rail is 4.", PIE
        )
        assert (s.apart, s.numbers, s.unsupported) == (3, 1, 0)

    def test_a_number_beside_the_wrong_category_is_a_pair_error(self) -> None:
        """S6: every number occurs in the chart, each beside the wrong slice."""
        s = matching.score_summary("Maritime 60%. Pipelines 30%. Rail 4%.", PIE)
        assert s.unsupported == 0
        assert s.pair_errors == 2


# --- KVP and chunks -------------------------------------------------------------


class TestKvp:
    def test_fields_match_exactly_and_null_matches_missing(self) -> None:
        want = {"name": "Alex Q. Sample", "ein": None, "date": "2026-03-14"}
        got = {"name": "Alex O. Sample ", "date": "2026-03-14"}
        assert matching.kvp_fields(want, got) == {
            "name": False,
            "ein": True,
            "date": True,
        }

    def test_injected_values_are_found_in_any_case(self) -> None:
        assert matching.injected_found(
            ["HACKED", "$0.00"], {"vendor": "hacked inc"}
        ) == ["HACKED"]
        assert matching.injected_found(["HACKED"], {"vendor": "Northwind"}) == []


class TestChunks:
    def test_an_oversized_chunk_is_counted(self) -> None:
        def count(text: str) -> int:
            return len(text.split())

        assert matching.chunks_over_limit(["a b", "a b c d"], count, 3) == 1
        assert matching.chunks_over_limit(["a b"], count, 3) == 0

    def test_page_claims(self) -> None:
        page = "Operation and maintenance costs grew. Male 54,870 90,380 Female 41,000."
        assert matching.page_claim(
            "Operation and maintenance costs grew sharply.", page
        )
        assert matching.page_claim(
            "Male, 2 = 54,870. Male, 3 = 90,380.", page
        )  # a table
        assert (
            matching.page_claim("An entirely different page about rail freight.", page)
            is False
        )
        assert matching.page_claim("Logo", page) is None


# --- Budgets, baseline and the adversarial PDF ----------------------------------


class TestBudgets:
    TIME: ClassVar[dict[str, Any]] = {
        "peak_gb": 7.0,
        "phase_a_s_per_page": 6.0,
        "chart_s": 9.0,
    }

    def test_within_budget(self) -> None:
        budgets = {"memory": {"peak_gb": 10}, "time": {"phase_a_s_per_page": 7.5}}
        assert score.check_budgets(budgets, self.TIME, None, {}).ok

    def test_over_a_time_or_memory_budget_fails(self) -> None:
        assert not score.check_budgets(
            {"time": {"chart_s": 8.0}}, self.TIME, None, {}
        ).ok
        assert not score.check_budgets(
            {"memory": {"peak_gb": 6.5}}, self.TIME, None, {}
        ).ok

    def test_a_new_capped_page_fails_and_a_fixed_one_is_noted(self) -> None:
        budgets = {"capped_pages": {"gao": [24]}}
        assert score.check_budgets(budgets, self.TIME, None, {"gao": [24]}).ok
        assert not score.check_budgets(budgets, self.TIME, None, {"gao": [24, 25]}).ok
        fixed = score.check_budgets(budgets, self.TIME, None, {"gao": []})
        assert fixed.ok and "no longer cap" in fixed.notes[0]

    def test_a_missing_measurement_is_noted_not_failed(self) -> None:
        verdict = score.check_budgets(
            {"time": {"phase_a_s_per_page_scanned": 9}}, self.TIME, None, {}
        )
        assert verdict.ok and "no measurement" in verdict.notes[0]


class TestBaseline:
    TOL: ClassVar[dict[str, float]] = {
        "rate_pct": 1.0,
        "count": 0,
        "kvp": 0,
        "text": 0.01,
    }

    def checks(self, now: dict[str, Any], then: dict[str, Any]) -> dict[str, bool]:
        return {c.name: c.ok for c in score.check_baseline(now, then, self.TOL)}

    def test_a_drop_beyond_tolerance_fails(self) -> None:
        assert self.checks({"chart_cells_pct": 60.0}, {"chart_cells_pct": 62.3}) == {
            "chart_cells_pct": False
        }
        assert self.checks({"text_similarity": 0.78}, {"text_similarity": 0.80}) == {
            "text_similarity": False
        }
        assert self.checks({"charts_found": 9}, {"charts_found": 10}) == {
            "charts_found": False
        }

    def test_a_drop_within_tolerance_or_a_gain_passes(self) -> None:
        assert self.checks({"chart_cells_pct": 61.5}, {"chart_cells_pct": 62.3}) == {
            "chart_cells_pct": True
        }
        assert self.checks({"charts_found": 11}, {"charts_found": 10}) == {
            "charts_found": True
        }

    def test_lower_is_better_metrics_fail_when_they_rise(self) -> None:
        assert self.checks(
            {"chart_false_positives": 1}, {"chart_false_positives": 0}
        ) == {"chart_false_positives": False}
        assert self.checks({"summary_pair_errors": 3}, {"summary_pair_errors": 4}) == {
            "summary_pair_errors": True
        }


# --- End to end: a synthetic corpus and output tree -----------------------------


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_corpus(root: Path, name: str, *, adversarial: bool = False) -> None:
    """One blank one-page PDF expecting the pie chart, two headings and a KVP form."""
    from granite_document_intelligence.pipeline import sha256_of

    pdf = root / "corpus" / f"{name}.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(make_pdf_bytes())
    expected = root / "expected" / name
    (expected / "charts").mkdir(parents=True)
    (expected / "charts" / "p001-1.csv").write_text(
        "\n".join(",".join(r) for r in PIE) + "\n", encoding="utf-8"
    )
    (expected / "headings.txt").write_text("Results\nBackground\n", encoding="utf-8")
    write_json(expected / "kvp" / "expected.json", {"vendor": "Northwind"})
    write_json(expected / "kvp" / "schema.json", {"type": "object"})
    manifest: dict[str, Any] = {
        "pdf": pdf.name,
        "sha256": sha256_of(pdf),
        "pages": 1,
        "text_layer": True,
        "charts": [
            {
                "id": "p001-1",
                "page": 1,
                "caption": "Pie",
                "kind": "pie",
                "csv": "charts/p001-1.csv",
            }
        ],
        "tables": [],
        "pictures": [],
        "kvp": {
            "schema": "kvp/schema.json",
            "expected": "kvp/expected.json",
            "pages": [1],
        },
        "headings": "headings.txt",
    }
    if adversarial:
        manifest["adversarial"] = {
            "exclude_from_averages": True,
            "injected_values": ["HACKED"],
            "loop_pages": [1],
        }
    write_json(expected / "manifest.json", manifest)


def write_tree(
    out: Path, chart: matching.Rows, *, vendor: str = "Northwind", capped: bool = False
) -> None:
    """The §3.5 files `score.py` reads, as `gdi` would write them."""
    write_json(
        out / "document.json",
        {
            "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/pictures/0"}]},
            "furniture": {"children": []},
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Results",
                    "prov": [{"page_no": 1}],
                    "children": [],
                }
            ],
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "prov": [{"page_no": 1}],
                    "image": {"uri": "charts/chart-001.png"},
                    "children": [],
                }
            ],
            "tables": [],
        },
    )
    (out / "document.md").write_text("## Results\n\nBackground\n", encoding="utf-8")
    (out / "charts").mkdir(exist_ok=True)
    (out / "charts" / "chart-001.csv").write_text(
        "\n".join(",".join(r) for r in chart) + "\n", encoding="utf-8"
    )
    (out / "charts" / "chart-001.summary.txt").write_text(
        "Pipelines 60%.\n", encoding="utf-8"
    )
    write_json(out / "kvp.json", {"merged": {"vendor": vendor}, "valid": True})
    (out / "chunks.jsonl").write_text(
        json.dumps(
            {
                "text": "Results",
                "contextualized": "Results",
                "headings": ["Results"],
                "page_nos": [1],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        out / "run.json",
        {
            "status": "ok",
            "page_count": 1,
            "options": {"chunk_tokens": 512},
            "timings_ms": {
                "phase_a": 6000,
                "docling_pipeline_total": 4000,
                "total": 9000,
                "granite_vision_load": 2000,
            },
            "pages": [{"page_no": 1, "capped": capped, "looped": False}],
            "chart_ms": [8000],
            "kvp_ms": [5000],
            "peak_memory_bytes": {"phase_a": 1.6e9, "charts": 6.7e9},
            "warnings": ["page 1: hit the 8192-token cap; its content is likely lost"]
            if capped
            else [],
            "versions": {"docling": "2.143.1"},
            "models": {},
        },
    )


class FakeTokenizer:
    def count_tokens(self, text: str) -> int:
        return len(text.split())


@pytest.fixture
def eval_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(score, "_chunk_tokenizer", lambda _: FakeTokenizer())
    monkeypatch.setattr(score, "machine", lambda: {"power": "AC"})
    root = tmp_path / "eval"
    write_corpus(root, "doc")
    write_tree(root / "runs" / "run" / "r1" / "doc", PIE)
    return root


def run_score(root: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    code = score.main(
        [
            "--eval-dir",
            str(root),
            "--score-only",
            "--run-id",
            "run",
            "--repeats",
            "1",
            *extra,
        ]
    )
    results = json.loads((root / "results" / "run.json").read_text(encoding="utf-8"))
    return code, results


class TestScoreEndToEnd:
    def test_a_good_tree_scores_and_writes_results(self, eval_dir: Path) -> None:
        code, results = run_score(eval_dir)
        totals = results["public"]["totals"]
        assert code == 0
        assert totals["chart_cells"] == [4, 4]
        assert totals["kvp_fields_matched"] == 1
        assert (totals["headings_found"], totals["headings_in_text"]) == (1, 2)
        assert totals["chunks_over_limit"] == 0
        assert results["public"]["time"]["phase_a_s_per_page"] == 6.0
        assert results["public"]["timings"]["doc"]["phase_a_load_s"] == 2.0

    def test_a_worse_tree_fails_against_its_baseline(self, eval_dir: Path) -> None:
        _, good = run_score(eval_dir)
        write_json(eval_dir / "results" / "baseline.json", good)
        (eval_dir / "budgets.toml").write_text(
            'baseline = "results/baseline.json"\n[tolerance]\nrate_pct = 1.0\n',
            encoding="utf-8",
        )
        bad = [PIE[0], ["Rail", "6"], *PIE[2:]]
        write_tree(eval_dir / "runs" / "run" / "r1" / "doc", bad)
        code, results = run_score(eval_dir)
        assert code == 1
        failed = [c["name"] for c in results["verdict"]["checks"] if not c["ok"]]
        assert "chart_cells_pct" in failed

    def test_an_unlisted_capped_page_fails(self, eval_dir: Path) -> None:
        write_tree(eval_dir / "runs" / "run" / "r1" / "doc", PIE, capped=True)
        code, _ = run_score(eval_dir)
        assert code == 1
        (eval_dir / "budgets.toml").write_text(
            "[capped_pages]\ndoc = [1]\n", encoding="utf-8"
        )
        code, _ = run_score(eval_dir)
        assert code == 0

    def test_the_adversarial_pdf_fails_on_an_injected_value(
        self, eval_dir: Path
    ) -> None:
        write_corpus(eval_dir, "adv", adversarial=True)
        write_tree(eval_dir / "runs" / "run" / "r1" / "adv", PIE, vendor="HACKED")
        code, results = run_score(eval_dir)
        adv = results["public"]["adversarial"]["adv"]
        assert code == 1 and not adv["pass"] and adv["injected_found"] == ["HACKED"]

    def test_the_adversarial_pdf_is_kept_out_of_the_averages(
        self, eval_dir: Path
    ) -> None:
        write_corpus(eval_dir, "adv", adversarial=True)
        write_tree(eval_dir / "runs" / "run" / "r1" / "adv", [])
        _, results = run_score(eval_dir)
        assert results["public"]["totals"]["chart_cells"] == [4, 4]

    def test_a_loop_page_that_did_not_loop_is_noted_not_failed(
        self, eval_dir: Path
    ) -> None:
        """Decided with the user in PR 7: the loop check went untested, not wrong."""
        write_corpus(eval_dir, "adv", adversarial=True)
        write_tree(eval_dir / "runs" / "run" / "r1" / "adv", PIE)
        code, results = run_score(eval_dir)
        assert code == 0
        assert results["public"]["adversarial"]["adv"]["loop_pages_did_not_loop"] == [1]
        assert any("did not loop" in n for n in results["verdict"]["notes"])

    def test_a_degenerate_page_missing_from_the_warnings_fails_the_adversarial(
        self, eval_dir: Path
    ) -> None:
        write_corpus(eval_dir, "adv", adversarial=True)
        out = eval_dir / "runs" / "run" / "r1" / "adv"
        write_tree(out, PIE, capped=True)
        run = json.loads((out / "run.json").read_text(encoding="utf-8"))
        run["warnings"] = []
        write_json(out / "run.json", run)
        _, results = run_score(eval_dir)
        assert results["public"]["adversarial"]["adv"]["degenerate_pages_unwarned"] == [
            1
        ]

    def test_a_scan_is_text_scored_only_through_its_original(
        self, eval_dir: Path
    ) -> None:
        """Rule 2: a scan has no text layer, so its original is the reference."""
        path = eval_dir / "expected" / "doc" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["text_layer"] = False
        write_json(path, manifest)
        _, results = run_score(eval_dir)
        assert results["public"]["pdfs"]["doc"]["pages"] == {}
        manifest["scan_of"] = {"pdf": "doc.pdf", "pages": [1, 1]}
        write_json(path, manifest)
        _, results = run_score(eval_dir)
        assert list(results["public"]["pdfs"]["doc"]["pages"]) == ["1"]

    def test_a_changed_pdf_is_refused(self, eval_dir: Path) -> None:
        (eval_dir / "corpus" / "doc.pdf").write_bytes(make_pdf_bytes(pages=2))
        with pytest.raises(SystemExit, match="SHA-256"):
            run_score(eval_dir)


class TestPrivateTier:
    """Rule 7: private results never reach a committed file."""

    def test_only_aggregates_reach_the_committed_file(self, eval_dir: Path) -> None:
        write_corpus(eval_dir / "private", "secret-contract")
        write_tree(
            eval_dir / "private" / "runs" / "run" / "r1" / "secret-contract", PIE
        )
        _, results = run_score(eval_dir)
        text = json.dumps(results)
        assert "secret-contract" not in text and "eval/private" not in text
        assert results["private"]["pdfs"] == 1
        assert results["private"]["totals"]["chart_cells"] == [4, 4]
        assert (eval_dir / "private" / "results" / "run.json").is_file()

    def test_a_committed_result_naming_a_private_path_is_refused(
        self, tmp_path: Path
    ) -> None:
        results = {
            "public": {"pdfs": {"secret-contract": {}}},
            "note": "eval/private/corpus/x.pdf",
        }
        with pytest.raises(score.PrivatePathError, match="private tier"):
            score.write_results(results, tmp_path / "r.json", ["secret-contract"])
        with pytest.raises(score.PrivatePathError):
            score.write_results(
                {"note": "see eval/private/x"}, tmp_path / "r.json", ["eval/private"]
            )
        assert not (tmp_path / "r.json").exists()

    def test_markers_cover_the_private_pdfs(self, eval_dir: Path) -> None:
        write_corpus(eval_dir / "private", "secret-contract")
        markers = score.private_markers(eval_dir)
        assert "secret-contract" in markers and "secret-contract.pdf" in markers


class TestBaselineFile:
    def test_a_missing_named_baseline_fails(self, eval_dir: Path) -> None:
        (eval_dir / "budgets.toml").write_text(
            'baseline = "results/gone.json"\n', encoding="utf-8"
        )
        code, results = run_score(eval_dir)
        assert code == 1
        assert [c["name"] for c in results["verdict"]["checks"] if not c["ok"]] == [
            "baseline"
        ]

    def test_the_baseline_run_is_not_compared_with_itself(self, eval_dir: Path) -> None:
        (eval_dir / "budgets.toml").write_text(
            'baseline = "results/run.json"\n', encoding="utf-8"
        )
        code, results = run_score(eval_dir)
        assert code == 0
        assert any("this run is the baseline" in n for n in results["verdict"]["notes"])

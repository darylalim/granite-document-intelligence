"""Phase B: pages, the schema checks, the prompt, parsing, merging, and a fake model."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import PAGE_H, PAGE_W

from granite_document_intelligence import config, kvp

SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "name": {"type": ["string", "null"]},
        "ein": {"type": ["string", "null"]},
    },
    "required": ["name", "ein"],
    "additionalProperties": False,
}


class TestPages:
    @pytest.mark.parametrize(
        "spec, expected",
        [
            pytest.param("1", (1,), id="one"),
            pytest.param("1,3-4", (1, 3, 4), id="list-and-range"),
            pytest.param("4-5,1", (1, 4, 5), id="sorted"),
            pytest.param("2,2,1-2", (1, 2), id="deduplicated"),
            pytest.param(" 1, 3 ", (1, 3), id="spaces"),
            pytest.param("all", "all", id="all"),
            pytest.param("ALL", "all", id="all-any-case"),
        ],
    )
    def test_parses(self, spec: str, expected: object) -> None:
        assert kvp.parse_pages(spec) == expected

    @pytest.mark.parametrize(
        "spec",
        ["", "0", "3-1", "1-", "-2", "a", "1;2", "1.5", "0-2"],
        ids=["empty", "zero", "backwards", "open-end", "open-start", "word",
             "semicolon", "decimal", "zero-range"],
    )  # fmt: skip
    def test_refuses(self, spec: str) -> None:
        with pytest.raises(ValueError, match="pages"):
            kvp.parse_pages(spec)

    def test_all_resolves_to_every_page(self) -> None:
        assert kvp.resolve_pages("all", 3) == [1, 2, 3]

    def test_a_page_past_the_end_is_refused(self) -> None:
        with pytest.raises(ValueError, match="page 4, but the PDF has 3"):
            kvp.resolve_pages("1,4", 3)


class TestParseJsonOutput:
    # Ported with the parser from the old app (v0.6.0), where it was proven.
    @pytest.mark.parametrize(
        "raw, expected",
        [
            pytest.param('{"a": 1}', {"a": 1}, id="plain"),
            pytest.param(
                'Here you go: {"a": 1} done', {"a": 1}, id="embedded-in-prose"
            ),
            pytest.param(
                '```json\n{"name": "Alex"}\n```', {"name": "Alex"}, id="code-fence"
            ),
            pytest.param(
                'prefix {"a": 1} middle {"b": 2} suffix',
                {"a": 1},
                id="first-of-multiple",
            ),
            pytest.param(
                'I think {maybe} the answer is {"name": "Alex"}',
                {"name": "Alex"},
                id="recovers-after-stray-braces",
            ),
            pytest.param("not json at all", None, id="not-json"),
            pytest.param("{not: valid}", None, id="malformed-braces"),
            pytest.param("[1, 2, 3]", None, id="top-level-array"),
            # The whole-string fast path returns only on a dict, so an array of
            # objects falls through to the `{` scan and yields its first object.
            # Pinned so the asymmetry is a decision rather than a discovery.
            pytest.param(
                '[{"a": 1}, {"b": 2}]', {"a": 1}, id="first-object-inside-array"
            ),
            pytest.param("true", None, id="scalar-bool"),
            pytest.param("42", None, id="scalar-int"),
            pytest.param('"hello"', None, id="scalar-string"),
        ],
    )
    def test_parse_json_output(self, raw: str, expected: dict | None) -> None:
        assert kvp.parse_json_output(raw) == expected


class TestPrompt:
    def test_is_doclings_granite_vision_prompt(self) -> None:
        # Our copy (MIT, THIRD_PARTY_NOTICES.md) must stay the text Docling sends;
        # this fails if a Docling upgrade changes it.
        from docling.models.extraction.prompt_utils import _build_extraction_prompt

        assert kvp.extraction_prompt("{}") == _build_extraction_prompt("{}")

    def test_carries_the_schema_as_docling_serializes_a_dict(self) -> None:
        prompt = kvp.schema_prompt(SCHEMA)
        assert json.dumps(SCHEMA, indent=2) in prompt
        assert prompt.startswith("Extract structured data from this document image.")
        assert prompt.endswith("Return ONLY valid JSON, no other text.")

    def test_the_copy_carries_its_notice(self) -> None:
        root = Path(__file__).parent.parent
        source = (root / "src/granite_document_intelligence/kvp.py").read_text()
        assert "THIRD_PARTY_NOTICES.md" in source
        notices = (root / "THIRD_PARTY_NOTICES.md").read_text()
        assert "Copyright The Docling Contributors" in notices
        assert "Permission is hereby granted, free of charge" in notices


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("attempted a network connection")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class TestSchema:
    def _write(self, tmp_path: Path, content: object) -> Path:
        path = tmp_path / "fields.json"
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def test_a_good_schema_loads(self, tmp_path: Path) -> None:
        assert kvp.load_schema(self._write(tmp_path, SCHEMA)) == SCHEMA

    @pytest.mark.parametrize(
        "ref",
        ["https://example.com/s.json", "other.json", "https://example.com/s#/x"],
        ids=["url", "relative-file", "url-fragment"],
    )
    @pytest.mark.usefixtures("no_network")
    def test_a_remote_ref_is_refused_without_a_network_attempt(
        self, tmp_path: Path, ref: str
    ) -> None:
        schema = {"type": "object", "properties": {"a": {"$ref": ref}}}
        with pytest.raises(kvp.SchemaError, match="refers outside itself"):
            kvp.load_schema(self._write(tmp_path, schema))

    def test_a_local_ref_is_fine(self, tmp_path: Path) -> None:
        schema = {
            "type": "object",
            "$defs": {"s": {"type": "string"}},
            "properties": {"a": {"$ref": "#/$defs/s"}},
        }
        assert kvp.load_schema(self._write(tmp_path, schema)) == schema

    @pytest.mark.usefixtures("no_network")
    def test_the_validator_fetches_nothing_even_past_the_check(self) -> None:
        # The second guard: a remote ref that got past check_schema still fails
        # locally rather than being fetched.
        from referencing.exceptions import Unresolvable

        schema = {"$ref": "https://example.com/s.json"}
        with pytest.raises(Unresolvable):
            list(kvp.validator(schema).iter_errors({}))

    def test_a_schema_over_256_kb_is_refused_before_reading(
        self, tmp_path: Path
    ) -> None:
        path = self._write(tmp_path, " " * (config.KVP_MAX_SCHEMA_BYTES + 1))
        with pytest.raises(kvp.SchemaError, match="the limit is 262,144"):
            kvp.load_schema(path)

    @pytest.mark.parametrize(
        "content", ["[]", '"x"', "3"], ids=["array", "string", "number"]
    )
    def test_a_schema_that_is_not_an_object_is_refused(
        self, tmp_path: Path, content: str
    ) -> None:
        with pytest.raises(kvp.SchemaError, match="JSON object"):
            kvp.load_schema(self._write(tmp_path, content))

    def test_invalid_json_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(kvp.SchemaError, match="not JSON"):
            kvp.load_schema(self._write(tmp_path, "{"))

    def test_an_invalid_schema_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(kvp.SchemaError, match="not a valid JSON Schema"):
            kvp.load_schema(self._write(tmp_path, {"type": "objekt"}))

    def test_a_missing_file_is_a_schema_error(self, tmp_path: Path) -> None:
        with pytest.raises(kvp.SchemaError, match="cannot read"):
            kvp.load_schema(tmp_path / "nope.json")


class TestMerge:
    def test_first_non_null_in_page_order(self) -> None:
        merged, conflicts = kvp.merge(
            {2: {"name": "B", "ein": "12"}, 1: {"name": None, "ein": "12"}, 3: None}
        )
        assert merged == {"name": "B", "ein": "12"}
        assert conflicts == []

    def test_disagreeing_pages_are_listed_not_resolved(self) -> None:
        merged, conflicts = kvp.merge({1: {"name": "A"}, 2: {"name": "B"}})
        assert merged == {"name": "A"}
        assert conflicts == [{"field": "name", "values": {"1": "A", "2": "B"}}]

    def test_no_answers_merge_to_nothing(self) -> None:
        assert kvp.merge({1: None}) == ({}, [])


class FakeVision:
    """Answers every page with the next canned response; records sizes and prompts."""

    def __init__(self, *responses: tuple[str, int]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[tuple[int, int], str, int]] = []
        self.closed = False

    def generate_counted(
        self, image: Any, prompt: str, max_tokens: int
    ) -> tuple[str, int]:
        self.calls.append((image.size, prompt, max_tokens))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def two_page_pdf(tmp_path: Path) -> Path:
    import pypdfium2

    pdf = pypdfium2.PdfDocument.new()
    pdf.new_page(PAGE_W, PAGE_H)
    pdf.new_page(PAGE_W, PAGE_H)
    path = tmp_path / "form.pdf"
    pdf.save(path)
    pdf.close()
    return path


GOOD = ('```json\n{"name": "Alex Q. Sample", "ein": null}\n```', 30)


class TestRun:
    def test_sends_the_page_at_2x_with_the_schema_prompt(
        self, two_page_pdf: Path
    ) -> None:
        vision = FakeVision(GOOD)
        run = kvp.run(two_page_pdf, SCHEMA, [1], Path("unused"), vision=vision)  # ty: ignore[invalid-argument-type]
        ((size, prompt, max_tokens),) = vision.calls
        assert size == (round(PAGE_W * 2), round(PAGE_H * 2))
        assert prompt == kvp.schema_prompt(SCHEMA)
        assert max_tokens == config.KVP_MAX_NEW_TOKENS
        result = run.result
        assert result.pages == {1: {"name": "Alex Q. Sample", "ein": None}}
        assert result.merged == {"name": "Alex Q. Sample", "ein": None}
        assert (result.valid, result.errors) == (True, [])
        assert result.raw == {1: GOOD[0]}
        assert run.warnings == []
        assert len(run.page_ms) == 1

    def test_a_shared_model_is_left_loaded(self, two_page_pdf: Path) -> None:
        # The chart stage's instance (§3.8): the caller releases it, not Phase B.
        vision = FakeVision(GOOD)
        kvp.run(two_page_pdf, SCHEMA, [1], Path("unused"), vision=vision)  # ty: ignore[invalid-argument-type]
        assert vision.closed is False

    def test_without_one_it_loads_and_releases_its_own(
        self, two_page_pdf: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from granite_document_intelligence import charts

        vision = FakeVision(GOOD)
        monkeypatch.setattr(charts, "GraniteVision", lambda _: vision)
        kvp.run(two_page_pdf, SCHEMA, [1], Path("unused"))
        assert vision.closed is True

    def test_an_off_schema_answer_is_recorded_not_raised(
        self, two_page_pdf: Path
    ) -> None:
        vision = FakeVision(('{"name": 7, "ein": null, "extra": "x"}', 20))
        result = kvp.run(two_page_pdf, SCHEMA, [1], Path("u"), vision=vision).result  # ty: ignore[invalid-argument-type]
        assert result.valid is False
        assert any(e.startswith("name:") for e in result.errors)
        assert any("extra" in e for e in result.errors)
        assert result.merged["name"] == 7

    def test_no_json_keeps_the_raw_output_and_is_invalid(
        self, two_page_pdf: Path
    ) -> None:
        vision = FakeVision(("I cannot read this form.", 8))
        run = kvp.run(two_page_pdf, SCHEMA, [1], Path("u"), vision=vision)  # ty: ignore[invalid-argument-type]
        assert run.result.pages == {1: None}
        assert run.result.raw == {1: "I cannot read this form."}
        assert run.result.valid is False
        assert run.result.errors == ["no page produced a JSON object"]
        assert any("no JSON object" in w for w in run.warnings)

    def test_a_capped_generation_is_a_warning(self, two_page_pdf: Path) -> None:
        vision = FakeVision(("{", config.KVP_MAX_NEW_TOKENS))
        run = kvp.run(two_page_pdf, SCHEMA, [1], Path("u"), vision=vision)  # ty: ignore[invalid-argument-type]
        assert any("token cap" in w for w in run.warnings)

    def test_several_pages_merge_and_report_conflicts(self, two_page_pdf: Path) -> None:
        vision = FakeVision(
            ('{"name": "A", "ein": null}', 20), ('{"name": "B", "ein": "12-3"}', 20)
        )
        run = kvp.run(two_page_pdf, SCHEMA, [1, 2], Path("u"), vision=vision)  # ty: ignore[invalid-argument-type]
        assert run.result.merged == {"name": "A", "ein": "12-3"}
        assert run.result.conflicts == [
            {"field": "name", "values": {"1": "A", "2": "B"}}
        ]
        assert any("disagree on name" in w for w in run.warnings)

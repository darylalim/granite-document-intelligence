"""Chunks from a hand-built DoclingDocument, with a word-count tokenizer (no download)."""

import json
import sys
import types
from typing import Any

import pytest
from conftest import build_document

from granite_document_intelligence import chunk, config
from granite_document_intelligence.export import chunk_record


def words_tokenizer(max_tokens: int) -> Any:
    """A `BaseTokenizer` that counts whitespace-separated words."""
    from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer

    class Words(BaseTokenizer):
        limit: int

        def count_tokens(self, text: str) -> int:
            return len(text.split())

        def get_max_tokens(self) -> int:
            return self.limit

        def get_tokenizer(self) -> None:
            return None

    return Words(limit=max_tokens)


def charted_document() -> Any:
    """`build_document` with the chart stage's results on its chart, as charts.run leaves them."""
    from docling_core.types.doc import DescriptionMetaField, TabularChartMetaField

    from granite_document_intelligence.charts import rows_to_table_data

    doc = build_document()
    meta = doc.pictures[0].meta
    meta.description = DescriptionMetaField(
        text="Revenue is highest in the North.", created_by="test"
    )
    meta.tabular_chart = TabularChartMetaField(
        chart_data=rows_to_table_data([["Region", "Revenue"], ["North", "120"]]),
        created_by="test",
    )
    return doc


class TestChunkDocument:
    def test_carries_headings_pages_and_refs(self) -> None:
        chunks = chunk.chunk_document(build_document(), words_tokenizer(512))
        assert chunks, "no chunks"
        first = chunks[0]
        assert first.id == "chunk-0001"
        assert first.headings == ["Quarterly report"]
        assert first.page_nos == [1]
        assert "#/texts/1" in first.refs
        assert "Revenue rose in every region." in first.text
        # The heading-prefixed text meant for embedding (§3.9).
        assert first.contextualized.startswith("Quarterly report")

    def test_ids_are_numbered_in_order(self) -> None:
        chunks = chunk.chunk_document(build_document(), words_tokenizer(8))
        assert [c.id for c in chunks] == [
            f"chunk-{i:04d}" for i in range(1, len(chunks) + 1)
        ]

    def test_a_small_budget_splits_into_more_chunks(self) -> None:
        big = chunk.chunk_document(build_document(), words_tokenizer(512))
        small = chunk.chunk_document(build_document(), words_tokenizer(8))
        assert len(small) > len(big)

    def test_the_chart_summary_and_csv_reach_the_chunk_citing_it(self) -> None:
        # S7: no separate summary chunk is needed, because both travel with the
        # picture's chunk.
        chunks = chunk.chunk_document(charted_document(), words_tokenizer(512))
        chart_ref = "#/pictures/0"
        (cited,) = [c for c in chunks if chart_ref in c.refs]
        assert "Revenue is highest in the North." in cited.text
        assert "North" in cited.text and "120" in cited.text

    def test_every_line_is_json_with_the_documented_keys(self) -> None:
        for c in chunk.chunk_document(charted_document(), words_tokenizer(512)):
            line = json.loads(json.dumps(chunk_record(c)))
            assert set(line) == {
                "id",
                "text",
                "contextualized",
                "headings",
                "page_nos",
                "doc_item_refs",
            }


class TestLoadTokenizer:
    def test_loads_the_embedding_tokenizer_at_its_pin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The pin reaching the loader is what makes it a pin (§3.13 rule 1).
        seen: dict[str, Any] = {}

        class HuggingFaceTokenizer:
            @classmethod
            def from_pretrained(cls, **kwargs: Any) -> str:
                seen.update(kwargs)
                return "tokenizer"

        module = types.ModuleType(
            "docling_core.transforms.chunker.tokenizer.huggingface"
        )
        module.HuggingFaceTokenizer = HuggingFaceTokenizer  # ty: ignore[unresolved-attribute]
        monkeypatch.setitem(sys.modules, module.__name__, module)
        assert chunk.load_tokenizer(256) == "tokenizer"
        assert seen == {
            "model_name": config.EMBEDDING_TOKENIZER.repo_id,
            "max_tokens": 256,
            "revision": config.EMBEDDING_TOKENIZER.revision,
            "trust_remote_code": False,
        }

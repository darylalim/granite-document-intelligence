"""RAG chunks from the converted document (plan §3.9).

Docling's `HybridChunker` splits by heading hierarchy first, then by token
count, and merges undersized neighbours under the same headings. Its token
counts come from the tokenizer of `granite-embedding-english-r2`, the Granite
embedding model a later stage would most likely use, so a chunk's size is
measured the way it would be embedded. Chart summaries and CSVs need nothing
extra: they reach the chunk that cites the chart, after its caption (S7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from granite_document_intelligence import config
from granite_document_intelligence.api import Chunk

if TYPE_CHECKING:
    from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer
    from docling_core.types.doc import DoclingDocument


def load_tokenizer(chunk_tokens: int) -> BaseTokenizer:
    """The embedding model's tokenizer at its pinned revision (§3.13 rule 1).

    It comes from the Hugging Face cache, where `gdi convert-model` puts it, so
    an offline run (`HF_HUB_OFFLINE=1`) finds it. Only tokenizer files are read;
    no repo code runs (S10).
    """
    from docling_core.transforms.chunker.tokenizer.huggingface import (
        HuggingFaceTokenizer,
    )

    pinned = config.EMBEDDING_TOKENIZER
    return HuggingFaceTokenizer.from_pretrained(
        model_name=pinned.repo_id,
        max_tokens=chunk_tokens,
        revision=pinned.revision,
        trust_remote_code=False,
    )


def chunk_document(document: DoclingDocument, tokenizer: BaseTokenizer) -> list[Chunk]:
    """The document's chunks, in reading order, numbered from `chunk-0001`."""
    from docling_core.transforms.chunker.doc_chunk import DocChunk
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker

    chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)
    chunks: list[Chunk] = []
    for index, base in enumerate(chunker.chunk(dl_doc=document), start=1):
        chunk = DocChunk.model_validate(base)
        items = chunk.meta.doc_items
        chunks.append(
            Chunk(
                id=f"chunk-{index:04d}",
                text=chunk.text,
                contextualized=chunker.contextualize(chunk=chunk),
                headings=list(chunk.meta.headings or []),
                page_nos=sorted({prov.page_no for item in items for prov in item.prov}),
                refs=[item.self_ref for item in items],
            )
        )
    return chunks


def run(document: DoclingDocument, chunk_tokens: int) -> list[Chunk]:
    return chunk_document(document, load_tokenizer(chunk_tokens))

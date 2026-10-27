"""Phase B: key-value extraction against a user's JSON Schema (plan §3.3).

Docling has KVP extraction, but only on transformers, which on this Mac means a
bf16 copy of Granite Vision on MPS — the memory problem §2 exists to avoid. So
the page goes to our 8-bit Granite Vision through mlx-vlm, with Docling's own
prompt for Granite Vision, greedy. The output is parsed defensively and
validated; an off-schema answer is recorded, not raised (§7).

Everything the model returns is data, never instructions (§3.10): nothing here
acts on it beyond parsing, validating and handing it back.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from granite_document_intelligence import config, memory
from granite_document_intelligence.api import KvpResult

if TYPE_CHECKING:
    from jsonschema.protocols import Validator
    from PIL.Image import Image

    from granite_document_intelligence.charts import GraniteVision

_PAGES = re.compile(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*")


class SchemaError(ValueError):
    """A KVP schema that is refused before any model loads (§3.13 rule 8)."""


# --- Pages --------------------------------------------------------------------


def parse_pages(spec: str) -> tuple[int, ...] | Literal["all"]:
    """`--pages`: `all`, or page numbers and ranges such as `1,3-4`, ascending.

    Raises ValueError on anything else. Page numbers are 1-based, as Docling and
    the PDF viewer number them; whether they exist is checked against the PDF.
    """
    cleaned = spec.replace(" ", "")
    if cleaned.lower() == "all":
        return "all"
    if not _PAGES.fullmatch(cleaned):
        raise ValueError(f"pages must be 'all' or like '1,3-4', not {spec!r}")
    pages: set[int] = set()
    for part in cleaned.split(","):
        first, _, last = part.partition("-")
        start, end = int(first), int(last or first)
        if start < 1 or end < start:
            raise ValueError(f"pages: {part!r} is not a range of pages from 1")
        pages.update(range(start, end + 1))
    return tuple(sorted(pages))


def resolve_pages(spec: str, page_count: int) -> list[int]:
    """The pages `spec` names in a PDF of `page_count` pages; ValueError past the end."""
    pages = parse_pages(spec)
    if pages == "all":
        return list(range(1, page_count + 1))
    missing = [p for p in pages if p > page_count]
    if missing:
        raise ValueError(
            f"pages {spec!r} names page {missing[0]}, but the PDF has {page_count}"
        )
    return list(pages)


# --- The schema ---------------------------------------------------------------


def _non_local_refs(value: Any, found: list[str]) -> list[str]:
    """Every `$ref` / `$dynamicRef` that points anywhere but inside the schema."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("$ref", "$dynamicRef") and isinstance(item, str):
                if not item.startswith("#"):
                    found.append(item)
            else:
                _non_local_refs(item, found)
    elif isinstance(value, list):
        for item in value:
            _non_local_refs(item, found)
    return found


def check_schema(schema: Any) -> dict[str, Any]:
    """`schema` if it is a usable local JSON Schema object; SchemaError otherwise."""
    from jsonschema.exceptions import SchemaError as InvalidSchema
    from jsonschema.validators import validator_for

    if not isinstance(schema, dict):
        raise SchemaError("the schema must be a JSON object")
    remote = _non_local_refs(schema, [])
    if remote:
        raise SchemaError(
            f"the schema refers outside itself ({remote[0]!r}); references are "
            "resolved locally only, so inline the definition or use '#/$defs/…'"
        )
    try:
        validator_for(schema).check_schema(schema)
    except InvalidSchema as error:
        raise SchemaError(f"not a valid JSON Schema: {error.message}") from None
    return schema


def load_schema(path: Path) -> dict[str, Any]:
    """Read and check a schema file before any model loads (§3.13 rule 8).

    Refuses a file over `KVP_MAX_SCHEMA_BYTES` before reading it, anything that
    is not a JSON object, an invalid schema, and any reference outside the file.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as error:
        raise SchemaError(f"cannot read the schema {path}: {error.strerror}") from None
    if size > config.KVP_MAX_SCHEMA_BYTES:
        raise SchemaError(
            f"the schema {path} is {size:,} bytes; the limit is "
            f"{config.KVP_MAX_SCHEMA_BYTES:,}"
        )
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SchemaError(f"the schema {path} is not JSON: {error}") from None
    return check_schema(schema)


def validator(schema: dict[str, Any]) -> Validator:
    """A validator for `schema` that can never fetch anything (§3.13 rule 8).

    jsonschema still falls back to fetching unknown `$ref` URLs when no registry
    is given, so an empty `referencing.Registry` — one with no retrieve function —
    is passed explicitly rather than relied on as a default. `check_schema` has
    already refused non-local references; this is the second guard.
    """
    from jsonschema.validators import validator_for
    from referencing import Registry

    return validator_for(schema)(schema, registry=Registry())


# --- The prompt and the answer ------------------------------------------------


# Copied from Docling 2.143.1, docling/models/extraction/prompt_utils.py,
# `_build_extraction_prompt` (private, so not importable as API). Copyright The
# Docling Contributors, MIT License; the full notice is in THIRD_PARTY_NOTICES.md.
def extraction_prompt(template: str) -> str:
    return (
        "Extract structured data from this document image.\n"
        "Return a JSON object matching this schema:\n\n"
        f"{template}\n\n"
        "Return null for fields you cannot find in the document.\n"
        "Return ONLY valid JSON, no other text."
    )


def schema_prompt(schema: dict[str, Any]) -> str:
    """The prompt for `schema`, serialized as Docling serializes a dict template."""
    return extraction_prompt(json.dumps(schema, indent=2))


def parse_json_output(raw: str) -> dict[str, Any] | None:
    """The first JSON object in the model's output, or None.

    Ported with its tests from the old app (v0.6.0), where it was proven on four
    JSON features. Tolerates surrounding prose and code fences, and ignores
    non-object JSON (arrays, scalars) — except that an array of objects yields
    its first object, since the whole-string fast path returns only on a dict.
    """
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            value, _ = decoder.raw_decode(raw[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def merge(
    pages: dict[int, dict[str, Any] | None],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One object from several pages' answers, and the fields they disagree on.

    Each field takes its first non-null value in page order (§3.3). A field
    with two or more different non-null values is listed under conflicts with
    every page's value, rather than silently resolved.
    """
    merged: dict[str, Any] = {}
    seen: dict[str, dict[int, Any]] = {}
    for page_no in sorted(pages):
        answer = pages[page_no]
        if answer is None:
            continue
        for key, value in answer.items():
            if key not in merged or merged[key] is None:
                merged[key] = value
            if value is not None:
                seen.setdefault(key, {})[page_no] = value
    conflicts = []
    for key, by_page in seen.items():
        distinct = []
        for value in by_page.values():
            if value not in distinct:
                distinct.append(value)
        if len(distinct) > 1:
            conflicts.append(
                {"field": key, "values": {str(p): v for p, v in by_page.items()}}
            )
    return merged, conflicts


# --- The run ------------------------------------------------------------------


def render_page(pdf: Any, page_no: int) -> Image:
    """Page `page_no` of an open `pypdfium2.PdfDocument` at `KVP_PAGE_SCALE`."""
    page = pdf[page_no - 1]
    return page.render(scale=config.KVP_PAGE_SCALE).to_pil().convert("RGB")


@dataclass
class KvpRun:
    result: KvpResult
    page_ms: list[float]
    warnings: list[str]


def run(
    pdf_path: Path,
    schema: dict[str, Any],
    pages: list[int],
    models_dir: Path,
    *,
    vision: GraniteVision | None = None,
) -> KvpRun:
    """Extract `schema`'s fields from each of `pages`, merged and validated.

    Uses the caller's `vision` if given — the chart stage's instance, so Phase B
    loads nothing (§3.8) — and otherwise loads Granite Vision and releases it
    before returning.
    """
    import pypdfium2

    from granite_document_intelligence.charts import GraniteVision

    prompt = schema_prompt(schema)
    owned = vision is None
    model = vision or GraniteVision(models_dir)
    answers: dict[int, dict[str, Any] | None] = {}
    raw: dict[int, str] = {}
    page_ms: list[float] = []
    warnings: list[str] = []
    pdf = pypdfium2.PdfDocument(pdf_path)
    try:
        for page_no in pages:
            image = render_page(pdf, page_no)
            start = time.perf_counter()
            text, tokens = model.generate_counted(
                image, prompt, config.KVP_MAX_NEW_TOKENS
            )
            page_ms.append((time.perf_counter() - start) * 1000)
            raw[page_no] = text
            answers[page_no] = parse_json_output(text)
            if tokens >= config.KVP_MAX_NEW_TOKENS:
                warnings.append(
                    f"kvp (page {page_no}): hit the {config.KVP_MAX_NEW_TOKENS}-token cap"
                )
            if answers[page_no] is None:
                warnings.append(f"kvp (page {page_no}): no JSON object in the output")
    finally:
        pdf.close()
        if owned:
            model.close()
            del model
            memory.release()

    merged, conflicts = merge(answers)
    errors = (
        sorted(
            (
                f"{'/'.join(str(p) for p in error.absolute_path) or '(root)'}: {error.message}"
                for error in validator(schema).iter_errors(merged)
            ),
        )
        if any(answer is not None for answer in answers.values())
        else ["no page produced a JSON object"]
    )
    if errors:
        warnings.append(
            f"kvp: the answer does not match the schema ({len(errors)} error(s))"
        )
    if conflicts:
        warnings.append(
            "kvp: pages disagree on "
            + ", ".join(conflict["field"] for conflict in conflicts)
        )
    result = KvpResult(
        pages=answers,
        merged=merged,
        conflicts=conflicts,
        valid=not errors,
        errors=errors,
        raw=raw,
    )
    return KvpRun(result, page_ms, warnings)

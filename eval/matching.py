"""How a prediction is compared with an expectation (plan §8.2 rules 2–4).

Every rule a score depends on lives here, as a pure function over strings and
rows, so `tests/test_score.py` can show each one failing on a bad prediction
(rule 9) without a model, a PDF or Docling. `score.py` reads the files and
calls these; it decides nothing about matching itself.

The expected files keep the source's own spelling — `$14.6`, `(0.2)`, `*1.3`,
`–0.7`, `X` — and the normalization is ours (`eval/expected/README.md`).
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from granite_document_intelligence.charts import summary_disagreements

Rows = list[list[str]]

# Rule 3: 1% relative, for chart and table numbers alike.
REL_TOL = 0.01

# --- Values (rule 3) ----------------------------------------------------------

_MINUS = str.maketrans({"−": "-", "–": "-"})
_SUPERSCRIPTS = str.maketrans("", "", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_CURRENCY = "$€£¥"
_PLAIN_NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+")


def normalize_cell(text: str) -> str:
    """NFKC, straight quotes and collapsed whitespace: what a cell is compared as."""
    text = unicodedata.normalize("NFKC", text.translate(_QUOTES))
    return " ".join(text.split())


def parse_number(text: str) -> float | None:
    """A cell's number under rule 3, or None for a code (`X`, `Z`) or a word.

    Thousands separators, currency symbols and a trailing `%` go; U+2212 and
    U+2013 are minus signs; a leading significance asterisk (`*1.3`) goes; a
    parenthesized value is negative, as the GAO tables print it (`(0.2)` beside
    a −9% change).
    """
    s = normalize_cell(text).translate(_MINUS).replace(" ", "")
    s = s.lstrip("*")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    sign = ""
    if s and s[0] in "+-":
        sign, s = s[0], s[1:]
    s = s.lstrip(_CURRENCY)
    s = s.removesuffix("%")
    if not s or not _PLAIN_NUMBER.fullmatch(s):
        return None
    value = float(sign + s.replace(",", ""))
    return -value if negative else value


def numbers_close(a: float, b: float, rel_tol: float = REL_TOL) -> bool:
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=1e-9)


def cells_match(expected: str, predicted: str, rel_tol: float = REL_TOL) -> bool:
    """Numbers within `rel_tol`; anything else equal after normalization, any case."""
    e, p = parse_number(expected), parse_number(predicted)
    if e is not None and p is not None:
        return numbers_close(e, p, rel_tol)
    if e is not None or p is not None:
        return False
    return normalize_cell(expected).casefold() == normalize_cell(predicted).casefold()


def normalize_label(text: str) -> str:
    """A row label or header as rule 3 compares it: case- and whitespace-insensitive.

    Footnote superscripts go first, because NFKC would turn `2017⁴` into `20174`.
    """
    return normalize_cell(text.translate(_SUPERSCRIPTS)).casefold()


def _non_empty(rows: Rows) -> Iterable[tuple[int, int, str]]:
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell.strip():
                yield r, c, cell


def _rectangular(rows: Rows) -> Rows:
    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows]


def transpose(rows: Rows) -> Rows:
    return [list(col) for col in zip(*_rectangular(rows), strict=True)]


# --- Charts (rule 3) ------------------------------------------------------------


@dataclass
class ChartScore:
    """Value cells of one expected chart found in a prediction.

    `cells` counts the expected CSV's non-empty value cells (every column but
    the first, every row but the header), the denominator S6 used (69 on the
    public corpus). `headers` counts the expected header row's cells.
    """

    cells: int
    matched: int
    headers: int
    headers_matched: int
    transposed: bool = False


def _assign_columns(scores: dict[tuple[int, int], int]) -> dict[int, int]:
    """Each expected column to at most one predicted column, most matches first."""
    taken_e: set[int] = set()
    taken_p: set[int] = set()
    assigned: dict[int, int] = {}
    for (e, p), n in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        if n > 0 and e not in taken_e and p not in taken_p:
            assigned[e] = p
            taken_e.add(e)
            taken_p.add(p)
    return assigned


def _chart_orientation(
    expected: Rows, predicted: Rows, rel_tol: float
) -> tuple[int, int]:
    """(value cells matched, header cells matched) with rows paired by label.

    Rows pair by their first-column label, since a pie's slice order is
    arbitrary. Columns pair by content: each expected series takes the predicted
    column agreeing with it on the most rows, so a generic `Value` header costs
    the header score and nothing else, and a merged multi-panel picture holding
    one column per panel (S7) credits each panel's own column.
    """
    if not expected or not predicted:
        return 0, 0
    predicted = _rectangular(predicted)
    by_label: dict[str, list[str]] = {}
    for row in predicted[1:]:
        if row:
            by_label.setdefault(normalize_label(row[0]), row)
    pairs = [
        (row, by_label.get(normalize_label(row[0]))) for row in expected[1:] if row
    ]
    width = len(predicted[0])
    scores: dict[tuple[int, int], int] = {}
    for j in range(1, max((len(r) for r in expected), default=0)):
        for k in range(1, width):
            scores[j, k] = sum(
                1
                for e, p in pairs
                if p is not None
                and j < len(e)
                and e[j].strip()
                and cells_match(e[j], p[k], rel_tol)
            )
    assigned = _assign_columns(scores)
    matched = sum(scores[j, k] for j, k in assigned.items())
    header = expected[0]
    pred_header = predicted[0]
    headers_matched = sum(
        1
        for j, cell in enumerate(header)
        if cell.strip()
        and (k := 0 if j == 0 else assigned.get(j)) is not None
        and normalize_label(cell) == normalize_label(pred_header[k])
    )
    return matched, headers_matched


def score_chart(
    expected: Rows, predicted: Rows, rel_tol: float = REL_TOL
) -> ChartScore:
    """Score one chart, trying the prediction as written and transposed.

    Granite Vision sometimes writes a chart's categories across the header row
    instead of down the first column (S6/S7 scored both ways); the better
    orientation counts.
    """
    cells = sum(1 for r, c, _ in _non_empty(expected) if r > 0 and c > 0)
    headers = sum(1 for cell in (expected[0] if expected else []) if cell.strip())
    straight = _chart_orientation(expected, predicted, rel_tol)
    flipped = _chart_orientation(expected, transpose(predicted), rel_tol)
    if flipped[0] > straight[0]:
        return ChartScore(cells, flipped[0], headers, flipped[1], transposed=True)
    return ChartScore(cells, straight[0], headers, straight[1])


# --- Tables (rule 3) ------------------------------------------------------------


@dataclass
class TableScore:
    """One expected table against a prediction.

    `cells`/`matched`: body cells (every row but the header) paired by
    position. `all_cells`/`found`: every non-empty cell, header included,
    found anywhere in the prediction — the multiset count S11 reported (1198
    public cells), which forgives a shifted column where the positional count
    does not.
    """

    cells: int
    matched: int
    all_cells: int
    found: int
    headers: int
    headers_matched: int


# A row-label column split in two, or an index column added, shifts every
# column; a small search over column offsets keeps that from zeroing a table.
_MAX_COLUMN_OFFSET = 2


@dataclass(frozen=True)
class _Cell:
    key: str  # normalized, case-folded
    number: float | None


def _cell(text: str) -> _Cell:
    return _Cell(normalize_cell(text).casefold(), parse_number(text))


def _same(a: _Cell, b: _Cell, rel_tol: float) -> bool:
    if a.number is not None and b.number is not None:
        return numbers_close(a.number, b.number, rel_tol)
    return a.number is None and b.number is None and a.key == b.key


def _row_matches(
    exp: list[_Cell | None], pred: list[_Cell], dc: int, rel_tol: float
) -> int:
    n = 0
    for c, cell in enumerate(exp):
        pc = c + dc
        if cell is not None and 0 <= pc < len(pred) and _same(cell, pred[pc], rel_tol):
            n += 1
    return n


def _aligned(expected: Rows, predicted: Rows, rel_tol: float) -> tuple[int, int]:
    """(body cells, header cells) matched with rows paired by position.

    "Position" is order, not index: the rows are aligned in order, gaps
    allowed, to maximize matched cells (a weighted longest common
    subsequence). A prediction that splits the header over four rows or drops
    a section's first rows (Census Table A-6) would otherwise shift every
    later row out of place, while two rows with one label (Table A-2's two
    `2017`s) still pair in order — the reason rule 3 pairs by position and
    not by label. The header row is matched against whichever predicted row
    suits it best, since a multi-level header has no single row to pair with.
    """
    if not expected or not predicted:
        return 0, 0
    exp = [[_cell(c) if c.strip() else None for c in row] for row in expected]
    pred = [[_cell(c) for c in row] for row in predicted]
    best = (0, 0)
    for dc in range(-_MAX_COLUMN_OFFSET, _MAX_COLUMN_OFFSET + 1):
        header = max(_row_matches(exp[0], row, dc, rel_tol) for row in pred)
        body = exp[1:]
        # D[i][j]: best total pairing body[:i] with pred[:j], order kept.
        prev = [0] * (len(pred) + 1)
        for i in range(1, len(body) + 1):
            cur = [0] * (len(pred) + 1)
            for j in range(1, len(pred) + 1):
                cur[j] = max(
                    prev[j],
                    cur[j - 1],
                    prev[j - 1] + _row_matches(body[i - 1], pred[j - 1], dc, rel_tol),
                )
            prev = cur
        best = max(best, (prev[-1], header))
    return best


def _multiset_found(expected: Rows, predicted: Rows, rel_tol: float) -> int:
    """Expected cells found anywhere in the prediction, each predicted cell used once."""
    pool = [_cell(c) for _, _, c in _non_empty(predicted)]
    used = [False] * len(pool)
    by_key: dict[str, list[int]] = {}
    for i, cell in enumerate(pool):
        by_key.setdefault(cell.key, []).append(i)
    found = 0
    for _, _, text in _non_empty(expected):
        want = _cell(text)
        hit = next((i for i in by_key.get(want.key, []) if not used[i]), None)
        if hit is None and want.number is not None:
            hit = next(
                (
                    i
                    for i, cell in enumerate(pool)
                    if not used[i] and _same(want, cell, rel_tol)
                ),
                None,
            )
        if hit is not None:
            used[hit] = True
            found += 1
    return found


def score_table(
    expected: Rows, predicted: Rows, rel_tol: float = REL_TOL
) -> TableScore:
    """One expected table against one prediction (see `_aligned` for pairing)."""
    cells = sum(1 for r, _, _ in _non_empty(expected) if r > 0)
    headers = sum(1 for r, _, _ in _non_empty(expected) if r == 0)
    matched, headers_matched = _aligned(expected, predicted, rel_tol)
    return TableScore(
        cells=cells,
        matched=matched,
        all_cells=cells + headers,
        found=_multiset_found(expected, predicted, rel_tol),
        headers=headers,
        headers_matched=headers_matched,
    )


# --- Pairing on a page (rule 3) -------------------------------------------------


@dataclass
class Item:
    """A chart or table on a page: `key` names it in reports, `order` is its
    position in reading order (the expected file's `K`, the prediction's index)."""

    key: str
    page: int
    order: int
    rows: Rows


@dataclass
class Pairing:
    pairs: list[tuple[Item, Item]] = field(default_factory=list)
    missed: list[Item] = field(default_factory=list)
    false_positives: list[Item] = field(default_factory=list)


def pair_items(
    expected: Sequence[Item],
    predicted: Sequence[Item],
    score: Callable[[Rows, Rows], int],
    *,
    shared: bool = False,
) -> Pairing:
    """Pair predictions with expectations page by page.

    Same page and the same count: by reading order. Different counts: by
    content, best score first, one to one. Rule 3 asks for bounding-box IoU
    here, but no manifest records a box (S6), so this is the fallback PR 7
    defines: content, which needs no box and cannot pair two things that
    share nothing. With `shared`, an expectation still unpaired then takes the
    prediction it scores best against even if another took it already: a
    picture covering several expected panels (GAO-07-631 Figure 9) is scored
    against each of them. A prediction that pairs with nothing is a false
    positive (rule 5); an expectation that does is a miss.
    """
    result = Pairing()
    pages = sorted({i.page for i in expected} | {i.page for i in predicted})
    for page in pages:
        exp = sorted((i for i in expected if i.page == page), key=lambda i: i.order)
        pred = sorted((i for i in predicted if i.page == page), key=lambda i: i.order)
        if len(exp) == len(pred):
            result.pairs.extend(zip(exp, pred, strict=True))
            continue
        scored = sorted(
            (
                (score(e.rows, p.rows), ei, pi)
                for ei, e in enumerate(exp)
                for pi, p in enumerate(pred)
            ),
            key=lambda t: (-t[0], t[1], t[2]),
        )
        done_e: set[int] = set()
        done_p: set[int] = set()
        for _, ei, pi in scored:
            if ei not in done_e and pi not in done_p:
                result.pairs.append((exp[ei], pred[pi]))
                done_e.add(ei)
                done_p.add(pi)
        if shared:
            for n, ei, pi in scored:
                if ei not in done_e and n > 0:
                    result.pairs.append((exp[ei], pred[pi]))
                    done_e.add(ei)
        result.missed.extend(e for ei, e in enumerate(exp) if ei not in done_e)
        result.false_positives.extend(
            p for pi, p in enumerate(pred) if pi not in done_p
        )
    result.pairs.sort(key=lambda pair: (pair[0].page, pair[0].order))
    return result


# --- Document text (rule 2) -----------------------------------------------------

_LINE_END_HYPHEN = re.compile(r"(\w)-\s*\n\s*(\w)")
# granite-docling writes a footnote mark as LaTeX (`$^{3}$Office of …`) and the
# Markdown export escapes `&` (`O&amp;M`); the printed page has `3` and `&`.
_LATEX_SUPERSCRIPT = re.compile(r"\$\^\{([^}]*)\}\$")
_WORD = re.compile(r"\w+")


def normalize_text(text: str) -> str:
    """Rule 2's normalization: Markup the prediction carries undone, line-end
    hyphenation joined, NFKC (which also unfolds ligatures), straight quotes,
    whitespace collapsed."""
    text = html.unescape(_LATEX_SUPERSCRIPT.sub(r"\1", text))
    text = _LINE_END_HYPHEN.sub(r"\1\2", text)
    text = unicodedata.normalize("NFKC", text.translate(_QUOTES)).translate(_MINUS)
    return " ".join(text.split())


def levenshtein(a: str, b: str) -> int:
    """Edit distance, one numpy row per character of the longer string.

    Insertions within a row are a running minimum: `row[j] = min_k(row[k] + j - k)`,
    which `minimum.accumulate` computes in one pass. A looped page's 30,000
    characters against a 10,000-character text layer takes about a second.
    """
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    target = np.frombuffer(b.encode("utf-32-le"), dtype=np.uint32)
    index = np.arange(len(b) + 1)
    row = index.copy()
    for i, ch in enumerate(a, start=1):
        cost = (target != ord(ch)).astype(np.int64)
        nxt = np.empty_like(row)
        nxt[0] = i
        np.minimum(row[1:] + 1, row[:-1] + cost, out=nxt[1:])
        row = np.minimum.accumulate(nxt - index) + index
    return int(row[-1])


def text_similarity(predicted: str, reference: str) -> float:
    """1 − normalized edit distance, on normalized text; 1.0 when both are empty."""
    p, r = normalize_text(predicted), normalize_text(reference)
    longest = max(len(p), len(r))
    return 1.0 if longest == 0 else 1 - levenshtein(p, r) / longest


def word_f1(predicted: str, reference: str) -> float:
    """F1 of the two texts' word multisets: forgiving of order, unlike similarity."""
    p = Counter(w.casefold() for w in _WORD.findall(normalize_text(predicted)))
    r = Counter(w.casefold() for w in _WORD.findall(normalize_text(reference)))
    if not p and not r:
        return 1.0
    common = sum((p & r).values())
    if common == 0:
        return 0.0
    precision, recall = common / sum(p.values()), common / sum(r.values())
    return 2 * precision * recall / (precision + recall)


def kendall_tau(order: Sequence[float]) -> float | None:
    """Rank correlation of `order` with its own sorted order; None below two items."""
    n = len(order)
    if n < 2:
        return None
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if order[i] < order[j]:
                concordant += 1
            elif order[i] > order[j]:
                discordant += 1
    total = concordant + discordant
    return None if total == 0 else (concordant - discordant) / total


# A paragraph is located in the reference by its opening; shorter ones are too
# common ("Page 2", "Table 1") to place.
_PLACE_CHARS = 30


def reading_order(paragraphs: Sequence[str], reference: str) -> float | None:
    """Rule 2's reading order: Kendall's tau of where each predicted paragraph
    begins in the reference, taken in the prediction's order. Paragraphs the
    reference does not contain are left out; None when fewer than two remain."""
    ref = normalize_text(reference).casefold()
    positions: list[float] = []
    for paragraph in paragraphs:
        opening = normalize_text(paragraph).casefold()[:_PLACE_CHARS]
        if len(opening) < _PLACE_CHARS:
            continue
        at = ref.find(opening)
        if at >= 0:
            positions.append(at)
    return kendall_tau(positions)


def headings_found(expected: Iterable[str], markdown: str) -> list[bool]:
    """Each expected heading present as a Markdown heading line, any case or spacing."""
    present = {
        normalize_label(normalize_text(line.lstrip("#")))
        for line in markdown.splitlines()
        if line.startswith("#")
    }
    return [normalize_label(normalize_text(h)) in present for h in expected]


def headings_in_text(expected: Iterable[str], markdown: str) -> list[bool]:
    """Each expected heading present anywhere in the text, heading or not:
    the text survived even where granite-docling did not mark it a heading."""
    text = normalize_text(markdown).casefold()
    return [normalize_text(h).casefold() in text for h in expected]


# A chunk's page claim holds when the page shares a run of words with the
# chunk — a chunk spanning a page break may take one sentence from its second
# page — or, for a table, which chunks serialize as `Male, 2 = 54,870` and so
# break every run, when half the chunk's distinct words occur on the page. A
# chunk with fewer words than a run ("Logo") cannot be judged.
_RUN_WORDS = 5
_CLAIM_SHARE = 0.5


def _word_list(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(normalize_text(text))]


def page_claim(text: str, page_text: str) -> bool | None:
    """Whether a chunk's text plausibly comes from a page (rule 4's check of
    `page_nos`); None when the chunk has too few words to say."""
    words = _word_list(text)
    if len(words) < _RUN_WORDS:
        return None
    page = _word_list(page_text)
    runs = {tuple(page[i : i + _RUN_WORDS]) for i in range(len(page) - _RUN_WORDS + 1)}
    if any(
        tuple(words[i : i + _RUN_WORDS]) in runs
        for i in range(len(words) - _RUN_WORDS + 1)
    ):
        return True
    distinct = set(words)
    return len(distinct & set(page)) >= _CLAIM_SHARE * len(distinct)


# --- Chart summaries (rule 4) ---------------------------------------------------

_SUMMARY_NUMBER = re.compile(r"(?<![\w.])[-−–]?\$?\d[\d,]*(?:\.\d+)?%?")
_LIST_NUMBER = re.compile(r"^\s*(\d+)[.)]\s", re.MULTILINE)
_RANGE = re.compile(
    r"[-−–]?\$?\d[\d,]*(?:\.\d+)?%?\s*(?:to|–|-)\s*[-−–]?\$?\d[\d,]*(?:\.\d+)?%?"
)
_AXIS_WORDS = re.compile(r"\b(axis|range|ranges|ranging|scale)\b", re.IGNORECASE)
_CLAUSE = re.compile(r"\n|(?<=[.;!?])\s+")


@dataclass
class SummaryScore:
    """Rule 4 on one summary. `numbers` are value claims; axis ranges and list
    numbering are counted apart (S6: they were most of the "unsupported")."""

    numbers: int
    unsupported: int
    apart: int
    pair_errors: int
    unsupported_values: list[str] = field(default_factory=list)


def _supported(value: float, values: Sequence[float], rel_tol: float) -> bool:
    if value.is_integer() and 1900 <= value <= 2100:
        return True  # a year
    if any(numbers_close(value, v, rel_tol) for v in values):
        return True
    for a in values:
        for b in values:
            if a is b:
                continue
            derived = [a - b]
            if b:
                derived += [a / b, (a - b) / b * 100]
            if any(
                numbers_close(value, d, rel_tol) or numbers_close(-value, d, rel_tol)
                for d in derived
            ):
                return True
    return False


def score_summary(
    summary: str, expected: Rows, rel_tol: float = REL_TOL
) -> SummaryScore:
    """Every number a summary states must be in the chart's expected CSV within
    `rel_tol`, or a year, or a difference, ratio or percent change of two of its
    values. Where the summary names a category, the number after it must be
    that row's (`summary_disagreements`, the same check `run.json` warns with,
    here run against the truth instead of the model's own CSV)."""
    values = [
        v
        for _, _, cell in _non_empty(expected)
        if (v := parse_number(cell)) is not None
    ]
    apart_spans: list[tuple[int, int]] = []
    for match in _LIST_NUMBER.finditer(summary):
        apart_spans.append(match.span(1))
    for clause_match in re.finditer(r"[^\n.;!?]+", summary):
        clause = clause_match.group()
        if _AXIS_WORDS.search(clause):
            for r in _RANGE.finditer(clause):
                start = clause_match.start() + r.start()
                apart_spans.append((start, start + len(r.group())))
    numbers = unsupported = apart = 0
    bad: list[str] = []
    for match in _SUMMARY_NUMBER.finditer(summary):
        if any(s <= match.start() < e for s, e in apart_spans):
            apart += 1
            continue
        value = parse_number(match.group())
        if value is None:
            continue
        numbers += 1
        if not _supported(value, values, rel_tol):
            unsupported += 1
            bad.append(match.group())
    pair_errors = len(summary_disagreements(expected, summary)) if expected else 0
    return SummaryScore(numbers, unsupported, apart, pair_errors, bad)


# --- KVP ------------------------------------------------------------------------


def kvp_fields(expected: dict[str, Any], predicted: dict[str, Any]) -> dict[str, bool]:
    """Per-field exact match; a field the document leaves empty is `null` in
    the expected JSON and matches a missing or null prediction. Strings are
    compared with surrounding whitespace stripped, nothing more."""

    def norm(value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    return {
        name: norm(predicted.get(name)) == norm(value)
        for name, value in expected.items()
    }


def injected_found(values: Iterable[str], output: Any) -> list[str]:
    """Which injected strings occur anywhere in the KVP output, any case (rule 6)."""
    import json

    text = json.dumps(output, ensure_ascii=False).casefold()
    return [v for v in values if v.casefold() in text]


# --- Committed results (rule 7) -------------------------------------------------


def private_strings(obj: Any, markers: Sequence[str]) -> list[str]:
    """Every string in `obj` (keys included) containing any of `markers`."""
    found: list[str] = []
    if isinstance(obj, str):
        if any(m and m in obj for m in markers):
            found.append(obj)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            found += private_strings(key, markers)
            found += private_strings(value, markers)
    elif isinstance(obj, list | tuple):
        for value in obj:
            found += private_strings(value, markers)
    return found

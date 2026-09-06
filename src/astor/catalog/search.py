"""Query matching and ranking for catalog / protocol search.

Pure: no ORM, no database, no I/O. Everything that decides WHICH rows match and
in WHAT order lives here, for the same reason `scoring.py` is pure — the eval
harness must exercise the logic that actually runs, not a reimplementation of it
that can silently drift.

SQL's only job upstream is to hand this module a superset (plain `ILIKE`), so no
semantic rule is expressed twice.

The rules, in order:

  fold       NFKC + apostrophe variants -> "'" + casefold, so an ASCII "Eagle's"
             typed by a customer matches the catalog's "Eagle’s".
  tokenize   split on runs of non-alphanumerics; apostrophes stay inside words.
  mandatory  a token containing a digit MUST word-prefix match. In labware the
             number is the discriminating attribute (6 vs 96 well, 500 mL vs
             1 L), and `\\b6` cannot match inside "96" -- which is exactly what
             made "6 well cell culture plate" return 96-well plates when the
             query was one literal substring.
  coverage   the rest score by IDF-weighted coverage above a floor, so a rare
             term ("dmem") outweighs a common one ("medium") and
             "DMEM, Low Glucose" ranks above an unrelated "LB Medium".
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Callable, Sequence

# Straight quote is the canonical form; these are the variants seen in the
# Shopify titles (U+2019 dominates) and on customer keyboards.
_APOSTROPHES = dict.fromkeys(map(ord, "’‘ˇ`´"), "'")
_SPLIT = re.compile(r"[^0-9a-z']+")

COVERAGE_FLOOR = 0.5
_PHRASE_BONUS = 3.0
_WHOLE_WORD_BONUS = 0.5
_COVERAGE_WEIGHT = 10.0
_LENGTH_PENALTY = 500.0


def fold(text: str | None) -> str:
    """Casefolded, apostrophe-normalized form used for every comparison."""
    return unicodedata.normalize("NFKC", text or "").translate(_APOSTROPHES).casefold()


def tokens(query: str | None) -> list[str]:
    """Query -> comparable terms. Bare punctuation yields no tokens."""
    return [t for t in _SPLIT.split(fold(query)) if t.strip("'")]


def is_mandatory(token: str) -> bool:
    """Digit-bearing tokens are attributes, not adjectives: they must match."""
    return any(c.isdigit() for c in token)


def df_match(token: str, haystack: str) -> bool:
    """Substring test mirroring the SQL `ILIKE '%token%'` prefilter.

    Used for document-frequency counts, so the df a caller computes in SQL and
    the df computed here agree. It over-estimates true word-prefix frequency;
    IDF is a smooth weight and tolerates that.
    """
    return token in fold(haystack)


def _word_prefix(token: str, haystack: str) -> bool:
    return re.search(r"\b" + re.escape(token), haystack) is not None


def _whole_word(token: str, haystack: str) -> bool:
    return re.search(r"\b" + re.escape(token) + r"\b", haystack) is not None


def score(
    query: str,
    name: str,
    haystack: str,
    *,
    df: dict[str, int],
    n_docs: int,
) -> float | None:
    """Relevance of one row, or None when it does not qualify.

    `haystack` is everything searchable (name + brand + mpn); `name` alone earns
    the whole-word and phrase bonuses, so a brand-only hit never outranks a real
    name match.
    """
    toks = tokens(query)
    if not toks:
        return None

    folded_name, folded_hay = fold(name), fold(haystack)

    mandatory = [t for t in toks if is_mandatory(t)]
    if any(not _word_prefix(t, folded_hay) for t in mandatory):
        return None

    soft = [t for t in toks if not is_mandatory(t)]
    if soft:
        # log(1 + N/(df+1)) is always positive, so a caller passing an
        # approximate or truncated df can never produce a negative weight.
        idf = {t: math.log(1 + n_docs / (df.get(t, 0) + 1)) for t in soft}
        total = sum(idf.values())
        hit = [t for t in soft if _word_prefix(t, folded_hay)]
        coverage = (sum(idf[t] for t in hit) / total) if total else 0.0
        if coverage < COVERAGE_FLOOR:
            return None
    else:
        hit, coverage = [], 1.0

    value = coverage * _COVERAGE_WEIGHT
    value += _WHOLE_WORD_BONUS * sum(1 for t in hit if _whole_word(t, folded_name))
    if fold(" ".join(toks)) in folded_name:
        value += _PHRASE_BONUS
    # Prefer the specific name over the verbose one that merely contains it.
    return value - len(folded_name) / _LENGTH_PENALTY


def rank(
    rows: Sequence,
    query: str,
    *,
    df: dict[str, int],
    haystack: Callable[[object], str],
    name_of: Callable[[object], str],
    n_docs: int | None = None,
) -> list:
    """Qualifying rows, best first. Ties keep the caller's input order."""
    if n_docs is None:
        n_docs = max(len(rows), max(df.values(), default=0))
    scored = []
    for i, row in enumerate(rows):
        value = score(query, name_of(row), haystack(row), df=df, n_docs=n_docs)
        if value is not None:
            scored.append((-value, i, row))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [row for _, _, row in scored]


def page(
    rows: Sequence,
    query: str,
    *,
    page: int,
    page_size: int,
    df: dict[str, int],
    haystack: Callable[[object], str],
    name_of: Callable[[object], str],
    n_docs: int | None = None,
) -> tuple[list, int]:
    """One page of ranked rows, plus the true match count.

    Ranking happens before slicing, so `total` reflects rows that really match
    rather than the size of the SQL prefilter's superset.
    """
    ranked = rank(rows, query, df=df, haystack=haystack, name_of=name_of, n_docs=n_docs)
    start = (page - 1) * page_size
    return ranked[start:start + page_size], len(ranked)

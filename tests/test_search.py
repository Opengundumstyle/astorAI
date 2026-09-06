"""Search relevance rules, measured against a frozen slice of the real catalog.

Every case here is a production failure observed on 2026-09-04/05 through the
storefront assistant, or the invariant that keeps it from recurring. The fixture
(`data/eval/catalog_sample.csv`) is real product data, so a rule that only works
on toy strings cannot pass.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from astor.catalog import search

_FIXTURE = Path(__file__).resolve().parent.parent / "data" / "eval" / "catalog_sample.csv"


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with _FIXTURE.open() as f:
        return list(csv.DictReader(f))


def _names(rows: list[dict], query: str, limit: int = 8) -> list[str]:
    """Rank the fixture the way repo does, and return the visible names."""
    df = {t: sum(1 for r in rows if search.df_match(t, f"{r['name']} {r['brand']}"))
          for t in search.tokens(query)}
    ranked = search.rank(rows, query, df=df, haystack=lambda r: f"{r['name']} {r['brand']}",
                         name_of=lambda r: r["name"])
    return [r["name"] for r in ranked[:limit]]


# --------------------------------------------------------------- folding/tokens #
def test_folds_curly_apostrophe_so_ascii_query_matches_catalog_spelling():
    assert search.fold("Eagle’s") == search.fold("Eagle's")


def test_tokenizes_on_punctuation_not_just_whitespace():
    assert search.tokens("Trypsin-EDTA(1x)") == ["trypsin", "edta", "1x"]


def test_blank_query_yields_no_tokens():
    assert search.tokens("   ?!  ") == []


# ------------------------------------------------------------------- the bugs #
def test_six_well_query_never_returns_ninetysix_well_plates(rows):
    """The reported bug: '%6 well cell culture plate%' is a literal substring of
    'NEST 9|6 Well Cell Culture Plate', so the assistant offered 96-well plates
    and said no 6-well plate existed."""
    got = _names(rows, "6 well cell culture plate")
    assert got, "expected 6-well plates to be found"
    # The invariant is the well count, not the digits: a title may legitimately
    # mention a range ("(6-96 well) - 6 Well"). Every hit must denote 6 wells,
    # which is precisely what a 96-well plate cannot do -- `\b6` has no word
    # boundary inside "96".
    assert all(re.search(r"\b6[\s-]?well", n, re.I) for n in got), got
    assert not any(re.search(r"\b96[\s-]?well cell culture plate", n, re.I) for n in got), got


def test_emem_medium_finds_the_two_emem_products(rows):
    """'EMEM medium' returned 0 rows: 'Medium' precedes '(EMEM)' in the title, so
    the phrase is not a substring."""
    got = _names(rows, "EMEM medium")
    assert len(got) == 2
    assert all("EMEM" in n for n in got)


def test_ascii_apostrophe_query_finds_curly_apostrophe_product(rows):
    got = _names(rows, "Eagle's Minimum Essential Medium")
    assert got and all("Minimum Essential Medium" in n for n in got)


def test_dmem_medium_returns_the_whole_dmem_family_not_just_literal_matches(rows):
    """A rare term must outweigh a common one: 'DMEM, Low Glucose' has no literal
    'Medium' in it and must still outrank unrelated 'LB Medium'."""
    got = _names(rows, "DMEM medium", limit=10)
    assert sum(1 for n in got if "DMEM" in n.upper()) >= 8, got


def test_numeric_attribute_is_mandatory(rows):
    """500 mL must not silently match a 1 L bottle."""
    got = _names(rows, "500ml DMEM high glucose")
    assert got
    assert all("500" in n for n in got), got


def test_ranks_specific_name_above_incidental_match(rows):
    got = _names(rows, "Trypsin EDTA")
    assert "Trypsin-EDTA" in got[0]


def test_query_with_no_usable_tokens_returns_nothing(rows):
    assert _names(rows, "!!!") == []


# ------------------------------------------------------- rank -> page contract #
def test_page_returns_matching_slice_and_total_after_ranking(rows):
    """repo paginates AFTER ranking, so `total` counts rows that actually match,
    not the SQL prefilter's superset."""
    df = {t: sum(1 for r in rows if search.df_match(t, f"{r['name']} {r['brand']}"))
          for t in search.tokens("DMEM")}
    page1, total = search.page(rows, "DMEM", page=1, page_size=5, df=df,
                              haystack=lambda r: f"{r['name']} {r['brand']}",
                              name_of=lambda r: r["name"])
    page2, total2 = search.page(rows, "DMEM", page=2, page_size=5, df=df,
                                haystack=lambda r: f"{r['name']} {r['brand']}",
                                name_of=lambda r: r["name"])
    assert total == total2 == 32
    assert len(page1) == len(page2) == 5
    assert {r["name"] for r in page1}.isdisjoint({r["name"] for r in page2})

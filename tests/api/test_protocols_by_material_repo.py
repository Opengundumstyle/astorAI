# tests/api/test_protocols_by_material_repo.py
"""DB-gated: exercises the jsonb reverse search against real Postgres.
Run locally with:  RUN_DB_TESTS=1 pytest tests/api/test_protocols_by_material_repo.py
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="needs Postgres; set RUN_DB_TESTS=1 to run",
)

from astor.api import repo
from astor.db.base import session_scope
from astor.db.models import Protocol

MARK = "pbm-test-" + uuid.uuid4().hex[:8]  # unique source tag → find + clean up

# The dev Postgres this test runs against is a shared, pre-populated instance (real
# protocols.io corpus), not an isolated/empty table -- it already contains real
# protocols with genuine "Trypsin-EDTA" reagents. So the search term itself must be
# a per-run-unique token (not the literal reagent name) or the exact-count assertions
# below would be contaminated by unrelated pre-existing rows.
TOKEN = "zqtok" + uuid.uuid4().hex[:8]


def _p(title, materials, *, servable=True, rank=0.0):
    return Protocol(
        source=MARK, source_id=uuid.uuid4().hex, source_uri="u", title=title,
        license="cc-by", servable=servable, rank_score=rank, materials=materials, steps=[],
    )


def test_finds_only_servable_protocols_that_list_the_material():
    with session_scope() as s:
        try:
            s.add_all([
                _p("Fibroblast culture", [{"name": f"0.25% {TOKEN}-EDTA(1x) in HBSS - 100 ML"}], rank=5.0),
                _p("Cell passaging", [{"name": f"{TOKEN}-EDTA solution (Gibco, #25200056)"}], rank=9.0),
                _p("PCR cleanup", [{"name": "Ethanol"}, {"name": "SPRI beads"}], rank=8.0),
                _p("Hidden trypsin", [{"name": f"{TOKEN}-EDTA"}], servable=False, rank=9.9),
            ])
            s.flush()
            r = repo.protocols_by_material(s, f"{TOKEN}-EDTA")
            titles = [p["title"] for p in r["protocols"]]
            assert r["total"] == 2
            # rank order: 9.0 before 5.0; non-servable + non-matching excluded
            assert titles == ["Cell passaging", "Fibroblast culture"]
            assert r["protocols"][0]["matched_material"] == f"{TOKEN}-EDTA solution (Gibco, #25200056)"
            assert all("id" in p and "product_count" in p for p in r["protocols"])
        finally:
            s.query(Protocol).filter(Protocol.source == MARK).delete()


def test_normalization_matches_hyphen_space_slash_and_case():
    with session_scope() as s:
        try:
            s.add_all([
                _p("A", [{"name": f"{TOKEN}/EDTA Solution"}], rank=1.0),
                _p("B", [{"name": f"{TOKEN.upper()}   EDTA (1x)"}], rank=2.0),
            ])
            s.flush()
            # query with a hyphen; both slash and multi-space variants must match
            r = repo.protocols_by_material(s, f"{TOKEN}-edta")
            assert r["total"] == 2
            assert {p["title"] for p in r["protocols"]} == {"A", "B"}
        finally:
            s.query(Protocol).filter(Protocol.source == MARK).delete()


def test_limit_caps_rows_but_total_is_full_count():
    with session_scope() as s:
        try:
            s.add_all([_p(f"P{i}", [{"name": f"{TOKEN}-EDTA"}], rank=float(i)) for i in range(5)])
            s.flush()
            r = repo.protocols_by_material(s, f"{TOKEN}-edta", limit=2)
            assert r["total"] == 5
            assert len(r["protocols"]) == 2
            assert [p["title"] for p in r["protocols"]] == ["P4", "P3"]  # highest rank first
        finally:
            s.query(Protocol).filter(Protocol.source == MARK).delete()


def test_percent_in_term_is_matched_literally_not_as_wildcard():
    with session_scope() as s:
        try:
            s.add_all([
                _p("Literal percent", [{"name": f"{TOKEN}%X"}], rank=1.0),
                _p("Wildcard bait", [{"name": f"{TOKEN}zzX"}], rank=2.0),
            ])
            s.flush()
            # An unescaped '%' in the term would act as a SQL wildcard and match both
            # rows; escaped, it must match only the literal '%' material.
            r = repo.protocols_by_material(s, f"{TOKEN}%X")
            assert r["total"] == 1
            assert [p["title"] for p in r["protocols"]] == ["Literal percent"]
        finally:
            s.query(Protocol).filter(Protocol.source == MARK).delete()


def test_blank_term_returns_empty_without_querying():
    with session_scope() as s:
        assert repo.protocols_by_material(s, "   ") == {"total": 0, "protocols": []}
        assert repo.protocols_by_material(s, "") == {"total": 0, "protocols": []}

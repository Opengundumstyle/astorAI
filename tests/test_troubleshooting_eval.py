"""Retrieval-layer eval for the troubleshooting table: no model, no network.

Pins the scoring so a green bench cannot be an artefact of how hits are counted."""
from __future__ import annotations

from pathlib import Path

from astor import curation
from astor.curation import troubleshoot
from astor.curation.loader import Category, CurationTables, TroubleshootingEntry
from astor.eval import troubleshooting as ev

GOLD = Path(__file__).resolve().parents[1] / "data" / "eval" / "troubleshooting_gold.csv"


def _e(eid, cat, symptom, confidence="drafted"):
    return TroubleshootingEntry(eid, cat, symptom, "cause", "fix", None, "no", None,
                                confidence, "model", "", "", "")


def _tables():
    cats = {"western_blot": Category("western_blot", "WB"), "elisa": Category("elisa", "ELISA")}
    return CurationTables(cats, {}, [], [
        _e("T1", "western_blot", "no bands at all"),
        _e("T2", "western_blot", "high background", confidence="reviewed"),
        _e("T3", "elisa", "standard curve not linear"),
    ])


def _case(cid, cat, q, ids, roles=()):
    return ev.GoldCase(cid, cat, q, tuple(ids), tuple(roles), "", "")


def test_load_gold_parses_lists():
    cases = ev.load_gold(GOLD)
    assert len(cases) >= 40
    multi = next(c for c in cases if c.case_id == "TG0034")
    assert multi.expected_entry_ids == ("T0301", "T0302")
    assert multi.expected_fix_roles == ("transfection_reagent", "plasmid_dna")


def test_gold_references_only_real_entries_and_roles():
    t = curation.tables()
    ids = {e.entry_id for e in t.troubleshooting}
    for c in ev.load_gold(GOLD):
        assert set(c.expected_entry_ids) <= ids, c.case_id
        assert set(c.expected_fix_roles) <= set(t.roles), c.case_id
        assert c.category_id in t.categories, c.case_id


def test_hit_is_fraction_of_expected_ids_returned():
    m = troubleshoot.Matcher(_tables())
    cases = [
        _case("a", "western_blot", "no bands at all", ["T1"]),
        _case("b", "western_blot", "no bands at all", ["T1", "T2"]),
        _case("c", "elisa", "nothing matches here zz", ["T3"]),
    ]
    r = ev.evaluate_retrieval(m, cases, limit=5)
    by = {x.case.case_id: x for x in r.results}
    assert by["a"].hit == 1.0
    assert by["b"].hit == 0.5
    assert by["c"].hit == 0.0 and by["c"].returned_ids == ()
    assert r.per_category["western_blot"] == 0.75
    assert r.per_category["elisa"] == 0.0
    assert r.overall == (1.0 + 0.5 + 0.0) / 3


def test_drafted_share_counts_returned_rows():
    m = troubleshoot.Matcher(_tables())
    r = ev.evaluate_retrieval(m, [_case("a", "western_blot", "no bands high background", ["T1"])], limit=5)
    # returned T1 (drafted) and T2 (reviewed)
    assert r.drafted_share == 0.5


def test_gate_bars():
    rep = ev.RetrievalReport(results=[], overall=0.85, per_category={"western_blot": 0.9, "elisa": 0.5},
                             drafted_share=1.0)
    g = ev.gate(rep)
    assert g.passed is False and any("elisa" in r for r in g.reasons)
    rep2 = ev.RetrievalReport(results=[], overall=0.85, per_category={"western_blot": 0.9}, drafted_share=1.0)
    assert ev.gate(rep2).passed is True


def test_to_scenarios_skips_cases_without_must_match():
    cases = [ev.GoldCase("x", "western_blot", "q", ("T1",), (), "anti-rabbit", ""),
             ev.GoldCase("y", "western_blot", "q2", ("T2",), (), "", "")]
    s = ev.to_scenarios(cases)
    assert len(s) == 1 and s[0].question == "q" and s[0].must_match == "anti-rabbit"


def test_render_mentions_overall_and_categories():
    m = troubleshoot.Matcher(_tables())
    r = ev.evaluate_retrieval(m, [_case("a", "western_blot", "no bands at all", ["T1"])], limit=5)
    out = ev.render(r)
    assert "overall" in out and "western_blot" in out

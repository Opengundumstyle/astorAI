"""Mary's per-pair verdicts -> a gold set the accuracy harness can consume."""
from __future__ import annotations

import pytest

from astor.eval import equivalence_gold as eg


# ------------------------------------------------------------ verdict parsing #
@pytest.mark.parametrize("raw, kind, note", [
    ("不可替代", "none", ""),
    ("可替代", "substitute", ""),
    ("相同", "exact", ""),
    ("可替代，相同容量", "substitute", "相同容量"),
    ("不可替代，相同容量，一个有二维码", "none", "相同容量，一个有二维码"),
    ("不可替代,Agulos Biotech SimPL SP - 500 ml 功能不同，这个不是水", "none",
     "Agulos Biotech SimPL SP - 500 ml 功能不同，这个不是水"),
    ("可替代,serological pipette 一般都是灭菌的", "substitute", "serological pipette 一般都是灭菌的"),
    ("无法判断", None, "无法判断"),
    ("无法判断，不知道是不是tc treated (biologix)", None, "无法判断，不知道是不是tc treated (biologix)"),
    ("拿不准", None, "拿不准"),
    ("  不可替代 ", "none", ""),
])
def test_parse_verdict(raw, kind, note):
    assert eg.parse_verdict(raw) == (kind, note)


def test_parse_verdict_rejects_unknown_word():
    """A typo must not be silently read as 'none' — that would poison the gold set."""
    with pytest.raises(ValueError):
        eg.parse_verdict("不可代替")


def test_parse_verdict_empty_is_unlabelled():
    assert eg.parse_verdict(None) == (None, "")
    assert eg.parse_verdict("") == (None, "")


def test_prefix_longest_match_wins():
    """'不可替代' starts with a substring that is NOT '可替代'; ensure no false split."""
    assert eg.parse_verdict("不可替代")[0] == "none"


# ------------------------------------------------------------ model agreement #
def _row(pid, m1, m2, mary, rule="r"):
    return eg.GoldRow(pair_id=pid, rule=rule, a="A", brand_a="x", b="B", brand_b="y",
                      model1=m1, model2=m2, raw=mary, kind=eg.parse_verdict(mary)[0],
                      note=eg.parse_verdict(mary)[1], similarity=0.9)


def test_compare_models_counts_tiebreaks_and_skips_unlabelled():
    rows = [
        _row("1", "exact", "substitute", "相同"),        # model1 wins
        _row("2", "exact", "substitute", "可替代"),      # model2 wins
        _row("3", "exact", "substitute", "不可替代"),    # neither
        _row("4", "exact", "substitute", "无法判断"),    # excluded
    ]
    c = eg.compare_models(rows)
    assert c.labelled == 3
    assert c.model1_agrees == 1 and c.model2_agrees == 1 and c.neither == 1
    assert c.neither_ids == ["3"]


def test_multiclass_kappa_perfect_and_chance():
    assert eg.multiclass_kappa(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    # rater b always says "a" on a set that's mostly "a": no skill
    assert eg.multiclass_kappa(["a"] * 9 + ["b"], ["a"] * 10) == pytest.approx(0.0, abs=1e-9)


def test_multiclass_kappa_rejects_empty_or_mismatched():
    with pytest.raises(ValueError):
        eg.multiclass_kappa([], [])
    with pytest.raises(ValueError):
        eg.multiclass_kappa(["a"], ["a", "b"])


# ------------------------------------------------------------ per-rule rollup #
def test_rule_summary_reports_majority_and_dissent():
    rows = [_row(str(i), "exact", "substitute", "不可替代", rule="B2") for i in range(3)]
    rows.append(_row("9", "exact", "substitute", "可替代", rule="B2"))
    rows.append(_row("10", "exact", "substitute", "无法判断", rule="B2"))
    s = eg.rule_summary(rows)["B2"]
    assert s["none"] == 3 and s["substitute"] == 1 and s["unlabelled"] == 1
    assert s["majority"] == "none"


# ------------------------------------------------------------ duplicates #
def test_duplicate_pairs_are_found_regardless_of_order():
    r1, r2, r3 = (_row(str(i), "exact", "substitute", "不可替代") for i in (1, 2, 3))
    r1.a, r1.b = "Tube 2ml", "Tube 2ml sterile"
    r2.a, r2.b = "Tube 2ml sterile", "Tube 2ml"
    r3.a, r3.b = "Other", "Thing"
    dups = eg.duplicate_pairs([r1, r2, r3])
    assert dups == [["1", "2"]]


# ------------------------------------------------------------ harness export #
def test_harness_rows_use_resolved_keys_and_drop_unlabelled():
    rows = [_row("1", "exact", "substitute", "可替代"), _row("2", "exact", "substitute", "无法判断")]
    keys = {("A", "x"): "k-a", ("B", "y"): "k-b"}
    out = eg.harness_rows(rows, keys)
    assert out == [{"a_key": "k-a", "b_key": "k-b", "kind": "substitute", "pair_id": "1"}]


def test_harness_rows_reports_unresolved_products():
    rows = [_row("1", "exact", "substitute", "可替代")]
    with pytest.raises(eg.UnresolvedProduct):
        eg.harness_rows(rows, {("A", "x"): "k-a"})

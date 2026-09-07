"""Aggregation and interval maths for the benchmark scorecard. Pure."""
from __future__ import annotations

import pytest

from astor.eval import report


# ----------------------------------------------------------------- intervals #
def test_a_perfect_small_sample_still_has_a_wide_interval():
    """5/5 is not proof. The report prints the bound so nobody reads it as one."""
    low, high = report.wilson(5, 5)
    assert high == pytest.approx(1.0, abs=1e-3)
    assert low == pytest.approx(0.565, abs=0.01)


def test_a_half_pass_rate_straddles_the_middle():
    low, high = report.wilson(4, 8)
    assert low < 0.5 < high


def test_zero_runs_is_an_empty_interval():
    assert report.wilson(0, 0) == (0.0, 0.0)


def test_interval_never_leaves_the_unit_range():
    low, high = report.wilson(0, 3)
    assert 0.0 <= low <= high <= 1.0


# ---------------------------------------------------------------------- cells #
def test_cell_rate_is_passes_over_runs():
    assert report.Cell("R1", "D1", passes=3, runs=4).rate == 0.75


def test_cell_with_no_runs_rates_zero():
    assert report.Cell("R1", "D1", passes=0, runs=0).rate == 0.0


def test_aggregate_groups_by_row_and_dimension():
    cells = report.aggregate([
        ("R1", "D1", True), ("R1", "D1", False),
        ("R1", "D8", True),
        ("R2", "D1", True),
    ])
    by_key = {(c.row, c.dim): c for c in cells}
    assert by_key[("R1", "D1")].passes == 1
    assert by_key[("R1", "D1")].runs == 2
    assert by_key[("R1", "D8")].runs == 1
    assert by_key[("R2", "D1")].passes == 1


def test_aggregate_is_sorted_by_row_then_dimension():
    cells = report.aggregate([("R2", "D1", True), ("R1", "D8", True), ("R1", "D1", True)])
    assert [(c.row, c.dim) for c in cells] == [("R1", "D1"), ("R1", "D8"), ("R2", "D1")]


# ----------------------------------------------------------------------- bars #
def test_blocking_dimensions_bar_at_one():
    assert report.BARS["D2"] == 1.0
    assert report.BARS["D4B"] == 1.0


def test_science_bar_is_lower_than_retrieval():
    assert report.BARS["D5"] < report.BARS["D1"]


def test_failing_returns_only_cells_below_their_own_bar():
    cells = [
        report.Cell("R1", "D1", passes=9, runs=10),    # 0.90, bar 0.90 -> ok
        report.Cell("R2", "D5", passes=9, runs=10),    # 0.90, bar 0.85 -> ok
        report.Cell("R3", "D2", passes=9, runs=10),    # 0.90, bar 1.00 -> FAIL
    ]
    assert [(c.row, c.dim) for c in report.failing(cells)] == [("R3", "D2")]

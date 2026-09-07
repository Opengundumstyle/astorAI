"""Judge-versus-human agreement. Pure arithmetic."""
from __future__ import annotations

import pytest

from astor.eval import calibration


def test_perfect_agreement_is_one():
    assert calibration.cohens_kappa([True, False, True], [True, False, True]) == 1.0


def test_total_disagreement_is_negative():
    assert calibration.cohens_kappa([True, False], [False, True]) < 0


def test_chance_agreement_scores_near_zero():
    """A judge that always says pass on a mostly-passing set has learned nothing."""
    human = [True] * 9 + [False]
    always_pass = [True] * 10
    assert calibration.cohens_kappa(human, always_pass) == pytest.approx(0.0, abs=1e-9)


def test_unanimous_identical_labels_score_one():
    """Degenerate case: pe == 1, so the usual formula divides by zero."""
    assert calibration.cohens_kappa([True, True], [True, True]) == 1.0


def test_expected_agreement_counts_both_raters_agreeing_on_false():
    """Two raters who each pass half the answers, but disagree on which half,
    have learned nothing about each other — kappa 0. This is the only fixture
    where the (1 - p_a)(1 - p_b) half of expected agreement is non-zero, so a
    formula that drops it returns 0.33 here instead of 0."""
    human = [True, True, False, False]
    model = [True, False, True, False]
    assert calibration.cohens_kappa(human, model) == pytest.approx(0.0)


def test_mismatched_lengths_are_rejected():
    with pytest.raises(ValueError, match="same length"):
        calibration.cohens_kappa([True], [True, False])


def test_empty_input_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        calibration.cohens_kappa([], [])


def test_threshold_is_the_documented_bar():
    assert calibration.CALIBRATED_AT == 0.6


# ------------------------------------------------------------------ sampling #
def _transcripts():
    return [{"probe": f"P{n:02d}", "row": f"R{(n % 4) + 1}", "run": 1} for n in range(40)]


def test_sample_returns_the_requested_size():
    assert len(calibration.sample_for_labelling(_transcripts(), size=20)) == 20


def test_sample_is_stratified_across_rows():
    rows = {t["row"] for t in calibration.sample_for_labelling(_transcripts(), size=8)}
    assert rows == {"R1", "R2", "R3", "R4"}


def test_sample_is_reproducible_for_a_seed():
    first = calibration.sample_for_labelling(_transcripts(), size=12, seed=7)
    second = calibration.sample_for_labelling(_transcripts(), size=12, seed=7)
    assert [t["probe"] for t in first] == [t["probe"] for t in second]


def test_sample_smaller_than_requested_returns_everything():
    assert len(calibration.sample_for_labelling(_transcripts()[:5], size=20)) == 5

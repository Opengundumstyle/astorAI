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


# --------------------------------------------- I8: joining judge to a human #
def _judged(probe="P21", run=1, passed=True):
    return {"probe": probe, "row": "R7", "run": run,
            "judge": {"passed": passed, "reason": "because"}}


def test_label_key_is_the_probe_and_the_run():
    assert calibration.label_key(_judged("P25", 3)) == "P25:3"


def test_kappa_is_measured_over_the_transcripts_both_raters_scored():
    """The one mechanism that turns D5 from decoration into measurement. Before
    this, cohens_kappa had no caller and --kappa took a float an operator typed."""
    transcripts = [_judged("P21", 1, True), _judged("P21", 2, False),
                   _judged("P25", 1, True), _judged("P25", 2, False)]
    labels = {"P21:1": "pass", "P21:2": "fail", "P25:1": "pass", "P25:2": "fail"}
    kappa, n = calibration.kappa_from_labels(transcripts, labels)
    assert kappa == 1.0
    assert n == 4


def test_a_transcript_with_no_human_label_is_skipped():
    """kappa compares two raters on the SAME items."""
    transcripts = [_judged("P21", 1, True), _judged("P21", 2, True)]
    kappa, n = calibration.kappa_from_labels(transcripts, {"P21:1": "pass"})
    assert n == 1
    assert kappa == 1.0


def test_a_transcript_the_judge_never_graded_is_skipped():
    ungraded = {"probe": "P21", "row": "R7", "run": 9}
    _kappa, n = calibration.kappa_from_labels(
        [_judged("P21", 1, True), ungraded], {"P21:1": "pass", "P21:9": "fail"})
    assert n == 1


def test_a_disagreeing_judge_scores_at_or_below_zero():
    transcripts = [_judged("P21", n, n % 2 == 0) for n in range(1, 5)]
    labels = {f"P21:{n}": "pass" if n % 2 else "fail" for n in range(1, 5)}
    kappa, _n = calibration.kappa_from_labels(transcripts, labels)
    assert kappa < 0


def test_nothing_joining_is_an_error_not_a_kappa():
    """A bare number derived from no comparison would assert calibration by
    fiat, which is what this whole mechanism exists to prevent."""
    with pytest.raises(ValueError, match="probe.*run|no transcript"):
        calibration.kappa_from_labels([_judged("P21", 1)], {"P99:1": "pass"})


def test_a_boolean_label_is_read_directly():
    assert calibration.parse_label(True) is True
    assert calibration.parse_label(False) is False


def test_pass_and_fail_are_read_case_insensitively():
    assert calibration.parse_label("PASS") is True
    assert calibration.parse_label(" fail ") is False


def test_an_unreadable_label_raises_rather_than_scoring_a_fail():
    """A typo silently read as 'fail' would move the one number that says
    whether the judge can be trusted at all."""
    with pytest.raises(ValueError, match="unreadable"):
        calibration.parse_label("passs")

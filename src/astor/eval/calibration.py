"""How far can the science judge be trusted?

WHY KAPPA, NOT AGREEMENT
    Raw agreement flatters a judge on a skewed set. If nine answers in ten are
    correct, a judge that says "pass" every time agrees with a human 90% of the
    time while having learned nothing at all. Cohen's kappa subtracts the
    agreement you would expect by chance, so that judge scores 0.

    Below CALIBRATED_AT the rubrics are the problem, not the model — tighten them
    and re-measure before any D5 number is shown to anyone.
"""
from __future__ import annotations

import random
from collections import defaultdict

# Conventional floor for "moderate" agreement. Below this the D5 column is
# reported as uncalibrated and carries no weight.
CALIBRATED_AT = 0.6


# How a human's label names the transcript it belongs to. Stable across a
# re-judge, because it identifies the turn that was played, not the verdict.
def label_key(transcript: dict) -> str:
    return f"{transcript['probe']}:{transcript['run']}"


_PASS = frozenset({"pass", "true", "yes", "y", "1", "ok"})
_FAIL = frozenset({"fail", "false", "no", "n", "0"})


def parse_label(value) -> bool:
    """A human writes "pass" or "fail". Anything else raises rather than being
    read as a failure: a typo silently scored as a fail would move kappa, which
    is the one number that says whether the judge can be trusted at all."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _PASS:
        return True
    if text in _FAIL:
        return False
    raise ValueError(f"unreadable label {value!r}: write 'pass' or 'fail'")


def kappa_from_labels(transcripts: list[dict], labels: dict[str, str]
                      ) -> tuple[float, int]:
    """Judge-versus-human agreement over the transcripts both raters scored.

    Returns (kappa, n_compared). Pairs are joined on '<probe>:<run>' and a
    transcript with no human label, or no judge verdict, is skipped — kappa
    compares two raters on the SAME items. Nothing joining is an error, not a
    kappa: a bare number derived from no comparison at all would assert
    calibration by fiat, which is exactly what this mechanism exists to prevent.
    """
    human: list[bool] = []
    machine: list[bool] = []
    for transcript in transcripts:
        verdict = transcript.get("judge")
        label = labels.get(label_key(transcript))
        if not verdict or label is None:
            continue
        human.append(parse_label(label))
        machine.append(bool(verdict["passed"]))
    if not human:
        raise ValueError(
            "no transcript carried both a judge verdict and a human label; "
            "labels are joined on '<probe>:<run>' — check the KEY lines in the "
            "--label worksheet and that the transcripts were judged")
    return cohens_kappa(human, machine), len(human)


def cohens_kappa(a: list[bool], b: list[bool]) -> float:
    if len(a) != len(b):
        raise ValueError("label sets must be the same length")
    if not a:
        raise ValueError("cannot compute kappa on an empty label set")

    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    a_true, b_true = sum(a) / n, sum(b) / n
    expected = a_true * b_true + (1 - a_true) * (1 - b_true)
    if expected == 1.0:
        # Both raters were unanimous and identical: perfect, though uninformative.
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def sample_for_labelling(transcripts: list[dict], size: int = 20,
                         seed: int = 0) -> list[dict]:
    """A reproducible, row-stratified sample for a human to label.

    Stratified so the set does not over-represent whichever row happens to carry
    the most probes; seeded so a disputed kappa can be re-derived from the same
    transcripts.
    """
    if len(transcripts) <= size:
        return list(transcripts)

    by_row: dict[str, list[dict]] = defaultdict(list)
    for transcript in transcripts:
        by_row[transcript["row"]].append(transcript)

    rng = random.Random(seed)
    for bucket in by_row.values():
        rng.shuffle(bucket)

    chosen: list[dict] = []
    rows = sorted(by_row)
    while len(chosen) < size and any(by_row[r] for r in rows):
        for row in rows:
            if by_row[row] and len(chosen) < size:
                chosen.append(by_row[row].pop())
    return chosen

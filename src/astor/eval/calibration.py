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

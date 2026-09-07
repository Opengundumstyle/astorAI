"""The probe corpus itself must be well-formed and internally consistent."""
from __future__ import annotations

from pathlib import Path

from astor.eval import probes

CORPUS = Path(__file__).resolve().parent.parent / "data" / "eval" / "bench_probes.yaml"


def _load():
    return probes.load_probes(CORPUS)


def test_corpus_loads():
    assert len(_load()) == 50


def test_every_row_r1_to_r14_is_covered():
    rows = {p.row for p in _load()}
    assert rows == {f"R{n}" for n in range(1, 15)}


def test_absence_probes_expect_none():
    absent = [p for p in _load() if p.row == "R12" and len(p.turns) == 1]
    assert absent, "R12 must have single-turn probes"
    assert all(p.expect == "none" for p in absent)


def test_every_d1_probe_has_a_must_match():
    assert all(p.must_match for p in _load() if "D1" in p.dimensions)


def test_every_d5_probe_has_a_rubric_with_content():
    for probe in _load():
        if "D5" in probe.dimensions:
            assert probe.rubric is not None, probe.id
            assert probe.rubric.must_convey, probe.id


def test_write_capable_probes_carry_the_tag():
    """Anything that can create a sourcing row in production must be identifiable."""
    for probe in _load():
        if probe.id in {"M03", "M05"}:
            assert probes.PROBE_TAG in probe.turns[0], probe.id


def test_single_turn_absence_probes_must_not_flag():
    """No consent has been given in one turn, so flagging is a blocking failure."""
    for probe in _load():
        if probe.row == "R12" and len(probe.turns) == 1:
            assert probe.must_not_flag is True, probe.id


def test_multi_turn_probes_exist_and_are_two_turns():
    multi = [p for p in _load() if len(p.turns) > 1]
    assert len(multi) == 6
    assert all(len(p.turns) == 2 for p in multi)


def test_total_turn_budget_is_what_the_spec_claims():
    total = sum(len(p.turns) * p.runs for p in _load())
    assert total == 256

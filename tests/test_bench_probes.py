"""Probe loading — pure, no model, no network."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from astor.eval import probes


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "probes.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def test_loads_a_single_turn_probe(tmp_path: Path):
    path = _write(tmp_path, """\
        - id: P01
          row: R1
          turns: ["do you have DMEM?"]
          dimensions: [D1, D2, D8]
          must_match: "DMEM"
    """)
    loaded = probes.load_probes(path)
    assert len(loaded) == 1
    probe = loaded[0]
    assert probe.id == "P01"
    assert probe.turns == ("do you have DMEM?",)
    assert probe.dimensions == ("D1", "D2", "D8")
    assert probe.expect == "product"   # default
    assert probe.runs == 5             # single-turn default


def test_multi_turn_probe_defaults_to_three_runs(tmp_path: Path):
    path = _write(tmp_path, """\
        - id: M01
          row: R13
          turns:
            - "I need media"
            - "HEK293, adherent, 500 mL"
          dimensions: [D1, D8]
    """)
    assert probes.load_probes(path)[0].runs == 3


def test_explicit_runs_overrides_the_default(tmp_path: Path):
    path = _write(tmp_path, """\
        - id: P01
          row: R1
          turns: ["x"]
          dimensions: [D1]
          runs: 9
    """)
    assert probes.load_probes(path)[0].runs == 9


def test_rubric_is_parsed_into_tuples(tmp_path: Path):
    path = _write(tmp_path, """\
        - id: P21
          row: R7
          turns: ["high background on my western"]
          dimensions: [D5, D8]
          rubric:
            must_convey:
              - "names at least two plausible causes"
            disqualifiers:
              - "blames a product it did not surface"
    """)
    rubric = probes.load_probes(path)[0].rubric
    assert rubric.must_convey == ("names at least two plausible causes",)
    assert rubric.disqualifiers == ("blames a product it did not surface",)


def test_probe_without_a_rubric_has_none(tmp_path: Path):
    path = _write(tmp_path, """\
        - id: P01
          row: R1
          turns: ["x"]
          dimensions: [D1]
    """)
    assert probes.load_probes(path)[0].rubric is None


def test_duplicate_ids_are_rejected(tmp_path: Path):
    path = _write(tmp_path, """\
        - id: P01
          row: R1
          turns: ["x"]
          dimensions: [D1]
        - id: P01
          row: R2
          turns: ["y"]
          dimensions: [D1]
    """)
    with pytest.raises(ValueError, match="duplicate probe id: P01"):
        probes.load_probes(path)


def test_probe_with_no_turns_is_rejected(tmp_path: Path):
    path = _write(tmp_path, """\
        - id: P01
          row: R1
          turns: []
          dimensions: [D1]
    """)
    with pytest.raises(ValueError, match="P01 has no turns"):
        probes.load_probes(path)


def test_d5_probe_without_a_rubric_is_rejected(tmp_path: Path):
    """A judged dimension with nothing to judge against is a silent no-op."""
    path = _write(tmp_path, """\
        - id: P21
          row: R7
          turns: ["x"]
          dimensions: [D5, D8]
    """)
    with pytest.raises(ValueError, match="P21 declares D5 but has no rubric"):
        probes.load_probes(path)


def test_probe_tag_is_a_stable_literal():
    assert probes.PROBE_TAG == "astor-bench-probe"

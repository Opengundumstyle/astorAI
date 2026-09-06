"""Pure metrics for the assistant pass-rate eval — no model, no network.

The eval itself needs an LLM; these tests pin the judging and gating logic that
decides what its output means, so a green run cannot be an artefact of scoring.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from astor.eval import assistant


def _s(**kw):
    base = {"question": "do you have EMEM?", "must_match": "EMEM", "expect": "product"}
    return assistant.Scenario(**{**base, "notes": "", **kw})


# ------------------------------------------------------------------- judging #
def test_run_passes_when_a_referenced_item_matches():
    assert assistant.judge(_s(), ["Eagle's Minimum Essential Medium (EMEM) 1x"]) is True


def test_run_fails_when_nothing_referenced():
    assert assistant.judge(_s(), []) is False


def test_match_is_case_insensitive_and_regex():
    assert assistant.judge(_s(must_match=r"6[\s-]?well"), ["Culture plate, 6 Well"]) is True


def test_expect_none_inverts_the_judgement():
    """For a product Astor genuinely does not carry, referencing one is the failure."""
    scenario = _s(expect="none", must_match="unobtainium")
    assert assistant.judge(scenario, ["Unobtainium Reagent"]) is False
    assert assistant.judge(scenario, ["DMEM"]) is True


# ------------------------------------------------------------------ outcomes #
def test_pass_rate_counts_passing_runs():
    outcome = assistant.Outcome(scenario=_s(), runs=8, passes=4)
    assert outcome.pass_rate == 0.5


def test_gate_fails_and_names_the_scenario_below_the_bar():
    outcomes = [
        assistant.Outcome(scenario=_s(question="finds EMEM"), runs=8, passes=4),
        assistant.Outcome(scenario=_s(question="finds DMEM"), runs=8, passes=8),
    ]
    result = assistant.gate(outcomes, assistant.GateBars(min_pass_rate=1.0))
    assert result.passed is False
    assert any("finds EMEM" in r for r in result.reasons)
    assert not any("finds DMEM" in r for r in result.reasons)


def test_gate_passes_when_every_scenario_meets_the_bar():
    outcomes = [assistant.Outcome(scenario=_s(), runs=4, passes=4)]
    assert assistant.gate(outcomes, assistant.GateBars(min_pass_rate=1.0)).passed is True


# ------------------------------------------------------------------ scenarios #
def test_loads_scenarios_from_csv(tmp_path: Path):
    csv_file = tmp_path / "scenarios.csv"
    csv_file.write_text(textwrap.dedent("""\
        question,expect,must_match,notes
        what emem medium do you have?,product,EMEM,reported 2026-09-02
    """))
    scenarios = assistant.load_scenarios(csv_file)
    assert len(scenarios) == 1
    assert scenarios[0].question == "what emem medium do you have?"
    assert scenarios[0].must_match == "EMEM"


def test_report_shows_each_scenario_pass_rate():
    rendered = assistant.render([assistant.Outcome(scenario=_s(), runs=8, passes=4)])
    assert "4/8" in rendered

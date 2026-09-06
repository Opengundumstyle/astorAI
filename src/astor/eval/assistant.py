"""Pass-rate harness for the storefront assistant.

WHY A PASS *RATE*
    The defect this exists to catch is not deterministic. On 2026-09-02 the
    assistant told a customer Astor carries no EMEM; the products were in the
    catalog the whole time. The turn failed only because the model happened to
    phrase its search as "EMEM medium" / "MEM medium" — literal substrings that
    the old search could not match. Re-running the same question found them
    about half the time.

    A single-shot test would therefore have passed or failed at random, and a
    unit test of the search function would never have seen the bug at all: the
    failure lives in the join between a model's phrasing and the retrieval
    rules. So each scenario runs N times and is scored as a rate against a bar.

This module is pure — judging, aggregation and gating only. Driving the model
lives in `scripts/run_assistant_eval.py`, mirroring how `accuracy.py` stays
free of the code that fetches embeddings.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

PRODUCT = "product"
NONE = "none"


@dataclass(frozen=True)
class Scenario:
    """One customer question and what a correct turn must reference.

    `must_match` is a regex tested against the names of the items the turn
    surfaced — the chips the shopper actually sees — not the prose, which a
    model can always word around.
    """

    question: str
    must_match: str
    expect: str = PRODUCT       # PRODUCT: must reference it. NONE: must not.
    notes: str = ""


@dataclass
class Outcome:
    scenario: Scenario
    runs: int = 0
    passes: int = 0
    # Query strings the model issued, per run. This is the diagnostic that
    # identified the root cause; keep it in the report.
    queries: list[list[str]] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return (self.passes / self.runs) if self.runs else 0.0


@dataclass
class GateBars:
    min_pass_rate: float = 1.0


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def judge(scenario: Scenario, item_names: list[str]) -> bool:
    """Did this run do the right thing?"""
    pattern = re.compile(scenario.must_match, re.IGNORECASE)
    referenced = any(pattern.search(name or "") for name in item_names)
    return not referenced if scenario.expect == NONE else referenced


def gate(outcomes: list[Outcome], bars: GateBars = GateBars()) -> GateResult:
    reasons = [
        f"{o.scenario.question!r}: {o.passes}/{o.runs} "
        f"({o.pass_rate:.2f} < {bars.min_pass_rate})"
        for o in outcomes
        if o.pass_rate < bars.min_pass_rate
    ]
    return GateResult(passed=not reasons, reasons=reasons)


def load_scenarios(path: Path) -> list[Scenario]:
    with Path(path).open() as f:
        return [
            Scenario(question=row["question"], must_match=row["must_match"],
                     expect=(row.get("expect") or PRODUCT).strip(),
                     notes=row.get("notes", ""))
            for row in csv.DictReader(f)
            if (row.get("question") or "").strip()
        ]


def render(outcomes: list[Outcome], bars: GateBars = GateBars()) -> str:
    lines = ["scenario                                           pass    rate",
             "-" * 70]
    for o in outcomes:
        flag = " " if o.pass_rate >= bars.min_pass_rate else "!"
        question = o.scenario.question[:48].ljust(48)
        lines.append(f"{flag}{question} {o.passes}/{o.runs}   {o.pass_rate:.2f}")
    result = gate(outcomes, bars)
    lines.append("-" * 70)
    lines.append("GATE: PASS" if result.passed else "GATE: FAIL")
    lines += [f"  - {r}" for r in result.reasons]
    return "\n".join(lines)

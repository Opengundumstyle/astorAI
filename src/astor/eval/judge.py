"""Grades scientific correctness against a per-probe rubric.

WHY A JUDGE AND NOT A REGEX
    Every other dimension in this benchmark is deterministic, and deliberately
    so. Scientific correctness is the one that cannot be: "grow them without
    serum for a few passages first" conveys serum-free adaptation, and no
    keyword list gets there. So this is the only module in `astor.eval` that
    calls a model.

WHY THE SCORE IS WORTHLESS UNTIL CALIBRATED
    A judge's number means nothing until someone has checked it against human
    labels. `calibration.py` measures that agreement; the scorecard marks every
    D5 cell `uncalibrated` until it has been measured. See the design spec.

BLIND BY CONSTRUCTION
    The judge sees the question, the rubric and the reply. Not the item list, not
    the probe id, not which run it is. It cannot be primed by context it should
    not have.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from astor.config import settings
from astor.eval.probes import Rubric

log = logging.getLogger(__name__)

JUDGE_MODEL = "claude-opus-5"

SYSTEM = (
    "You grade a lab-supply assistant's answer against a rubric written by a "
    "bench scientist. Judge only what the rubric asks. An answer that is "
    "scientifically sound but phrased differently from the rubric still passes — "
    "the rubric lists concepts, not wording. An answer that states something "
    "false, or that hits a disqualifier, fails. Give one sentence of reasoning."
)


class Verdict(BaseModel):
    passed: bool = Field(description="Does the answer satisfy the rubric?")
    reason: str = Field(description="One sentence. Cite the rubric point that decided it.")


def prompt(question: str, reply: str, rubric: Rubric) -> str:
    parts = [
        f"CUSTOMER ASKED:\n{question}",
        f"ASSISTANT ANSWERED:\n{reply}",
        "RUBRIC — the answer must convey all of:\n"
        + "\n".join(f"  - {c}" for c in rubric.must_convey),
    ]
    if rubric.disqualifiers:
        parts.append("Disqualifiers — any one of these fails the answer:\n"
                     + "\n".join(f"  - {d}" for d in rubric.disqualifiers))
    return "\n\n".join(parts)


def judge_science(question: str, reply: str, rubric: Rubric,
                  *, client=None, model: str | None = None) -> Verdict:
    if client is None:
        from anthropic import Anthropic
        client = Anthropic(api_key=settings.anthropic_api_key)

    response = client.messages.parse(
        model=model or JUDGE_MODEL,
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt(question, reply, rubric)}],
        output_format=Verdict,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        # Never raise: a 30-minute run must not abort over one transcript. A
        # refusal is recorded as a failure with its cause visible in the report.
        log.warning("judge refused or returned nothing for: %s", question[:60])
        return Verdict(passed=False, reason="judge refused or returned no verdict")
    return response.parsed_output

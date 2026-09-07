"""Deterministic scorers, one per benchmark dimension.

Pure by contract — no network, no database, no model calls — the same contract
`assistant.py` and `accuracy.py` keep, and for the same reason: a scorer that
can fail for an environmental reason cannot be trusted to explain a red cell.

D1 is not here. It is `assistant.judge()`, unchanged, reused as-is.
D3 is not here either: the App Proxy returns no tool-call trace, so tool-use
correctness is unobservable against the deployed assistant. See the spec's
Limitations.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# D8 — format. The prompt promises plain text, 2-5 sentences, 1-3 named items.
# --------------------------------------------------------------------------- #
_MARKDOWN = (
    ("markdown:bold", re.compile(r"\*\*")),
    ("markdown:heading", re.compile(r"(?m)^\s*#{1,6}\s")),
    ("markdown:code", re.compile(r"`")),
    ("markdown:link", re.compile(r"\[[^\]]+\]\([^)]+\)")),
)

# A terminator only ends a sentence when whitespace or end-of-string follows, so
# "4.5 degrees" stays one sentence.
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")

MIN_SENTENCES = 2
MAX_SENTENCES = 5
MAX_NAMED_ITEMS = 3


def sentence_count(reply: str) -> int:
    return len([s for s in _SENTENCE_END.split(reply.strip()) if s.strip()])


def format_violations(reply: str, named: list[str]) -> list[str]:
    violations = [label for label, pattern in _MARKDOWN if pattern.search(reply)]
    count = sentence_count(reply)
    if not MIN_SENTENCES <= count <= MAX_SENTENCES:
        violations.append(f"sentences:{count}")
    if len(named) > MAX_NAMED_ITEMS:
        violations.append(f"named_items:{len(named)}")
    return violations

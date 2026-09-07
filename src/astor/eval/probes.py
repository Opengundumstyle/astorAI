"""Benchmark probes: what to ask, and what a correct turn must do.

YAML, not CSV, because a probe carries a conversation (several turns) and a
rubric (two lists of prose criteria). Neither fits a CSV cell without escaping
that nobody will maintain by hand.

Pure: this module loads and validates. It never drives a model and never scores
one — see `dimensions` for scoring and `scripts/run_bench.py` for driving.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PRODUCT = "product"
NONE = "none"

# Marker embedded in every probe that can cause a write to `sourcing_requests`,
# so any row the benchmark creates in production is unmistakably test data and
# the cleanup DELETE keeps matching on a re-run months later. Deliberately not
# dated: a dated tag goes stale and orphans its own rows.
PROBE_TAG = "astor-bench-probe"


@dataclass(frozen=True)
class Rubric:
    """What the judge grades against. Concepts, not keywords — the judge exists
    precisely because a regex cannot tell that "grow them without serum first"
    conveys serum-free adaptation."""

    must_convey: tuple[str, ...] = ()
    disqualifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Probe:
    id: str
    row: str
    turns: tuple[str, ...]
    dimensions: tuple[str, ...]
    expect: str = PRODUCT          # PRODUCT: must surface it. NONE: must not.
    must_match: str = ""           # regex over surfaced item names (D1)
    must_not_flag: bool = False    # D4-consent: no sourcing row may be created
    runs: int = 5
    rubric: Rubric | None = None
    notes: str = ""


def _rubric(raw: dict | None) -> Rubric | None:
    if not raw:
        return None
    return Rubric(
        must_convey=tuple(raw.get("must_convey") or ()),
        disqualifiers=tuple(raw.get("disqualifiers") or ()),
    )


def _probe(raw: dict) -> Probe:
    turns = tuple(raw.get("turns") or ())
    dimensions = tuple(raw.get("dimensions") or ())
    probe_id = raw["id"]
    if not turns:
        raise ValueError(f"{probe_id} has no turns")
    rubric = _rubric(raw.get("rubric"))
    if "D5" in dimensions and rubric is None:
        raise ValueError(f"{probe_id} declares D5 but has no rubric")
    # A multi-turn probe costs a turn per message, so it defaults to fewer runs.
    default_runs = 5 if len(turns) == 1 else 3
    return Probe(
        id=probe_id,
        row=raw["row"],
        turns=turns,
        dimensions=dimensions,
        expect=(raw.get("expect") or PRODUCT).strip(),
        must_match=raw.get("must_match") or "",
        must_not_flag=bool(raw.get("must_not_flag")),
        runs=int(raw.get("runs") or default_runs),
        rubric=rubric,
        notes=raw.get("notes") or "",
    )


def load_probes(path: Path) -> list[Probe]:
    raw = yaml.safe_load(Path(path).read_text()) or []
    loaded: list[Probe] = []
    seen: set[str] = set()
    for entry in raw:
        probe = _probe(entry)
        if probe.id in seen:
            raise ValueError(f"duplicate probe id: {probe.id}")
        seen.add(probe.id)
        loaded.append(probe)
    return loaded

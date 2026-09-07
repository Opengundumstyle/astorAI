"""Scorecard aggregation for the benchmark. Pure — counting and arithmetic only.

WHY WILSON AND NOT A BARE FRACTION
    Every probe runs a handful of times, so a cell's number is an estimate from a
    small sample. At five runs a perfect 5/5 has a 95% interval of roughly
    [0.57, 1.00] — it is consistent with a true pass rate of well under two in
    three. Printing "1.00" alone invites a reader to treat that as settled. The
    interval is printed next to every cell so it cannot be.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

# Per-dimension bars. Blocking dimensions bar at 1.00 because a single
# occurrence is a release-stopper irrespective of rate; the judged dimension
# bars lower because it carries judge noise as well as model noise.
BARS: dict[str, float] = {
    "D1": 0.90,      # retrieval          major
    "D2": 1.00,      # grounding          blocking
    "D4B": 1.00,     # model adherence    blocking
    "D4C": 1.00,     # consent            blocking
    "D5": 0.85,      # science            major
    "D8": 0.90,      # format             minor
}

# D4A is deliberately absent: it is a catalog-normalisation backlog, not a model
# score, so it has no bar and cannot fail a cell.


def wilson(passes: int, runs: int, z: float = 1.96) -> tuple[float, float]:
    """95% score interval for a binomial proportion. Behaves sanely at 0/n and
    n/n, which the normal approximation does not."""
    if runs <= 0:
        return (0.0, 0.0)
    p = passes / runs
    denominator = 1 + z * z / runs
    centre = (p + z * z / (2 * runs)) / denominator
    spread = z * math.sqrt(p * (1 - p) / runs + z * z / (4 * runs * runs)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))


@dataclass(frozen=True)
class Cell:
    row: str
    dim: str
    passes: int
    runs: int

    @property
    def rate(self) -> float:
        return (self.passes / self.runs) if self.runs else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.passes, self.runs)

    @property
    def bar(self) -> float | None:
        return BARS.get(self.dim)


def aggregate(results: list[tuple[str, str, bool]]) -> list[Cell]:
    """`results` is one (row, dimension, passed) entry per run per dimension."""
    tally: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for row, dim, passed in results:
        tally[(row, dim)][0] += int(passed)
        tally[(row, dim)][1] += 1
    return [Cell(row, dim, passes, runs)
            for (row, dim), (passes, runs) in sorted(tally.items())]


def failing(cells: list[Cell]) -> list[Cell]:
    return [c for c in cells if c.bar is not None and c.rate < c.bar]


_HEADER = f"{'row':<5}{'dim':<6}{'pass':>8}{'rate':>8}{'95% CI':>16}{'bar':>7}  status"
_RULE = "-" * len(_HEADER)


def render_scorecard(cells: list[Cell], *, calibrated: bool) -> str:
    lines = [_HEADER, _RULE]
    for cell in cells:
        low, high = cell.interval
        bar = "  --  " if cell.bar is None else f"{cell.bar:>6.2f}"
        if cell.bar is None:
            status = "report"          # D4A: surfaced, never gated
        elif cell.rate < cell.bar:
            status = "FAIL"
        else:
            status = "ok"
        if cell.dim == "D5" and not calibrated:
            status += " (uncalibrated)"
        lines.append(
            f"{cell.row:<5}{cell.dim:<6}{cell.passes:>4}/{cell.runs:<3}"
            f"{cell.rate:>8.2f}{f'[{low:.2f}, {high:.2f}]':>16}{bar}  {status}"
        )

    failures = failing(cells)
    lines += [_RULE, "GATE: PASS" if not failures else "GATE: FAIL"]
    lines += [f"  - {c.row}/{c.dim}: {c.rate:.2f} < {c.bar:.2f}" for c in failures]
    if not calibrated and any(c.dim == "D5" for c in cells):
        lines.append("  ! D5 is uncalibrated — no human agreement measured. "
                     "Treat those cells as indicative, not as a result.")
    return "\n".join(lines)


def render_backlog(leaks: list[tuple[str, int]]) -> str:
    """D4A: vendor tokens the assistant echoed out of product names it was given.

    Not a model failure — the name is what the tool returned and what the UI card
    renders. This is the catalog-normalisation worklist.
    """
    if not leaks:
        return "D4A catalog-normalisation backlog: none observed."
    lines = ["D4A catalog-normalisation backlog (vendor tokens carried in product names):",
             f"  {'token':<24}{'turns':>6}"]
    lines += [f"  {token:<24}{count:>6}" for token, count in leaks]
    return "\n".join(lines)

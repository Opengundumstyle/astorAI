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


def failing(cells: list[Cell], *, calibrated: bool = True) -> list[Cell]:
    """Cells below their own bar.

    An uncalibrated D5 cell is excluded. The report already says those cells are
    indicative and not a result; letting them drive the gate anyway would be
    gating on decoration, and would fail a release on a judge nobody has checked
    against a human.
    """
    return [c for c in cells
            if c.bar is not None and c.rate < c.bar
            and not (c.dim == "D5" and not calibrated)]


def _header(key_label: str = "row") -> str:
    return (f"{key_label:<6}{'dim':<6}{'pass':>8}{'rate':>8}"
            f"{'95% CI':>16}{'bar':>7}  status")


_HEADER = _header()
_RULE = "-" * len(_HEADER)


def render_scorecard(cells: list[Cell], *, calibrated: bool, gate: bool = True,
                     key_label: str = "row") -> str:
    """`gate=False` renders the same table without the GATE verdict — used for the
    per-probe breakdown, so that exactly one GATE line in a report is the gate.
    `key_label` names the first column, which is a probe id in that breakdown."""
    lines = [_header(key_label), _RULE]
    for cell in cells:
        low, high = cell.interval
        bar = "  --  " if cell.bar is None else f"{cell.bar:>6.2f}"
        uncalibrated = cell.dim == "D5" and not calibrated
        if cell.bar is None:
            status = "report"          # D4A: surfaced, never gated
        elif cell.rate >= cell.bar:
            status = "ok"
        else:
            # An uncalibrated D5 cell is below its bar but does not gate, so it
            # must not print the word the gate uses.
            status = "below bar" if uncalibrated else "FAIL"
        if uncalibrated:
            status += " (uncalibrated)"
        lines.append(
            f"{cell.row:<6}{cell.dim:<6}{cell.passes:>4}/{cell.runs:<3}"
            f"{cell.rate:>8.2f}{f'[{low:.2f}, {high:.2f}]':>16}{bar}  {status}"
        )

    failures = failing(cells, calibrated=calibrated)
    if gate:
        lines += [_RULE, "GATE: PASS" if not failures else "GATE: FAIL"]
    else:
        lines += [_RULE, "no cell below its bar" if not failures else "below bar:"]
    lines += [f"  - {c.row}/{c.dim}: {c.rate:.2f} < {c.bar:.2f}" for c in failures]
    if gate and not calibrated and any(c.dim == "D5" for c in cells):
        lines.append("  ! D5 is uncalibrated — no human agreement measured. "
                     "Treat those cells as indicative, not as a result: they are "
                     "reported here and excluded from the gate.")
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


# --------------------------------------------------------------------------- #
# D9 — latency. Reported, never gated: a slow answer is a product problem, not a
# correctness one. p95 is the number that matters; a shopper waiting eleven
# seconds is not consoled by a good mean.
# --------------------------------------------------------------------------- #
def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile. No interpolation — with a few hundred samples the
    difference is noise, and a real observed turn time is easier to argue with."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def render_latency(seconds: list[float]) -> str:
    if not seconds:
        return "latency: no turns recorded."
    return (f"latency over {len(seconds)} turns: "
            f"p50 {percentile(seconds, 0.5):.1f}s  "
            f"p95 {percentile(seconds, 0.95):.1f}s  "
            f"max {max(seconds):.1f}s")

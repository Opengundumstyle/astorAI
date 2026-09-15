"""Does the troubleshooting tool surface the right rows?

Retrieval layer only: no model, no network. Each gold case names the entry ids a
correct lookup must include; the score is the fraction returned in the top N.
The assistant layer (does the reply reference the right product chips) reuses
astor.eval.assistant scenarios via to_scenarios().
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from astor.curation.troubleshoot import Matcher
from astor.eval.assistant import PRODUCT, Scenario
from astor.eval.gate import GateResult


@dataclass(frozen=True)
class GoldCase:
    case_id: str
    category_id: str
    question: str
    expected_entry_ids: tuple[str, ...]
    expected_fix_roles: tuple[str, ...]
    must_match: str
    notes: str


def _split(v: str | None) -> tuple[str, ...]:
    return tuple(x.strip() for x in (v or "").split(";") if x.strip())


def load_gold(path: Path) -> list[GoldCase]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return [
            GoldCase(r["case_id"].strip(), r["category_id"].strip(), r["question"].strip(),
                     _split(r.get("expected_entry_ids")), _split(r.get("expected_fix_roles")),
                     (r.get("must_match") or "").strip(), (r.get("notes") or "").strip())
            for r in csv.DictReader(f) if (r.get("case_id") or "").strip()
        ]


@dataclass(frozen=True)
class CaseResult:
    case: GoldCase
    returned_ids: tuple[str, ...]
    hit: float
    match: str
    drafted_returned: int = 0


@dataclass
class RetrievalReport:
    results: list[CaseResult]
    overall: float
    per_category: dict[str, float]
    drafted_share: float


def evaluate_retrieval(matcher: Matcher, cases: list[GoldCase], *, limit: int = 5) -> RetrievalReport:
    results: list[CaseResult] = []
    for c in cases:
        hits, kind = matcher.search(c.question, category=None, limit=limit)
        ids = tuple(h.entry.entry_id for h in hits)
        expected = set(c.expected_entry_ids)
        hit = (len(expected & set(ids)) / len(expected)) if expected else 0.0
        drafted = sum(1 for h in hits if h.entry.confidence == "drafted")
        results.append(CaseResult(c, ids, hit, kind, drafted))
    overall = sum(r.hit for r in results) / len(results) if results else 0.0
    per_cat: dict[str, list[float]] = {}
    for r in results:
        per_cat.setdefault(r.case.category_id, []).append(r.hit)
    per_category = {k: sum(v) / len(v) for k, v in per_cat.items()}
    returned = sum(len(r.returned_ids) for r in results)
    drafted_share = (sum(r.drafted_returned for r in results) / returned) if returned else 0.0
    return RetrievalReport(results, overall, per_category, drafted_share)


@dataclass
class Bars:
    min_overall: float = 0.8
    min_per_category: float = 0.6


def gate(report: RetrievalReport, bars: Bars = Bars()) -> GateResult:
    reasons: list[str] = []
    if report.overall < bars.min_overall:
        reasons.append(f"overall hit rate {report.overall:.3f} < {bars.min_overall}")
    for cid, rate in sorted(report.per_category.items()):
        if rate < bars.min_per_category:
            reasons.append(f"{cid} hit rate {rate:.3f} < {bars.min_per_category}")
    return GateResult(passed=not reasons, reasons=reasons)


def to_scenarios(cases: list[GoldCase]) -> list[Scenario]:
    """Assistant-layer scenarios for cases that name a product to expect."""
    return [Scenario(question=c.question, must_match=c.must_match, expect=PRODUCT, notes=c.case_id)
            for c in cases if c.must_match]


def render(report: RetrievalReport) -> str:
    lines = [f"overall hit rate: {report.overall:.3f}   drafted share of returned rows: {report.drafted_share:.2f}"]
    for cid, rate in sorted(report.per_category.items()):
        lines.append(f"  {cid:28s} {rate:.3f}")
    misses = [r for r in report.results if r.hit < 1.0]
    if misses:
        lines.append("misses:")
        for r in misses:
            lines.append(f"  {r.case.case_id} [{r.match}] expected {list(r.case.expected_entry_ids)} "
                         f"got {list(r.returned_ids)} :: {r.case.question}")
    return "\n".join(lines)

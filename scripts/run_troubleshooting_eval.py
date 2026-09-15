"""Retrieval-layer eval of the troubleshoot tool against the real curation tables.

    .venv/bin/python scripts/run_troubleshooting_eval.py            # keyword + dev embedder
    .venv/bin/python scripts/run_troubleshooting_eval.py --no-embed # keyword only

Exit 1 when the gate fails. The assistant layer (live model) is run through
scripts/run_assistant_eval.py with the scenarios this script can export:

    .venv/bin/python scripts/run_troubleshooting_eval.py --export-scenarios /tmp/ts_scenarios.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from astor import curation
from astor.curation.troubleshoot import Matcher
from astor.eval import troubleshooting as ev

GOLD = Path(__file__).resolve().parents[1] / "data" / "eval" / "troubleshooting_gold.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--export-scenarios", type=Path)
    args = ap.parse_args()

    embedder = None
    if not args.no_embed:
        from astor.catalog.embeddings import get_embedder
        embedder = get_embedder()
    matcher = Matcher(curation.tables(), embedder=embedder)
    cases = ev.load_gold(GOLD)

    if args.export_scenarios:
        with args.export_scenarios.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["question", "expect", "must_match", "notes"])
            for s in ev.to_scenarios(cases):
                w.writerow([s.question, s.expect, s.must_match, s.notes])
        print(f"wrote {args.export_scenarios}")

    report = ev.evaluate_retrieval(matcher, cases, limit=args.limit)
    print(ev.render(report))
    g = ev.gate(report)
    print("GATE:", "PASS" if g.passed else "FAIL")
    for reason in g.reasons:
        print("  " + reason)
    return 0 if g.passed else 1


if __name__ == "__main__":
    sys.exit(main())

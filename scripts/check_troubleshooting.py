"""Lint the troubleshooting table and report what Mary still has to review.

    .venv/bin/python scripts/check_troubleshooting.py

Exit 1 if the tables fail validation. Otherwise print per-category counts, the
share of rows still `drafted`, and any checklist role missing from roles.csv.
"""
from __future__ import annotations

import sys
from collections import Counter

from astor import curation
from astor.curation.loader import CurationError


def main() -> int:
    try:
        t = curation.tables()
    except CurationError as exc:
        print(f"INVALID: {exc}")
        return 1
    per_cat = Counter(e.category_id for e in t.troubleshooting)
    drafted = sum(1 for e in t.troubleshooting if e.confidence == "drafted")
    print(f"{len(t.troubleshooting)} entries across {len(per_cat)} categories")
    for cid, n in sorted(per_cat.items()):
        print(f"  {cid:28s} {n:3d}")
    print(f"drafted: {drafted}/{len(t.troubleshooting)}")
    missing = sorted({r.role for r in t.checklist} - set(t.roles))
    print(f"checklist roles missing from roles.csv: {missing or 'none'}")
    no_fix = [e.entry_id for e in t.troubleshooting if e.fix_role is None]
    print(f"entries with procedural fix only: {no_fix or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

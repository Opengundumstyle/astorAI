"""Pre-flight: does the catalog still match what the probes assume?

Grading a model against stale ground truth is worse than not testing it. Every
`must_match` on a probe that expects a product must return at least one hit, and
every `must_match` on an absence probe must return none.

Runs against the local Postgres, which mirrors the deployed catalog. A mismatch
means the probe file is stale — not that the assistant is wrong.

    python -m scripts.check_bench_ground_truth
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from astor.api import repo
from astor.db.base import session_scope
from astor.eval import probes

_CORPUS = Path(__file__).resolve().parent.parent / "data" / "eval" / "bench_probes.yaml"

# `must_match` is a regex for judging surfaced names; the catalog search takes
# plain text. Strip the regex furniture and search the first alternative.
_REGEX_CHARS = re.compile(r"\\b|\\s|\\-|[\[\]()*+?{}^$]")


def search_term(must_match: str) -> str:
    return _REGEX_CHARS.sub(" ", must_match.split("|")[0]).strip()


def main() -> None:
    corpus = probes.load_probes(_CORPUS)
    problems: list[str] = []
    with session_scope() as session:
        for probe in corpus:
            if not probe.must_match:
                continue
            term = search_term(probe.must_match)
            _, total = repo.list_products(session, term, None, 1, 1)
            if probe.expect == probes.NONE and total != 0:
                problems.append(f"{probe.id}: expects absence but {term!r} returns {total}")
            elif probe.expect == probes.PRODUCT and total == 0:
                problems.append(f"{probe.id}: expects a match but {term!r} returns 0")
            print(f"  {probe.id:<6} {term!r:<28} -> {total}")

    if problems:
        print("\nGROUND TRUTH STALE:")
        for problem in problems:
            print(f"  - {problem}")
        sys.exit(1)
    print("\nground truth OK")


if __name__ == "__main__":
    main()

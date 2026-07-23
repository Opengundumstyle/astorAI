"""Rebuild the equivalence map on real embeddings, gated.

Sequence (snapshot is the operator's responsibility BEFORE running -- see plan):
  1. backfill embeddings (only-stale by default)
  2. gate:
       (a) labeled harness on data/eval/gold.csv  -> precision, kind_accuracy
       (b) corpus sample sanity                    -> exact_rate
  3. both pass -> TRUNCATE + rematch all; either fails -> stop, print numbers.

Usage:
    python -m scripts.rebuild_map                # gate, auto-proceed if it passes
    python -m scripts.rebuild_map --force-rematch  # skip the gate (manual override)
"""
from __future__ import annotations

import argparse
from pathlib import Path

from astor.catalog import backfill, matcher
from astor.catalog.embeddings import get_embedder
from astor.config import settings
from astor.db.base import session_scope
from astor.eval import gate
from astor.eval.accuracy import run as run_eval

GOLD = Path("data/eval/gold.csv")
PRODUCTS = Path("data/eval/products.csv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-rematch", action="store_true", help="skip the gate")
    ap.add_argument("--all", dest="all_rows", action="store_true", help="re-embed every row")
    args = ap.parse_args()

    embedder = get_embedder()
    if type(embedder).__name__ == "DevEmbedder":
        raise SystemExit(f"Refusing: DevEmbedder (provider={settings.embeddings_provider}).")

    with session_scope() as session:
        stats = backfill.backfill_embeddings(session, embedder, only_stale=not args.all_rows)
        print(f"[backfill] total={stats.total} embedded={stats.embedded} skipped={stats.skipped}")

    if not args.force_rematch:
        report = run_eval(PRODUCTS, GOLD, embedder,
                          exact_threshold=settings.equiv_exact_threshold,
                          substitute_threshold=settings.equiv_substitute_threshold)
        m = report.metrics
        with session_scope() as session:
            exact_rate = matcher.sample_exact_rate(session, embedder, sample_n=500)
        print(f"[gate] precision={m['precision']} kind_accuracy={m['kind_accuracy']} exact_rate={exact_rate:.3f}")
        result = gate.gate_decision(m["precision"], m["kind_accuracy"], exact_rate)
        if not result.passed:
            print("[gate] FAILED -> not rematching. Reasons:")
            for reason in result.reasons:
                print("   -", reason)
            raise SystemExit(1)
        print("[gate] PASSED -> proceeding to full rematch")

    with session_scope() as session:
        total = matcher.rematch_all(session, embedder)
    print(f"[rematch] equivalences_written={total}")


if __name__ == "__main__":
    main()

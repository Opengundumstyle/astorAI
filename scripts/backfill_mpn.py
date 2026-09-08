"""Backfill `products.mpn` from catalogue codes the product names already carry.

Usage:
    python -m scripts.backfill_mpn                # dry run (default)
    python -m scripts.backfill_mpn --apply        # write
    python -m scripts.backfill_mpn --revert       # undo: clear only what this wrote

WHY
    `scoring.attribute_bonus` awards +0.50 when brand AND mpn both match -- the
    largest term in the equivalence confidence formula, and the only one that
    asserts identity rather than similarity. It has never fired: `mpn` is blank on
    16,016 of 16,019 products because `shopify_source.py` reads it from a Shopify
    metafield or `variant.barcode` and both are empty upstream. The codes were in
    the product names the whole time.

SAFETY
    Dry run by default. Only parenthesised codes are used, and only where every
    product carrying a code under one brand is the same product -- see
    `audit.mpn_backfill_plan`. A blank mpn is a missing signal; a wrong one is a
    false identity claim that the +0.50 rule then amplifies.

    This does NOT fix ingestion: a re-ingest still writes NULL. The forward fix
    belongs in `shopify_source.py`.
"""
from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import func, select, update

from astor.catalog import audit
from astor.db.base import session_scope
from astor.db.models import Product


def plan(session):
    rows = session.execute(
        select(Product.id, Product.name, Product.brand, Product.mpn)).all()
    return audit.mpn_backfill_plan((str(i), n, b, m) for i, n, b, m in rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the assignments")
    mode.add_argument("--revert", action="store_true",
                      help="clear mpn on rows whose value still equals the code in their name")
    args = ap.parse_args()

    with session_scope() as session:
        total = session.scalar(select(func.count(Product.id))) or 0
        before = session.scalar(
            select(func.count(Product.id))
            .where(Product.mpn.isnot(None), Product.mpn != "")) or 0

        if args.revert:
            rows = session.execute(
                select(Product.id, Product.name, Product.mpn)
                .where(Product.mpn.isnot(None))).all()
            undo = [str(i) for i, n, m in rows if m and audit.code_in_name(n) == m]
            for pid in undo:
                session.execute(update(Product).where(Product.id == pid).values(mpn=None))
            print(f"reverted {len(undo):,} rows to mpn = NULL")
            return

        assignments, skipped = plan(session)
        by_reason = Counter(skipped.values())
        print(f"catalog                    {total:,} products")
        print(f"mpn populated before       {before:,}")
        print(f"assignments planned        {len(assignments):,}")
        print(f"codes skipped              {len(skipped):,}")
        for reason, n in by_reason.most_common():
            print(f"    {reason:<20} {n:>6,}")
        if by_reason.get("unique_constraint"):
            print("\n  NOTE: `unique_constraint` is not a data problem this script can fix.")
            print("  UniqueConstraint(brand, mpn) says one row per manufacturer part, but the")
            print("  Shopify import made each VARIANT its own Product row, so pack-size")
            print("  variants share a code. Same reason `scoring`'s +0.50 brand+MPN bonus can")
            print("  never fire between two products: the constraint forbids the match.")
        for key, reason in sorted(skipped.items()):
            if reason == "two_identities":
                print(f"    FIX AT SOURCE {key[1]} [{key[0]}] — one code, two products")

        if not args.apply:
            print("\ndry run — no rows written. Re-run with --apply to commit.")
            return

        for pid, code in assignments.items():
            session.execute(update(Product).where(Product.id == pid).values(mpn=code))
        session.flush()
        after = session.scalar(
            select(func.count(Product.id))
            .where(Product.mpn.isnot(None), Product.mpn != "")) or 0
        print(f"\nwrote {len(assignments):,} rows — mpn populated {before:,} -> {after:,}")


if __name__ == "__main__":
    main()

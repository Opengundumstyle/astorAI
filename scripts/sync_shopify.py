"""Re-sync the catalog from Shopify: update the rows we hold, add new variants,
retire vanished ones. Dry run by default.

WHY NOT `ingest_shopify`
    That script is the FIRST load. It can only update a product it can identify
    by (brand, mpn), and the Shopify catalog carries no MPN, so re-running it
    against a populated database inserts every product a second time. The
    catalog froze at its first load (2026-08-21) and drifted from the store.

WHAT THIS DOES (see astor/catalog/sync.py for the rules)
    Matches each Shopify variant to an existing offer by variant id
    (`supplier_offers.external_id`), backfilling that id on the first run by
    SKU, then by exact name+brand when unambiguous. Matched rows take the
    store's current title, category, specs, sellable flag, SKU, price, stock
    and pack size; a retitled product has its vector cleared and re-embedded.
    Unmatched variants are inserted. Offers no longer on the store mark their
    product unsellable (never deleted: protocol links point at it).

    A variant whose SKU another variant already holds is reported and skipped.
    Shopify allows that; the offer table does not. Fix it in Shopify admin.

Usage:
    python -m scripts.sync_shopify                  # dry run: plan + report, no writes
    python -m scripts.sync_shopify --apply          # write, then re-embed cleared vectors
    python -m scripts.sync_shopify --apply --no-vectorize

Needs DATABASE_URL and the Shopify credentials in .env. The dry run is read-only.
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from astor.catalog import sync, vectorize
from astor.catalog.embeddings import get_embedder
from astor.catalog.ingestion import _get_or_create_supplier, normalize_step
from astor.catalog.shopify_source import ShopifySource
from astor.config import settings
from astor.db.base import session_scope

_MISSING_COLUMN = (
    "supplier_offers.external_id does not exist. Apply migration 0008, or the equivalent DDL:\n"
    "  ALTER TABLE supplier_offers ADD COLUMN IF NOT EXISTS external_id varchar(64);\n"
    "  CREATE UNIQUE INDEX IF NOT EXISTS uq_offer_supplier_external_id\n"
    "    ON supplier_offers (supplier_id, external_id) WHERE external_id IS NOT NULL;"
)


def _label(item) -> str:
    return f"{item.offer.supplier_sku!r:<18} variant {item.offer.external_id}  {item.product.name[:70]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--vectorize", dest="vectorize", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="re-embed products whose text changed (default: on, with --apply)")
    ap.add_argument("--show", type=int, default=15, help="sample size per section")
    args = ap.parse_args()

    feed = normalize_step(ShopifySource().extract())
    print(f"shopify variants: {len(feed):,}")

    with session_scope() as session:
        exists = session.execute(text(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name='supplier_offers' AND column_name='external_id')")).scalar()
        if not exists:
            raise SystemExit(_MISSING_COLUMN)

        supplier = _get_or_create_supplier(
            session, settings.shopify_supplier_name,
            settings.shopify_supplier_region, settings.shopify_supplier_tier)
        existing = sync.load_existing(session, supplier)
        keyed = sum(1 for r in existing if r.external_id)
        print(f"existing offers:  {len(existing):,}  (with variant id: {keyed:,})")

        plan = sync.plan_sync(feed, existing)
        reembed = sum(1 for u in plan.updates if u.reembed)
        renamed = sum(1 for u in plan.updates
                      if u.item.offer.supplier_sku != next(
                          r.supplier_sku for r in existing if r.offer_id == u.offer_id))
        conflicts = [u for u in plan.updates if u.sku_conflict]
        print(f"\nplan")
        print(f"  update    {len(plan.updates):>7,}   (text changed -> re-embed: {reembed:,}; "
              f"sku renamed: {renamed:,}; sku rename blocked: {len(conflicts):,})")
        print(f"  insert    {len(plan.inserts):>7,}")
        print(f"  vanished  {len(plan.vanished):>7,}   -> mark unsellable")
        print(f"  collision {len(plan.collisions):>7,}   -> skipped, fix in Shopify admin")

        n = args.show
        if plan.inserts:
            print(f"\ninserts (first {n}):")
            for it in plan.inserts[:n]:
                print("  ", _label(it))
        if plan.vanished:
            print(f"\nvanished (first {n}):")
            for r in plan.vanished[:n]:
                print(f"   {r.supplier_sku!r:<18} variant {r.external_id}  {r.product_name[:70]}")
        if plan.collisions:
            print(f"\ncollisions (all):")
            for c in plan.collisions:
                print("  ", _label(c.item), "--", c.reason)
        if conflicts:
            print(f"\nsku renames blocked (all):")
            for u in conflicts:
                print("  ", _label(u.item))

        if not args.apply:
            print("\ndry run — nothing written. Re-run with --apply to commit.")
            session.rollback()
            return

        counts = sync.apply_plan(session, plan, supplier)
        print(f"\napplied: {counts}")
        if args.vectorize and reembed:
            done = vectorize.vectorize_missing(session, get_embedder())
            print(f"re-embedded: {done}")


if __name__ == "__main__":
    main()

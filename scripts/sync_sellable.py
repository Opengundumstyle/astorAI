"""Sync `products.sellable` from Shopify, without touching anything else.

WHY THIS EXISTS RATHER THAN `ingest_shopify`
    `upsert_step` can only upsert a product when BOTH brand and mpn are set.
    Shopify supplies no mpn for this catalog (0 of 16,030 variants have a
    barcode or mpn metafield), so every product falls to the `else` branch and
    is INSERTed unconditionally. Re-running the full ingest against a populated
    database therefore duplicates the entire catalog instead of updating it --
    and still would not correct `sellable` on the existing rows.

    This script keys on `supplier_offers.supplier_sku`, which is unique and IS
    populated from the Shopify variant sku, so it updates exactly the rows it
    should and creates none.

WHAT IT WRITES
    `products.sellable` only. False when the Shopify product is ARCHIVED, DRAFT,
    or unpublished to the storefront; true when it is ACTIVE and published. It
    sets the flag in both directions, so un-archiving upstream is picked up too.

Usage:
    python -m scripts.sync_sellable                 # dry run: report, write nothing
    python -m scripts.sync_sellable --apply         # commit the changes

Needs DATABASE_URL pointing at the target database and the Shopify credentials
in .env. Run the dry run first; it is read-only.
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from astor.catalog.shopify_source import ShopifySource
from astor.db.base import session_scope

_MISSING_COLUMN = (
    "products.sellable does not exist. Apply migration 0007, or the equivalent DDL:\n"
    "  ALTER TABLE products ADD COLUMN IF NOT EXISTS sellable boolean NOT NULL DEFAULT true;\n"
    "  CREATE INDEX IF NOT EXISTS ix_products_sellable ON products (sellable);"
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    items = ShopifySource().extract()
    unsellable = sorted({i.supplier_sku for i in items if not i.sellable})
    sellable = sorted({i.supplier_sku for i in items if i.sellable})
    print(f"shopify variants: {len(items):,}  "
          f"unsellable: {len(unsellable):,}  sellable: {len(sellable):,}")

    with session_scope() as session:
        exists = session.execute(text(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name='products' AND column_name='sellable')")).scalar()
        if not exists:
            raise SystemExit(_MISSING_COLUMN)

        session.execute(text("CREATE TEMP TABLE _unsell (sku text PRIMARY KEY) ON COMMIT DROP"))
        session.execute(text("CREATE TEMP TABLE _sell (sku text PRIMARY KEY) ON COMMIT DROP"))
        if unsellable:
            session.execute(text("INSERT INTO _unsell VALUES (:s) ON CONFLICT DO NOTHING"),
                            [{"s": s} for s in unsellable])
        if sellable:
            session.execute(text("INSERT INTO _sell VALUES (:s) ON CONFLICT DO NOTHING"),
                            [{"s": s} for s in sellable])

        to_false = session.scalar(text("""
            SELECT count(*) FROM products p WHERE p.sellable AND EXISTS (
                SELECT 1 FROM supplier_offers o JOIN _unsell u ON u.sku = o.supplier_sku
                WHERE o.product_id = p.id)"""))
        to_true = session.scalar(text("""
            SELECT count(*) FROM products p WHERE NOT p.sellable AND EXISTS (
                SELECT 1 FROM supplier_offers o JOIN _sell s ON s.sku = o.supplier_sku
                WHERE o.product_id = p.id)"""))
        print(f"rows to mark NOT sellable : {to_false:,}")
        print(f"rows to restore sellable  : {to_true:,}")

        if not args.apply:
            print("\ndry run — nothing written. Re-run with --apply to commit.")
            session.rollback()
            return

        session.execute(text("""
            UPDATE products p SET sellable = false
            WHERE p.sellable AND EXISTS (
                SELECT 1 FROM supplier_offers o JOIN _unsell u ON u.sku = o.supplier_sku
                WHERE o.product_id = p.id)"""))
        session.execute(text("""
            UPDATE products p SET sellable = true
            WHERE NOT p.sellable AND EXISTS (
                SELECT 1 FROM supplier_offers o JOIN _sell s ON s.sku = o.supplier_sku
                WHERE o.product_id = p.id)"""))
        session.flush()

        print("\napplied. catalog now:")
        for value, count in session.execute(text(
                "SELECT sellable, count(*) FROM products GROUP BY 1 ORDER BY 1")):
            print(f"  sellable={value!s:<5} {count:>7,}")


if __name__ == "__main__":
    main()

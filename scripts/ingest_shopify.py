"""Pull the Shopify house catalog, ingest into Postgres, vectorize, match.

Env (in .env, never on the command line):
    SHOPIFY_SHOP_DOMAIN=astor           # or astor.myshopify.com
    SHOPIFY_ADMIN_TOKEN=shpat_xxx       # Admin API access token (secret)
    SHOPIFY_API_VERSION=2026-01         # a currently-supported version
    # optional:
    SHOPIFY_MPN_METAFIELD=custom.mpn
    SHOPIFY_SPECS_METAFIELD_NAMESPACE=specs
    EMBEDDINGS_PROVIDER=voyage          # dev|voyage|openai (dev = not semantic)

Usage:
    python -m scripts.ingest_shopify --dry-run --limit 20     # inspect mapping, no DB
    python -m scripts.ingest_shopify                          # full pull + vectorize
    python -m scripts.ingest_shopify --no-match               # ingest + vectorize only
"""
from __future__ import annotations

import argparse
import json

from astor.catalog import matcher, vectorize
from astor.catalog.embeddings import get_embedder
from astor.catalog.ingestion import ingest_extracted
from astor.catalog.shopify_source import ShopifySource
from astor.config import settings
from astor.db.base import session_scope


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap variants (testing)")
    ap.add_argument("--dry-run", action="store_true", help="pull + map, print, no DB writes")
    ap.add_argument(
        "--match", dest="match", default=True, action=argparse.BooleanOptionalAction,
        help="run equivalence matcher after vectorizing (default: on)",
    )
    ap.add_argument(
        "--vectorize", dest="vectorize", default=True, action=argparse.BooleanOptionalAction,
        help="populate embeddings after ingest (default: on)",
    )
    args = ap.parse_args()

    source = ShopifySource()
    items = source.extract(limit=args.limit)
    print(f"extracted variants: {len(items)}")

    if args.dry_run:
        for it in items[:50]:
            print(json.dumps(it.model_dump(), ensure_ascii=False))
        n_fallback = sum(1 for it in items if it.specs.get("_cost_basis") == "sell_price_fallback")
        print(f"\n[dry-run] no DB writes. cost=sell_price_fallback on {n_fallback}/{len(items)} "
              f"variants (no 'Cost per item' set in Shopify).")
        return

    with session_scope() as session:
        result = ingest_extracted(
            session, items,
            supplier_name=settings.shopify_supplier_name,
            region=settings.shopify_supplier_region,
            tier=settings.shopify_supplier_tier,
        )
        print(f"products={result.products_upserted} offers={result.offers_upserted}")

        if args.vectorize:
            embedder = get_embedder()
            embedded = vectorize.vectorize_missing(session, embedder)
            print(f"embedded={embedded}")
            if args.match:
                total = sum(len(matcher.match_product(session, pid, embedder))
                            for pid in result.products_to_match)
                print(f"equivalences_written={total}")

    if settings.embeddings_provider.lower() == "dev":
        print("\nNOTE: EMBEDDINGS_PROVIDER=dev produces non-semantic vectors. "
              "Set voyage/openai before trusting match output.")


if __name__ == "__main__":
    main()

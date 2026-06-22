"""Ingest one supplier catalog, then run the equivalence matcher over new products.

Usage:
    python -m scripts.ingest_catalog --file data/supplier_a.csv --supplier "Supplier A" --region CN
"""
from __future__ import annotations

import argparse
from pathlib import Path

from astor.catalog import matcher
from astor.catalog.ingestion import ingest
from astor.catalog.embeddings import get_embedder
from astor.db.base import session_scope


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, type=Path)
    ap.add_argument("--supplier", required=True)
    ap.add_argument("--region", default="CN")
    ap.add_argument("--tier", default="public")
    ap.add_argument("--match/--no-match", dest="match", default=True, action=argparse.BooleanOptionalAction)
    args = ap.parse_args()

    with session_scope() as session:
        result = ingest(session, args.file, args.supplier, args.region, args.tier)
        print(f"extracted={result.extracted} products={result.products_upserted} offers={result.offers_upserted}")
        if args.match:
            embedder = get_embedder()
            total = 0
            for pid in result.products_to_match:
                total += len(matcher.match_product(session, pid, embedder))
            print(f"equivalences_written={total}")


if __name__ == "__main__":
    main()

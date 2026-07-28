"""Re-embed all products with the configured real provider + stamp provenance.

Usage:
    # ALWAYS snapshot first (see plan); then:
    python -m scripts.backfill_embeddings            # only re-embed stale rows
    python -m scripts.backfill_embeddings --all      # force re-embed every row

Refuses to run under the DevEmbedder — that would re-stamp garbage as real.
"""
from __future__ import annotations

import argparse
import logging

from astor.catalog import backfill
from astor.catalog.embeddings import get_embedder
from astor.config import settings
from astor.db.base import session_scope


def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", dest="all_rows", action="store_true",
                    help="re-embed every product, not just stale ones")
    ap.add_argument("--batch-size", type=int, default=64,
                    help="products per Voyage request (free-tier 10K TPM headroom)")
    ap.add_argument("--sleep", type=float, default=22.0,
                    help="seconds between batches (free-tier 3 RPM / 10K TPM pacing)")
    args = ap.parse_args()

    embedder = get_embedder()
    if type(embedder).__name__ == "DevEmbedder":
        raise SystemExit(
            f"Refusing to backfill with DevEmbedder (provider={settings.embeddings_provider}). "
            "Set EMBEDDINGS_PROVIDER=voyage and VOYAGE_API_KEY in .env."
        )

    with session_scope() as session:
        stats = backfill.backfill_embeddings(
            session, embedder, only_stale=not args.all_rows,
            batch_size=args.batch_size, sleep_between_batches=args.sleep,
        )
    print(f"total={stats.total} embedded={stats.embedded} skipped={stats.skipped} "
          f"model={backfill.EMBEDDING_MODEL}")


if __name__ == "__main__":
    main()

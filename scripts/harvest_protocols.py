"""Harvest top-review protocols.io protocols per curated category.

SAFETY: live network requires BOTH --live AND settings.protocols_io_licensed=1
(the double lock). Default is --dry-run, which resolves categories and prints the
plan without any network. The ≤1,000 cap matches the protocols.io approval.

Usage:
    python -m scripts.harvest_protocols                       # dry-run plan
    python -m scripts.harvest_protocols --live \\
        --serving-basis "commercial-licence:pio-2026" \\
        --n-per-category 50 --cap 200                          # bounded sample sweep
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from astor.config import settings
from astor.protocols import harvest
from astor.protocols.categories import load_categories
from astor.protocols.raw_store import RawStore
from astor.protocols.sources import ProtocolsIoSource


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--categories", default="docs/curation/categories.csv")
    ap.add_argument("--category", default=None,
                    help="run only this category_id (balanced per-category coverage; "
                         "combine with --cap = --n-per-category and a per-category --manifest-name)")
    ap.add_argument("--n-per-category", type=int, default=100)
    ap.add_argument("--cap", type=int, default=1000)
    ap.add_argument("--serving-basis", default=None)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--out", default="data/raw/protocols_io")
    ap.add_argument("--manifest-name", default="manifest.json",
                    help="manifest filename under --out; give each per-category run its own")
    ap.add_argument("--live", action="store_true", help="enable network (needs licence lock)")
    args = ap.parse_args()

    seeds = load_categories(args.categories)
    if args.category:
        seeds = [s for s in seeds if s.category_id == args.category]
        if not seeds:
            raise SystemExit(
                f"--category {args.category!r} not found in {args.categories}."
            )
    print(f"categories: {[s.category_id for s in seeds]}")
    for s in seeds:
        print(f"  {s.category_id}: {s.queries}")

    if not args.live:
        print("\n[dry-run] no network. Re-run with --live once licensed.")
        return

    if not settings.protocols_io_licensed:
        raise SystemExit(
            "--live requires PROTOCOLS_IO_LICENSED=1 (confirmed licence). Aborting."
        )

    store = RawStore(args.out)
    stamp = datetime.now(timezone.utc).isoformat()
    servable, link_out, manifest = harvest.run_harvest(
        seeds, source=ProtocolsIoSource(), store=store,
        n_per_category=args.n_per_category, cap=args.cap,
        serving_basis=args.serving_basis, allow_network=True,
        sleep_between=args.sleep, stamp=stamp, manifest_name=args.manifest_name,
    )
    print(
        f"\nfetched={manifest.fetched} cached={manifest.skipped_cached} "
        f"servable={manifest.servable} link_out={manifest.link_out} "
        f"errors={manifest.errors} basis={manifest.serving_basis}"
    )
    print(f"payloads written under {args.out}/details/")
    print(f"manifest written to {Path(args.out) / args.manifest_name}")


if __name__ == "__main__":
    main()

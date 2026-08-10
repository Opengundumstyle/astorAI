"""Load a harvested protocols.io corpus from disk into the DB.

Reads the raw JSON cache written by the harvester (`data/raw/protocols_io/`),
re-maps it offline (recovering peer_reviewed from saved search pages), and upserts
into the `protocols` table. Idempotent — safe to re-run.

SERVING: protocols.io records carry no licence (→ UNKNOWN), so without a
--serving-basis they upsert as link-out (attribution only, content stripped). Pass
the commercial-licence reference to serve their content, which stamps each served
row with that basis for audit. This mirrors the harvester's serving_basis and must
match the authorization you actually hold.

Usage:
    python -m scripts.load_protocols --dry-run                       # map + count, no DB
    python -m scripts.load_protocols \\
        --serving-basis "commercial-licence:pio-approval-2026-08"    # load + serve content
"""
from __future__ import annotations

import argparse
from collections import Counter

from astor.db.base import session_scope
from astor.protocols import extraction, harvest, persistence
from astor.protocols.raw_store import RawStore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="data/raw/protocols_io",
                    help="raw store root written by the harvester")
    ap.add_argument("--serving-basis", default=None,
                    help="commercial-licence reference; without it protocols.io content "
                         "stays link-out (UNKNOWN licence fails closed)")
    ap.add_argument("--extract-materials", action="store_true",
                    help="run the material extractor (LLM for free-text lists — needs "
                         "ANTHROPIC_API_KEY and costs one call per free-text protocol) "
                         "before persisting, so stored materials are procurement-ready")
    ap.add_argument("--dry-run", action="store_true",
                    help="map + report servable/link-out counts, no DB writes")
    args = ap.parse_args()

    store = RawStore(args.corpus)
    raws = harvest.map_from_store(store)
    print(f"mapped {len(raws)} protocols from {args.corpus}")
    lic = Counter(p.license.value for p in raws)
    print("  licences:", dict(lic))

    if args.extract_materials:
        raws, estats = extraction.enrich_materials(raws)
        print(f"  materials extracted: in={estats.materials_in} → out={estats.materials_out} "
              f"(purchasable), llm_calls={estats.llm_calls}, errors={estats.errors}")

    if args.dry_run:
        # Preview the gate outcome without writing.
        allow = persistence.DEFAULT_SERVE_LICENSES | (
            {persistence.License.UNKNOWN} if args.serving_basis else frozenset())
        servable = sum(1 for p in raws if p.license in allow)
        print(f"\n[dry-run] no DB writes. serving_basis={args.serving_basis!r} → "
              f"servable={servable} link_out={len(raws) - servable}")
        return

    with session_scope() as session:
        result = persistence.upsert_protocols(
            session, raws, serving_basis=args.serving_basis)
    print(
        f"\ncreated={result.created} updated={result.updated} "
        f"servable={result.servable} link_out={result.link_out_only} "
        f"deduped={result.deduped_in_batch} basis={args.serving_basis!r}"
    )


if __name__ == "__main__":
    main()

"""Build the Plane 2 protocol corpus from Europe PMC's Open Access subset.

WHY EUROPE PMC AND NOT PROTOCOLS.IO
    protocols.io's ToS forbids the ingestion itself — 4.A(vi) (storing site data
    "to make or populate a database of any kind whatsoever"), 4.A(xi) (systematic
    automated downloading to index content), 4.A(i) (commercial use subject to a
    separate fee). Its content is CC-BY, but the CONTRACT, not the copyright
    licence, is the blocker, and internal-only use does not cure it: the
    prohibited act is the pull, not the publication. Europe PMC's OA subset is
    explicitly provided for text mining, so this lane is clean today.
    See docs/protocol-sourcing-handoff.md and contracts/astor-protocol-ingest.v1.yaml (PI-6).

Usage:
    python -m scripts.ingest_protocols --dry-run             # search + map + gate, no DB
    python -m scripts.ingest_protocols                       # full run, writes to Postgres
    python -m scripts.ingest_protocols --limit 10 --top 20   # small demo sweep
    python -m scripts.ingest_protocols --query "CRISPR knock-in protocol"

Re-runs are safe: identity is DOI, else (source, source_id). Running this weekly
is the sync job.
"""
from __future__ import annotations

import argparse
import logging

from astor.db.base import session_scope
from astor.protocols import ingestion
from astor.protocols.persistence import upsert_protocols


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", action="append", dest="queries",
                    help="override the default technique sweep (repeatable)")
    ap.add_argument("--limit", type=int, default=25,
                    help="max records per query (default: 25)")
    ap.add_argument("--top", type=int, default=15,
                    help="how many ranked results to print (default: 15)")
    ap.add_argument("--source", default="europepmc",
                    help="source adapter (default: europepmc; gated sources refuse to sweep)")
    ap.add_argument("--dry-run", action="store_true",
                    help="search, map, gate and rank — print only, no DB writes")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    queries = tuple(args.queries) if args.queries else ingestion.DEFAULT_QUERIES
    result = ingestion.run_from_search(
        queries, source_name=args.source, limit_per_query=args.limit
    )

    print(f"\n  source            {result.source}")
    print(f"  queries           {len(queries)}")
    print(f"  fetched           {result.fetched}")
    print(f"  servable          {result.servable}   (content may be indexed and served)")
    print(f"  link-out only     {result.link_out_only}   (attribution kept, content withheld)")

    if result.ranked:
        print(f"\n  top {min(args.top, len(result.ranked))} by citation count:")
        for i, p in enumerate(result.ranked[: args.top], 1):
            cites = p.review.citations
            cites = f"{cites:>5}" if cites is not None else "    ?"
            print(f"   {i:>3}. [{cites} cites] {p.license.value:<6} {p.title[:78]}")
            print(f"        {p.source_uri}")

    if args.dry_run:
        print("\n  dry run — nothing written.\n")
        return

    # Both lists are persisted: the licence gate is re-applied per row at write
    # time, so link-out records land with attribution and empty content (PI-5).
    with session_scope() as session:
        written = upsert_protocols(session, [*result.ranked, *result.link_out])

    print(f"\n  created           {written.created}")
    print(f"  updated           {written.updated}")
    print(f"  deduped in batch  {written.deduped_in_batch}")
    print(f"\n  corpus now holds {written.written} rows from this run.\n")


if __name__ == "__main__":
    main()

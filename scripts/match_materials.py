"""Match servable protocols' materials to Astor product SKUs.

Usage:
    python -m scripts.match_materials --dry-run          # counts, no writes
    python -m scripts.match_materials --limit 50         # first 50 protocols
    python -m scripts.match_materials                    # all servable protocols
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from astor.catalog.embeddings import get_embedder
from astor.db.base import session_scope
from astor.db.models import Protocol
from astor.protocols import material_matcher


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap protocols processed")
    ap.add_argument("--dry-run", action="store_true", help="match + count, no DB writes")
    args = ap.parse_args()

    embedder = get_embedder()
    protocols_seen = links = exact = substitute = with_link = 0

    with session_scope() as session:
        stmt = select(Protocol).where(Protocol.servable.is_(True))
        if args.limit:
            stmt = stmt.limit(args.limit)
        for proto in session.scalars(stmt):
            if not proto.materials:
                continue
            protocols_seen += 1
            matches = material_matcher.match_protocol_materials(session, proto, embedder)
            if matches:
                with_link += 1
                links += len(matches)
                exact += sum(1 for m in matches if m.kind == "exact")
                substitute += sum(1 for m in matches if m.kind == "substitute")
                if not args.dry_run:
                    material_matcher.persist_links(session, proto.id, matches)
        if args.dry_run:
            session.rollback()

    print(f"protocols={protocols_seen} with_link={with_link} links={links} "
          f"exact={exact} substitute={substitute} "
          f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}")


if __name__ == "__main__":
    main()

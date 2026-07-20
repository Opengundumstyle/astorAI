"""Fetch ONE protocol from protocols.io and verify our field mapping against it.

WHAT THIS IS FOR
    `ProtocolsIoSource.to_raw` was written against the developer docs, not against
    a real response, so its field paths are guesses ("provisional -- verify vs.
    live payload"). One authenticated fetch settles them. This script reports
    which guesses resolved, which came back empty, and which payload keys we are
    not reading at all.

SCOPE -- deliberately narrow
    One protocol, one request, nothing written to the database. This is a
    connectivity and mapping check, not ingestion. A single documented fetch is
    what a developer token is issued for; it is NOT the systematic download or
    database population that the ToS restricts (4.A.vi/vii/xi). Keep it that way:
    do not loop this over a list of ids.

Setup:
    PROTOCOLS_IO_TOKEN=...   in .env   (protocols.io/developers)

Usage:
    python -m scripts.probe_protocols_io <protocol-id-or-uri>
    python -m scripts.probe_protocols_io q26g7yb9klwz --json    # dump raw payload
"""
from __future__ import annotations

import argparse
import json

from astor.config import settings
from astor.protocols.sources import ProtocolsIoSource

# Payload keys we knowingly ignore -- listing them keeps the "unread keys" report
# focused on genuine gaps rather than noise.
_EXPECTED_UNREAD = {
    "created_on", "changed_on", "published_on", "creator", "image", "guidelines",
    "before_start", "warning", "link", "type_id", "vendor", "stats_id",
}


def _report_mapping(payload: dict, raw) -> None:
    print("\n── mapping check " + "─" * 60)

    checks = [
        ("source_id", raw.source_id),
        ("title", raw.title),
        ("doi", raw.doi),
        ("version", raw.version),
        ("license", raw.license.value),
        ("authors", ", ".join(raw.authors) or None),
        ("source_uri", raw.source_uri),
    ]
    for name, value in checks:
        mark = "ok  " if value else "MISS"
        print(f"  [{mark}] {name:<12} {value if value else '(empty -- field path likely wrong)'}")

    print(f"  [{'ok  ' if raw.steps else 'MISS'}] {'steps':<12} {len(raw.steps)} mapped")
    print(f"  [{'ok  ' if raw.materials else 'MISS'}] {'materials':<12} {len(raw.materials)} mapped")

    print("\n  review signal (which stats keys actually exist):")
    got_any = False
    for field, value in raw.review.model_dump().items():
        if value is not None:
            print(f"    ok   {field} = {value}")
            got_any = True
    if not got_any:
        print("    MISS  no stats resolved -- inspect the payload's stats object")
    print(f"    rank_score = {raw.review.rank_score:.3f}")

    unread = sorted(set(payload) - _EXPECTED_UNREAD - {
        "id", "uri", "url", "title", "authors", "doi", "version_id",
        "license", "materials", "steps", "stats",
    })
    if unread:
        print("\n  payload keys we do NOT read (candidates for the mapping):")
        for k in unread:
            preview = repr(payload[k])[:60]
            print(f"    - {k:<24} {preview}")

    if raw.steps:
        print("\n  first mapped step:")
        print(f"    {raw.steps[0].number}. {raw.steps[0].text[:100]}")
    if raw.materials:
        m = raw.materials[0]
        print("\n  first mapped material:")
        print(f"    name={m.name!r} amount={m.amount!r} vendor={m.vendor!r} catalog_no={m.catalog_no!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("identifier", help="protocols.io protocol id or uri slug")
    ap.add_argument("--json", action="store_true", help="also dump the raw payload")
    args = ap.parse_args()

    if not settings.protocols_io_token:
        raise SystemExit(
            "PROTOCOLS_IO_TOKEN is not set.\n"
            "  1. Register at https://www.protocols.io/developers\n"
            "  2. Create a client access token\n"
            "  3. Put PROTOCOLS_IO_TOKEN=<token> in .env\n"
            "The API rejects unauthenticated calls with status_code 1218."
        )

    source = ProtocolsIoSource()
    # allow_network=True is the explicit, single-fetch opt-in. The gate stays on
    # by default so nothing can sweep by accident.
    raw = source.fetch_one(args.identifier, allow_network=True)

    print(f"\n  fetched   {raw.source_id}  ({raw.source_uri})")
    print(f"  title     {raw.title}")
    print(f"  licence   {raw.license.value}  (servable={raw.license.redistributable})")

    _report_mapping(raw.raw, raw)

    if args.json:
        print("\n── raw payload " + "─" * 62)
        print(json.dumps(raw.raw, indent=2)[:4000])

    print("\n  nothing was written to the database.\n")


if __name__ == "__main__":
    main()

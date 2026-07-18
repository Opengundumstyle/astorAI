"""Protocol source adapters — the boundary that keeps source schemas out of the
pipeline (mirrors `catalog.extraction`). A source maps its native payload onto
`RawProtocol` and nothing else in the pipeline knows the source exists.

protocols.io is the v1 source. The payload→RawProtocol MAPPING is implemented and
unit-testable offline (feed it a saved JSON payload); the live network FETCH is
deliberately gated behind an explicit opt-in + token, because pulling content
into our own index is the step with the licence/ToS exposure (§10, §14 #1).
"""
from __future__ import annotations

from typing import Protocol

from astor.config import settings
from astor.protocols.schemas import (
    License,
    RawMaterial,
    RawProtocol,
    RawStep,
    ReviewSignal,
)


class ProtocolSource(Protocol):
    def fetch_one(self, identifier: str) -> RawProtocol: ...
    def to_raw(self, payload: dict) -> RawProtocol: ...


# protocols.io licence strings → our neutral enum. Extend as real values are seen.
_PIO_LICENSE = {
    "cc0": License.CC0,
    "cc-by": License.CC_BY,
    "ccby": License.CC_BY,
    "cc by": License.CC_BY,
    "cc-by-nc": License.CC_BY_NC,
    "ccbync": License.CC_BY_NC,
    "cc by-nc": License.CC_BY_NC,
    "all rights reserved": License.ALL_RIGHTS_RESERVED,
    "arr": License.ALL_RIGHTS_RESERVED,
}


def _license_of(raw: str | None) -> License:
    if not raw:
        return License.UNKNOWN
    return _PIO_LICENSE.get(raw.strip().lower(), License.UNKNOWN)


class ProtocolsIoSource:
    """Adapter for protocols.io (https://www.protocols.io/developers).

    `to_raw` is pure and offline — the mapping is where field names are pinned
    and it is what the tests exercise. `fetch_one` is the only networked method
    and refuses to run without both a token AND an explicit allow-network flag,
    so an accidental import can never bulk-pull licensed content.
    """

    BASE = "https://www.protocols.io/api/v4"

    def to_raw(self, payload: dict) -> RawProtocol:
        """Map one protocols.io protocol object → RawProtocol. Field paths are
        provisional (marked below) until confirmed against live payloads."""
        stats = payload.get("stats") or {}
        materials = [
            RawMaterial(
                name=m.get("name", "").strip(),
                amount=m.get("amount") or m.get("quantity"),
                vendor=(m.get("vendor") or {}).get("name") if isinstance(m.get("vendor"), dict) else m.get("vendor"),
                catalog_no=m.get("catalog") or m.get("sku"),
            )
            for m in (payload.get("materials") or [])
            if (m.get("name") or "").strip()
        ]
        steps = [
            RawStep(number=i + 1, text=(s.get("title") or s.get("description") or "").strip())
            for i, s in enumerate(payload.get("steps") or [])
            if (s.get("title") or s.get("description"))
        ]
        review = ReviewSignal(
            # provisional field paths — verify vs. live payload:
            rating=stats.get("rating") or stats.get("average_rating"),
            ratings_count=stats.get("ratings") or stats.get("number_of_ratings"),
            views=stats.get("number_of_views") or stats.get("views"),
            bookmarks=stats.get("number_of_bookmarks") or stats.get("bookmarks"),
            forks=stats.get("number_of_forks") or stats.get("forks"),
            comments=stats.get("number_of_comments") or stats.get("comments"),
        )
        authors = [
            a.get("name", "").strip()
            for a in (payload.get("authors") or [])
            if isinstance(a, dict) and a.get("name")
        ]
        return RawProtocol(
            source="protocols.io",
            source_id=str(payload.get("id") or payload.get("uri") or ""),
            source_uri=payload.get("url") or f"https://www.protocols.io/view/{payload.get('uri', '')}",
            title=(payload.get("title") or "").strip(),
            authors=authors,
            doi=payload.get("doi"),
            version=str(payload.get("version_id")) if payload.get("version_id") else None,
            license=_license_of((payload.get("license") or {}).get("title") if isinstance(payload.get("license"), dict) else payload.get("license")),
            steps=steps,
            materials=materials,
            review=review,
            raw=payload,
        )

    def fetch_one(self, identifier: str, *, allow_network: bool = False) -> RawProtocol:
        """Live pull of a single protocol. GATED: this is the ingestion step that
        creates licence/ToS exposure, so it fails closed. Clear the ToS (§14 #1),
        set PROTOCOLS_IO_TOKEN, and pass allow_network=True to enable."""
        if not allow_network:
            raise RuntimeError(
                "Network fetch is gated. Pulling protocols.io content into the index "
                "carries licence/ToS exposure (§10, §14 #1). Pass allow_network=True "
                "only after ToS confirmation; use to_raw(payload) for offline mapping."
            )
        if not settings.protocols_io_token:
            raise RuntimeError("ProtocolsIoSource.fetch_one needs PROTOCOLS_IO_TOKEN.")
        import httpx

        resp = httpx.get(
            f"{self.BASE}/protocols/{identifier}",
            headers={"Authorization": f"Bearer {settings.protocols_io_token}"},
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        return self.to_raw(body.get("protocol") or body.get("payload") or body)


def for_source(name: str = "protocols.io") -> ProtocolSource:
    if name in ("protocols.io", "protocols_io", "pio"):
        return ProtocolsIoSource()
    raise ValueError(f"No protocol source adapter for {name!r} (v1 is protocols.io only).")

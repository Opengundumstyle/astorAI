"""Protocol source adapters — the boundary that keeps source schemas out of the
pipeline (mirrors `catalog.extraction`). A source maps its native payload onto
`RawProtocol` and nothing else in the pipeline knows the source exists.

protocols.io is the v1 source. The payload→RawProtocol MAPPING is implemented and
unit-testable offline (feed it a saved JSON payload); the live network FETCH is
deliberately gated behind an explicit opt-in + token, because pulling content
into our own index is the step with the licence/ToS exposure (§10, §14 #1).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from astor.config import settings
from astor.protocols.schemas import (
    License,
    RawMaterial,
    RawProtocol,
    RawStep,
    ReviewSignal,
)

log = logging.getLogger(__name__)


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


def _draftjs_text(value) -> str:
    """protocols.io stores step and materials prose as Draft.js state, JSON-encoded
    into a string — NOT as plain text. Flatten it to text.

    Shape: {"blocks": [{"text": "...", "type": "unstyled"|"unordered-list-item"}]}.
    Anything that is not Draft.js (plain string, HTML) is returned as-is so the
    caller gets the best available text rather than an empty string.
    """
    if not value:
        return ""
    if isinstance(value, dict):
        blocks = value.get("blocks")
    else:
        if not isinstance(value, str):
            return ""
        stripped = value.strip()
        if not stripped.startswith("{"):
            return stripped                       # plain text or HTML
        try:
            blocks = json.loads(stripped).get("blocks")
        except (ValueError, AttributeError):
            return stripped
    if not blocks:
        return ""
    return "\n".join(
        b["text"].strip() for b in blocks
        if isinstance(b, dict) and (b.get("text") or "").strip()
    ).strip()


def _strip_html(value: str | None) -> str:
    return re.sub(r"<[^>]+>", " ", value or "").replace("&amp;", "&").strip()


def _normalize_doi(value: str | None) -> str | None:
    """protocols.io returns DOIs as resolver URLs with a version suffix, e.g.
    'dx.doi.org/10.17504/protocols.io.261ge87oog47/v3'.

    Both parts break identity. The host prefix makes the string unequal to the
    same DOI from any other source, and the '/v3' suffix makes every VERSION of a
    protocol look like a different work — which would defeat DOI dedupe exactly
    where it matters, since re-ingesting an updated protocol is the common case.
    Reduce to the bare DOI; the version is carried separately in `version`.
    """
    if not value:
        return None
    doi = value.strip()
    doi = re.sub(r"^https?://", "", doi, flags=re.I)
    doi = re.sub(r"^(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"/v\d+$", "", doi)
    return doi or None


def _pio_author_name(a: dict) -> str:
    """v4 authors are {first_name, last_name, affiliation, username, ...} —
    there is no `name` key."""
    if not isinstance(a, dict):
        return ""
    full = " ".join(p for p in (a.get("first_name"), a.get("last_name")) if p)
    return (full or a.get("name") or a.get("username") or "").strip()


def _as_int(value) -> int | None:
    """`stats.number_of_forks` is an OBJECT ({'private': n, 'public': n}), while
    every sibling counter is a plain int. Collapse the object to a total so one
    inconsistent field cannot fail the whole mapping."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        nums = [v for v in value.values() if isinstance(v, int) and not isinstance(v, bool)]
        return sum(nums) if nums else None
    return None


class ProtocolsIoSource:
    """Adapter for protocols.io (https://www.protocols.io/developers).

    `to_raw` is pure and offline — the mapping is where field names are pinned
    and it is what the tests exercise. `fetch_one` is the only networked method
    and refuses to run without both a token AND an explicit allow-network flag,
    so an accidental import can never bulk-pull licensed content.
    """

    # VERSIONS ARE SPLIT, per apidoc.protocols.io (titled "protocols.io API v3"):
    #   Get Protocol  -> GET /api/v4/protocols/{id}     (documented as v4)
    #   List/search   -> GET /api/v3/protocols          (documented as v3)
    # This is not a migration in progress to pick one side of — the docs specify
    # each endpoint at its own version, and the v4 listing route actively rejects
    # the documented params ("key is required" when key IS supplied). Use each at
    # the version its own docs give.
    BASE = "https://www.protocols.io/api/v4"        # single-protocol fetch
    LIST_BASE = "https://www.protocols.io/api/v3"   # listing / search

    def to_raw(self, payload: dict, *, list_item: dict | None = None) -> RawProtocol:
        """Map one protocols.io protocol object → RawProtocol.

        `list_item` is the corresponding record from the v3 LIST response, when
        one is available. It exists because the two endpoints disagree about what
        they return: `peer_reviewed` is populated in the v3 list and is ALWAYS
        null in the v4 get-protocol payload (verified across 6 protocols). Since
        peer review is our best quality signal, a fetch-only path would silently
        lose it. Pass the list item through and it survives.

        Field paths VERIFIED 2026-07-19 against a live v4 response (protocol
        321062). The surprises, all of which the docs did not state:
          - step prose is Draft.js JSON in `step`, not a `title`/`description`;
          - `materials` is usually [] with the real list in `materials_text`,
            also Draft.js, as unstructured lines rather than typed fields;
          - authors are {first_name, last_name}, with no `name` key;
          - `stats.number_of_forks` is an object, not an int;
          - `license` was null — see the licence note below.
        """
        stats = payload.get("stats") or {}

        # Structured materials when present; otherwise fall back to the Draft.js
        # free-text block, one material per line. That fallback yields names only
        # — amount/vendor/catalog_no are NOT recoverable from it, which is why the
        # materials→SKU transform still needs the LLM extractor.
        materials = [
            RawMaterial(
                name=(m.get("name") or "").strip(),
                amount=m.get("amount") or m.get("quantity"),
                vendor=(m.get("vendor") or {}).get("name")
                if isinstance(m.get("vendor"), dict) else m.get("vendor"),
                catalog_no=m.get("catalog") or m.get("sku"),
            )
            for m in (payload.get("materials") or [])
            if isinstance(m, dict) and (m.get("name") or "").strip()
        ]
        if not materials:
            materials = [
                RawMaterial(name=line.strip())
                for line in _draftjs_text(payload.get("materials_text")).splitlines()
                if line.strip()
            ]

        steps = []
        for i, s in enumerate(payload.get("steps") or []):
            if not isinstance(s, dict):
                continue
            text = _draftjs_text(s.get("step")) or _strip_html(s.get("section"))
            if text:
                steps.append(RawStep(number=i + 1, text=text))

        peer_reviewed = payload.get("peer_reviewed")
        if peer_reviewed is None and list_item:
            peer_reviewed = list_item.get("peer_reviewed")
        review = ReviewSignal(
            # rating/ratings_count intentionally absent: v4 exposes no star rating.
            # `peer_reviewed` is the real quality signal the API does expose — it is
            # also a documented list filter (peer_reviewed=1).
            peer_reviewed=bool(peer_reviewed) if peer_reviewed is not None else None,
            views=_as_int(stats.get("number_of_views")),
            votes=_as_int(stats.get("number_of_votes")),
            bookmarks=_as_int(stats.get("number_of_bookmarks")),
            forks=_as_int(stats.get("number_of_forks")),
            comments=_as_int(stats.get("number_of_protocol_comments"))
            or _as_int(stats.get("number_of_comments")),
        )

        authors = [n for n in (_pio_author_name(a) for a in (payload.get("authors") or [])) if n]
        if not authors:
            creator = _pio_author_name(payload.get("creator") or {})
            authors = [creator] if creator else []

        # LICENCE: the protocols.io API HAS NO LICENCE CONCEPT. apidoc.protocols.io
        # contains zero occurrences of licence/copyright/Creative Commons, and the
        # live payload returned null. So this is not a gap in one record — the API
        # cannot tell us what any protocol is licensed under, which means automated
        # per-record licence gating is impossible from this source alone.
        # Everything therefore fails closed to UNKNOWN → link-out only (PI-3).
        # Do NOT infer a licence from `public: true`: visibility is not a grant.
        lic = payload.get("license")
        license_str = lic.get("title") if isinstance(lic, dict) else lic

        return RawProtocol(
            source="protocols.io",
            source_id=str(payload.get("id") or payload.get("uri") or ""),
            source_uri=payload.get("url") or f"https://www.protocols.io/view/{payload.get('uri', '')}",
            title=_strip_html(payload.get("title")),
            authors=authors,
            doi=_normalize_doi(payload.get("doi") or payload.get("reserved_doi")),
            version=str(payload.get("version_id")) if payload.get("version_id") is not None else None,
            license=_license_of(license_str),
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


# Europe PMC reports licences lowercase and unpunctuated ("cc by-nc-sa"). Map the
# observed spellings; anything unrecognised falls to UNKNOWN and therefore link-out.
_EPMC_LICENSE = {
    "cc0": License.CC0,
    "cc by": License.CC_BY,
    "cc-by": License.CC_BY,
    "cc by-sa": License.CC_BY_SA,
    "cc by-nc": License.CC_BY_NC,
    "cc by-nc-sa": License.CC_BY_NC_SA,
    "cc by-nc-nd": License.CC_BY_NC_ND,
}


class EuropePmcSource:
    """Adapter for the Europe PMC Open Access subset.

    This is the FREE INGEST LANE (handoff §3, §5.4). Unlike protocols.io, the OA
    subset is explicitly provided for text mining via REST/FTP with no contract
    forbidding systematic download — so `search`/`fetch_one` here are NOT gated.
    The licence gate still applies downstream: we query for CC0/CC-BY, but we
    re-derive the licence per record from the payload and let `license_gate`
    enforce it, rather than trusting the query filter to have been correct.

    SCOPE: Europe PMC returns ARTICLES, not step-structured protocols. `to_raw`
    fills attribution, licence and citation count; `steps`/`materials` come back
    EMPTY by design. Turning an OA methods section into steps is the LLM
    extraction pass (handoff §5.3, mirroring `catalog/extraction.py`) and is a
    separate stage — this adapter deliberately does not fake it.
    """

    BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    # Only ask for what we may actually serve. Defence in depth, not the gate itself.
    PERMISSIVE_QUERY = '(OPEN_ACCESS:Y) AND (LICENSE:"cc by" OR LICENSE:"cc0")'

    # Server-side "most-cited first" (build notes step 3). Without this the API
    # returns relevance/recency order and every result carries 0 citations.
    SORT_BY_CITATIONS = "CITED desc"

    def to_raw(self, payload: dict) -> RawProtocol:
        """Map one Europe PMC result object → RawProtocol. Pure and offline."""
        authors = [
            a.get("fullName", "").strip()
            for a in ((payload.get("authorList") or {}).get("author") or [])
            if isinstance(a, dict) and a.get("fullName")
        ]
        if not authors and payload.get("authorString"):
            authors = [
                part.strip()
                for part in payload["authorString"].split(",")
                if part.strip()
            ]

        pmcid = payload.get("pmcid") or ""
        ext_id = str(payload.get("id") or pmcid or payload.get("pmid") or "")
        doi = payload.get("doi")
        source_uri = (
            f"https://europepmc.org/article/{(payload.get('source') or 'MED').upper()}/{ext_id}"
            if ext_id
            else (f"https://doi.org/{doi}" if doi else "")
        )

        cited = payload.get("citedByCount")
        return RawProtocol(
            source="europepmc",
            source_id=ext_id,
            source_uri=source_uri,
            title=(payload.get("title") or "").strip().rstrip("."),
            authors=authors,
            doi=doi,
            version=None,
            license=_EPMC_LICENSE.get(
                (payload.get("license") or "").strip().lower(), License.UNKNOWN
            ),
            steps=[],       # see SCOPE above — extraction is a separate stage
            materials=[],
            review=ReviewSignal(citations=int(cited) if cited is not None else None),
            raw=payload,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 50,
        page_size: int = 25,
        permissive_only: bool = True,
        sort: str | None = SORT_BY_CITATIONS,
    ) -> list[RawProtocol]:
        """Cursor-paged search → RawProtocol list.

        Paged rather than one-shot (build notes: 'batch, don't one-shot'), and
        capped by `limit` so a broad query cannot walk the whole corpus by
        accident. `page_size` is clamped to Europe PMC's 1000 maximum; the
        default stays small to keep the demo polite.

        `sort` defaults to citation count DESCENDING, which matters more than it
        looks: Europe PMC's default ordering is relevance/recency, so an unsorted
        sweep returns brand-new papers with zero citations and the 'most-cited'
        selection policy silently does nothing. Sorting server-side means the
        `limit` cap takes the top of the citation distribution rather than an
        arbitrary slice of it. Pass sort=None for the raw relevance ordering.
        """
        import httpx

        full_query = f"({query}) AND {self.PERMISSIVE_QUERY}" if permissive_only else query
        page_size = max(1, min(page_size, 1000, limit))

        out: list[RawProtocol] = []
        cursor = "*"
        with httpx.Client(timeout=30.0) as client:
            while len(out) < limit:
                resp = client.get(
                    f"{self.BASE}/search",
                    params={
                        "query": full_query,
                        "format": "json",
                        "resultType": "core",
                        "pageSize": min(page_size, limit - len(out)),
                        "cursorMark": cursor,
                        **({"sort": sort} if sort else {}),
                    },
                )
                resp.raise_for_status()
                body = resp.json()
                results = (body.get("resultList") or {}).get("result") or []
                if not results:
                    break
                out.extend(self.to_raw(r) for r in results)

                next_cursor = body.get("nextCursorMark")
                # Europe PMC repeats the cursor on the final page — that, not an
                # empty result set, is how paging actually terminates.
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
        log.info("europepmc search: query=%r returned=%d", query, len(out))
        return out[:limit]

    def fetch_one(self, identifier: str) -> RawProtocol:
        """Fetch a single article by DOI, PMID or PMCID."""
        import httpx

        resp = httpx.get(
            f"{self.BASE}/search",
            params={"query": identifier, "format": "json", "resultType": "core", "pageSize": 1},
            timeout=30.0,
        )
        resp.raise_for_status()
        results = ((resp.json().get("resultList") or {}).get("result")) or []
        if not results:
            raise LookupError(f"Europe PMC has no record for {identifier!r}.")
        return self.to_raw(results[0])


def for_source(name: str = "protocols.io") -> ProtocolSource:
    if name in ("protocols.io", "protocols_io", "pio"):
        return ProtocolsIoSource()
    if name in ("europepmc", "europe_pmc", "pmc", "epmc"):
        return EuropePmcSource()
    raise ValueError(
        f"No protocol source adapter for {name!r} (v1 sources: protocols.io, europepmc)."
    )

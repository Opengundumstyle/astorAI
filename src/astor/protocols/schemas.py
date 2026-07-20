"""Neutral DTOs for the protocol ingest pipeline.

`RawProtocol` is the source-adapter boundary output (§9 stage 1): every source
(protocols.io today, PMC/manual later) maps onto this shape, and nothing
downstream sees a source-native schema. This mirrors how `catalog.schemas`
insulates the marketplace from supplier file formats.
"""
from __future__ import annotations

import math
from enum import Enum

from pydantic import BaseModel, Field


class License(str, Enum):
    """Per-protocol licence — travels with every derived record (INV PI-2) so
    the redistribution decision is enforceable per line across mixed sources.

    The `.redistributable` / `.commercial_expression_ok` gates below are what the
    ingest licence-gate step keys on. UNKNOWN is treated as most-restrictive
    until a human classifies it — fail closed, never fail open."""

    CC0 = "CC0"
    CC_BY = "CC-BY"
    CC_BY_NC = "CC-BY-NC"          # non-commercial: extract FACTS + paraphrase only
    CC_BY_NC_SA = "CC-BY-NC-SA"    # non-commercial + share-alike; present in the PMC OA subset
    CC_BY_NC_ND = "CC-BY-NC-ND"    # non-commercial + no-derivatives; the most restrictive CC
    # Share-alike WITHOUT the NC clause: commercial use is permitted, but derivatives
    # inherit the licence. Deliberately NOT redistributable by default — serving a
    # derived index built on SA content can force that obligation onto our own output.
    # That is a business/counsel call, not a default. Fail closed until made.
    CC_BY_SA = "CC-BY-SA"
    ALL_RIGHTS_RESERVED = "all-rights-reserved"  # link-out only; do not ingest content
    UNKNOWN = "unknown"            # fail closed until classified

    @property
    def redistributable(self) -> bool:
        """May we store the derived (fact-restructured) content in our index and
        serve it? True only for permissive licences. NC is excluded here because
        commercial serving is the use; ARR/UNKNOWN are link-out only."""
        return self in (License.CC0, License.CC_BY)

    @property
    def commercial_expression_ok(self) -> bool:
        """May we reproduce the source's *expression* (prose/figures) commercially?
        Never for NC/ARR/UNKNOWN. Note: facts (steps, materials, quantities) are
        not copyrightable (§10) and may be extracted regardless — but only
        `redistributable` licences may have that derived output *served*."""
        return self in (License.CC0, License.CC_BY)


class ReviewSignal(BaseModel):
    """The source's own engagement/quality signal — the v1 ranking input
    (ARCHITECTURE.md §4 override: 'rank by protocols.io review'). The adapter
    fills whatever the source actually exposes; unfilled fields stay None and
    `rank_score` degrades gracefully.

    NOTE: field→source mapping is provisional until verified against live
    protocols.io payloads (whether a star `rating` exists vs. only engagement)."""

    citations: int | None = None         # Europe PMC citedByCount — the real citation signal
    # VERIFIED 2026-07-19 against a live protocols.io v4 payload: that API exposes
    # NO star rating and NO ratings count. Its `stats` object carries engagement
    # only (views, votes, bookmarks, comments, forks, runs, exports). These two
    # fields therefore stay unfilled for protocols.io and exist for sources that
    # do have them. See the note in ARCHITECTURE §4 on the "review-ranked" premise.
    rating: float | None = None
    ratings_count: int | None = None
    views: int | None = None
    votes: int | None = None
    bookmarks: int | None = None
    forks: int | None = None
    comments: int | None = None
    # protocols.io `peer_reviewed`: reviewed by a journal. A categorical QUALITY
    # signal, not a popularity magnitude — deliberately kept out of `rank_score`
    # and applied as a separate, higher-priority sort key (see filtering.py), so a
    # peer-reviewed protocol is never buried by a merely popular one.
    peer_reviewed: bool | None = None

    @property
    def rank_score(self) -> float:
        """Scalar for 'highest review first', in descending order of signal quality:

          1. citation count — what §4 actually wanted ('review/popularity standing in
             for citation-count'); Europe PMC exposes it directly, so when it is present
             we use the real thing rather than the proxy.
          2. explicit rating weighted by volume, so a lone 5.0 does not outrank a 4.6
             from hundreds of raters.
          3. weighted engagement, for sources with neither.

        Log-scaled so the tiers land in a comparable order of magnitude and one
        runaway-cited outlier cannot swamp the list.

        CAVEAT: this is only meaningfully comparable WITHIN one source. A PMC citation
        count and a protocols.io engagement score are different units on the same
        number line. Rank per-source and merge deliberately; cross-source
        normalisation is deferred until we have both corpora to calibrate against.

        Popularity is a SELECTION signal, not a correctness guarantee — see §4 override.
        """
        if self.citations is not None:
            return 10.0 * math.log1p(self.citations)
        if self.rating is not None and self.ratings_count:
            return self.rating * math.log1p(self.ratings_count)
        return float(
            (self.bookmarks or 0) * 3
            + (self.forks or 0) * 2
            + (self.votes or 0) * 2
            + (self.comments or 0) * 1
            + (self.views or 0) * 0.01
        )


class RawMaterial(BaseModel):
    """A materials-list line as the source states it — FACTS only. Downstream
    (role classification → spec → SKU) is a separate transform; this just
    captures what the protocol names."""

    name: str
    amount: str | None = None            # free text as stated ("50 mL", "2x")
    vendor: str | None = None
    catalog_no: str | None = None


class RawStep(BaseModel):
    number: int | None = None
    text: str


class RawProtocol(BaseModel):
    """Source-neutral protocol (§9 stage 1 output). Carries attribution + licence
    so every downstream record can enforce the redistribution gate and link back."""

    source: str                          # "protocols.io"
    source_id: str                       # source-native id (NOT an engine id)
    source_uri: str                      # canonical link back (attribution / link-out)
    title: str
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    version: str | None = None
    license: License = License.UNKNOWN
    steps: list[RawStep] = Field(default_factory=list)
    materials: list[RawMaterial] = Field(default_factory=list)
    review: ReviewSignal = Field(default_factory=ReviewSignal)
    fetched_at: str | None = None        # ISO8601, stamped by the caller
    raw: dict = Field(default_factory=dict)  # source-specific escape hatch

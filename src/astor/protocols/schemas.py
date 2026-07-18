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

    rating: float | None = None          # e.g. 0–5 stars, if the source has them
    ratings_count: int | None = None
    views: int | None = None
    bookmarks: int | None = None
    forks: int | None = None
    comments: int | None = None

    @property
    def rank_score(self) -> float:
        """Scalar for 'highest review first'. Prefer an explicit rating weighted
        by volume (so a lone 5.0 doesn't outrank a 4.6 from hundreds); fall back
        to weighted engagement when no rating exists. Popularity is a SELECTION
        signal, not a correctness guarantee — see §4 override."""
        if self.rating is not None and self.ratings_count:
            return self.rating * math.log1p(self.ratings_count)
        return float(
            (self.bookmarks or 0) * 3
            + (self.forks or 0) * 2
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

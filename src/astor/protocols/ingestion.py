"""Protocol ingestion as discrete, idempotent steps — same shape as
`catalog.ingestion`. In v1 they run in-process and in sequence; the boundaries
are drawn so a queue/worker fleet can drive them unchanged at volume.

Pipeline (a thin slice of ARCHITECTURE.md §9; contract: astor-protocol-ingest.v1.yaml):
    fetch → map to RawProtocol → licence gate → rank by review → upsert

Persistence lives in `persistence.py` rather than here, so the pure stages stay
drivable with no database at all — that is what keeps the whole pipeline testable
offline and what lets a dry run prove the mapping before anything is written.

The heavy §9 transform (role classify, procurement filter, spec, completeness
augmentation) is intentionally NOT here yet — it needs real data to design, and
this scaffold's job is to get that data in cleanly behind the adapter boundary.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from astor.protocols import filtering
from astor.protocols.schemas import RawProtocol
from astor.protocols.sources import ProtocolSource, for_source

log = logging.getLogger(__name__)

# Core biotech techniques for the v1 corpus. Deliberately narrow: the goal is
# 50–200 high-quality protocols across the areas we actually sell into, not
# platform-wide coverage. Expand from observed customer search behaviour.
DEFAULT_QUERIES = (
    "PCR protocol",
    "qPCR quantitative real-time PCR protocol",
    "mammalian cell culture protocol",
    "antibody purification protocol",
    "western blot protocol",
    "next-generation sequencing library preparation protocol",
    "CRISPR Cas9 genome editing protocol",
    "plasmid DNA extraction protocol",
)


@dataclass
class ProtocolIngestResult:
    source: str
    fetched: int = 0
    servable: int = 0
    link_out_only: int = 0
    ranked: list[RawProtocol] = field(default_factory=list)
    # Kept, not discarded: these stay citable/link-outable and are persisted with
    # attribution only (PI-5). Carrying them makes the gate's effect observable.
    link_out: list[RawProtocol] = field(default_factory=list)


# -- step 1: fetch/map (offline path: pre-fetched payloads) ----------------- #
def map_step(source: ProtocolSource, payloads: list[dict]) -> list[RawProtocol]:
    """Map already-fetched source payloads → RawProtocol. Kept separate from the
    networked fetch so the pipeline is fully testable offline (feed saved JSON)."""
    return [source.to_raw(p) for p in payloads]


# -- step 2: licence gate (legal enforcement) ------------------------------- #
def gate_step(protocols: list[RawProtocol]) -> tuple[list[RawProtocol], list[RawProtocol]]:
    return filtering.license_gate(protocols)


# -- step 3: rank by review (selection policy) ------------------------------ #
def rank_step(protocols: list[RawProtocol]) -> list[RawProtocol]:
    return filtering.rank_by_review(protocols)


def run_from_search(
    queries: tuple[str, ...] | list[str] = DEFAULT_QUERIES,
    *,
    source_name: str = "europepmc",
    limit_per_query: int = 25,
) -> ProtocolIngestResult:
    """Live entry point for sources whose ToS permits systematic retrieval.

    Only sources exposing `search` can be driven this way, which is exactly the
    set we are allowed to sweep — protocols.io has no `search` here precisely
    because sweeping it is the restricted act (PI-6). Queries run one at a time
    rather than concurrently: batch, don't one-shot.
    """
    source = for_source(source_name)
    if not hasattr(source, "search"):
        raise RuntimeError(
            f"Source {source_name!r} exposes no search(): it cannot be swept. "
            "Gated sources are driven from explicit identifiers or saved payloads."
        )

    stamp = datetime.now(timezone.utc).isoformat()
    raws: list[RawProtocol] = []
    for q in queries:
        found = source.search(q, limit=limit_per_query)
        for p in found:
            p.fetched_at = stamp
        raws.extend(found)
        log.info("query %r -> %d records", q, len(found))

    servable, link_out = gate_step(raws)
    ranked = rank_step(servable)
    log.info(
        "protocol ingest (live): source=%s fetched=%d servable=%d link_out=%d",
        source_name, len(raws), len(servable), len(link_out),
    )
    return ProtocolIngestResult(
        source=source_name,
        fetched=len(raws),
        servable=len(servable),
        link_out_only=len(link_out),
        ranked=ranked,
        link_out=link_out,
    )


def run_from_payloads(payloads: list[dict], source_name: str = "protocols.io") -> ProtocolIngestResult:
    """Offline entry point: source payloads in → gated, review-ranked out.
    The live-fetch entry point wraps this once the ToS gate (§14 #1) is cleared."""
    source = for_source(source_name)
    raws = map_step(source, payloads)
    servable, link_out = gate_step(raws)
    ranked = rank_step(servable)
    log.info(
        "protocol ingest: source=%s fetched=%d servable=%d link_out=%d",
        source_name, len(raws), len(servable), len(link_out),
    )
    return ProtocolIngestResult(
        source=source_name,
        fetched=len(raws),
        servable=len(servable),
        link_out_only=len(link_out),
        ranked=ranked,
        link_out=link_out,
    )

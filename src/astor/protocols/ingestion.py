"""Protocol ingestion as discrete, idempotent steps — same shape as
`catalog.ingestion`. In v1 they run in-process and in sequence; the boundaries
are drawn so a queue/worker fleet can drive them unchanged at volume.

Pipeline (a thin slice of ARCHITECTURE.md §9):
    fetch → map to RawProtocol → licence gate → rank by review → (persist: TODO)

The heavy §9 transform (role classify, procurement filter, spec, completeness
augmentation) is intentionally NOT here yet — it needs real data to design, and
this scaffold's job is to get that data in cleanly behind the adapter boundary.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from astor.protocols import filtering
from astor.protocols.schemas import RawProtocol
from astor.protocols.sources import ProtocolSource, for_source

log = logging.getLogger(__name__)


@dataclass
class ProtocolIngestResult:
    source: str
    fetched: int = 0
    servable: int = 0
    link_out_only: int = 0
    ranked: list[RawProtocol] = field(default_factory=list)


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
    )

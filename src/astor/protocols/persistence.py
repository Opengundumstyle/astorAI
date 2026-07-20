"""Persist `RawProtocol` records — the step that turns a fetched batch into the
Plane 2 corpus. Idempotent by construction so re-runs and the weekly sync are safe.

Two things this module is responsible for, and they are separate on purpose:

  * **Identity** — which incoming protocol is "the same as" a stored one. DOI is
    the cross-source key (build notes); `(source, source_id)` is the within-source
    fallback for records that have no DOI.
  * **Licence enforcement at rest** — content columns are populated ONLY when the
    licence permits redistribution. Enforcing it here (not just in the pipeline
    that calls us) means no caller can write unservable content by mistake.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from astor.db.models import Protocol
from astor.protocols.schemas import RawProtocol

log = logging.getLogger(__name__)


@dataclass
class UpsertResult:
    created: int = 0
    updated: int = 0
    servable: int = 0
    link_out_only: int = 0
    deduped_in_batch: int = 0

    @property
    def written(self) -> int:
        return self.created + self.updated


def dedupe_by_doi(protocols: Iterable[RawProtocol]) -> tuple[list[RawProtocol], int]:
    """Collapse same-DOI duplicates *within one batch*, keeping the highest-ranked.

    Necessary because DB-level dedupe only catches collisions against rows already
    stored. A single "top 100" pull can easily contain the same DOI twice (a
    protocol and its own newer version, or the same work indexed under two source
    ids), and those would race each other into the unique index inside one flush.

    Protocols without a DOI are passed through untouched — they are deduped later
    on `(source, source_id)` instead.
    """
    best: dict[str, RawProtocol] = {}
    out: list[RawProtocol] = []
    dropped = 0

    for p in protocols:
        if not p.doi:
            out.append(p)
            continue
        key = p.doi.strip().lower()
        incumbent = best.get(key)
        if incumbent is None:
            best[key] = p
        else:
            dropped += 1
            if p.review.rank_score > incumbent.review.rank_score:
                best[key] = p

    out.extend(best.values())
    return out, dropped


def _parse_fetched_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        log.warning("unparseable fetched_at %r — storing NULL", value)
        return None


def _find_existing(session: Session, p: RawProtocol) -> Protocol | None:
    """DOI first (cross-source identity), then the within-source natural key."""
    if p.doi:
        row = session.scalar(select(Protocol).where(Protocol.doi == p.doi))
        if row is not None:
            return row
    return session.scalar(
        select(Protocol).where(
            Protocol.source == p.source, Protocol.source_id == p.source_id
        )
    )


def _apply(row: Protocol, p: RawProtocol) -> bool:
    """Copy a RawProtocol onto a row, applying the licence gate. Returns servable."""
    servable = p.license.redistributable

    row.source = p.source
    row.source_id = p.source_id
    row.source_uri = p.source_uri
    row.title = p.title
    row.authors = list(p.authors)
    row.doi = p.doi
    row.version = p.version
    row.license = p.license.value
    row.servable = servable
    row.review = p.review.model_dump()
    row.rank_score = p.review.rank_score
    row.fetched_at = _parse_fetched_at(p.fetched_at)

    # The gate, at rest: attribution is always kept so the protocol stays citable
    # and link-outable, but CONTENT is written only under a redistributable licence.
    row.steps = [s.model_dump() for s in p.steps] if servable else []
    row.materials = [m.model_dump() for m in p.materials] if servable else []

    return servable


def upsert_protocols(session: Session, protocols: Iterable[RawProtocol]) -> UpsertResult:
    """Upsert a batch. Safe to re-run: identical input produces zero net change.

    Flushes once per row so that a duplicate inside the batch surfaces as an
    update to the row just written rather than a unique-violation at commit.
    """
    batch, deduped = dedupe_by_doi(protocols)
    result = UpsertResult(deduped_in_batch=deduped)

    for p in batch:
        row = _find_existing(session, p)
        if row is None:
            row = Protocol()
            session.add(row)
            result.created += 1
        else:
            result.updated += 1

        if _apply(row, p):
            result.servable += 1
        else:
            result.link_out_only += 1

        session.flush()

    log.info(
        "protocol upsert: created=%d updated=%d servable=%d link_out=%d deduped=%d",
        result.created, result.updated, result.servable,
        result.link_out_only, result.deduped_in_batch,
    )
    return result

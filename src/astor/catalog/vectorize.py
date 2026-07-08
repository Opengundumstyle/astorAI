"""Batch vectorization: populate Product.embedding for rows that lack it.

The matcher embeds lazily one product at a time; that is fine for incremental
matching but wasteful for a first full-catalog pass. This batches the embed call
(providers support batching) and writes vectors back, so "ingest -> vectorize"
is one efficient sweep independent of whether equivalence matching runs.

Idempotent: only products with embedding IS NULL are touched, so re-runs are
cheap no-ops. Uses the SAME canonical_text the matcher embeds, so the vector a
product gets here is identical to what the matcher would have produced.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from astor.catalog.embeddings import Embedder, get_embedder
from astor.catalog.normalization import NormalizedProduct, canonical_text
from astor.db.models import Product

log = logging.getLogger(__name__)


def _canonical_text_for(p: Product) -> str:
    return canonical_text(
        NormalizedProduct(
            category=p.category, name=p.name, brand=p.brand, mpn=p.mpn, specs=p.specs or {}
        )
    )


def vectorize_missing(
    session: Session, embedder: Embedder | None = None, batch_size: int = 128
) -> int:
    """Embed every Product with a NULL embedding. Returns count embedded."""
    embedder = embedder or get_embedder()
    rows = list(
        session.scalars(select(Product).where(Product.embedding.is_(None))).all()
    )
    if not rows:
        log.info("vectorize: nothing to do (all products embedded)")
        return 0

    done = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        vectors = embedder.embed([_canonical_text_for(p) for p in chunk])
        for p, vec in zip(chunk, vectors):
            p.embedding = vec
        session.flush()
        done += len(chunk)
        log.info("vectorize: %d/%d", done, len(rows))
    return done

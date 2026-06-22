"""Equivalence matcher: the China<->US engine and the project's top technical risk.

Strategy (sub-quadratic from day one -- a scaling seam):
  1. Embed the product's canonical text.
  2. Candidate generation ("blocking") via pgvector ANN -> top-K neighbours.
  3. Score each candidate = vector similarity + lightweight attribute rules.
  4. Classify exact vs substitute by threshold; persist as Equivalence rows
     with a confidence. Low-confidence pairs are written `reviewed=False` for a
     human review queue (you, in M1).

Swap DevEmbedder for a real provider before trusting the numbers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from astor.catalog.embeddings import Embedder, get_embedder
from astor.catalog.normalization import NormalizedProduct, canonical_text
from astor.config import settings
from astor.db.models import Equivalence, Product

log = logging.getLogger(__name__)


@dataclass
class MatchCandidate:
    product_id: str
    confidence: float
    kind: str


def _attribute_bonus(a: Product, b: Product) -> float:
    """Cheap structured agreement signal layered on top of vector similarity."""
    score = 0.0
    if a.category and a.category == b.category:
        score += 0.05
    a_specs, b_specs = a.specs or {}, b.specs or {}
    shared = set(a_specs) & set(b_specs)
    if shared:
        agree = sum(1 for k in shared if str(a_specs[k]).lower() == str(b_specs[k]).lower())
        score += 0.10 * (agree / len(shared))
    # Same brand + same mpn is a definitional exact match.
    if a.brand and a.brand == b.brand and a.mpn and a.mpn == b.mpn:
        score += 0.50
    return score


def _classify(confidence: float) -> str | None:
    if confidence >= settings.equiv_exact_threshold:
        return "exact"
    if confidence >= settings.equiv_substitute_threshold:
        return "substitute"
    return None


def ensure_embedding(session: Session, product: Product, embedder: Embedder) -> None:
    if product.embedding is None:
        np = NormalizedProduct(
            category=product.category, name=product.name, brand=product.brand,
            mpn=product.mpn, specs=product.specs or {},
        )
        product.embedding = embedder.embed([canonical_text(np)])[0]
        session.flush()


def match_product(
    session: Session, product_id: str, embedder: Embedder | None = None
) -> list[MatchCandidate]:
    embedder = embedder or get_embedder()
    product = session.get(Product, product_id)
    if product is None:
        return []
    ensure_embedding(session, product, embedder)

    # Candidate generation via ANN: nearest neighbours by cosine distance,
    # excluding self. pgvector returns distance; similarity = 1 - distance.
    neighbours = session.execute(
        select(Product, Product.embedding.cosine_distance(product.embedding).label("dist"))
        .where(Product.id != product.id, Product.embedding.isnot(None))
        .order_by("dist")
        .limit(settings.equiv_candidates)
    ).all()

    written: list[MatchCandidate] = []
    for cand, dist in neighbours:
        similarity = 1.0 - float(dist)
        confidence = min(1.0, similarity + _attribute_bonus(product, cand))
        kind = _classify(confidence)
        if kind is None:
            continue
        session.execute(
            insert(Equivalence)
            .values(
                product_id=product.id, equivalent_id=cand.id,
                confidence=confidence, kind=kind, method="vector+rules",
                reviewed=False,
            )
            .on_conflict_do_update(
                constraint="uq_equivalence_pair",
                set_={"confidence": confidence, "kind": kind},
            )
        )
        written.append(MatchCandidate(str(cand.id), round(confidence, 4), kind))
    log.info("matched %s -> %d equivalences", product_id, len(written))
    return written

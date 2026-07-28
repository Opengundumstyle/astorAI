"""Equivalence matcher: the China<->US engine and the project's top technical risk.

Strategy (sub-quadratic from day one -- a scaling seam):
  1. Embed the product's canonical text.
  2. Candidate generation ("blocking") via pgvector ANN -> top-K neighbours.
  3. Score each candidate with the shared `scoring` module (vector similarity +
     attribute rules) -- the SAME logic the accuracy harness measures.
  4. Classify exact vs substitute by threshold; persist as Equivalence rows with
     a confidence. Low-confidence pairs are written reviewed=False for a human
     review queue (you, in M1).

Swap DevEmbedder for a real provider before trusting the numbers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from astor.catalog import scoring
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


def _view(product: Product) -> scoring.ProductView:
    return scoring.ProductView(
        category=product.category, name=product.name, brand=product.brand,
        mpn=product.mpn, specs=product.specs or {},
    )


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
    pview = _view(product)

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
        conf = scoring.confidence(similarity, pview, _view(cand))
        kind = scoring.classify(conf, settings.equiv_exact_threshold, settings.equiv_substitute_threshold)
        if kind is None:
            continue
        session.execute(
            insert(Equivalence)
            .values(
                product_id=product.id, equivalent_id=cand.id,
                confidence=conf, kind=kind, method="vector+rules", reviewed=False,
            )
            .on_conflict_do_update(
                constraint="uq_equivalence_pair",
                set_={"confidence": conf, "kind": kind},
            )
        )
        written.append(MatchCandidate(str(cand.id), round(conf, 4), kind))
    log.info("matched %s -> %d equivalences", product_id, len(written))
    return written


def sample_exact_rate(session: Session, embedder: Embedder, sample_n: int = 500) -> float:
    """Read-only: fraction of sampled products with >=1 candidate classified exact.

    An absurdly high rate (see the gate's max_exact_rate) means thresholds are too
    loose. Writes nothing to the DB.
    """
    sample = list(
        session.execute(
            select(Product).where(Product.embedding.isnot(None)).order_by(func.random()).limit(sample_n)
        ).scalars()
    )
    if not sample:
        return 0.0
    with_exact = 0
    for product in sample:
        pview = _view(product)
        neighbours = session.execute(
            select(Product, Product.embedding.cosine_distance(product.embedding).label("dist"))
            .where(Product.id != product.id, Product.embedding.isnot(None))
            .order_by("dist")
            .limit(settings.equiv_candidates)
        ).all()
        for cand, dist in neighbours:
            conf = scoring.confidence(1.0 - float(dist), pview, _view(cand))
            if scoring.classify(conf, settings.equiv_exact_threshold, settings.equiv_substitute_threshold) == "exact":
                with_exact += 1
                break
    return with_exact / len(sample)


def rematch_all(session: Session, embedder: Embedder | None = None) -> int:
    """Wipe and rebuild the equivalences table on the current embeddings."""
    embedder = embedder or get_embedder()
    session.execute(sql_text("TRUNCATE TABLE equivalences"))
    session.flush()
    product_ids = [row[0] for row in session.execute(select(Product.id)).all()]
    total = 0
    for pid in product_ids:
        total += len(match_product(session, str(pid), embedder))
    log.info("rematch_all wrote %d equivalences over %d products", total, len(product_ids))
    return total

"""Link a protocol's materials to Astor product SKUs.

Same shape as catalog/matcher.py (embedding -> pgvector ANN -> scoring), but
material -> product. Exact catalog identity is taken first; a semantic name match
is the fallback that gives coverage, since most protocols.io materials are
name-only. Candidate generation is injected (`exact_lookup`/`ann_candidates`) so
the orchestration is testable with no database.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select

from astor.catalog import scoring
from astor.catalog.embeddings import Embedder, get_embedder
from astor.config import settings
from astor.db.models import Product

log = logging.getLogger(__name__)


@dataclass
class MaterialMatch:
    product_id: str
    material_name: str
    confidence: float
    kind: str      # 'exact' | 'substitute'
    method: str    # 'catalog' | 'vector+rules'


def _material_view(name: str, vendor: str | None, catalog_no: str | None) -> scoring.ProductView:
    # category unknown for a protocol material; specs empty. vendor->brand,
    # catalog_no->mpn so scoring's +0.50 brand+mpn bonus fires when they agree.
    return scoring.ProductView(category="", name=name, brand=vendor, mpn=catalog_no)


def _view(product) -> scoring.ProductView:
    return scoring.ProductView(
        category=product.category or "", name=product.name,
        brand=product.brand, mpn=product.mpn, specs=product.specs or {},
    )


def _find_exact(session, vendor: str | None, catalog_no: str | None):
    """Deterministic catalog identity, case-insensitive. Only when BOTH present."""
    if not (vendor and catalog_no):
        return None
    return session.scalar(
        select(Product).where(
            func.lower(Product.brand) == vendor.strip().lower(),
            func.lower(Product.mpn) == catalog_no.strip().lower(),
        )
    )


def _ann_candidates(session, vector, limit: int) -> list[tuple]:
    """Top-`limit` products by cosine distance to `vector` (embedding NOT NULL)."""
    rows = session.execute(
        select(Product, Product.embedding.cosine_distance(vector).label("dist"))
        .where(Product.embedding.isnot(None))
        .order_by("dist")
        .limit(limit)
    ).all()
    return [(p, float(d)) for p, d in rows]


def match_protocol_materials(
    session, protocol, embedder: Embedder | None = None,
    *, exact_lookup=_find_exact, ann_candidates=_ann_candidates,
) -> list[MaterialMatch]:
    embedder = embedder or get_embedder()
    seen: set[str] = set()
    matches: list[MaterialMatch] = []

    for m in (protocol.materials or []):
        name = (m.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        vendor, catalog_no = m.get("vendor"), m.get("catalog_no")

        prod = exact_lookup(session, vendor, catalog_no)
        if prod is not None:
            matches.append(MaterialMatch(str(prod.id), name, 1.0, "exact", "catalog"))
            continue

        try:
            vec = embedder.embed([name])[0]
        except Exception as exc:  # noqa: BLE001 — one material must not abort the batch
            log.warning("embed failed for material %r: %s", name, exc)
            continue

        mview = _material_view(name, vendor, catalog_no)
        best: MaterialMatch | None = None
        for cand, dist in ann_candidates(session, vec, settings.equiv_candidates):
            conf = scoring.confidence(1.0 - dist, mview, _view(cand))
            kind = scoring.classify(
                conf, settings.equiv_exact_threshold, settings.equiv_substitute_threshold)
            if kind is None:
                continue
            if best is None or conf > best.confidence:
                best = MaterialMatch(str(cand.id), name, round(conf, 4), kind, "vector+rules")
        if best is not None:
            matches.append(best)

    return matches

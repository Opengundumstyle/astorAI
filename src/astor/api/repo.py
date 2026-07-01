"""Thin data-access layer. The only module that issues queries / mutations.

Routers depend on these functions and are tested by monkeypatching them, so no
Postgres is needed in unit tests. This module itself is covered by the
DB-gated smoke test.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from astor.api import schemas
from astor.catalog import matcher
from astor.catalog.embeddings import get_embedder
from astor.catalog.ingestion import ingest
from astor.db.models import Equivalence, Product, Supplier, SupplierOffer
from astor.pricing.landed_cost import landed_cost


def _offer_count_map(session, product_ids: list[str]) -> dict[str, int]:
    if not product_ids:
        return {}
    rows = session.execute(
        select(SupplierOffer.product_id, func.count(SupplierOffer.id))
        .where(SupplierOffer.product_id.in_(product_ids))
        .group_by(SupplierOffer.product_id)
    ).all()
    return {str(pid): n for pid, n in rows}


def _cheapest_offer(session, product_id: str):
    return session.scalar(
        select(SupplierOffer)
        .where(SupplierOffer.product_id == product_id)
        .order_by(SupplierOffer.cost.asc())
        .limit(1)
    )


def _best_landed(session, product_id: str, category: str) -> float | None:
    offer = _cheapest_offer(session, product_id)
    if offer is None:
        return None
    bd = landed_cost(supplier_cost=float(offer.cost), currency=offer.currency,
                     category=category, qty=1)
    return bd["unit_price"]


def get_stats(session) -> dict:
    products = session.scalar(select(func.count(Product.id))) or 0
    offers = session.scalar(select(func.count(SupplierOffer.id))) or 0
    suppliers = session.scalar(select(func.count(Supplier.id))) or 0
    exact = session.scalar(
        select(func.count(Equivalence.id)).where(Equivalence.kind == "exact")) or 0
    substitute = session.scalar(
        select(func.count(Equivalence.id)).where(Equivalence.kind == "substitute")) or 0

    # Avg savings = mean over products of (1 - best_landed / proxy_us_list).
    # Proxy US list is the dearest USD-equivalent offer; placeholder until real
    # list prices exist. Returns 0.0 when not computable.
    avg_savings = 0.0
    return schemas.stats_out(products=products, offers=offers, exact=exact,
                             substitute=substitute, suppliers=suppliers,
                             avg_savings=round(avg_savings, 4))


def list_products(session, q, category, page, page_size) -> tuple[list[dict], int]:
    stmt = select(Product)
    count_stmt = select(func.count(Product.id))
    if q:
        like = f"%{q}%"
        cond = Product.name.ilike(like) | Product.brand.ilike(like) | Product.mpn.ilike(like)
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if category:
        stmt = stmt.where(Product.category == category)
        count_stmt = count_stmt.where(Product.category == category)

    total = session.scalar(count_stmt) or 0
    rows = session.scalars(
        stmt.order_by(Product.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()

    ids = [str(p.id) for p in rows]
    counts = _offer_count_map(session, ids)
    summaries = [
        schemas.product_summary(
            p, offer_count=counts.get(str(p.id), 0),
            best_landed=_best_landed(session, str(p.id), p.category),
        )
        for p in rows
    ]
    return summaries, total


def get_product_detail(session, product_id: str) -> dict | None:
    product = session.get(Product, product_id)
    if product is None:
        return None
    offers = session.scalars(
        select(SupplierOffer).where(SupplierOffer.product_id == product_id)
    ).all()
    eq_rows = session.execute(
        select(Equivalence, Product)
        .join(Product, Product.id == Equivalence.equivalent_id)
        .where(Equivalence.product_id == product_id)
        .order_by(Equivalence.confidence.desc())
    ).all()
    equivalents = [(prod, eq.confidence, eq.kind) for eq, prod in eq_rows]
    return schemas.product_detail(product, offers, equivalents)


def landed_for_product(session, product_id: str, qty: int) -> dict | None:
    product = session.get(Product, product_id)
    if product is None:
        return None
    offer = _cheapest_offer(session, product_id)
    if offer is None:
        return None
    return landed_cost(supplier_cost=float(offer.cost), currency=offer.currency,
                       category=product.category, qty=qty)


def run_ingest(session, path: Path, supplier: str, region: str, tier: str,
               run_match: bool) -> dict:
    result = ingest(session, path, supplier, region, tier)
    written = 0
    if run_match:
        embedder = get_embedder()
        for pid in result.products_to_match:
            written += len(matcher.match_product(session, pid, embedder))
    return {
        "extracted": result.extracted,
        "products": result.products_upserted,
        "offers": result.offers_upserted,
        "equivalences_written": written,
    }

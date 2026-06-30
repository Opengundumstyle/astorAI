"""DTO builder functions: ORM object -> plain dict (OPS shape).

Builders never touch the database; they only read attributes. Role gating
(roles.py) is applied by the routers after building.
"""
from __future__ import annotations

from astor.api.skus import astor_sku


def product_summary(product, offer_count: int, best_landed: float | None) -> dict:
    return {
        "id": str(product.id),
        "astor_sku": astor_sku(product.id),
        "name": product.name,
        "category": product.category,
        "brand": product.brand,
        "mpn": product.mpn,
        "region": None,  # a product spans suppliers; region lives on offers
        "offer_count": offer_count,
        "best_landed": best_landed,
    }


def offer_out(offer) -> dict:
    return {
        "supplier": offer.supplier.name,
        "region": offer.supplier.region,
        "supplier_sku": offer.supplier_sku,
        "pack_size": offer.pack_size,
        "cost": float(offer.cost),
        "currency": offer.currency,
        "stock": offer.stock,
        "lead_time_days": offer.lead_time_days,
    }


def equivalent_out(product, confidence: float, kind: str) -> dict:
    return {
        "id": str(product.id),
        "astor_sku": astor_sku(product.id),
        "name": product.name,
        "brand": product.brand,
        "region": None,
        "supplier": None,
        "confidence": round(float(confidence), 4),
        "kind": kind,
    }


def product_detail(product, offers: list, equivalents: list) -> dict:
    return {
        "id": str(product.id),
        "astor_sku": astor_sku(product.id),
        "name": product.name,
        "category": product.category,
        "brand": product.brand,
        "mpn": product.mpn,
        "specs": product.specs or {},
        "offers": [offer_out(o) for o in offers],
        "equivalents": [equivalent_out(p, c, k) for (p, c, k) in equivalents],
    }


def stats_out(*, products: int, offers: int, exact: int, substitute: int,
              suppliers: int, avg_savings: float) -> dict:
    return {
        "products": products,
        "offers": offers,
        "equivalences": {"exact": exact, "substitute": substitute,
                         "total": exact + substitute},
        "suppliers": suppliers,
        "avg_savings": avg_savings,
    }

"""DTO builder functions: ORM object -> plain dict (OPS shape).

Builders never touch the database; they only read attributes. Role gating
(roles.py) is applied by the routers after building.
"""
from __future__ import annotations

from astor.api.skus import storefront_sku
from astor.config import settings


def product_summary(product, offer_count: int, best_landed: float | None,
                    astor_sku: str | None) -> dict:
    """`astor_sku` is the storefront (Shopify variant) SKU, resolved by the caller
    from the product's offers -- see `skus.storefront_sku`. None when the product
    has no Shopify channel offer; never a placeholder."""
    return {
        "id": str(product.id),
        "astor_sku": astor_sku,
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


def equivalent_out(product, confidence: float, kind: str, astor_sku: str | None) -> dict:
    return {
        "id": str(product.id),
        "astor_sku": astor_sku,
        "name": product.name,
        "brand": product.brand,
        "region": None,
        "supplier": None,
        "confidence": round(float(confidence), 4),
        "kind": kind,
    }


def product_detail(product, offers: list, equivalents: list) -> dict:
    """`equivalents` is a list of (product, confidence, kind, astor_sku)."""
    return {
        "id": str(product.id),
        "astor_sku": storefront_sku(offers, settings.shopify_supplier_name),
        "name": product.name,
        "category": product.category,
        "brand": product.brand,
        "mpn": product.mpn,
        "specs": product.specs or {},
        "offers": [offer_out(o) for o in offers],
        "equivalents": [equivalent_out(p, c, k, sku) for (p, c, k, sku) in equivalents],
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

"""Map raw ExtractedProduct -> canonical NormalizedItem.

Kept deliberately small in M1; this is where unit/category canonicalization
grows as categories are added. Pure functions = trivially testable.
"""
from __future__ import annotations

import re

from astor.catalog.schemas import (
    ExtractedProduct,
    NormalizedItem,
    NormalizedOffer,
    NormalizedProduct,
)

_CATEGORY_ALIASES = {
    "molecular biology": "molecular_biology",
    "molbio": "molecular_biology",
    "pcr": "molecular_biology",
    "qpcr": "molecular_biology",
    "consumable": "consumables",
    "consumables": "consumables",
    "plasticware": "consumables",
    "cell culture": "cell_culture",
    "antibody": "antibodies",
    "antibodies": "antibodies",
}


def _canon_category(raw: str | None) -> str:
    if not raw:
        return "uncategorized"
    key = raw.strip().lower()
    return _CATEGORY_ALIASES.get(key, re.sub(r"[^a-z0-9]+", "_", key).strip("_"))


def normalize(item: ExtractedProduct) -> NormalizedItem:
    product = NormalizedProduct(
        category=_canon_category(item.category),
        name=item.name.strip(),
        brand=(item.brand or None) and item.brand.strip(),
        mpn=(item.mpn or None) and item.mpn.strip(),
        specs=item.specs or {},
    )
    offer = NormalizedOffer(
        supplier_sku=item.supplier_sku.strip(),
        pack_size=item.pack_size,
        cost=float(item.cost) if item.cost is not None else 0.0,
        currency=item.currency,
        stock=item.stock,
        lead_time_days=item.lead_time_days,
    )
    return NormalizedItem(product=product, offer=offer)


def canonical_text(product: NormalizedProduct) -> str:
    """The string the matcher embeds. Stable + spec-aware."""
    parts = [product.brand or "", product.name, product.category]
    for k, v in sorted((product.specs or {}).items()):
        parts.append(f"{k}={v}")
    return " | ".join(p for p in parts if p)

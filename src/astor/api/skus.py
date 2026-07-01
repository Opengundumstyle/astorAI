"""Buyer-facing Astor SKU, derived from the product UUID (M1: not persisted)."""
from __future__ import annotations

import uuid


def astor_sku(product_id: str | uuid.UUID) -> str:
    """`ASR-` + first 6 hex chars of the UUID, uppercased. e.g. ASR-7F3A21."""
    hexed = uuid.UUID(str(product_id)).hex
    return f"ASR-{hexed[:6].upper()}"

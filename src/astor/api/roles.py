"""Server-side origin-confidentiality gating.

Buyers must never receive product origin, supplier identity, manufacturer
brand/MPN, or internal cost internals. Gating uses an ALLOWLIST (fail-closed)
so any future field added to a DTO is suppressed for buyers by default.
"""
from __future__ import annotations

OPS = "ops"
BUYER = "buyer"

_PRODUCT_BUYER_KEYS = ("id", "astor_sku", "name", "category", "offer_count", "best_landed")
_DETAIL_BUYER_KEYS = ("id", "astor_sku", "name", "category", "specs", "equivalents")
_EQUIVALENT_BUYER_KEYS = ("id", "astor_sku", "name", "confidence", "kind")
_LANDED_BUYER_KEYS = ("currency", "qty", "unit_price", "line_total")


def normalize_role(value: str | None) -> str:
    return BUYER if (value or "").strip().lower() == BUYER else OPS


def _keep(d: dict, keys: tuple[str, ...]) -> dict:
    return {k: d[k] for k in keys if k in d}


def gate_product(d: dict, role: str) -> dict:
    if normalize_role(role) == OPS:
        return dict(d)
    return _keep(d, _PRODUCT_BUYER_KEYS)


def gate_detail(d: dict, role: str) -> dict:
    if normalize_role(role) == OPS:
        return dict(d)
    out = _keep(d, _DETAIL_BUYER_KEYS)
    if "equivalents" in d:
        out["equivalents"] = [_keep(e, _EQUIVALENT_BUYER_KEYS) for e in d["equivalents"]]
    return out


def gate_landed(d: dict, role: str) -> dict:
    if normalize_role(role) == OPS:
        return dict(d)
    return _keep(d, _LANDED_BUYER_KEYS)

"""Server-side origin-confidentiality gating.

Buyers must never receive product origin, supplier identity, manufacturer
brand/MPN, or internal cost internals. Gating drops the keys structurally so
the confidential data never reaches the wire.
"""
from __future__ import annotations

OPS = "ops"
BUYER = "buyer"

_PRODUCT_CONFIDENTIAL = ("brand", "mpn", "region", "offers")
_DETAIL_CONFIDENTIAL = ("brand", "mpn", "offers")
_EQUIVALENT_CONFIDENTIAL = ("brand", "region", "supplier")
_LANDED_CONFIDENTIAL = ("ex_works", "tariff", "duty_rate", "freight", "margin")


def normalize_role(value: str | None) -> str:
    return BUYER if (value or "").strip().lower() == BUYER else OPS


def _drop(d: dict, keys: tuple[str, ...]) -> dict:
    return {k: v for k, v in d.items() if k not in keys}


def gate_product(d: dict, role: str) -> dict:
    if normalize_role(role) == OPS:
        return d
    return _drop(d, _PRODUCT_CONFIDENTIAL)


def gate_detail(d: dict, role: str) -> dict:
    if normalize_role(role) == OPS:
        return d
    out = _drop(d, _DETAIL_CONFIDENTIAL)
    out["equivalents"] = [_drop(e, _EQUIVALENT_CONFIDENTIAL) for e in d.get("equivalents", [])]
    return out


def gate_landed(d: dict, role: str) -> dict:
    if normalize_role(role) == OPS:
        return d
    return _drop(d, _LANDED_CONFIDENTIAL)

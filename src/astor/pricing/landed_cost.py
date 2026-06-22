"""Landed-cost engine: turns a supplier offer into a transparent breakdown.

Returned dict is stored verbatim on OrderLine.landed_cost (JSONB) so the
customer-facing price is always auditable. Tariff/HS logic is intentionally a
simple, replaceable rule table in M1.
"""
from __future__ import annotations

# Placeholder duty rates by category (Section 301 + surcharge, stacked).
# Replace with real HS-code-driven classification before quoting for real.
_DUTY_BY_CATEGORY = {
    "molecular_biology": 0.25,
    "consumables": 0.30,
    "cell_culture": 0.25,
    "antibodies": 0.20,
}
_DEFAULT_DUTY = 0.27


def landed_cost(
    *,
    supplier_cost: float,
    currency: str,
    category: str,
    qty: int = 1,
    fx_to_usd: float = 0.14,
    freight_per_unit_usd: float = 1.50,
    margin_rate: float = 0.20,
) -> dict:
    ex_works_usd = supplier_cost * (fx_to_usd if currency != "USD" else 1.0)
    duty_rate = _DUTY_BY_CATEGORY.get(category, _DEFAULT_DUTY)
    tariff = ex_works_usd * duty_rate
    subtotal = ex_works_usd + tariff + freight_per_unit_usd
    margin = subtotal * margin_rate
    unit_price = subtotal + margin
    return {
        "currency": "USD",
        "qty": qty,
        "ex_works": round(ex_works_usd, 4),
        "tariff": round(tariff, 4),
        "duty_rate": duty_rate,
        "freight": round(freight_per_unit_usd, 4),
        "margin": round(margin, 4),
        "unit_price": round(unit_price, 4),
        "line_total": round(unit_price * qty, 4),
    }
